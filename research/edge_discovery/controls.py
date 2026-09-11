"""The controls that decide whether a ranked candidate means anything.

`REJECTED_CANDIDATES.md` is a list of scores that looked real and were not, and
each entry names the control that would have caught it. Those controls are the
ones implemented here, so a candidate cannot reach a report without facing them:

**C1 — null scores.** A constant and a seeded random score, on the identical
eligible rows. Both must land at 0.5. If they do not, the metric is picking up
the session's own up/down mix and every number above it is inflated. (The
session used here is 43 % up / 57 % down, which is exactly the situation where
an unchecked metric drifts.)

**C2 — the past move.** P45-4 collapsed from lift 1.84 to 1.29 the moment
*today's own move* entered the comparison. The equivalent here is the recent mid
change: if it scores as well as the candidate, the candidate is measuring "this
already moved", not "this is about to move".

**C3 — per symbol.** I-012's lesson is that a pooled count hides a concentrated
one. A candidate carried by three names out of fourteen is a fact about those
names.

**C4 — time split.** The first and second halves of the session, scored apart.
Not out-of-sample — it is the same session — but a candidate that only works in
one half is not describing a stable mechanism.

**C5 — the incremental gate.** The one that killed P45-5 at the last step. A new
quantity must beat the *strongest existing baseline* on the same rows, and the
margin needs an interval that clears zero. A candidate can pass every absolute
bar and still fail here, and failing here is failing.

**C6 — episode anchoring.** One row per firing run per symbol, per the prereg's
cooldown. Overlapping horizons make adjacent frames near-duplicates; the episode
count is the honest n.

**C7 — the ablation.** For a quantity built on an idea, the idea's own twin
without it. TLPI's idea is tick distance, so its twin is the same decay on level
rank; the difference between them is the contribution of distance alone, and it
is measured inside the two book-shape populations rather than pooled, because on
a mirror-symmetric ladder the two are algebraically identical and pooling dilutes
the comparison with rows where it cannot say anything.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .evaluate import auc, episodes
from .labels import PRIMARY_HORIZON_S


def _eligible(f: pd.DataFrame, horizon_s: int) -> pd.DataFrame:
    tk = f[f"fwd_mid_ticks_{horizon_s}"]
    return f[f[f"fwd_valid_{horizon_s}"].astype(bool) & (tk != 0)].copy()


def paired_auc_difference(e: pd.DataFrame, a_col: str, b_col: str, horizon_s: int,
                          block_col: str = "symbol", replicates: int = 2000,
                          seed: int = 7) -> Dict[str, Any]:
    """AUC(a) − AUC(b) with a block bootstrap over `block_col`.

    Paired on identical rows: both scores must be present, or neither is counted.
    Comparing a candidate measured on 1,997 rows against a baseline measured on
    1,330 is not a comparison.
    """
    a = pd.to_numeric(e[a_col], errors="coerce").to_numpy(dtype=float)
    b = pd.to_numeric(e[b_col], errors="coerce").to_numpy(dtype=float)
    pos = (e[f"fwd_mid_ticks_{horizon_s}"] > 0).to_numpy(dtype=bool)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b, pos = a[ok], b[ok], pos[ok]
    blocks = e[block_col].to_numpy()[ok]
    uq = pd.unique(blocks)
    point = auc(a, pos) - auc(b, pos)
    if len(uq) < 2:
        return {"a": a_col, "b": b_col, "rows": int(ok.sum()), "blocks": int(len(uq)),
                "auc_a": auc(a, pos), "auc_b": auc(b, pos), "diff": point,
                "ci_lo": None, "ci_hi": None, "p_diff_gt_0": None,
                "established": False, "note": "fewer than 2 blocks — no interval exists"}
    by = {s: np.where(blocks == s)[0] for s in uq}
    rng = np.random.default_rng(seed)
    d: List[float] = []
    for _ in range(replicates):
        sel = np.concatenate([by[s] for s in rng.choice(uq, size=len(uq), replace=True)])
        va, vb = auc(a[sel], pos[sel]), auc(b[sel], pos[sel])
        if np.isfinite(va) and np.isfinite(vb):
            d.append(va - vb)
    arr = np.asarray(d)
    lo, hi = (float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))) if len(arr) else (None, None)
    return {"a": a_col, "b": b_col, "rows": int(ok.sum()), "blocks": int(len(uq)),
            "auc_a": round(auc(a, pos), 5), "auc_b": round(auc(b, pos), 5),
            "diff": round(point, 5),
            "ci_lo": round(lo, 5) if lo is not None else None,
            "ci_hi": round(hi, 5) if hi is not None else None,
            "p_diff_gt_0": round(float((arr > 0).mean()), 4) if len(arr) else None,
            "established": bool(lo is not None and lo > 0),
            "block_col": block_col,
            "note": "blocks are %s. The prereg's block is a whole SESSION; with one "
                    "session this is a within-session consistency check, not the "
                    "preregistered inference." % block_col}


def ablation(f: pd.DataFrame, candidate_col: str, twin_col: str, horizon_s: int,
             population_col: str = "book_shape", block_col: str = "symbol") -> Dict[str, Any]:
    """The candidate against its own twin-without-the-idea, inside each population.

    Pooled, the comparison is diluted by every row where the two are identical by
    construction. Split, it says where the idea contributes and where it does not
    — which is a mechanism, not a score.
    """
    e = _eligible(f, horizon_s)
    out: Dict[str, Any] = {"candidate": candidate_col, "twin": twin_col,
                           "pooled": paired_auc_difference(e, candidate_col, twin_col,
                                                           horizon_s, block_col)}
    by: List[Dict[str, Any]] = []
    if population_col in e.columns:
        for g, sub in e.groupby(population_col, sort=True):
            if len(sub) < 100:
                by.append({"population": str(g), "rows": int(len(sub)),
                           "note": "fewer than 100 eligible rows"})
                continue
            r = paired_auc_difference(sub, candidate_col, twin_col, horizon_s, block_col)
            by.append({"population": str(g), **r})
    out["by_population"] = by
    return out


def run_controls(f: pd.DataFrame, candidate_col: str, baseline_cols: Sequence[str],
                 horizon_s: int = PRIMARY_HORIZON_S,
                 past_move_cols: Sequence[str] = ("mid_change_1_ticks", "mid_change_w_ticks"),
                 seed: int = 7) -> Dict[str, Any]:
    """Every control above, for one candidate against the baselines it must beat."""
    e = _eligible(f, horizon_s)
    if not len(e):
        return {"candidate": candidate_col, "horizon_s": horizon_s,
                "note": "no eligible rows"}
    pos = (e[f"fwd_mid_ticks_{horizon_s}"] > 0).to_numpy(dtype=bool)
    rng = np.random.default_rng(seed)

    c1 = {"constant": round(auc(np.ones(len(e)), pos), 5),
          "random": round(auc(rng.normal(size=len(e)), pos), 5),
          "up_rate": round(float(pos.mean()), 5),
          "clean": None}
    c1["clean"] = bool(abs(c1["constant"] - 0.5) < 1e-9 and abs(c1["random"] - 0.5) < 0.05)

    cand = pd.to_numeric(e[candidate_col], errors="coerce").to_numpy(dtype=float)
    c2 = {}
    for col in past_move_cols:
        if col in e:
            c2[col] = round(auc(pd.to_numeric(e[col], errors="coerce").to_numpy(dtype=float), pos), 5)
    c2["candidate"] = round(auc(cand, pos), 5)
    c2["already_moved_confound"] = bool(
        any(abs(v - 0.5) >= abs(c2["candidate"] - 0.5) for k, v in c2.items()
            if k not in ("candidate", "already_moved_confound")))

    per_symbol = []
    for sym, g in e.groupby("symbol", sort=True):
        p = (g[f"fwd_mid_ticks_{horizon_s}"] > 0).to_numpy(dtype=bool)
        if p.sum() < 10 or (~p).sum() < 10:
            per_symbol.append({"symbol": sym, "rows": int(len(g)), "auc": None,
                               "note": "fewer than 10 of a class"})
            continue
        per_symbol.append({"symbol": sym, "rows": int(len(g)),
                           "auc": round(auc(pd.to_numeric(g[candidate_col], errors="coerce")
                                            .to_numpy(dtype=float), p), 5), "note": ""})
    scored = [r["auc"] for r in per_symbol if r["auc"] is not None]
    c3 = {"symbols_measured": len(scored),
          "above_half": int(sum(1 for v in scored if v > 0.5)),
          "median": round(float(np.median(scored)), 5) if scored else None,
          "min": round(min(scored), 5) if scored else None,
          "max": round(max(scored), 5) if scored else None,
          "per_symbol": per_symbol}

    e = e.sort_values("t_frame")
    cut = e["t_frame"].quantile(0.5)
    c4 = {}
    for nm, sub in (("first_half", e[e["t_frame"] <= cut]), ("second_half", e[e["t_frame"] > cut])):
        p = (sub[f"fwd_mid_ticks_{horizon_s}"] > 0).to_numpy(dtype=bool)
        c4[nm] = {"rows": int(len(sub)),
                  "auc": round(auc(pd.to_numeric(sub[candidate_col], errors="coerce")
                                   .to_numpy(dtype=float), p), 5) if len(sub) else None}
    c4["stable"] = bool(c4["first_half"]["auc"] is not None and c4["second_half"]["auc"] is not None
                        and abs(c4["first_half"]["auc"] - c4["second_half"]["auc"]) < 0.05)

    c5 = [paired_auc_difference(e, candidate_col, b, horizon_s) for b in baseline_cols
          if b in e.columns and b != candidate_col]
    strongest = max(c5, key=lambda r: r["auc_b"]) if c5 else None

    fire = cand > 0
    ep = episodes(e, fire) | episodes(e, ~fire)
    sub = e[ep]
    p = (sub[f"fwd_mid_ticks_{horizon_s}"] > 0).to_numpy(dtype=bool)
    c6 = {"episodes": int(len(sub)), "rows": int(len(e)),
          "auc_episode_anchored": round(auc(pd.to_numeric(sub[candidate_col], errors="coerce")
                                            .to_numpy(dtype=float), p), 5) if len(sub) else None,
          "auc_all_rows": round(auc(cand, pos), 5)}

    return {
        "candidate": candidate_col, "horizon_s": horizon_s, "eligible_rows": int(len(e)),
        "C1_null_scores": c1,
        "C2_past_move": c2,
        "C3_per_symbol": c3,
        "C4_time_split": c4,
        "C5_incremental_vs_baselines": c5,
        "C5_strongest_baseline": strongest,
        "C5_beats_strongest_established": bool(strongest and strongest["established"]),
        "C6_episode_anchored": c6,
        "C7_ablation": (ablation(f, candidate_col, candidate_col + "_rank", horizon_s)
                        if (candidate_col + "_rank") in f.columns else None),
    }


def controls_table(reports: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    """One row per candidate: did it survive each control, in a form you can sort."""
    rows = []
    for r in reports:
        if "C1_null_scores" not in r:
            continue
        s = r["C5_strongest_baseline"]
        rows.append({
            "candidate": r["candidate"], "horizon_s": r["horizon_s"],
            "eligible_rows": r["eligible_rows"],
            "auc": r["C2_past_move"]["candidate"],
            "C1_nulls_clean": r["C1_null_scores"]["clean"],
            "C2_already_moved_confound": r["C2_past_move"]["already_moved_confound"],
            "C3_symbols_above_half": f"{r['C3_per_symbol']['above_half']}/{r['C3_per_symbol']['symbols_measured']}",
            "C3_median_auc": r["C3_per_symbol"]["median"],
            "C4_stable_across_halves": r["C4_time_split"]["stable"],
            "C5_strongest_baseline": s["b"] if s else None,
            "C5_margin": s["diff"] if s else None,
            "C5_margin_ci_lo": s["ci_lo"] if s else None,
            "C5_margin_ci_hi": s["ci_hi"] if s else None,
            "C5_beats_baseline_established": r["C5_beats_strongest_established"],
            "C6_episodes": r["C6_episode_anchored"]["episodes"],
            "C6_auc_episode_anchored": r["C6_episode_anchored"]["auc_episode_anchored"],
            "C7_ablation_margin": (r["C7_ablation"]["pooled"]["diff"]
                                   if r.get("C7_ablation") else None),
            "C7_ablation_established": (r["C7_ablation"]["pooled"]["established"]
                                        if r.get("C7_ablation") else None),
        })
    return pd.DataFrame(rows)
