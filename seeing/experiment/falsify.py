"""Falsification battery and verdict. The hypothesis is not protected.

Every test returns one or more rows with the same columns so the report is
one table: test, variant, split, h, n_signal, episodes, lift_vs_base,
incremental_vs_best_baseline, passed (True/False/None), note.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..features.micro import features, labels, largest_wall_removed_imbalances
from .design import BASELINES, DESIGN, VERDICT_RULE, Design
from .run_experiment import (_episodes, _lift_uncond, assign_splits, best_baseline, block_bootstrap_ci,
                             incremental, permutation_p)


def _row(test: str, variant: str, fs: pd.DataFrame, sig_col: str, h: int, best_b: Optional[str],
         passed: Optional[bool], note: str = "", split: str = "holdout") -> Dict[str, Any]:
    s = fs[sig_col].fillna(False).astype(bool) if sig_col in fs else pd.Series(False, index=fs.index)
    lift = _lift_uncond(fs, s, h)
    inc = (lift - _lift_uncond(fs, fs[best_b], h)) if (best_b and best_b in fs) else np.nan
    return {"test": test, "variant": variant, "split": split, "h": h, "n_frames": int(len(fs)),
            "n_signal": int((s & fs[f"fwd_valid_h{h}"]).sum()), "episodes": _episodes(s, fs["symbol"]),
            "lift_vs_base": lift, "incremental_vs_best_baseline": inc, "passed": passed, "note": note}


def _shifted(fs: pd.DataFrame, col: str, k: int) -> pd.Series:
    """Signal shifted by k frames within symbol (k>0: uses FUTURE frames — leak control;
    k<0: PAST — placebo)."""
    return fs.groupby("symbol")[col].shift(-k).fillna(False).astype(bool)


def run_falsifications(f: pd.DataFrame, frames_raw: pd.DataFrame, d: Design = DESIGN,
                       liquidity_groups: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    h = d.primary_h
    hold = f[f["split"] == "holdout"]
    inc = incremental(f, d, "holdout", h)
    best_b = best_baseline(inc)
    rows: List[Dict[str, Any]] = []
    real = _row("real", "holdout", hold, "composite", h, best_b, None, f"best simple baseline = {best_b}")
    rows.append(real)
    real_lift = real["lift_vs_base"]
    real_inc = real["incremental_vs_best_baseline"]

    # (a) baseline comparison with block-bootstrap CI
    ci = block_bootstrap_ci(hold, "composite", best_b, h, d) if best_b else {"ci_lo": np.nan, "ci_hi": np.nan, "point": np.nan}
    rows.append({"test": "baseline_comparison", "variant": f"composite - {best_b}", "split": "holdout", "h": h,
                 "n_frames": len(hold), "n_signal": real["n_signal"], "episodes": real["episodes"],
                 "lift_vs_base": real_lift, "incremental_vs_best_baseline": ci.get("point"),
                 "passed": bool(np.isfinite(ci.get("ci_lo", np.nan)) and ci["ci_lo"] > 0),
                 "note": f"block bootstrap 95% CI [{ci.get('ci_lo'):.4f}, {ci.get('ci_hi'):.4f}] (blocks of {d.block_len} frames, {ci.get('n_boot_valid')} resamples)"
                 if np.isfinite(ci.get("ci_lo", np.nan)) else "no valid bootstrap resamples"})
    # each baseline individually
    for b in BASELINES:
        rows.append(_row("baseline", b, hold, b, h, None, None))
    for k in range(3, 8):
        hold2 = hold.assign(**{f"score_ge_{k}": hold["composite_score"] >= k})
        rows.append(_row("graded_score", f"score_ge_{k}", hold2, f"score_ge_{k}", h, best_b, None))

    # (b) timestamp permutation
    perm = permutation_p(hold, "composite", h, d)
    rows.append({"test": "timestamp_permutation", "variant": "circular shift within symbol", "split": "holdout", "h": h,
                 "n_frames": len(hold), "n_signal": real["n_signal"], "episodes": real["episodes"],
                 "lift_vs_base": perm["observed"], "incremental_vs_best_baseline": np.nan,
                 "passed": bool(np.isfinite(perm["p_value"]) and perm["p_value"] < d.alpha),
                 "note": f"p = {perm['p_value']:.4f} over {perm['n_perm']} permutations; null mean {perm.get('null_mean', np.nan):.4f}, null p95 {perm.get('null_p95', np.nan):.4f}"
                 if np.isfinite(perm["p_value"]) else "no signal frames"})

    # (c) side flip: mirrored composite must predict DOWN
    valid = hold[f"fwd_valid_h{h}"]
    m = hold["mirror_composite"].fillna(False).astype(bool)
    down_lift = float(hold.loc[m & valid, f"fwd_down_h{h}"].mean() - hold.loc[valid, f"fwd_down_h{h}"].mean()) if (m & valid).any() else np.nan
    rows.append({"test": "side_flip", "variant": "mirror_composite → P(down)", "split": "holdout", "h": h,
                 "n_frames": len(hold), "n_signal": int((m & valid).sum()), "episodes": _episodes(m, hold["symbol"]),
                 "lift_vs_base": down_lift, "incremental_vs_best_baseline": np.nan,
                 "passed": (bool(down_lift > 0) if np.isfinite(down_lift) else None),
                 "note": "down-lift of the side-flipped composite (must be > 0 for a real, symmetric mechanism)"})

    # (d) anchor shift: placebo (−h) and leak control (+h)
    for k, name in ((-h, "placebo_shift_-h"), (h, "leak_control_shift_+h")):
        hs = hold.assign(shifted=_shifted(hold, "composite", k))
        r = _row("anchor_shift", name, hs, "shifted", h, best_b, None,
                 "placebo must show |lift| < ½ real lift" if k < 0 else "future-shifted signal: expected to inflate (test sensitivity)")
        if k < 0:
            r["passed"] = bool(np.isfinite(r["lift_vs_base"]) and np.isfinite(real_lift) and abs(r["lift_vs_base"]) < 0.5 * abs(real_lift))
        rows.append(r)

    # (e) removals — sign of the incremental lift must be preserved
    removals = {
        "stale_removed": ~hold["stale_book"] & ~hold["stale_watch"].fillna(False).astype(bool),
        "duplicate_removed": ~hold["dup_payload"].fillna(False).astype(bool),
        "crossed_locked_removed": ~hold["bad_book"],
    }
    for name, keep in removals.items():
        fs = hold[keep]
        r = _row("removal", name, fs, "composite", h, best_b, None, f"kept {int(keep.sum())}/{len(hold)} frames")
        r["passed"] = bool(np.isfinite(r["incremental_vs_best_baseline"]) and np.isfinite(real_inc) and
                           np.sign(r["incremental_vs_best_baseline"]) == np.sign(real_inc) and real_inc != 0)
        rows.append(r)
    # largest-wall removal: recompute the imbalance-driven components without the largest level
    fw = frames_raw.copy()
    fw["imb_top5"] = largest_wall_removed_imbalances(fw)
    fw["imb_top3"] = fw["imb_top5"]      # conservative: the same wall-free measure feeds every imbalance component
    fw["imb_l1"] = fw["imb_top5"]
    fw = assign_splits(labels(features(fw, d), d), d)
    fwh = fw[fw["split"] == "holdout"]
    r = _row("removal", "largest_wall_removed", fwh, "composite", h, best_b, None,
             "composite recomputed with the largest visible level removed from the imbalance inputs")
    r["passed"] = bool(np.isfinite(r["incremental_vs_best_baseline"]) and np.isfinite(real_inc) and
                       np.sign(r["incremental_vs_best_baseline"]) == np.sign(real_inc) and real_inc != 0)
    rows.append(r)

    # (f) leave-one-symbol-out
    loso_ok = True
    for sym in sorted(hold["symbol"].unique()):
        fs = hold[hold["symbol"] != sym]
        r = _row("leave_one_symbol_out", f"without {sym}", fs, "composite", h, best_b, None)
        r["passed"] = bool(np.isfinite(r["incremental_vs_best_baseline"]) and r["incremental_vs_best_baseline"] > 0)
        loso_ok &= bool(r["passed"])
        rows.append(r)

    # (g) liquidity split
    if liquidity_groups is None:
        med = frames_raw.groupby("symbol")["day_value_mn"].max().median()
        liquidity_groups = {s: ("top" if v >= med else "mid") for s, v in frames_raw.groupby("symbol")["day_value_mn"].max().items()}
    liq_ok = True
    for grp in ("top", "mid"):
        syms = [s for s, g in liquidity_groups.items() if g == grp]
        fs = hold[hold["symbol"].isin(syms)]
        r = _row("liquidity_split", grp, fs, "composite", h, best_b, None, f"symbols: {', '.join(syms)}")
        r["passed"] = bool(np.isfinite(r["incremental_vs_best_baseline"]) and r["incremental_vs_best_baseline"] > 0)
        liq_ok &= bool(r["passed"])
        rows.append(r)

    table = pd.DataFrame(rows)
    verdict = decide(table, f, d, best_b, ci, perm, loso_ok, liq_ok)
    return {"table": table, "verdict": verdict, "incremental": inc, "best_baseline": best_b, "bootstrap": ci,
            "permutation": perm}


def decide(table: pd.DataFrame, f: pd.DataFrame, d: Design, best_b: Optional[str], ci: Dict[str, Any],
           perm: Dict[str, Any], loso_ok: bool, liq_ok: bool) -> Dict[str, Any]:
    hold = f[f["split"] == "holdout"]
    episodes = _episodes(hold["composite"].fillna(False).astype(bool), hold["symbol"])
    reasons: List[str] = []
    if len(f) < d.n_min_frames:
        return {"verdict": "BLOCKED", "reasons": [f"only {len(f)} fused frames < n_min_frames {d.n_min_frames}"],
                "holdout_composite_episodes": episodes, "rule": VERDICT_RULE}
    if episodes < d.n_min_episodes:
        return {"verdict": "BLOCKED", "reasons": [f"only {episodes} distinct composite episodes in the holdout < n_min_episodes {d.n_min_episodes}: the denominator is too small to decide; more sessions are required (capture is designed to run unchanged)"],
                "holdout_composite_episodes": episodes, "rule": VERDICT_RULE}

    def _p(test: str, variant: Optional[str] = None) -> Optional[bool]:
        t = table[table["test"] == test]
        if variant is not None:
            t = t[t["variant"] == variant]
        if not len(t):
            return None
        v = t["passed"].iloc[0]
        return None if pd.isna(v) else bool(v)

    checks = {
        "a_beats_best_baseline_ci": _p("baseline_comparison"),
        "b_permutation": _p("timestamp_permutation"),
        "c_side_flip": _p("side_flip"),
        "d_placebo": _p("anchor_shift", "placebo_shift_-h"),
        "e_removals": all(bool(x) for x in table[table["test"] == "removal"]["passed"].tolist()) if len(table[table["test"] == "removal"]) else None,
        "f_loso": loso_ok,
        "g_liquidity": liq_ok,
    }
    for k, v in checks.items():
        if v is not True:
            reasons.append(f"{k}: {'FAILED' if v is False else 'not evaluable'}")
    verdict = "KEEP" if all(v is True for v in checks.values()) else "KILL"
    return {"verdict": verdict, "checks": checks, "reasons": reasons or ["all pre-registered checks passed"],
            "holdout_composite_episodes": episodes, "best_baseline": best_b, "bootstrap": ci,
            "permutation": {k: v for k, v in perm.items()}, "rule": VERDICT_RULE}
