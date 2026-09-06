"""Frozen feature / baseline / outcome construction. Obeys MICRO_PREREG.json exactly.
Every feature uses information at or before frame t. No fwd_* column may enter X.
"""
import numpy as np, pandas as pd

W_FLOW = 6
HORIZONS_S = (60, 180, 600)
PRIMARY_H = 180
UNIVERSE = ["BEXIMCO","BRACBANK","BXPHARMA","GP","IPDC","LOVELLO","MALEKSPIN","ORIONPHARM",
            "POWERGRID","PTL","SAIHAMCOT","SAIHAMTEX","SHARPIND","SQURPHARMA"]

FEATURES = [
 # BOOK
 "imb_l1","imb_top5","imb_weighted","micro_disp_ticks","spread_ticks","depth_ratio",
 "wall_concentration","touch_depletion","replenishment","multi_level_transition","resilience",
 # FLOW
 "int_d_trades","int_d_volume","int_d_value_mn","signed_flow_w","ofi_w","trade_intensity",
 "trade_acceleration","sweep_proxy","book_pressure","trade_pressure",
 # CONTEXT
 "mkt_breadth","mkt_stat_breadth","mkt_pressure","xs_pressure_rank","tod_bucket",
 "circuit_dist_up","circuit_dist_dn","liquidity_state","liquidity_change_w","mkt_move",
 "watch_age_s","market_age_s","book_agree","book_diff",
]
BASELINES = ["B1_imb_l1","B2_imb_top5","B3_imb_weighted","B4_micro_disp","B5_one_frame_pressure",
             "B6_signed_flow","B7_ofi","B8_book_pressure","B9_trade_pressure","B10_combined_rule"]
THETA_IMB = 0.20


def _roll(df, col, w, fn="sum"):
    g = df.groupby("symbol", sort=False)[col]
    return getattr(g.rolling(w, min_periods=1), fn)().reset_index(level=0, drop=True)


def build(fr: pd.DataFrame) -> pd.DataFrame:
    d = fr.copy()
    d = d[d["symbol"].isin(UNIVERSE)]
    d = d.sort_values(["symbol", "t_frame"], kind="mergesort").reset_index(drop=True)
    tick = d["tick_size"].replace(0, np.nan)
    liq = d["liquidity_top5"].fillna(0) + 1.0

    # ---- QUALITY GATES (input only, never predictors) ----
    d["q_exclude"] = (d.get("bad_book", False).astype(bool) | d.get("stale_book", False).astype(bool)
                      | d.get("one_sided", False).astype(bool)
                      | d.get("crossed", False).astype(bool) | d.get("empty", False).astype(bool)
                      | d["mid"].isna() | tick.isna() | (d["watch_age_s"] > 120))
    # dup_payload is a ROBUSTNESS-REMOVAL flag (repo precedent), never a hard input gate
    d["r_dup"] = d.get("dup_payload", False).astype(bool)
    d["q_degraded"] = (d["book_agree"].isna() | (d.get("market_age_s", 0) > 300))

    # ---- BOOK ----
    d["micro_disp_ticks"] = (d["microprice"] - d["mid"]) / tick
    d["depth_ratio"] = np.log1p(d["bid_depth_top5"]) - np.log1p(d["ask_depth_top5"])
    wall_sign = np.where(d["largest_wall_side"].astype(str).str.lower().str.startswith("b"), 1.0,
                np.where(d["largest_wall_side"].astype(str).str.lower().str.startswith("a"), -1.0, 0.0))
    d["wall_concentration"] = d["largest_wall_share"].fillna(0) * wall_sign
    d["touch_depletion"] = (d["ev_bid_touch_consumed"] - d["ev_ask_touch_consumed"]) / liq
    d["replenishment"] = (d["ev_bid_replenish"] - d["ev_ask_replenish"]) / liq
    d["multi_level_transition"] = d["multi_level_transition"].astype(float)
    d["resilience"] = (d["ask_replenished_after_depletion"].astype(float)
                       - d["bid_touch_depleted"].astype(float))

    # ---- FLOW ----
    d["int_d_value_mn"] = d["tape_d_value_mn"].fillna(d["snap_d_value_mn"])
    d["signed_flow_w"] = (_roll(d, "signed_int_volume", W_FLOW)
                          / (_roll(d, "int_d_volume", W_FLOW) + 1.0))
    d["_ofi_raw"] = ((d["ev_bid_add_qty"] - d["ev_bid_reduce_qty"])
                     - (d["ev_ask_add_qty"] - d["ev_ask_reduce_qty"]))
    d["ofi_w"] = _roll(d, "_ofi_raw", W_FLOW) / liq
    d["trade_intensity"] = d["int_d_trades"] / d["frame_dt_s"].replace(0, np.nan)
    d["trade_acceleration"] = d["trade_intensity"] - _roll(d, "trade_intensity", W_FLOW, "mean")
    d["sweep_proxy"] = d["ev_sweeps"] / liq
    d["book_pressure"] = d["one_frame_pressure"]
    d["trade_pressure"] = d["signed_int_volume"] / (d["int_d_volume"] + 1.0)

    # ---- CONTEXT ----
    d["mkt_breadth"] = (d["mkt_up"] - d["mkt_down"]) / d["mkt_n"].clip(lower=1)
    tot = (d["mkt_stat_up"] + d["mkt_stat_down"] + d["mkt_stat_flat"]).clip(lower=1)
    d["mkt_stat_breadth"] = (d["mkt_stat_up"] - d["mkt_stat_down"]) / tot
    d["mkt_pressure"] = d.groupby("t_frame")["one_frame_pressure"].transform("mean")
    d["xs_pressure_rank"] = d.groupby("t_frame")["one_frame_pressure"].rank(pct=True)
    d["circuit_dist_up"] = (d["upper_limit"] - d["ltp"]) / (tick * 100)
    d["circuit_dist_dn"] = (d["ltp"] - d["lower_limit"]) / (tick * 100)
    d["liquidity_state"] = np.log1p(d["liquidity_top5"])
    d["mkt_move"] = np.log1p(d["market_value_mn"].fillna(0))

    # ---- BASELINES (directional score, + = up) ----
    d["B1_imb_l1"] = d["imb_l1"]
    d["B2_imb_top5"] = d["imb_top5"]
    d["B3_imb_weighted"] = d["imb_weighted"]
    d["B4_micro_disp"] = d["micro_disp_ticks"]
    d["B5_one_frame_pressure"] = d["one_frame_pressure"]
    d["B6_signed_flow"] = d["trade_pressure"]
    d["B7_ofi"] = d["ofi_w"]
    d["B8_book_pressure"] = _roll(d, "one_frame_pressure", W_FLOW, "mean")
    d["B9_trade_pressure"] = d["signed_flow_w"]
    strong = d["imb_top5"].abs() > THETA_IMB
    persist = d.get("persistent_bid_pressure", False).astype(bool)
    d["B10_combined_rule"] = np.where(strong & (persist | (d["imb_top5"] < -THETA_IMB)),
                                      np.sign(d["imb_top5"]), 0.0)

    # ---- OUTCOMES: timestamp-anchored, repo up/down semantics ----
    for H in HORIZONS_S:
        d[f"fwd_ticks_{H}"] = np.nan
        d[f"valid_{H}"] = False
    for sym, g in d.groupby("symbol", sort=False):
        idx = g.index.to_numpy()
        ts = (g["t_frame"] - g["t_frame"].iloc[0]).dt.total_seconds().to_numpy()
        mid = g["mid"].to_numpy(dtype=float)
        tk = g["tick_size"].to_numpy(dtype=float)
        for H in HORIZONS_S:
            j = np.searchsorted(ts, ts + H, side="left")
            ok = j < len(ts)
            jj = np.where(ok, np.clip(j, 0, len(ts) - 1), 0)
            dt = np.where(ok, ts[jj] - ts, np.nan)
            valid = ok & (dt <= 2 * H) & np.isfinite(mid) & np.isfinite(mid[jj]) & (tk > 0)
            ticks = np.where(valid, (mid[jj] - mid) / np.where(tk > 0, tk, np.nan), np.nan)
            d.loc[idx, f"fwd_ticks_{H}"] = ticks
            d.loc[idx, f"valid_{H}"] = valid
    for H in HORIZONS_S:
        d[f"up_{H}"] = d[f"fwd_ticks_{H}"] > 0
        d[f"down_{H}"] = d[f"fwd_ticks_{H}"] < 0
        d[f"y_{H}"] = np.where(d[f"fwd_ticks_{H}"] > 0, 2,
                       np.where(d[f"fwd_ticks_{H}"] < 0, 0, 1)).astype(float)
        d.loc[~d[f"valid_{H}"], f"y_{H}"] = np.nan

    d["eligible"] = (~d["q_exclude"]) & d[f"valid_{PRIMARY_H}"]
    assert not any(c.startswith("fwd_") or c.startswith("y_") or c.startswith("up_")
                   or c.startswith("down_") or c.startswith("valid_") for c in FEATURES), \
        "a future field leaked into the frozen feature list"
    return d
