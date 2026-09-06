"""The main experiment: does the composite dynamic state carry information
beyond simple imbalance? Pre-registered design: ``seeing.experiment.design``.

Inputs: fused frames with features and labels (``seeing.features.micro``).
Outputs (all with the full denominator — every frame counts):

    evaluate()          per (split, signal, horizon): n frames, n valid outcomes,
                        distinct episodes, P(up), P(down), mean fwd ticks,
                        matched-control P(up), lift vs matched controls,
                        lift vs the unconditional base rate
    incremental()       composite vs each simple baseline on the same split and
                        horizon: incremental lift (composite − baseline) and the
                        within-baseline test (P(up | baseline ∧ composite) −
                        P(up | baseline ∧ ¬composite))
    block_bootstrap_ci  95 % CI of the incremental lift over the best baseline
    permutation_p       timestamp-permutation p-value of the composite's lift
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .design import BASELINES, DESIGN, Design

SIGNALS = ("composite", "mirror_composite") + BASELINES


def assign_splits(f: pd.DataFrame, d: Design = DESIGN) -> pd.DataFrame:
    out = f.copy()
    out["split"] = "holdout"
    for sym, g in out.groupby("symbol", sort=False):
        n = len(g)
        order = g.sort_values("t_frame").index
        k_dev = int(np.floor(d.dev_frac * n))
        k_val = int(np.floor((d.dev_frac + d.val_frac) * n))
        out.loc[order[:k_dev], "split"] = "dev"
        out.loc[order[k_dev:k_val], "split"] = "val"
        out.loc[order[k_val:], "split"] = "holdout"
    # matching keys
    out["tod_bucket"] = out.groupby("symbol")["t_frame"].rank(pct=True).mul(d.n_tod_buckets).clip(upper=d.n_tod_buckets - 1e-9).astype(int)
    out["spread_bucket"] = out["spread_ticks"].clip(upper=3).fillna(-1).astype(int)
    return out


def _episodes(mask: pd.Series, symbol: pd.Series) -> int:
    starts = mask & ~mask.groupby(symbol).shift(1).fillna(False).astype(bool)
    return int(starts.sum())


def matched_controls(f: pd.DataFrame, sig: pd.Series, h: int, d: Design, rng: np.random.Generator) -> Tuple[float, float, int]:
    """For each signal frame draw one non-signal frame with the same match keys.
    Returns (P(up | control), mean fwd ticks | control, n matched)."""
    valid = f[f"fwd_valid_h{h}"]
    pool = f[(~sig) & valid]
    if not len(pool):
        return np.nan, np.nan, 0
    keys = list(d.match_keys)
    groups = {k: v.index.values for k, v in pool.groupby(keys)}
    picks: List[np.ndarray] = []
    for key, g in f[sig & valid].groupby(keys):
        cand = groups.get(key)
        if cand is None or not len(cand):
            continue
        picks.append(rng.choice(cand, size=len(g), replace=True))
    if not picks:
        return np.nan, np.nan, 0
    c = f.loc[np.concatenate(picks)]
    return float(c[f"fwd_up_h{h}"].mean()), float(c[f"fwd_mid_ticks_h{h}"].mean()), int(len(c))


def signal_row(f: pd.DataFrame, name: str, sig: pd.Series, h: int, d: Design, rng: np.random.Generator,
               split: str) -> Dict[str, Any]:
    valid = f[f"fwd_valid_h{h}"]
    s = sig.fillna(False).astype(bool)
    n_all = int(len(f))
    n_sig = int(s.sum())
    n_sig_valid = int((s & valid).sum())
    base_up = float(f.loc[valid, f"fwd_up_h{h}"].mean()) if valid.any() else np.nan
    base_ticks = float(f.loc[valid, f"fwd_mid_ticks_h{h}"].mean()) if valid.any() else np.nan
    if n_sig_valid:
        p_up = float(f.loc[s & valid, f"fwd_up_h{h}"].mean())
        p_down = float(f.loc[s & valid, f"fwd_down_h{h}"].mean())
        mean_ticks = float(f.loc[s & valid, f"fwd_mid_ticks_h{h}"].mean())
    else:
        p_up = p_down = mean_ticks = np.nan
    c_up, c_ticks, n_c = matched_controls(f, s, h, d, rng)
    return {"split": split, "signal": name, "h": h, "n_frames": n_all, "n_signal": n_sig,
            "n_signal_valid": n_sig_valid, "episodes": _episodes(s, f["symbol"]),
            "share_of_frames": n_sig / n_all if n_all else np.nan,
            "p_up": p_up, "p_down": p_down, "mean_fwd_ticks": mean_ticks,
            "base_p_up": base_up, "base_mean_ticks": base_ticks,
            "ctrl_p_up": c_up, "ctrl_mean_ticks": c_ticks, "n_matched": n_c,
            "lift_vs_matched": (p_up - c_up) if (n_c and n_sig_valid) else np.nan,
            "lift_vs_base": (p_up - base_up) if n_sig_valid else np.nan,
            "ticks_vs_base": (mean_ticks - base_ticks) if n_sig_valid else np.nan}


def evaluate(f: pd.DataFrame, d: Design = DESIGN, splits: Sequence[str] = ("dev", "val", "holdout"),
             extra_signals: Optional[Dict[str, pd.Series]] = None) -> pd.DataFrame:
    rng = np.random.default_rng(d.seed)
    rows = []
    for split in splits:
        fs = f[f["split"] == split]
        if not len(fs):
            continue
        sigs: Dict[str, pd.Series] = {s: fs[s] for s in SIGNALS if s in fs}
        for k in range(3, 8):
            sigs[f"score_ge_{k}"] = fs["composite_score"] >= k
        if extra_signals:
            for k, v in extra_signals.items():
                sigs[k] = v.loc[fs.index]
        for h in d.horizons:
            for name, sig in sigs.items():
                rows.append(signal_row(fs, name, sig, h, d, rng, split))
    return pd.DataFrame(rows)


def incremental(f: pd.DataFrame, d: Design = DESIGN, split: str = "holdout", h: Optional[int] = None,
                composite_col: str = "composite") -> pd.DataFrame:
    h = h or d.primary_h
    fs = f[f["split"] == split]
    valid = fs[f"fwd_valid_h{h}"]
    up = fs[f"fwd_up_h{h}"]
    comp = fs[composite_col].fillna(False).astype(bool)
    base_up = float(up[valid].mean()) if valid.any() else np.nan
    rows = []
    for b in BASELINES:
        bs = fs[b].fillna(False).astype(bool)
        n_b = int((bs & valid).sum())
        p_b = float(up[bs & valid].mean()) if n_b else np.nan
        both = bs & comp & valid
        only_b = bs & ~comp & valid
        rows.append({"split": split, "h": h, "baseline": b, "n_baseline": n_b, "p_up_baseline": p_b,
                     "lift_baseline_vs_base": p_b - base_up if n_b else np.nan,
                     "n_composite": int((comp & valid).sum()),
                     "p_up_composite": float(up[comp & valid].mean()) if (comp & valid).any() else np.nan,
                     "incremental_lift": (float(up[comp & valid].mean()) - p_b) if ((comp & valid).any() and n_b) else np.nan,
                     "n_both": int(both.sum()), "p_up_both": float(up[both].mean()) if both.any() else np.nan,
                     "n_only_baseline": int(only_b.sum()),
                     "p_up_only_baseline": float(up[only_b].mean()) if only_b.any() else np.nan,
                     "within_baseline_gain": (float(up[both].mean()) - float(up[only_b].mean()))
                     if (both.any() and only_b.any()) else np.nan})
    return pd.DataFrame(rows)


def best_baseline(inc: pd.DataFrame) -> Optional[str]:
    if inc is None or not len(inc):
        return None
    v = inc.dropna(subset=["lift_baseline_vs_base"])
    if not len(v):
        return None
    return str(v.sort_values("lift_baseline_vs_base", ascending=False).iloc[0]["baseline"])


def _lift_uncond(fs: pd.DataFrame, sig: pd.Series, h: int) -> float:
    valid = fs[f"fwd_valid_h{h}"]
    up = fs[f"fwd_up_h{h}"]
    s = sig.fillna(False).astype(bool)
    if not (s & valid).any() or not valid.any():
        return np.nan
    return float(up[s & valid].mean() - up[valid].mean())


def block_bootstrap_ci(fs: pd.DataFrame, sig_a: str, sig_b: Optional[str], h: int, d: Design = DESIGN) -> Dict[str, Any]:
    """95 % block-bootstrap CI of lift(sig_a) − lift(sig_b) (sig_b None → lift(sig_a) alone).
    Blocks of d.block_len consecutive frames per symbol, resampled with replacement per symbol."""
    rng = np.random.default_rng(d.seed + 1)
    per_sym = {s: g.sort_values("t_frame") for s, g in fs.groupby("symbol", sort=False)}
    blocks: Dict[str, List[np.ndarray]] = {}
    for s, g in per_sym.items():
        idx = g.index.values
        blocks[s] = [idx[i:i + d.block_len] for i in range(0, len(idx), d.block_len)]
    stats = []
    for _ in range(d.n_boot):
        pick: List[np.ndarray] = []
        for s, bl in blocks.items():
            if not bl:
                continue
            k = rng.integers(0, len(bl), size=len(bl))
            pick.extend(bl[i] for i in k)
        if not pick:
            break
        sample = fs.loc[np.concatenate(pick)]
        la = _lift_uncond(sample, sample[sig_a], h)
        lb = _lift_uncond(sample, sample[sig_b], h) if sig_b else 0.0
        stats.append(la - lb)
    arr = np.array([x for x in stats if np.isfinite(x)])
    if not len(arr):
        return {"ci_lo": np.nan, "ci_hi": np.nan, "n_boot_valid": 0, "point": np.nan}
    point = _lift_uncond(fs, fs[sig_a], h) - (_lift_uncond(fs, fs[sig_b], h) if sig_b else 0.0)
    return {"ci_lo": float(np.percentile(arr, 2.5)), "ci_hi": float(np.percentile(arr, 97.5)),
            "n_boot_valid": int(len(arr)), "point": float(point), "block_len": d.block_len}


def permutation_p(fs: pd.DataFrame, sig: str, h: int, d: Design = DESIGN) -> Dict[str, Any]:
    """Circularly shift the signal series within each symbol by a random offset —
    the signal keeps its run structure, loses its alignment with outcomes."""
    rng = np.random.default_rng(d.seed + 2)
    obs = _lift_uncond(fs, fs[sig], h)
    if not np.isfinite(obs):
        return {"observed": np.nan, "p_value": np.nan, "n_perm": 0}
    null = []
    order = fs.sort_values(["symbol", "t_frame"])
    sym = order["symbol"].values
    s_all = order[sig].fillna(False).astype(bool).values
    up = order[f"fwd_up_h{h}"].values
    valid = order[f"fwd_valid_h{h}"].values.astype(bool)
    base = up[valid].mean()
    bounds = np.flatnonzero(np.r_[True, sym[1:] != sym[:-1], True])
    for _ in range(d.n_perm):
        s_perm = s_all.copy()
        for a, b in zip(bounds[:-1], bounds[1:]):
            if b - a > 1:
                s_perm[a:b] = np.roll(s_all[a:b], rng.integers(1, b - a))
        m = s_perm & valid
        null.append(up[m].mean() - base if m.any() else np.nan)
    null_arr = np.array([x for x in null if np.isfinite(x)])
    p = float((np.sum(null_arr >= obs) + 1) / (len(null_arr) + 1)) if len(null_arr) else np.nan
    return {"observed": float(obs), "p_value": p, "n_perm": int(len(null_arr)),
            "null_mean": float(null_arr.mean()) if len(null_arr) else np.nan,
            "null_p95": float(np.percentile(null_arr, 95)) if len(null_arr) else np.nan}


def denominator(f: pd.DataFrame, d: Design = DESIGN) -> Dict[str, Any]:
    """The full denominator: every frame, every symbol, every split, every state."""
    out: Dict[str, Any] = {"n_frames": int(len(f)), "n_symbols": int(f["symbol"].nunique()),
                           "frames_per_symbol": f.groupby("symbol").size().to_dict(),
                           "frames_per_split": f["split"].value_counts().to_dict(),
                           "t_range": [str(f["t_frame"].min()), str(f["t_frame"].max())],
                           "median_frame_dt_s": float(f["frame_dt_s"].median()),
                           "composite_frames": int(f["composite"].sum()),
                           "composite_episodes_total": _episodes(f["composite"].fillna(False).astype(bool), f["symbol"]),
                           "composite_episodes_holdout": _episodes(
                               (f["composite"].fillna(False).astype(bool) & (f["split"] == "holdout")), f["symbol"]),
                           "component_frames": {c: int(f[c].fillna(False).astype(bool).sum()) for c in
                                                ("persistent_bid_pressure", "ask_thinning", "bid_replenishment",
                                                 "multi_level_transition", "spread_stable", "time_persistence",
                                                 "price_response_ok")},
                           "score_histogram": f["composite_score"].value_counts().sort_index().to_dict(),
                           "baseline_frames": {b: int(f[b].fillna(False).astype(bool).sum()) for b in BASELINES},
                           "bad_book_frames": int(f["bad_book"].sum()), "stale_book_frames": int(f["stale_book"].sum()),
                           "dup_payload_frames": int(f["dup_payload"].fillna(False).astype(bool).sum()),
                           "frames_with_tape_rows": int((f["tape_rows"] > 0).sum()),
                           "frames_with_two_book_sensors": int(f["book_agree"].notna().sum()),
                           "book_agree_rate": float(f["book_agree"].mean()) if f["book_agree"].notna().any() else np.nan}
    if "state" in f:
        out["state_frames"] = f["state"].value_counts().to_dict()
    for h in d.horizons:
        out[f"valid_outcomes_h{h}"] = int(f[f"fwd_valid_h{h}"].sum())
        out[f"base_p_up_h{h}"] = float(f.loc[f[f"fwd_valid_h{h}"], f"fwd_up_h{h}"].mean()) if f[f"fwd_valid_h{h}"].any() else np.nan
    return out
