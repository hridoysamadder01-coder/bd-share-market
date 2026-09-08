"""Frame-horizon outcomes and the matched-control evaluation the E1 reference used.

Two horizon systems exist in this repository and they are not the same thing:

* `tower/experiment.py` measures forward outcomes in **seconds** (60/180/600),
  which is what `micro/MICRO_PREREG.json` freezes.
* The E1 reference numbers in the handoff are in **frames** (h2/h4/h8), from the
  micro pipeline, where the median gap was ~43.7 s so h4 ≈ 3 minutes.

Both are reproduced here rather than one being silently substituted for the
other. A frame horizon on an irregular clock needs its own validity gate, so the
per-frame analogue of the prereg's `(t_fwd − t) ≤ 2H` rule is used: a frame
window whose elapsed time exceeds `MAX_FRAME_GAP_MULT` × h × the session's own
median cadence is void. Without it, "4 frames ahead" silently becomes forty
minutes across a capture gap — and a capture gap is exactly where the market moved.

The matching rule is `tower/experiment.py`'s, not a new one: for each signal row,
draw with replacement one non-signal row with the same **(symbol, tod_bucket,
spread_bucket)**. `matched_controls` and `episodes` are imported from there so
the reference number this reproduces is computed by the same code that produced
it. What is added here is the reporting the handoff asks for and that function
does not carry: MFE, MAE, time to first favourable and adverse move, symbol
coverage, and the raw-vs-matched pair side by side.

Every quantity is an outcome. Nothing in this module may become a feature, and
`assert_no_labels` in `labels.py` is what enforces that on the scoring side.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from tower.experiment import episodes as tower_episodes
from tower.experiment import matched_controls as tower_matched_controls

FRAME_HORIZONS: Tuple[int, ...] = (2, 4, 8)
PRIMARY_FRAME_H = 4
# A frame window may stretch to this multiple of (h × median cadence) before the
# two ends stop being adjacent observations of one process.
MAX_FRAME_GAP_MULT = 3.0
N_TOD_BUCKETS = 5


def add_frame_outcomes(states: pd.DataFrame, horizons: Sequence[int] = FRAME_HORIZONS,
                       max_gap_mult: float = MAX_FRAME_GAP_MULT) -> pd.DataFrame:
    """Forward mid move in ticks, h FRAMES ahead, same symbol, same session.

    Also records, per horizon, the path rather than only the endpoint:
    `fwd_mfe_h{h}` and `fwd_mae_h{h}` are the best and worst mid reached anywhere
    inside the window, and `fwd_t_fav_h{h}` / `fwd_t_adv_h{h}` are the seconds to
    the first favourable and first adverse tick. An endpoint alone cannot say
    whether a position would have survived the path to it.
    """
    d = states.sort_values(["symbol", "t_frame"], kind="mergesort").reset_index(drop=True).copy()
    tick = d["tick_size"].astype(float).to_numpy()
    mid = d["mid"].astype(float).to_numpy()
    tsec = d["t_frame"].values.astype("datetime64[ns]").astype("int64") / 1e9

    cadence = float(pd.Series(d["frame_dt_s"]).median(skipna=True))
    d.attrs["median_cadence_s"] = cadence

    for h in horizons:
        ticks = np.full(len(d), np.nan)
        mfe = np.full(len(d), np.nan)
        mae = np.full(len(d), np.nan)
        t_fav = np.full(len(d), np.nan)
        t_adv = np.full(len(d), np.nan)
        dt_w = np.full(len(d), np.nan)
        for _, idx in d.groupby(["symbol", "session"], sort=False).indices.items():
            idx = np.asarray(idx)
            order = idx[np.argsort(tsec[idx])]
            m, t, tk = mid[order], tsec[order], tick[order]
            n = len(order)
            for i in range(n - h):
                j = i + h
                if not (np.isfinite(m[i]) and np.isfinite(m[j]) and tk[i] > 0):
                    continue
                span = t[j] - t[i]
                if not np.isfinite(span) or span <= 0:
                    continue
                if np.isfinite(cadence) and cadence > 0 and span > max_gap_mult * h * cadence:
                    continue                      # a gap, not a window
                path = (m[i + 1:j + 1] - m[i]) / tk[i]
                good = np.isfinite(path)
                if not good.any():
                    continue
                ticks[order[i]] = (m[j] - m[i]) / tk[i]
                mfe[order[i]] = float(np.nanmax(path))
                mae[order[i]] = float(np.nanmin(path))
                dt_w[order[i]] = span
                up = np.where(good & (path > 0))[0]
                dn = np.where(good & (path < 0))[0]
                if len(up):
                    t_fav[order[i]] = t[i + 1 + up[0]] - t[i]
                if len(dn):
                    t_adv[order[i]] = t[i + 1 + dn[0]] - t[i]
        d[f"fwd_mid_ticks_h{h}"] = ticks
        d[f"fwd_valid_h{h}"] = np.isfinite(ticks)
        d[f"fwd_up_h{h}"] = np.where(np.isfinite(ticks), ticks > 0, np.nan)
        d[f"fwd_down_h{h}"] = np.where(np.isfinite(ticks), ticks < 0, np.nan)
        d[f"fwd_move_h{h}"] = np.where(np.isfinite(ticks), ticks != 0, np.nan)
        d[f"fwd_mfe_h{h}"] = mfe
        d[f"fwd_mae_h{h}"] = mae
        d[f"fwd_t_fav_h{h}"] = t_fav
        d[f"fwd_t_adv_h{h}"] = t_adv
        d[f"fwd_window_s_h{h}"] = dt_w
    return d


def add_matching_keys(d: pd.DataFrame, n_tod_buckets: int = N_TOD_BUCKETS) -> pd.DataFrame:
    """`tod_bucket` and `spread_bucket`, by `tower/experiment.py`'s rule exactly.

    Time-of-day is a within-symbol percentile rank rather than a wall-clock cut,
    so a symbol that only trades in bursts still contributes to every bucket.
    """
    out = d.copy()
    tod = (out["t_frame"].dt.hour * 3600 + out["t_frame"].dt.minute * 60
           + out["t_frame"].dt.second).astype(float)
    rank = tod.groupby(out["symbol"]).rank(pct=True, method="first")
    out["tod_bucket"] = rank.mul(n_tod_buckets).clip(upper=n_tod_buckets - 1e-9).astype(int)
    out["spread_bucket"] = out["spread_ticks"].astype(float).clip(upper=3).fillna(-1).round().astype(int)
    return out


# The matched control is DRAWN with replacement, so a single-seed matched lift is
# one sample of a random quantity, not the quantity. Measured on this capture its
# seed-to-seed spread is 0.7-1.8 pp (E1 ranges +13.76 to +17.28 across 20 seeds),
# which is larger than most of the differences between candidates. Every matched
# lift here is therefore the MEAN over this many independent draws, reported with
# its own standard deviation, so a reader can see when two candidates are not
# actually distinguishable.
CONTROL_DRAWS = 20


def evaluate_binary(d: pd.DataFrame, name: str, sig: pd.Series, h: int,
                    outcome: str = "up", seed: int = 7,
                    extra: Optional[Dict[str, Any]] = None,
                    draws: int = CONTROL_DRAWS) -> Dict[str, Any]:
    """One candidate, one horizon: raw and matched, with the path and the coverage.

    `matched_lift_pp` is in **percentage points** — P(outcome | signal) minus
    P(outcome | same symbol, same time-of-day bucket, same spread bucket) — and is
    the mean over `draws` independent control draws, with `matched_lift_sd`
    beside it. The raw rate is always reported too: a matched lift with no raw
    rate is unreadable, and a raw rate with no matched control is what
    `REJECTED_CANDIDATES.md` is full of.
    """
    out_col = f"fwd_{outcome}_h{h}"
    valid_col, ticks_col = f"fwd_valid_h{h}", f"fwd_mid_ticks_h{h}"
    fs = d.reset_index(drop=True)
    s = pd.Series(np.asarray(sig, dtype=object), index=fs.index).fillna(False).astype(bool)
    valid = fs[valid_col].astype(bool)
    sv = s & valid
    n_sig_valid = int(sv.sum())

    base_p = float(fs.loc[valid, out_col].mean()) if valid.any() else np.nan
    ps: List[float] = []
    tks: List[float] = []
    n_c = 0
    for k in range(max(1, draws)):
        rng = np.random.default_rng(seed + k)
        p_k, t_k, n_k = tower_matched_controls(fs, s, out_col, valid_col, ticks_col, None, rng)
        if np.isfinite(p_k):
            ps.append(float(p_k))
            tks.append(float(t_k))
            n_c = int(n_k)
    c_p = float(np.mean(ps)) if ps else np.nan
    c_ticks = float(np.mean(tks)) if tks else np.nan
    c_sd = float(np.std(ps, ddof=1)) if len(ps) > 1 else np.nan

    row: Dict[str, Any] = {
        "candidate_id": name, "horizon_frames": h, "outcome": outcome,
        "n_rows": int(len(fs)), "n_valid": int(valid.sum()),
        "n_signal": int(s.sum()), "n_signal_valid": n_sig_valid,
        "episodes": int(tower_episodes(s, fs["symbol"])),
        "symbol_coverage": int(fs.loc[sv, "symbol"].nunique()) if n_sig_valid else 0,
        "share_of_valid_rows": round(n_sig_valid / max(int(valid.sum()), 1), 5),
        "base_p_outcome": _r(base_p),
        "n_matched": int(n_c), "ctrl_p_outcome": _r(c_p),
        "control_draws": len(ps),
    }
    if n_sig_valid:
        g = fs.loc[sv]
        row.update({
            "p_up": _r(float(g[f"fwd_up_h{h}"].mean())),
            "p_down": _r(float(g[f"fwd_down_h{h}"].mean())),
            "p_outcome": _r(float(g[out_col].mean())),
            "mean_fwd_ticks": _r(float(g[ticks_col].mean())),
            "median_fwd_ticks": _r(float(g[ticks_col].median())),
            "mfe": _r(float(g[f"fwd_mfe_h{h}"].mean())),
            "mae": _r(float(g[f"fwd_mae_h{h}"].mean())),
            "median_t_favourable_s": _r(float(g[f"fwd_t_fav_h{h}"].median())),
            "median_t_adverse_s": _r(float(g[f"fwd_t_adv_h{h}"].median())),
        })
    else:
        for k in ("p_up", "p_down", "p_outcome", "mean_fwd_ticks", "median_fwd_ticks",
                  "mfe", "mae", "median_t_favourable_s", "median_t_adverse_s"):
            row[k] = None
    row["lift_vs_base_pp"] = _r(100.0 * (row["p_outcome"] - base_p)) if n_sig_valid else None
    row["matched_lift_pp"] = (_r(100.0 * (row["p_outcome"] - c_p))
                              if (n_c and n_sig_valid and np.isfinite(c_p)) else None)
    # the spread of the lift across control draws — the number's own precision
    row["matched_lift_sd"] = (_r(100.0 * c_sd) if (n_c and n_sig_valid and np.isfinite(c_sd))
                              else None)
    row["ctrl_mean_ticks"] = _r(c_ticks)
    row["ticks_vs_ctrl"] = (_r(row["mean_fwd_ticks"] - c_ticks)
                            if (n_c and n_sig_valid and np.isfinite(c_ticks)) else None)
    if extra:
        row.update(extra)
    return row


def _r(v: Any, nd: int = 5) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, nd)


def decile_response(d: pd.DataFrame, score_col: str, h: int, outcome: str = "up",
                    q: int = 10, min_rows: int = 30) -> pd.DataFrame:
    """P(outcome) and mean forward ticks by score decile — the response curve.

    A monotone curve across deciles is a far stronger statement than one lucky
    cutoff, and it is the thing a threshold cannot tell you: whether more of the
    quantity means more of the outcome, or whether one bin carries everything.
    """
    valid = d[f"fwd_valid_h{h}"].astype(bool)
    s = pd.to_numeric(d[score_col], errors="coerce")
    sub = d[valid & s.notna()].copy()
    if len(sub) < q * min_rows:
        q = max(2, len(sub) // max(min_rows, 1))
    if q < 2 or not len(sub):
        return pd.DataFrame([{"score": score_col, "horizon_frames": h, "decile": None,
                              "rows": int(len(sub)), "note": "too few rows for a curve"}])
    sub["_bin"] = pd.qcut(pd.to_numeric(sub[score_col], errors="coerce").rank(method="first"),
                          q, labels=False)
    rows = []
    for b, g in sub.groupby("_bin", sort=True):
        rows.append({
            "score": score_col, "horizon_frames": h, "decile": int(b) + 1, "of": int(q),
            "rows": int(len(g)),
            "score_min": _r(float(pd.to_numeric(g[score_col], errors="coerce").min())),
            "score_max": _r(float(pd.to_numeric(g[score_col], errors="coerce").max())),
            "p_up": _r(float(g[f"fwd_up_h{h}"].mean())),
            "p_down": _r(float(g[f"fwd_down_h{h}"].mean())),
            "mean_fwd_ticks": _r(float(g[f"fwd_mid_ticks_h{h}"].mean())),
            "mae": _r(float(g[f"fwd_mae_h{h}"].mean())),
            "mfe": _r(float(g[f"fwd_mfe_h{h}"].mean())),
        })
    t = pd.DataFrame(rows)
    t["monotone_p_up"] = bool(t["p_up"].is_monotonic_increasing)
    t["monotone_mean_ticks"] = bool(t["mean_fwd_ticks"].is_monotonic_increasing)
    return t


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Rank correlation — how monotone a response curve is, in one number."""
    a, b = pd.Series(list(x)), pd.Series(list(y))
    ok = a.notna() & b.notna()
    if ok.sum() < 3:
        return float("nan")
    return float(a[ok].rank().corr(b[ok].rank()))
