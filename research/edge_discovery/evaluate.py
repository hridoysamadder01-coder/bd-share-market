"""Scoring candidates, and the vocabulary for saying what a score does not prove.

The metric is the prereg's: **AUC-ROC for UP vs DOWN at the horizon, FLAT rows
excluded, every candidate scored on the identical eligible rows.** Nothing here
invents a metric, and nothing here fits a threshold.

Two of this repository's own recorded failures shape the code:

* **I-012** — 102 "hits" turned out to be 28 distinct events. So the sample size
  reported is never the row count. A run of frames on one symbol with the same
  reading is one **episode** (prereg: first frame anchors it, 300 s cooldown,
  later frames of the same episode are not counted again), and `episodes` is
  what the ranking sorts on.
* **I-013** — "lift > 1" passed rows that beat the base by under 10 %. So AUC is
  reported with a confidence interval, and a candidate whose interval spans 0.5
  is WEAK however good its point estimate looks.

Status vocabulary
-----------------
=================== ==========================================================
PROMISING           AUC interval clear of 0.5 on an adequate sample
WEAK                measured, interval spans 0.5
KILLED              measured, and the interval is clear of 0.5 the WRONG way,
                    or the effect reverses against its own control
INSUFFICIENT_SAMPLE too few independent blocks to make any statement
NOT_OBSERVABLE      the inputs the candidate needs were never observed
=================== ==========================================================

**INSUFFICIENT_SAMPLE is never converted to KILLED.** It is the difference
between "we looked and it is not there" and "we could not look", and collapsing
them is how a real effect gets buried under a thin denominator.

The block for resampling is a **whole session**, per the prereg. With one session
available there is exactly one block, so no interval over sessions exists at all
— `blocks` reports it and the status is INSUFFICIENT_SAMPLE regardless of how
strong the point estimate is. Within-session intervals are still reported, and
are labelled as what they are: a statement about frames, not about sessions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .labels import PRIMARY_HORIZON_S, assert_no_labels

# Below this many independent episodes, no statement is made at all.
MIN_EPISODES = 30
# The prereg's block is a whole session. Fewer than this many and there is no
# between-session interval to compute, whatever the within-session numbers say.
MIN_BLOCKS = 3
EPISODE_COOLDOWN_S = 300
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 7

PROMISING, WEAK, KILLED = "PROMISING", "WEAK", "KILLED"
INSUFFICIENT_SAMPLE, NOT_OBSERVABLE = "INSUFFICIENT_SAMPLE", "NOT_OBSERVABLE"


def auc(score: np.ndarray, positive: np.ndarray) -> float:
    """Rank AUC with ties at half credit — the Mann-Whitney form.

    Ties matter here: a book that is exactly balanced gives the identical score to
    many rows, and counting ties as wins would inflate every imbalance candidate.
    """
    s = np.asarray(score, dtype=float)
    y = np.asarray(positive, dtype=bool)
    ok = np.isfinite(s)
    s, y = s[ok], y[ok]
    npos, nneg = int(y.sum()), int((~y).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    r = pd.Series(s).rank(method="average").to_numpy()
    return float((r[y].sum() - npos * (npos + 1) / 2.0) / (npos * nneg))


def episodes(d: pd.DataFrame, fires: np.ndarray,
             cooldown_s: float = EPISODE_COOLDOWN_S) -> np.ndarray:
    """Mark the first frame of each firing run per symbol; suppress the rest.

    The prereg's rule exactly: an episode starts when the candidate fires and no
    episode of the same direction is already active for that symbol, and the
    episode's first frame supplies the single outcome.
    """
    out = np.zeros(len(d), dtype=bool)
    t = d["t_frame"].values.astype("datetime64[ns]").astype("int64") / 1e9
    f = np.asarray(fires, dtype=bool)
    for _, idx in d.groupby("symbol", sort=False).indices.items():
        idx = np.asarray(idx)
        last = -np.inf
        for i in idx[np.argsort(t[idx])]:
            if f[i] and (t[i] - last) > cooldown_s:
                out[i] = True
                last = t[i]
    return out


def _boot_ci(score: np.ndarray, positive: np.ndarray, blocks: np.ndarray,
             replicates: int = BOOTSTRAP_REPLICATES,
             seed: int = BOOTSTRAP_SEED) -> Tuple[Optional[float], Optional[float]]:
    """Block bootstrap over whole blocks. Returns (None, None) with too few blocks."""
    uniq = pd.unique(blocks)
    if len(uniq) < MIN_BLOCKS:
        return None, None
    rng = np.random.default_rng(seed)
    by = {b: np.where(blocks == b)[0] for b in uniq}
    vals: List[float] = []
    for _ in range(replicates):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        sel = np.concatenate([by[b] for b in pick])
        a = auc(score[sel], positive[sel])
        if np.isfinite(a):
            vals.append(a)
    if len(vals) < replicates // 10:
        return None, None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def _within_ci(score: np.ndarray, positive: np.ndarray,
               replicates: int = 500, seed: int = BOOTSTRAP_SEED) -> Tuple[float, float]:
    """A row-resampled interval. Says something about frames, NOT about sessions."""
    rng = np.random.default_rng(seed)
    n = len(score)
    vals = []
    for _ in range(replicates):
        sel = rng.integers(0, n, n)
        a = auc(score[sel], positive[sel])
        if np.isfinite(a):
            vals.append(a)
    if not vals:
        return float("nan"), float("nan")
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


@dataclass
class Result:
    name: str
    horizon_s: int
    rows: int
    episodes: int
    blocks: int
    auc: float
    ci_lo: Optional[float]
    ci_hi: Optional[float]
    ci_kind: str
    up: int
    down: int
    status: str
    note: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> Dict[str, Any]:
        return {"candidate": self.name, "horizon_s": self.horizon_s, "rows": self.rows,
                "episodes": self.episodes, "blocks": self.blocks,
                "auc": round(self.auc, 5) if np.isfinite(self.auc) else None,
                "ci_lo": round(self.ci_lo, 5) if self.ci_lo is not None else None,
                "ci_hi": round(self.ci_hi, 5) if self.ci_hi is not None else None,
                "ci_kind": self.ci_kind, "up": self.up, "down": self.down,
                "status": self.status, "note": self.note, **self.extra}


def evaluate(d: pd.DataFrame, name: str, score_col: str, horizon_s: int = PRIMARY_HORIZON_S,
             block_col: str = "session", fires: Optional[np.ndarray] = None,
             extra: Optional[Dict[str, Any]] = None) -> Result:
    """One candidate at one horizon on the eligible rows. Reports; decides nothing.

    Eligible = the label is valid AND not FLAT (the prereg excludes flat rows from
    AUC) AND the score exists. Every candidate compared against another must be
    evaluated on the same eligible set, which is why the caller passes the frame.
    """
    assert_no_labels([score_col])
    tk = d[f"fwd_mid_ticks_{horizon_s}"]
    s = pd.to_numeric(d[score_col], errors="coerce")
    elig = d[f"fwd_valid_{horizon_s}"].astype(bool) & (tk != 0) & s.notna()
    if fires is not None:
        elig &= np.asarray(fires, dtype=bool)

    sub = d[elig]
    if not len(sub):
        return Result(name, horizon_s, 0, 0, 0, float("nan"), None, None, "none", 0, 0,
                      NOT_OBSERVABLE, f"{score_col} is never present on a labelled row",
                      extra or {})

    sv = pd.to_numeric(sub[score_col], errors="coerce").to_numpy(dtype=float)
    pos = (sub[f"fwd_mid_ticks_{horizon_s}"] > 0).to_numpy(dtype=bool)
    blocks = sub[block_col].to_numpy() if block_col in sub else np.zeros(len(sub))
    n_blocks = int(len(pd.unique(blocks)))

    # Episodes: for a continuous score the independent unit is a run of the same
    # sign on one symbol, which is what the prereg's cooldown collapses.
    ep = int(episodes(sub, sv > np.nanmedian(sv)).sum() + episodes(sub, sv <= np.nanmedian(sv)).sum())
    a = auc(sv, pos)

    lo, hi = _boot_ci(sv, pos, blocks)
    kind = "block_bootstrap_over_sessions"
    if lo is None:
        lo, hi = _within_ci(sv, pos)
        kind = "within_session_rows_only"

    if not np.isfinite(a):
        status, note = NOT_OBSERVABLE, "no UP/DOWN contrast on the eligible rows"
    elif n_blocks < MIN_BLOCKS:
        status = INSUFFICIENT_SAMPLE
        note = (f"{n_blocks} independent block(s); the prereg's block is a whole session and "
                f"{MIN_BLOCKS} are needed before any between-session statement exists. "
                f"The interval shown is over rows within the session and does not "
                f"generalise. NOT a kill.")
    elif ep < MIN_EPISODES:
        status = INSUFFICIENT_SAMPLE
        note = f"{ep} independent episodes < {MIN_EPISODES}. NOT a kill."
    elif lo is not None and lo > 0.5:
        status, note = PROMISING, "interval clear of 0.5"
    elif hi is not None and hi < 0.5:
        status, note = KILLED, "interval clear of 0.5 in the wrong direction"
    else:
        status, note = WEAK, "interval spans 0.5"

    return Result(name, horizon_s, int(len(sub)), ep, n_blocks, a, lo, hi, kind,
                  int(pos.sum()), int((~pos).sum()), status, note, extra or {})


def response_curve(d: pd.DataFrame, columns: Sequence[str], parameter: Sequence[float],
                   horizon_s: int = PRIMARY_HORIZON_S, block_col: str = "session",
                   label: str = "lambda") -> pd.DataFrame:
    """The same measurement across a parameter grid, on the identical eligible rows.

    A curve is more informative than its best point: a flat curve says the
    parameter carries nothing, and an interior peak says the market has a scale.
    Reading only the maximum turns a grid into a fitted threshold, which is
    exactly the failure `REJECTED_CANDIDATES.md` records.
    """
    rows = []
    for col, p in zip(columns, parameter):
        r = evaluate(d, col, col, horizon_s, block_col)
        rows.append({label: p, **r.as_row()})
    return pd.DataFrame(rows)


def split_by(d: pd.DataFrame, name: str, score_col: str, group_col: str,
             horizon_s: int = PRIMARY_HORIZON_S, block_col: str = "session",
             min_rows: int = 100) -> pd.DataFrame:
    """The same candidate measured inside each group. Never pooled across groups.

    A candidate that works only in one bucket, or flips sign between buckets, is
    telling you about the bucket rather than about the market.
    """
    rows = []
    for g, sub in d.groupby(group_col, sort=True):
        if len(sub) < min_rows:
            rows.append({group_col: g, "candidate": name, "rows": int(len(sub)),
                         "status": INSUFFICIENT_SAMPLE,
                         "note": f"{len(sub)} rows < {min_rows}"})
            continue
        rows.append({group_col: g, **evaluate(sub, name, score_col, horizon_s, block_col).as_row()})
    return pd.DataFrame(rows)


def rank(results: Iterable[Result]) -> pd.DataFrame:
    """Ordered by |AUC - 0.5|, with status carried so a thin sample cannot lead.

    Sorting on effect size alone puts a two-episode fluke at the top. The sort key
    is (status rank, |AUC-0.5|), so anything INSUFFICIENT_SAMPLE sits below
    everything measured however large its point estimate.
    """
    order = {PROMISING: 0, WEAK: 1, KILLED: 2, INSUFFICIENT_SAMPLE: 3, NOT_OBSERVABLE: 4}
    df = pd.DataFrame([r.as_row() for r in results])
    if df.empty:
        return df
    df["_edge"] = (df["auc"].astype(float) - 0.5).abs()
    df["_st"] = df["status"].map(order).fillna(9)
    df = df.sort_values(["_st", "_edge"], ascending=[True, False]).drop(columns=["_st", "_edge"])
    return df.reset_index(drop=True)
