"""Microstructure features on fused frames (per symbol, frame-ordered).

Every feature is computed from frames at or before t (strictly causal; the
leakage test in tests/test_features.py corrupts the future and requires every
feature at earlier frames to be unchanged). Forward outcomes are computed
separately in ``labels`` and are never used by any feature.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from ..experiment.design import BASELINES, COMPONENTS, DESIGN, Design


def _shift(g: pd.core.groupby.SeriesGroupBy, n: int) -> pd.Series:
    return g.shift(n)


def features(frames: pd.DataFrame, d: Design = DESIGN) -> pd.DataFrame:
    f = frames.sort_values(["symbol", "t_frame"], kind="mergesort").reset_index(drop=True).copy()
    g = f.groupby("symbol", sort=False)
    W = d.W

    # ---------- one-frame quantities (causal by construction)
    tot5 = f["bid_depth_top5"] + f["ask_depth_top5"]
    f["one_frame_pressure"] = ((g["bid_depth_top5"].diff() - g["ask_depth_top5"].diff()) / tot5.replace(0, np.nan))
    f["liquidity_top5"] = tot5
    f["liquidity_change_w"] = tot5 - g["liquidity_top5"].shift(W) if "liquidity_top5" in f else np.nan
    f["liquidity_change_w"] = f["liquidity_top5"] - f.groupby("symbol")["liquidity_top5"].shift(W)
    f["mid_change_w_ticks"] = (f["mid"] - g["mid"].shift(W)) / f["tick_size"].fillna(0.10)
    f["mid_change_1_ticks"] = g["mid"].diff() / f["tick_size"].fillna(0.10)

    # ---------- windowed components
    imb_gt = (f["imb_top5"] > d.theta_imb).astype(float)
    f["bid_pressure_frac_w"] = imb_gt.groupby(f["symbol"]).transform(lambda s: s.rolling(W, min_periods=W).mean())
    f["persistent_bid_pressure"] = f["bid_pressure_frac_w"] >= d.persist_frac

    ask_prev = g["ask_depth_top5"].shift(W)
    f["ask_thin_ratio_w"] = f["ask_depth_top5"] / ask_prev.replace(0, np.nan)
    f["ask_thinning"] = (f["ask_thin_ratio_w"] <= d.thinning_ratio) & ask_prev.gt(0)

    bid_prev = g["bid_depth_top5"].shift(W)
    rep = f.get("ev_bid_replenish", pd.Series(0, index=f.index)).groupby(f["symbol"]).transform(
        lambda s: s.rolling(W, min_periods=1).sum())
    add = f.get("ev_bid_add_qty", pd.Series(0, index=f.index)).groupby(f["symbol"]).transform(
        lambda s: s.rolling(W, min_periods=1).sum())
    red = f.get("ev_bid_reduce_qty", pd.Series(0, index=f.index)).groupby(f["symbol"]).transform(
        lambda s: s.rolling(W, min_periods=1).sum())
    f["bid_replenish_events_w"] = rep
    f["bid_net_add_w"] = add - red
    f["bid_replenishment"] = (f["bid_depth_top5"] >= bid_prev) & bid_prev.notna() & ((rep >= 1) | (add >= red))

    imb_prev = g["imb_top5"].shift(W)
    # a positive multi-level transition = a crossing from ≤ θ/2 (W frames earlier) to > θ on
    # L1, top-3 and top-5 together, observed within the last W frames (rolling max of the
    # per-frame crossing indicator). Pre-registered as "within the window": a transition
    # and a persistence condition cannot hold on the same frame otherwise.
    trans_now = ((imb_prev <= d.theta_imb / 2) & (f["imb_top5"] > d.theta_imb) &
                 (f["imb_top3"] > d.theta_imb) & (f["imb_l1"] > d.theta_imb)).astype(float)
    f["multi_level_transition"] = trans_now.groupby(f["symbol"]).transform(
        lambda s: s.rolling(W, min_periods=1).max()) > 0

    sp = f["spread_ticks"]
    sp_const = sp.groupby(f["symbol"]).transform(lambda s: s.rolling(W, min_periods=W).apply(lambda x: float(np.nanmax(x) == np.nanmin(x)), raw=True))
    f["spread_stable"] = (sp_const == 1.0) & (sp <= d.max_spread_ticks) & sp.notna()

    core = (f["persistent_bid_pressure"] & f["ask_thinning"]).astype(int)
    def _run(s: pd.Series) -> pd.Series:
        out = np.zeros(len(s), dtype=int)
        run = 0
        for i, v in enumerate(s.values):
            run = run + 1 if v else 0
            out[i] = run
        return pd.Series(out, index=s.index)
    f["core_run"] = core.groupby(f["symbol"]).transform(_run)
    f["time_persistence"] = f["core_run"] >= d.P

    f["price_response_ok"] = f["mid_change_w_ticks"] >= 0

    # ---------- composite and score
    comp = f[list(COMPONENTS)].fillna(False).astype(bool)
    f["composite_score"] = comp.sum(axis=1)
    f["composite"] = comp.all(axis=1)
    # distinct episodes: a composite run that starts after a non-composite frame
    f["composite_episode_start"] = f["composite"] & ~f.groupby("symbol")["composite"].shift(1).fillna(False).astype(bool)
    f["composite_episode_id"] = f.groupby("symbol")["composite_episode_start"].cumsum().where(f["composite"])

    # ---------- mirrored (side-flipped) composite — used by the side-flip falsification
    imb_lt = (f["imb_top5"] < -d.theta_imb).astype(float)
    m_persist = imb_lt.groupby(f["symbol"]).transform(lambda s: s.rolling(W, min_periods=W).mean()) >= d.persist_frac
    m_thin = (f["bid_depth_top5"] / bid_prev.replace(0, np.nan) <= d.thinning_ratio) & bid_prev.gt(0)
    repa = f.get("ev_ask_replenish", pd.Series(0, index=f.index)).groupby(f["symbol"]).transform(lambda s: s.rolling(W, min_periods=1).sum())
    adda = f.get("ev_ask_add_qty", pd.Series(0, index=f.index)).groupby(f["symbol"]).transform(lambda s: s.rolling(W, min_periods=1).sum())
    reda = f.get("ev_ask_reduce_qty", pd.Series(0, index=f.index)).groupby(f["symbol"]).transform(lambda s: s.rolling(W, min_periods=1).sum())
    m_rep = (f["ask_depth_top5"] >= ask_prev) & ask_prev.notna() & ((repa >= 1) | (adda >= reda))
    m_trans_now = ((imb_prev >= -d.theta_imb / 2) & (f["imb_top5"] < -d.theta_imb) &
                   (f["imb_top3"] < -d.theta_imb) & (f["imb_l1"] < -d.theta_imb)).astype(float)
    m_trans = m_trans_now.groupby(f["symbol"]).transform(lambda s: s.rolling(W, min_periods=1).max()) > 0
    m_core = (m_persist & m_thin).astype(int)
    m_run = m_core.groupby(f["symbol"]).transform(_run)
    f["mirror_composite"] = m_persist & m_thin & m_rep & m_trans & f["spread_stable"] & (m_run >= d.P) & (f["mid_change_w_ticks"] <= 0)

    # ---------- simple baselines (one-frame)
    f["b_imb_l1"] = f["imb_l1"] > d.theta_imb
    f["b_imb_top5"] = f["imb_top5"] > d.theta_imb
    f["b_imb_weighted"] = f["imb_weighted"] > d.theta_imb
    f["b_largest_wall_bid"] = f["largest_wall_side"] == "bid"
    f["b_one_frame_pressure"] = f["one_frame_pressure"] > d.theta_pressure

    # ---------- depletion / replenishment / resilience (descriptive, INFERRED)
    f["ask_touch_depleted"] = (g["best_ask"].diff() > 0)            # best ask moved up: touch level exhausted
    f["bid_touch_depleted"] = (g["best_bid"].diff() < 0)
    f["ask_replenished_after_depletion"] = f["ask_touch_depleted"].groupby(f["symbol"]).shift(1).fillna(False).astype(bool) & (g["best_ask"].diff() < 0)
    # resilience: frames until ask_depth_top5 recovers to ≥ 80 % of its level before a depletion
    f["resilience_frames"] = np.nan
    for sym, gg in f.groupby("symbol", sort=False):
        idx = gg.index.values
        dep = gg["ask_touch_depleted"].values
        depth = gg["ask_depth_top5"].values
        for k in np.where(dep)[0]:
            if k == 0:
                continue
            target = 0.8 * depth[k - 1]
            for j in range(k, min(k + 3 * W, len(idx))):
                if depth[j] >= target:
                    f.at[idx[k], "resilience_frames"] = j - k
                    break

    # ---------- staleness / quality flags used by falsification removals
    f["stale_book"] = f["unchanged_run"] >= d.stale_unchanged_run
    f["stale_watch"] = f.get("watch_age_s", pd.Series(np.nan, index=f.index)) > d.stale_watch_age_s
    f["bad_book"] = f["crossed"] | f["locked"] | f["empty"] | f["one_sided"]
    f.attrs["truth"] = dict(getattr(frames, "attrs", {}).get("truth", {}))
    f.attrs["truth"]["features"] = "INFERRED (causal rules; parameters pre-registered in seeing.experiment.design)"
    return f


def labels(f: pd.DataFrame, d: Design = DESIGN) -> pd.DataFrame:
    """Forward outcomes — kept in separate columns prefixed fwd_; never features."""
    out = f.copy()
    g = out.groupby("symbol", sort=False)
    tick = out["tick_size"].fillna(0.10)
    for h in d.horizons:
        out[f"fwd_mid_ticks_h{h}"] = (g["mid"].shift(-h) - out["mid"]) / tick
        out[f"fwd_up_h{h}"] = out[f"fwd_mid_ticks_h{h}"] > 0
        out[f"fwd_down_h{h}"] = out[f"fwd_mid_ticks_h{h}"] < 0
        out[f"fwd_valid_h{h}"] = out[f"fwd_mid_ticks_h{h}"].notna()
        ltp_fwd = g["ltp"].shift(-h)
        out[f"fwd_ltp_ticks_h{h}"] = (ltp_fwd - out["ltp"]) / tick
    return out


def largest_wall_removed_imbalances(f: pd.DataFrame) -> pd.DataFrame:
    """Recompute top-5 imbalance with the largest single level removed from
    whichever side holds it — the 'largest-wall removal' falsification."""
    rows = []
    for bids, asks in zip(f["bid_levels"], f["ask_levels"]):
        b = [tuple(x) for x in (bids or [])][:5]
        a = [tuple(x) for x in (asks or [])][:5]
        allv = [("b", i, q) for i, (_, q) in enumerate(b)] + [("a", i, q) for i, (_, q) in enumerate(a)]
        if allv:
            side, i, _ = max(allv, key=lambda x: x[2])
            if side == "b":
                b = b[:i] + b[i + 1:]
            else:
                a = a[:i] + a[i + 1:]
        bd, ad = sum(q for _, q in b), sum(q for _, q in a)
        rows.append((bd - ad) / (bd + ad) if (bd + ad) > 0 else np.nan)
    return pd.Series(rows, index=f.index, name="imb_top5_wall_removed")
