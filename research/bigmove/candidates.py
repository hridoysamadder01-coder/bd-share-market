"""Big-move candidate families, tested independently before any combination.

The chain under test is:

    ABNORMAL ACTIVITY -> price has NOT already expanded -> absorption
    -> causal relative leadership -> supply tightening -> expansion

Nothing here assumes that chain is true. Each link is a separate family with its
own mask, and a link only earns a place in a combination after it survives alone.
Families are deliberately overlapping in places, because the comparison between a
raw shock and the same shock with a pre-move filter IS the experiment.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

import numpy as np
import pandas as pd


def _f(d: pd.DataFrame, c: str) -> pd.Series:
    return pd.to_numeric(d[c], errors="coerce") if c in d.columns else pd.Series(np.nan, index=d.index)


def families(d: pd.DataFrame) -> List[Tuple[str, str, str, pd.Series]]:
    """(name, family, description, mask). Masks are boolean, NaN-safe."""
    out: List[Tuple[str, str, str, pd.Series]] = []

    def add(name, family, desc, mask):
        out.append((name, family, desc, mask.fillna(False)))

    rv, vz, tz = _f(d, "rel_volume"), _f(d, "vol_z"), _f(d, "turnover_z")
    acc = _f(d, "vol_accel")
    p3, p5, p1 = _f(d, "prior_3d_ret"), _f(d, "prior_5d_ret"), _f(d, "prior_1d_ret")
    dh = _f(d, "dist_from_20d_high")
    rp = _f(d, "range_pos_20d")
    vca = _f(d, "volume_compression_activity")
    br = _f(d, "body_ratio")
    rc = _f(d, "range_compression")
    dvs = _f(d, "down_vol_share_5")
    spd = _f(d, "sell_pressure_drop")
    dex = _f(d, "downside_excursion_5")
    xr = _f(d, "xs_rank_asof")
    xz = _f(d, "share_z_asof")

    # ---------------------------------------------------- A. abnormal activity
    for thr in (2.0, 3.0, 5.0):
        add(f"A_relvol_ge{thr:g}", "activity",
            f"today's volume >= {thr:g}x its own trailing 20-day median", rv >= thr)
    for z in (2.0, 3.0, 4.0):
        add(f"A_volz_ge{z:g}", "activity", f"volume z >= {z:g} vs own 60-day history", vz >= z)
    add("A_turnover_z_ge3", "activity", "turnover z >= 3 vs own 60-day history", tz >= 3.0)
    add("A_vol_accel_ge2", "activity", "relative volume doubled against yesterday's", acc >= 2.0)
    add("A_first_shock", "activity", "first abnormal-volume day after a quiet five",
        d.get("is_first_shock", pd.Series(False, index=d.index)))
    add("A_repeat_shock", "activity", "abnormal volume with another in the prior five",
        d.get("is_repeat_shock", pd.Series(False, index=d.index)))

    # -------------------------------- B. the pre-move test — the central question
    # Does abnormal activity still predict a big move when price has NOT already run?
    for thr in (2.0, 3.0):
        base = rv >= thr
        add(f"B_relvol{thr:g}_premove_p3lt2", "premove",
            f"{thr:g}x volume AND prior 3-day return below +2 %", base & (p3 < 0.02))
        add(f"B_relvol{thr:g}_premove_p3lt5", "premove",
            f"{thr:g}x volume AND prior 3-day return below +5 %", base & (p3 < 0.05))
        add(f"B_relvol{thr:g}_postmove_p3ge5", "premove",
            f"{thr:g}x volume AFTER a prior 3-day return of +5 % or more — the echo",
            base & (p3 >= 0.05))
        add(f"B_relvol{thr:g}_below20dhigh", "premove",
            f"{thr:g}x volume AND still 5 % or more below the 20-day high",
            base & (dh <= -0.05))
        add(f"B_relvol{thr:g}_atbreakout", "premove",
            f"{thr:g}x volume AND within 1 % of the 20-day high", base & (dh >= -0.01))

    # ------------------------------------ C. volume compression (the SAI shape)
    for q in (0.95, 0.98, 0.99):
        thr = vca.quantile(q)
        add(f"C_vca_q{int(q*100)}", "compression",
            f"volume-compression activity above its {int(q*100)}th percentile ({thr:.2f})",
            vca >= thr)
    add("C_vca_q95_premove", "compression",
        "volume compression in the top 5 % AND prior 3-day return below +2 %",
        (vca >= vca.quantile(0.95)) & (p3 < 0.02))
    add("C_bigvol_smallbody", "compression",
        "3x volume with a body under a quarter of the day's range",
        (rv >= 3.0) & (br <= 0.25))

    # -------------------------------------------- D. supply tightening proxies
    add("D_range_compression", "tightening",
        "5-day true range under 60 % of the 20-day, price in the top half of its range",
        (rc <= 0.6) & (rp >= 0.5))
    add("D_sell_pressure_drop", "tightening",
        "down-volume share fell by more than 15 points against the prior window",
        spd >= 0.15)
    add("D_shallow_pullback", "tightening",
        "worst low of the last five within 3 % of today's close, near the 20-day high",
        (dex >= -0.03) & (dh >= -0.05))
    add("D_shock_then_quiet_selling", "tightening",
        "abnormal volume in the prior five days AND down-volume share now under 35 %",
        (_f(d, "abn_days_prev5") >= 1) & (dvs <= 0.35))

    # ------------------------------------- E. causal cross-sectional leadership
    if "xs_rank_asof" in d.columns:
        for q in (0.90, 0.95, 0.99):
            add(f"E_xsrank_ge{int(q*100)}", "leadership",
                f"causal cross-sectional rank of relative volume >= {q:.2f}", xr >= q)
        add("E_xsz_ge3", "leadership",
            "relative volume 3 robust deviations above the causal market median", xz >= 3.0)
        add("E_xsrank95_premove", "leadership",
            "causal rank >= 0.95 AND prior 3-day return below +2 %",
            (xr >= 0.95) & (p3 < 0.02))

    # ------------------------------------------------------ F. circuit room
    add("F_room_available", "constraint",
        "abnormal volume that did NOT close at its circuit limit",
        (rv >= 2.0) & ~d.get("at_limit_up", pd.Series(False, index=d.index)).fillna(False))
    add("F_at_limit", "constraint",
        "abnormal volume that DID close at its limit — no room left tomorrow",
        (rv >= 2.0) & d.get("at_limit_up", pd.Series(False, index=d.index)).fillna(False))

    return out


def combinations(d: pd.DataFrame, survivors: List[str]) -> List[Tuple[str, str, str, pd.Series]]:
    """Chain links, built only from families that survived on their own."""
    rv = _f(d, "rel_volume")
    p3 = _f(d, "prior_3d_ret")
    dh = _f(d, "dist_from_20d_high")
    dvs = _f(d, "down_vol_share_5")
    xr = _f(d, "xs_rank_asof")
    out: List[Tuple[str, str, str, pd.Series]] = []

    def add(name, desc, mask):
        out.append((name, "chain", desc, mask.fillna(False)))

    shock = rv >= 2.0
    premove = p3 < 0.02
    below = dh <= -0.05
    tight = dvs <= 0.35
    lead = xr >= 0.95

    add("CHAIN_2_shock_premove", "shock + not already run", shock & premove)
    add("CHAIN_3_shock_premove_below", "shock + not run + below the 20-day high",
        shock & premove & below)
    add("CHAIN_4_shock_premove_below_tight",
        "shock + not run + below high + light sell-side", shock & premove & below & tight)
    if "xs_rank_asof" in d.columns:
        add("CHAIN_4_shock_premove_lead",
            "shock + not run + causal cross-sectional leadership", shock & premove & lead)
        add("CHAIN_5_full",
            "shock + not run + below high + leadership + light sell-side",
            shock & premove & below & lead & tight)
    return out
