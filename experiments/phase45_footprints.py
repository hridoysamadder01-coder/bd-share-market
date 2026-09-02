#!/usr/bin/env python3
"""PHASE 4.5 — doorstep footprint research (v2, after adversarial review).

THE CORRECTED QUESTION
----------------------
Phase 4 asked "does a state carry tradeable alpha after costs?" and answered
no. That was the wrong question for this track. The actual question is:

    From PUBLIC end-of-day data alone, do certain footprints appear BEFORE an
    abnormal price event (a limit-up/limit-down proxy hit, an abnormal run)
    more often than chance — by how much, with what lead time, and how often
    does the footprint fire with nothing following it?

No trade, no cost layer, no BUY/SELL.

WHAT v2 CHANGED, AND WHY (every item came out of the five-lens review of v1)
---------------------------------------------------------------------------
1. The v1 "fresh" filter was ONE-SIDED: for down-outcomes it removed only
   rows with a recent DOWN door, so a footprint firing on or just after a
   limit-UP day was counted as a doorstep for the limit-DOWN that followed.
   That reversal carried essentially all of v1's down-door candidates.
   v2 adds `fresh_both` (no door of EITHER direction in t-5..t, applied to
   occurrences and base) and makes it the population for candidacy.
2. The v1 limit proxy had no far bound, so ex-date / bonus reference-price
   resets (drops far beyond the band) counted as limit-downs. v2 counts a
   limit hit only AT the band; a beyond-band day is a corporate-action
   suspect and any window containing one is UNMEASURABLE.
3. F08 (idiosyncratic move) and F16 (already moved) were unsigned, so an
   idiosyncratic DROP was scored as a doorstep for a limit-UP (a bounce), and
   the "already moved" reference was a mixed bag. v2 signs both.
4. The trailing-σ quintile match does not control for TODAY's shock. v2 adds
   a shock-matched base (date × σ-quintile × |ret_1|/σ quintile) and requires
   a candidate to clear it too.
5. Per-date excess series are serially correlated for k ≥ 5 and persistent
   footprints (ACF1 up to 0.63). v2 reports a Newey-West t (L = 10) and
   gates on it; the iid t is kept for comparison.
6. n_hit counted the same door many times for persistent footprints. v2
   reports distinct (symbol, door-date) events and gates on them.
7. Horizons are nested. v2 also scores the INCREMENTAL outcome (first door in
   (k_prev, k]) and only lets a longer horizon stand as a finding if the
   increment itself passes.
8. "Beats both references" was a point comparison. v2 uses a date-block
   bootstrap lower bound on the lift ratio, plus a within-parent paired test
   for footprints that are subsets of a reference.
9. A placebo (every footprint shifted +20 sessions) calibrates what the gates
   mean under this base-rate construction.
10. BH-FDR is applied to door outcomes and non-reference footprints only, and
    reported as what it is: non-binding here.

Pre-registration note: v1's design was written before v1 was run, but design,
code and results were committed together after the run, so pre-registration
rests on the author's statement, not on version history. Every v2 change was
made after seeing v1 results. The sealed holdout is the only real test.

  python3 experiments/phase45_footprints.py --tag dse_eod
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bdlib import config as C  # noqa: E402
from bdlib import io as bio  # noqa: E402
from bdlib import panels as P  # noqa: E402

HORIZONS = (1, 2, 3, 5, 10)
PREV_H = {1: 0, 2: 1, 3: 2, 5: 3, 10: 5}
UP_OUTCOMES = ("limit_up", "abn_up", "run20")
DOWN_OUTCOMES = ("limit_down", "abn_down")
DOOR_OUTCOMES = UP_OUTCOMES + DOWN_OUTCOMES
ALL_OUTCOMES = DOOR_OUTCOMES + ("activity",)
VARIANTS = ("any", "fresh", "fresh_both")
OUTCOME_NOTE = {
    "limit_up": "any session in (t,t+k] closing AT the upper band (95%..100%+tol of the band)",
    "limit_down": "any session in (t,t+k] closing AT the lower band",
    "abn_up": "max cumulative log return over (t,t+k] ≥ max(2.5·σ_prev·√k, 5%)",
    "abn_down": "min cumulative log return over (t,t+k] ≤ −max(2.5·σ_prev·√k, 5%)",
    "run20": "max cumulative log return over (t,t+k] ≥ ln(1.20)",
    "activity": "any session in (t,t+k] with rel_volume_z ≥ 2 — MECHANICAL, never a door",
}

MIN_N, MIN_DATES, MIN_DOORS, FDR_Q = 200, 30, 30, 0.10
MIN_PRICE = 10.0
CAND_LIFT, CAND_T = 1.5, 3.0
NW_LAGS = 10
BOOT_REPS, BOOT_BLOCK, BOOT_SEED = 500, 10, 20260902
PLACEBO_SHIFT = 20

FP = dict(z_abn=2.0, z_mid=1.5, z_elev=1.0, calm_mult=1.0, dip_mult=1.0, win=5,
          persist_min=3, absorb_min=2, market_quiet_breadth=0.05, coil_z=-1.0,
          close_strength=0.8, accum_thresh=0.5, vpd_thresh=2.0, move_mult=2.0,
          out_move_mult=2.5, out_move_floor=0.05, run_thresh=0.20)

# (id, family, definition). v2 renames three footprints to what they measure
# (review S-12): "absorption" → "dip recovered on volume"; "accumulation
# proxy" → "volume-weighted closing strength (10 sessions)"; F08/F16 signed.
FOOTPRINTS = [
    ("F01_quiet_volume", "quiet volume", "rel_volume_z ≥ 2 and |ret_1| ≤ σ_prev"),
    ("F02_quiet_volume_persistent", "quiet volume",
     "≥3 of the last 5 sessions rel_volume_z ≥ 1 and |ret_5| ≤ σ_prev·√5"),
    ("F03_dip_recovered", "dip recovered",
     "low ≤ prev_close·(1−σ_prev) and close ≥ prev_close and rel_volume_z ≥ 1 (v1: 'absorption')"),
    ("F04_dip_recovered_persistent", "dip recovered", "≥2 dip-recovered sessions in the last 5"),
    ("F05_departure_calm", "own-baseline departure",
     "rung-2 state ∈ {DEPARTURE, EXTREME} and |ret_1| ≤ σ_prev"),
    ("F06_departure_any", "own-baseline departure", "rung-2 state ∈ {DEPARTURE, EXTREME}"),
    ("F07_idio_activity", "idiosyncratic",
     "rel_volume_z ≥ 2 while ≤5% of names are abnormal-volume that day"),
    ("F08u_idio_move_up", "idiosyncratic (post-move)",
     "market_relative_ret ≥ +2σ_prev while |market_ret| ≤ σ_market_prev"),
    ("F08d_idio_move_down", "idiosyncratic (post-move)",
     "market_relative_ret ≤ −2σ_prev while |market_ret| ≤ σ_market_prev"),
    ("F09_idio_quiet_volume", "idiosyncratic", "F01 while ≤5% of names are abnormal-volume"),
    ("F10_coil_then_volume", "compression → activity",
     "mean range_z over the prior 5 ≤ −1 and rel_volume_z ≥ 1.5 today"),
    ("F11_closing_strength", "closing strength",
     "close_location ≥ 0.8 and rel_volume_z ≥ 1.5 and |ret_1| ≤ σ_prev"),
    ("F12_closing_strength_10s", "closing strength",
     "accumulation_proxy ≥ 0.5 = 10-session mean of (close_location−½)·rel_volume_z "
     "(v1: 'accumulation proxy')"),
    ("F13_volume_price_divergence", "quiet volume", "volume_price_divergence ≥ 2"),
    ("F14_volume_no_range", "quiet volume",
     "rel_turnover_z ≥ 2 and range_z ≤ 0 — turnover is DERIVED (close×volume), so this is "
     "abnormal volume without range expansion (v1: 'turnover_no_range')"),
    ("F15_REF_abnormal_volume", "REFERENCE", "rel_volume_z ≥ 2 — plain abnormal volume"),
    ("F16u_REF_moved_up", "REFERENCE", "ret_1 ≥ +2σ_prev — already moved up"),
    ("F16d_REF_moved_down", "REFERENCE", "ret_1 ≤ −2σ_prev — already moved down"),
    ("F17_abnormal_volume_persistent", "persistence",
     "abnormal_persistence ≥ 2 (strict subset of F15)"),
    ("F18_quiet_volume_repeat", "quiet volume", "F01 today and F01 on ≥1 of the prior 5"),
]
FP_IDS = [f[0] for f in FOOTPRINTS]
REF_IDS = ("F15_REF_abnormal_volume", "F16u_REF_moved_up", "F16d_REF_moved_down")
# Parent for the within-parent paired increment test (footprint ⊂ parent, or ≈).
PARENT = {
    "F01_quiet_volume": "F15_REF_abnormal_volume",
    "F07_idio_activity": "F15_REF_abnormal_volume",
    "F09_idio_quiet_volume": "F07_idio_activity",
    "F14_volume_no_range": "F15_REF_abnormal_volume",
    "F17_abnormal_volume_persistent": "F15_REF_abnormal_volume",
    "F18_quiet_volume_repeat": "F01_quiet_volume",
    "F08u_idio_move_up": "F16u_REF_moved_up",
    "F08d_idio_move_down": "F16d_REF_moved_down",
    "F05_departure_calm": "F06_departure_any",
    "F11_closing_strength": "F15_REF_abnormal_volume",
}
PREDOOR_FEATURES = ["rel_volume_z", "rel_turnover_z", "range_z", "close_location",
                    "market_relative_ret", "volume_price_divergence",
                    "accumulation_proxy", "amihud_z", "ret_1"]


def moved_ref_for(fp: str, outcome: str) -> str:
    """The 'already moved' reference a footprint must beat: same sign for the
    signed footprints; for unsigned ones the STRONGER of the two (conservative)."""
    if fp == "F08u_idio_move_up":
        return "F16u_REF_moved_up"
    if fp == "F08d_idio_move_down":
        return "F16d_REF_moved_down"
    return "MAX(F16u,F16d)"


# --------------------------------------------------------------------------- #
def band_of(price: pd.Series) -> pd.Series:
    edges = [0.0] + [cap for cap, _ in C.CIRCUIT_BANDS_UNVERIFIED]
    bands = [b for _, b in C.CIRCUIT_BANDS_UNVERIFIED]
    cut = pd.cut(price, bins=edges, labels=False, right=True, include_lowest=False)
    arr = np.array(bands + [np.nan])
    idx = cut.fillna(len(bands)).astype(int).to_numpy()
    return pd.Series(arr[idx], index=price.index)


def _roll_sum(f: pd.DataFrame, s: pd.Series, w: int, shift: int = 0) -> pd.Series:
    return (s.astype(float).groupby(f["symbol"], sort=False)
            .transform(lambda x: x.shift(shift).rolling(w, min_periods=w).sum()))


def build_footprints(f: pd.DataFrame) -> pd.DataFrame:
    """Pre-registered footprints at the CLOSE of row t (causal; see
    verify_footprint_causality.py). `f` = one regime, any row order."""
    f = f.sort_values(["symbol", "ts"], kind="mergesort")
    g = f.groupby("symbol", sort=False)
    p = C.DEFAULT.features

    close = f["close"]
    prev_close = g["close"].shift(1)
    sigma = g["realized_vol"].shift(1)
    sigma = sigma.where(sigma > p.min_meaningful_vol)
    ret1 = f["ret_1"]
    ret5 = np.log(close / g["close"].shift(FP["win"]))
    vz, tz, rz = f["rel_volume_z"], f["rel_turnover_z"], f["range_z"]
    mkt = f.groupby("ts")["market_ret"].first().sort_index()
    msig = f["ts"].map(mkt.shift(1).rolling(p.vol_window, min_periods=20).std())

    calm1 = ret1.abs() <= FP["calm_mult"] * sigma
    calm5 = ret5.abs() <= FP["calm_mult"] * sigma * np.sqrt(FP["win"])
    dip = f["low"] / prev_close - 1.0
    market_quiet = f["xs_breadth_abnormal"] <= FP["market_quiet_breadth"]
    mrel = f["market_relative_ret"]
    mcalm = f["market_ret"].abs() <= msig

    out = pd.DataFrame(index=f.index)
    out["symbol"], out["ts"] = f["symbol"], f["ts"]
    out["sigma_prev"] = sigma
    out["shock"] = (ret1.abs() / sigma)
    out["guard"] = (close >= MIN_PRICE) & sigma.notna() & vz.notna()

    F = {}
    F["F01_quiet_volume"] = (vz >= FP["z_abn"]) & calm1
    F["F02_quiet_volume_persistent"] = (_roll_sum(f, (vz >= FP["z_elev"]), FP["win"])
                                        >= FP["persist_min"]) & calm5
    dipr = (dip <= -FP["dip_mult"] * sigma) & (close >= prev_close) & (vz >= FP["z_elev"])
    F["F03_dip_recovered"] = dipr
    F["F04_dip_recovered_persistent"] = _roll_sum(f, dipr, FP["win"]) >= FP["absorb_min"]
    elevated = f["state"].isin(("DEPARTURE", "EXTREME"))
    F["F05_departure_calm"] = elevated & calm1
    F["F06_departure_any"] = elevated
    F["F07_idio_activity"] = (vz >= FP["z_abn"]) & market_quiet
    F["F08u_idio_move_up"] = (mrel >= FP["move_mult"] * sigma) & mcalm
    F["F08d_idio_move_down"] = (mrel <= -FP["move_mult"] * sigma) & mcalm
    F["F09_idio_quiet_volume"] = F["F01_quiet_volume"] & market_quiet
    coil = rz.groupby(f["symbol"], sort=False).transform(
        lambda x: x.shift(1).rolling(FP["win"], min_periods=FP["win"]).mean())
    F["F10_coil_then_volume"] = (coil <= FP["coil_z"]) & (vz >= FP["z_mid"])
    F["F11_closing_strength"] = ((f["close_location"] >= FP["close_strength"])
                                 & (vz >= FP["z_mid"]) & calm1)
    F["F12_closing_strength_10s"] = f["accumulation_proxy"] >= FP["accum_thresh"]
    F["F13_volume_price_divergence"] = f["volume_price_divergence"] >= FP["vpd_thresh"]
    F["F14_volume_no_range"] = (tz >= FP["z_abn"]) & (rz <= 0)
    F["F15_REF_abnormal_volume"] = vz >= FP["z_abn"]
    F["F16u_REF_moved_up"] = ret1 >= FP["move_mult"] * sigma
    F["F16d_REF_moved_down"] = ret1 <= -FP["move_mult"] * sigma
    F["F17_abnormal_volume_persistent"] = f["abnormal_persistence"] >= 2
    F["F18_quiet_volume_repeat"] = F["F01_quiet_volume"] & (
        _roll_sum(f, F["F01_quiet_volume"], FP["win"], shift=1) >= 1)

    for k in FP_IDS:
        out[k] = F[k].fillna(False).astype(bool) & out["guard"]
    # PLACEBO: the same footprint 20 sessions stale. Must show lift ≈ 1.
    for k in FP_IDS:
        out["PLACEBO_" + k] = (out[k].groupby(f["symbol"], sort=False)
                               .shift(PLACEBO_SHIFT).fillna(False).astype(bool) & out["guard"])
    return out


# --------------------------------------------------------------------------- #
def forward_outcomes(f: pd.DataFrame) -> pd.DataFrame:
    """Outcomes over (t, t+k] on the regime's trading calendar, as FIRST-HIT
    OFFSETS (NaN = no hit). Limit hits are AT the band; a beyond-band day is a
    corporate-action suspect: never a hit, and any window containing one is
    unmeasurable. `open` is never used."""
    piv = f.pivot_table(index="ts", columns="symbol", values="close", aggfunc="last")
    vzp = f.pivot_table(index="ts", columns="symbol", values="rel_volume_z",
                        aggfunc="last").reindex(index=piv.index, columns=piv.columns)
    sig = f.pivot_table(index="ts", columns="symbol", values="sigma_prev",
                        aggfunc="last").reindex(index=piv.index, columns=piv.columns)
    logC = np.log(piv)
    R = piv / piv.shift(1) - 1.0
    band = piv.shift(1).apply(band_of)
    absR = R.abs()
    tol = C.LIMIT_BAND_TOLERANCE
    at_band = (absR >= C.CIRCUIT_PROXY_FRACTION * band) & (absR <= band + tol)
    CA = (absR > band + tol)                                  # corporate-action suspect
    LU = ((R > 0) & at_band)
    LD = ((R < 0) & at_band)
    AB = (vzp >= FP["z_abn"])
    thresh1 = np.maximum(FP["out_move_mult"] * sig, FP["out_move_floor"])
    abn_up_day = (np.log1p(R) >= thresh1) & ~CA
    abn_dn_day = (np.log1p(R) <= -thresh1) & ~CA
    up_day = (LU | abn_up_day).astype(float)
    dn_day = (LD | abn_dn_day).astype(float)
    present = piv.notna().astype(float)
    Rn = R.notna()

    def first_offset(cond_fn, kmax):
        off = pd.DataFrame(np.nan, index=piv.index, columns=piv.columns)
        for j in range(1, kmax + 1):
            cj = cond_fn(j)
            off = off.where(~(off.isna() & cj), float(j))
        return off

    KMAX = max(HORIZONS)
    LUf, LDf, ABf = LU.astype(float), LD.astype(float), AB.astype(float)
    off_static = {
        "limit_up": first_offset(lambda j: LUf.shift(-j).fillna(0.0) == 1.0, KMAX),
        "limit_down": first_offset(lambda j: LDf.shift(-j).fillna(0.0) == 1.0, KMAX),
        "run20": first_offset(lambda j: ((logC.shift(-j) - logC) >= np.log1p(FP["run_thresh"]))
                              .fillna(False), KMAX),
        "activity": first_offset(lambda j: ABf.shift(-j).fillna(0.0) == 1.0, KMAX),
    }
    cols = {}
    for k in HORIZONS:
        n_fwd = present.rolling(k).sum().shift(-k)
        ca_in = CA.astype(float).rolling(k).sum().shift(-k)
        vz_in = vzp.notna().astype(float).rolling(k).sum().shift(-k)
        measurable = (n_fwd == k) & (ca_in == 0)
        thresh_k = np.maximum(FP["out_move_mult"] * sig * np.sqrt(k), FP["out_move_floor"])
        off_k = dict(off_static)
        off_k["abn_up"] = first_offset(
            lambda j: ((logC.shift(-j) - logC) >= thresh_k).fillna(False), k)
        off_k["abn_down"] = first_offset(
            lambda j: ((logC.shift(-j) - logC) <= -thresh_k).fillna(False), k)
        for o in ALL_OUTCOMES:
            off = off_k[o].where(off_k[o] <= k)
            m = measurable.copy()
            if o in ("abn_up", "abn_down"):
                m &= thresh_k.notna()
            if o == "activity":
                m &= (vz_in == k)
            cols[f"y_{o}_{k}"] = off.notna().astype(float).where(m)
            cols[f"off_{o}_{k}"] = off.where(m)
    frames = [mat.stack(future_stack=True).rename(n) for n, mat in cols.items()]
    out = pd.concat(frames, axis=1).reset_index()
    out.columns = ["ts", "symbol"] + list(cols)

    W = FP["win"] + 1
    d = pd.concat([
        LU.astype(float).where(Rn).stack(future_stack=True).rename("door_limit_up_day"),
        abn_up_day.astype(float).where(Rn).stack(future_stack=True).rename("door_abn_up_day"),
        CA.astype(float).where(Rn).stack(future_stack=True).rename("ca_day"),
        (up_day.rolling(W, min_periods=1).sum() >= 1).stack(future_stack=True).rename("recent_door_up"),
        (dn_day.rolling(W, min_periods=1).sum() >= 1).stack(future_stack=True).rename("recent_door_down"),
        (CA.astype(float).rolling(W, min_periods=1).sum() >= 1).stack(future_stack=True).rename("recent_ca"),
    ], axis=1).reset_index()
    d.columns = ["ts", "symbol", "door_limit_up_day", "door_abn_up_day", "ca_day",
                 "recent_door_up", "recent_door_down", "recent_ca"]
    out = out.merge(d, on=["ts", "symbol"], how="left")
    out.attrs["date_index"] = piv.index
    return out


def population_mask(df: pd.DataFrame, outcome: str, variant: str) -> pd.Series:
    """any: guarded rows without a corporate-action suspect in t-5..t.
    fresh: also no door of the OUTCOME's direction in t-5..t (v1's one-sided rule).
    fresh_both: no door of EITHER direction in t-5..t (v2 primary)."""
    m = df["guard"] & ~df["recent_ca"].fillna(False).astype(bool)
    up = df["recent_door_up"].fillna(False).astype(bool)
    dn = df["recent_door_down"].fillna(False).astype(bool)
    if variant == "fresh":
        if outcome in UP_OUTCOMES:
            m &= ~up
        elif outcome in DOWN_OUTCOMES:
            m &= ~dn
    elif variant == "fresh_both":
        m &= ~up & ~dn
    return m


# --------------------------------------------------------------------------- #
def nw_t(e: pd.Series, lags: int = NW_LAGS) -> tuple[float, float, int]:
    """(t_iid, t_newey_west, n) on a per-date series treated as consecutive."""
    e = e.dropna().sort_index()
    n = len(e)
    if n < 2:
        return np.nan, np.nan, n
    x = e.to_numpy(dtype=float)
    m = x.mean()
    u = x - m
    g0 = float((u * u).mean())
    if g0 <= 0:
        return np.nan, np.nan, n
    var = g0
    for l in range(1, min(lags, n - 1) + 1):
        var += 2.0 * (1.0 - l / (lags + 1.0)) * float((u[l:] * u[:-l]).mean())
    var = max(var, 1e-18)
    return float(m / np.sqrt(g0 / n)), float(m / np.sqrt(var / n)), n


def bh_fdr(p: np.ndarray, q: float) -> tuple[np.ndarray, float]:
    ok = np.isfinite(p)
    idx = np.where(ok)[0]
    keep = np.zeros_like(p, dtype=bool)
    if len(idx) == 0:
        return keep, np.nan
    order = idx[np.argsort(p[idx])]
    m = len(order)
    thr = q * (np.arange(1, m + 1) / m)
    passed = p[order] <= thr
    cutoff = np.nan
    if passed.any():
        last = np.max(np.where(passed)[0])
        keep[order[:last + 1]] = True
        cutoff = float(thr[last])
    return keep, cutoff


def two_sided_p(t: np.ndarray) -> np.ndarray:
    from math import erfc, sqrt
    return np.array([erfc(abs(v) / sqrt(2)) if np.isfinite(v) else np.nan for v in t])


def loo_base(df: pd.DataFrame, y: pd.Series, m: pd.Series, keys) -> pd.Series:
    grp = df.loc[m].groupby(keys)[y.name]
    S, N = grp.transform("sum"), grp.transform("size")
    return ((S - y[m]) / (N - 1)).where(N > 1).reindex(df.index)


def row_stats(d: pd.DataFrame, y: str, off: str, date_index) -> dict:
    """d has columns y, off, b, bv, bs, ts, symbol (already restricted)."""
    d = d.dropna(subset=[y, "b", "bv", "bs"])
    n = len(d)
    if n == 0:
        return {"n_measured": 0}
    hit = float(d[y].mean())
    per = d.groupby("ts")[[y, "b", "bv", "bs"]].mean()
    t_b, _, n_dates = nw_t(per[y] - per["b"])
    t_vm, t_vm_nw, _ = nw_t(per[y] - per["bv"])
    t_sh, t_sh_nw, _ = nw_t(per[y] - per["bs"])
    base, base_vm, base_sh = float(d["b"].mean()), float(d["bv"].mean()), float(d["bs"].mean())
    hits = d[d[y] == 1]
    n_hit = len(hits)
    if n_hit:
        pos = date_index.get_indexer(hits["ts"])
        door_pos = pos + hits[off].to_numpy(dtype=int)
        doors = pd.Series(list(zip(hits["symbol"], door_pos)))
        n_doors = int(doors.nunique())
        sym_counts = hits["symbol"].value_counts()
        top3 = float(sym_counts.head(3).sum() / n_hit)
        n_sym = int(len(sym_counts))
    else:
        n_doors, top3, n_sym = 0, np.nan, 0
    return {
        "n_measured": n, "n_dates": n_dates, "n_hit": n_hit, "n_failed": n - n_hit,
        "n_distinct_doors": n_doors, "n_symbols_hit": n_sym, "top3_symbol_share": top3,
        "hit_rate": hit, "fail_share": 1 - hit,
        "base_sameday": base, "lift_sameday": (hit / base) if base > 0 else np.nan,
        "t_sameday": t_b,
        "base_volmatched": base_vm, "excess_volmatched": hit - base_vm,
        "lift_volmatched": (hit / base_vm) if base_vm > 0 else np.nan,
        "t_volmatched": t_vm, "t_volmatched_nw": t_vm_nw,
        "base_shockmatched": base_sh,
        "lift_shockmatched": (hit / base_sh) if base_sh > 0 else np.nan,
        "t_shockmatched_nw": t_sh_nw,
    }


def analyse(df: pd.DataFrame, regime: str, date_index) -> tuple[pd.DataFrame, dict]:
    """Every footprint × outcome × horizon × variant, plus incremental
    outcomes and placebos. Base rates are computed per (variant, outcome, k)
    on the population, leave-one-out, on three strata: same day; same day ×
    σ_prev quintile; same day × σ_prev quintile × |ret_1|/σ_prev quintile."""
    df = df.copy()
    gd = df["guard"]
    r = df.loc[gd].groupby("ts")["sigma_prev"].rank(pct=True)
    df["volq"] = np.minimum((r * 5).fillna(0).astype(int), 4).reindex(df.index)
    rs = df.loc[gd].groupby("ts")["shock"].rank(pct=True)
    df["shockq"] = np.minimum((rs * 5).fillna(0).astype(int), 4).reindex(df.index)
    n_guard = int(gd.sum())
    rows, cache = [], {}
    fps = FP_IDS + ["ALL (base rate)"]
    for variant in VARIANTS:
        for o in ALL_OUTCOMES:
            pop = population_mask(df, o, variant)
            for k in HORIZONS:
                specs = [(o, f"y_{o}_{k}", f"off_{o}_{k}", None)]
                if o in DOOR_OUTCOMES and k > 1 and variant == "fresh_both":
                    # INCREMENTAL outcome: first door in (k_prev, k]
                    kp = PREV_H[k]
                    yf = f"yfirst_{o}_{k}"
                    df[yf] = ((df[f"off_{o}_{k}"] > kp) & (df[f"off_{o}_{k}"] <= k)) \
                        .astype(float).where(df[f"y_{o}_{k}"].notna())
                    specs.append((f"{o}(first)", yf, f"off_{o}_{k}", None))
                for oname, y, off, _ in specs:
                    ys = df[y]
                    m = ys.notna() & pop
                    df["b"] = loo_base(df, ys, m, "ts")
                    df["bv"] = loo_base(df, ys, m, ["ts", "volq"])
                    df["bs"] = loo_base(df, ys, m, ["ts", "volq", "shockq"])
                    dpop = df[m]
                    fp_list = list(fps)
                    if variant == "fresh_both" and o in DOOR_OUTCOMES and k in (1, 3, 5) \
                            and not oname.endswith("(first)"):
                        fp_list += ["PLACEBO_" + f for f in FP_IDS]
                    for fp in fp_list:
                        occ = dpop if fp.startswith("ALL") else dpop[dpop[fp]]
                        n_occ = int((df[fp] & pop).sum()) if not fp.startswith("ALL") \
                            else int(pop.sum())
                        st = row_stats(occ[[y, off, "b", "bv", "bs", "ts", "symbol"]],
                                       y, off, date_index)
                        row = {"regime": regime, "variant": variant, "footprint": fp,
                               "outcome": oname, "horizon": k,
                               "door_outcome": o in DOOR_OUTCOMES,
                               "placebo": fp.startswith("PLACEBO_"),
                               "n_guarded_rows": n_guard, "n_population": int(pop.sum()),
                               "n_occurrences": n_occ,
                               "n_no_outcome": n_occ - st.get("n_measured", 0),
                               "unmeasurable_share": ((n_occ - st.get("n_measured", 0)) / n_occ)
                               if n_occ else np.nan}
                        row.update(st)
                        rows.append(row)
                        if fp in REF_IDS or fp in PARENT.values():
                            cache[(variant, oname, k, fp)] = occ[[y, "bv", "ts"]].rename(
                                columns={y: "y"})
                    # within-parent paired increment: F vs not-F inside parent P
                    for fp, parent in PARENT.items():
                        key = (variant, oname, k, parent)
                        if key not in cache:
                            continue
                        par = dpop[dpop[parent]]
                        if par.empty:
                            continue
                        inF = par[fp]
                        a = par[inF].groupby("ts")[y].mean()
                        b = par[~inF].groupby("ts")[y].mean()
                        diff = (a - b).dropna()
                        if len(diff) >= 2:
                            _, t_nw, nd = nw_t(diff)
                            dmean, test = float(diff.mean()), "paired-by-date"
                        else:
                            # A DATE-LEVEL condition (e.g. "market quiet") never
                            # shares a date with its complement, so the paired
                            # test is empty by construction. Compare the parent's
                            # per-date vol-matched excess on condition-dates vs
                            # other dates instead (Welch t over dates).
                            exF = (a - par[inF].groupby("ts")["bv"].mean()).dropna()
                            exN = (b - par[~inF].groupby("ts")["bv"].mean()).dropna()
                            if len(exF) > 1 and len(exN) > 1:
                                se = np.sqrt(exF.var(ddof=1) / len(exF) + exN.var(ddof=1) / len(exN))
                                dmean = float(exF.mean() - exN.mean())
                                t_nw = float(dmean / se) if se > 0 else np.nan
                                nd = int(len(exF) + len(exN))
                            else:
                                dmean, t_nw, nd = np.nan, np.nan, 0
                            test = "between-dates (Welch)"
                        rows.append({"regime": regime, "variant": variant, "footprint": fp,
                                     "outcome": oname, "horizon": k, "_within_parent": True,
                                     "within_parent": parent, "within_parent_t_nw": t_nw,
                                     "within_parent_dates": nd, "within_parent_diff": dmean,
                                     "within_parent_test": test})
    res = pd.DataFrame(rows)
    flag = (res["_within_parent"].fillna(False).astype(bool) if "_within_parent" in res.columns
            else pd.Series(False, index=res.index))
    wp = res[flag]
    res = res[~flag].copy()
    res = res.drop(columns=[c for c in ("_within_parent", "within_parent", "within_parent_t_nw",
                                        "within_parent_dates", "within_parent_diff",
                                        "within_parent_test")
                            if c in res.columns])
    if len(wp):
        res = res.merge(wp[["regime", "variant", "footprint", "outcome", "horizon",
                            "within_parent", "within_parent_t_nw", "within_parent_dates",
                            "within_parent_diff", "within_parent_test"]],
                        on=["regime", "variant", "footprint", "outcome", "horizon"], how="left")
    for c in ("door_outcome", "placebo"):          # object dtype after the concat ⇒ cast
        res[c] = res[c].fillna(False).astype(bool)
    # ALL rows: the leave-one-out identity makes every excess exactly 0 → no t.
    allm = res["footprint"].str.startswith("ALL")
    for c in [c for c in res.columns if c.startswith("t_") or c.startswith("lift_")]:
        res.loc[allm, c] = np.nan
    # references at the same (variant, outcome, horizon) — lift ratios
    key = list(zip(res["variant"], res["outcome"], res["horizon"]))
    refs = {rid: res[res["footprint"] == rid].set_index(["variant", "outcome", "horizon"])
            ["lift_volmatched"] for rid in REF_IDS}
    lv = res["lift_volmatched"].to_numpy()
    r15 = refs["F15_REF_abnormal_volume"].reindex(key).to_numpy()
    r16u = refs["F16u_REF_moved_up"].reindex(key).to_numpy()
    r16d = refs["F16d_REF_moved_down"].reindex(key).to_numpy()
    rmax = np.fmax(r16u, r16d)
    ref_moved = np.where(res["footprint"] == "F08u_idio_move_up", r16u,
                         np.where(res["footprint"] == "F08d_idio_move_down", r16d, rmax))
    res["ref_moved_used"] = [moved_ref_for(fp, o) for fp, o in zip(res["footprint"], res["outcome"])]
    res["lift_vm_vs_ref_volume"] = lv / r15
    res["lift_vm_vs_ref_moved"] = lv / ref_moved
    # FDR: door outcomes, non-reference, non-placebo, non-incremental, per (regime, variant), on NW t
    res["p_volmatched_nw"] = np.nan
    res["fdr10_volmatched_nw"] = False
    fdr_info = {}
    for variant in VARIANTS:
        fam = (res["variant"].eq(variant) & res["door_outcome"] & ~res["placebo"]
               & ~res["footprint"].isin(REF_IDS) & ~allm & ~res["outcome"].str.endswith("(first)"))
        p = two_sided_p(res.loc[fam, "t_volmatched_nw"].to_numpy(dtype=float))
        keep, cutoff = bh_fdr(p, FDR_Q)
        res.loc[fam, "p_volmatched_nw"] = p
        res.loc[fam, "fdr10_volmatched_nw"] = keep
        fdr_info[variant] = {"tests": int(fam.sum()), "survivors": int(keep.sum()),
                             "bh_cutoff_p": cutoff}
    res["eligible"] = ((res["n_measured"] >= MIN_N) & (res["n_dates"] >= MIN_DATES)
                       & (res["n_distinct_doors"] >= MIN_DOORS))
    return res, {"fdr": fdr_info, "frame": df}


# --------------------------------------------------------------------------- #
def boot_ratio_lower(df: pd.DataFrame, pop: pd.Series, fp: str, ref_fps: list, y: str,
                     bv: pd.Series, date_index, reps=BOOT_REPS, block=BOOT_BLOCK) -> float:
    """Lower 2.5% bound of lift(F)/lift(ref) over a date-block bootstrap, where
    ref = the strongest of `ref_fps` at each resample (conservative)."""
    m = pop & df[y].notna() & bv.notna()
    dates = date_index
    pos = pd.Series(dates.get_indexer(df.loc[m, "ts"]), index=df.index[m])

    def agg(mask):
        S = np.zeros(len(dates)); B = np.zeros(len(dates))
        sub = df.index[m & mask]
        np.add.at(S, pos[sub].to_numpy(), df.loc[sub, y].to_numpy())
        np.add.at(B, pos[sub].to_numpy(), bv[sub].to_numpy())
        return S, B
    SF, BF = agg(df[fp])
    refs = [agg(df[r]) for r in ref_fps]
    rng = np.random.default_rng(BOOT_SEED)
    nb = int(np.ceil(len(dates) / block))
    starts = np.arange(0, len(dates), block)
    out = np.empty(reps)
    for i in range(reps):
        pick = rng.choice(starts, nb, replace=True)
        idx = np.concatenate([np.arange(s, min(s + block, len(dates))) for s in pick])
        lf = SF[idx].sum() / max(BF[idx].sum(), 1e-12)
        lr = max(SR[idx].sum() / max(BR[idx].sum(), 1e-12) for SR, BR in refs)
        out[i] = lf / lr if lr > 0 else np.nan
    return float(np.nanpercentile(out, 2.5))


def stability(df: pd.DataFrame, regime: str, variant: str, rows: pd.DataFrame,
              date_index) -> pd.DataFrame:
    out = []
    df = df.assign(year=df["ts"].dt.year,
                   price_bucket=pd.cut(df["close"], [MIN_PRICE, 50, 200, np.inf],
                                       labels=["10-50", "50-200", ">200"]))
    for _, r in rows.iterrows():
        fp, o, k = r["footprint"], r["outcome"], int(r["horizon"])
        if o.endswith("(first)"):
            continue
        y, off = f"y_{o}_{k}", f"off_{o}_{k}"
        pop = population_mask(df, o, variant)
        ys = df[y]
        m = ys.notna() & pop
        df["b"] = loo_base(df, ys, m, "ts")
        df["bv"] = loo_base(df, ys, m, ["ts", "volq"])
        df["bs"] = loo_base(df, ys, m, ["ts", "volq", "shockq"])
        d = df[m & df[fp]]
        for by in ("year", "price_bucket"):
            for lvl, g in d.groupby(by, observed=True):
                st = row_stats(g[[y, off, "b", "bv", "bs", "ts", "symbol"]], y, off, date_index)
                out.append({"regime": regime, "variant": variant, "footprint": fp,
                            "outcome": o, "horizon": k, "split": by, "level": str(lvl),
                            **{c: st.get(c) for c in ("n_measured", "n_dates", "n_hit",
                                                      "n_distinct_doors", "hit_rate",
                                                      "base_volmatched", "lift_volmatched",
                                                      "t_volmatched_nw")}})
    return pd.DataFrame(out)


def recall_and_lead(df: pd.DataFrame, regime: str) -> pd.DataFrame:
    """Of the FRESH doors (no door of either direction and no corporate-action
    suspect in the prior 5 sessions) how many had the footprint in the prior
    w sessions, against the share of PRE-DOOR rows (same condition, ≥ w rows of
    history) with the footprint in any prior-w window. Row-based per symbol."""
    df = df.sort_values(["symbol", "ts"], kind="mergesort")
    g = df.groupby("symbol", sort=False)
    pos = g.cumcount()
    # "no door either direction in prior 5 rows": the grid flags shifted one row
    prior_up = df["recent_door_up"].fillna(False).groupby(df["symbol"], sort=False).shift(1).fillna(False)
    prior_dn = df["recent_door_down"].fillna(False).groupby(df["symbol"], sort=False).shift(1).fillna(False)
    prior_ca = df["recent_ca"].fillna(False).groupby(df["symbol"], sort=False).shift(1).fillna(False)
    predoor = df["guard"] & ~prior_up & ~prior_dn & ~prior_ca
    rows = []
    for door in ("door_limit_up_day", "door_abn_up_day"):
        dd = df[door].fillna(0).astype(float)
        fresh = (dd == 1) & predoor
        n_doors = int(fresh.sum())
        for fp in FP_IDS:
            fpv = df[fp].astype(float)
            for w in (5, 10):
                inw = _roll_sum(df, fpv, w, shift=1) >= 1
                hist = pos >= w
                base_pop = predoor & hist
                recall = float(inw[fresh & hist].mean()) if (fresh & hist).any() else np.nan
                base = float(inw[base_pop].mean()) if base_pop.any() else np.nan
                last_fp = pos.where(df[fp]).groupby(df["symbol"], sort=False).ffill()
                lead = pos - last_fp.groupby(df["symbol"], sort=False).shift(1)
                lead_at = lead[fresh & hist & (inw == True)]  # noqa: E712
                lead_at = lead_at[lead_at <= w]
                rows.append({"regime": regime, "door": door.replace("door_", "").replace("_day", ""),
                             "footprint": fp, "window": w, "n_fresh_doors": n_doors,
                             "n_doors_with_footprint": int((inw[fresh & hist] == True).sum()),  # noqa: E712
                             "recall": recall, "base_share_predoor_rows": base,
                             "recall_lift": (recall / base) if (base and base > 0) else np.nan,
                             "lead_median": float(lead_at.median()) if len(lead_at) else np.nan,
                             "lead_p25": float(lead_at.quantile(0.25)) if len(lead_at) else np.nan,
                             "lead_p75": float(lead_at.quantile(0.75)) if len(lead_at) else np.nan})
    return pd.DataFrame(rows)


def predoor_profile(df: pd.DataFrame, regime: str) -> pd.DataFrame:
    df = df.sort_values(["symbol", "ts"], kind="mergesort")
    prior_up = df["recent_door_up"].fillna(False).groupby(df["symbol"], sort=False).shift(1).fillna(False)
    prior_dn = df["recent_door_down"].fillna(False).groupby(df["symbol"], sort=False).shift(1).fillna(False)
    prior_ca = df["recent_ca"].fillna(False).groupby(df["symbol"], sort=False).shift(1).fillna(False)
    fresh = (df["door_limit_up_day"].fillna(0) == 1) & df["guard"] & ~prior_up & ~prior_dn & ~prior_ca
    rows = []
    for feat in PREDOOR_FEATURES:
        xs_mean = df[feat].where(df["guard"]).groupby(df["ts"]).transform("mean")
        ex = df[feat] - xs_mean
        for offn in range(1, 11):
            v = ex.groupby(df["symbol"], sort=False).shift(offn)[fresh]
            per_date = v.groupby(df["ts"][fresh]).mean()
            t, t_nw, nd = nw_t(per_date)
            rows.append({"regime": regime, "feature": feat, "sessions_before_door": offn,
                         "n_events": int(v.notna().sum()), "n_dates": nd,
                         "mean_excess_vs_xs": float(v.mean()) if v.notna().any() else np.nan,
                         "t_over_dates": t, "t_nw": t_nw})
    return pd.DataFrame(rows)


def coverage(df: pd.DataFrame, regime: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    g = df[df["guard"]]
    rows = [{"regime": regime, "footprint": fp, "n_fired": int(g[fp].sum()),
             "share_of_guarded_rows": float(g[fp].mean()),
             "n_symbols": int(g.loc[g[fp], "symbol"].nunique()),
             "n_dates": int(g.loc[g[fp], "ts"].nunique()),
             "share_with_up_door_open": float(g.loc[g[fp], "recent_door_up"].fillna(False).mean()),
             "share_with_down_door_open": float(g.loc[g[fp], "recent_door_down"].fillna(False).mean()),
             } for fp in FP_IDS]
    M = g[FP_IDS].astype(float).to_numpy()
    inter = M.T @ M
    sz = M.sum(axis=0)
    union = sz[:, None] + sz[None, :] - inter
    jac = pd.DataFrame(np.where(union > 0, inter / np.maximum(union, 1), np.nan),
                       index=FP_IDS, columns=FP_IDS)
    jac.insert(0, "regime", regime)
    return pd.DataFrame(rows), jac.reset_index().rename(columns={"index": "footprint"})


def band_evidence(bars: pd.DataFrame) -> pd.DataFrame:
    b = bars.sort_values(["symbol", "ts"], kind="mergesort")
    prev = b.groupby("symbol", sort=False)["close"].shift(1)
    r = b["close"] / prev - 1.0
    band = band_of(prev)
    rows = []
    for cap, bb in C.CIRCUIT_BANDS_UNVERIFIED:
        m = (band == bb) & r.notna() & (r.abs() >= 0.02)
        n = int(m.sum())
        if n == 0:
            continue
        rr = r[m]
        rows.append({"band_bucket_prev_close_le": cap, "band": bb, "n_moves_ge_2pct": n,
                     "share_at_+band": float(((rr - bb).abs() <= 0.0025).mean()),
                     "share_at_-band": float(((rr + bb).abs() <= 0.0025).mean()),
                     "share_beyond_band": float((rr.abs() > bb + 0.0025).mean()),
                     "n_at_+band": int(((rr - bb).abs() <= 0.0025).sum()),
                     "n_at_+band_minus_0.25": int(((rr - bb + 0.0025).abs() <= 0.00125).sum()),
                     "n_at_+band_plus_0.25": int(((rr - bb - 0.0025).abs() <= 0.00125).sum()),
                     "n_beyond_band_down": int((rr < -(bb + 0.0025)).sum())})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
def load_regimes(tag: str) -> tuple[dict, dict]:
    paths = bio.paths()
    feat = pd.read_parquet(os.path.join(paths["results"], f"{tag}_features.parquet"))
    bars = pd.read_parquet(os.path.join(paths["results"], f"{tag}_bars_annotated.parquet"),
                           columns=["symbol", "ts", "high", "low", "close", "qa_exclude"])
    bars = bars[~bars["qa_exclude"]].drop(columns="qa_exclude")
    states = pd.read_parquet(os.path.join(paths["results"], "STATE_EVENT_LOG.parquet"),
                             columns=["symbol", "ts", "state"])
    d = feat.merge(bars, on=["symbol", "ts"], how="inner").merge(
        states, on=["symbol", "ts"], how="left")
    last_bar = d.groupby("symbol")["ts"].max()
    h0, h1 = (pd.Timestamp(x) for x in C.HOLDOUT_WINDOW)
    in_holdout = d["ts"].between(h0, h1)
    n_sealed = int(in_holdout.sum())
    d = d[~in_holdout]
    assert not d["ts"].between(h0, h1).any(), "holdout rows survived the seal"
    d0, d1 = (pd.Timestamp(x) for x in C.DISCOVERY_WINDOW)
    f0, f1 = (pd.Timestamp(x) for x in C.FLOOR_ERA)
    prim = P.select(d, "PRIMARY")
    regimes = {"DISCOVERY": prim[prim["ts"].between(d0, d1)],
               "FLOOR": prim[prim["ts"].between(f0, f1)],
               "POSTBREAK": P.select(d, "POSTBREAK")}
    meta = {"sealed_holdout_rows": n_sealed, "holdout_window": list(C.HOLDOUT_WINDOW),
            "discovery_window": list(C.DISCOVERY_WINDOW),
            "survivorship": {"symbols": int(len(last_bar)),
                             "symbols_ending_before_2019": int((last_bar < "2019-01-01").sum()),
                             "note": "the universe contains no symbol whose history ends before "
                                     "2019: discovery is conditioned on survival to 2019+; "
                                     "delisted / collapsed names are absent"}}
    return regimes, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="dse_eod")
    ap.add_argument("--max-symbols", type=int, default=None,
                    help="SMOKE TEST ONLY: restrict every regime to its first N symbols")
    a = ap.parse_args()
    paths = bio.paths()

    print("=" * 78)
    print("PHASE 4.5 v2 — doorstep footprint research (DISCOVERY only; holdout SEALED)")
    print("=" * 78)
    regimes, meta = load_regimes(a.tag)
    if a.max_symbols:
        print(f"*** SMOKE TEST: first {a.max_symbols} symbols per regime — outputs are NOT results ***")
        regimes = {k: v[v["symbol"].isin(sorted(v["symbol"].unique())[:a.max_symbols])]
                   for k, v in regimes.items()}
    print(f"sealed holdout {meta['holdout_window']}: {meta['sealed_holdout_rows']:,} rows dropped at load")
    print(f"discovery window {meta['discovery_window']} · survivorship: "
          f"{meta['survivorship']['symbols_ending_before_2019']} of {meta['survivorship']['symbols']} "
          "symbols end before 2019 (universe conditioned on survival)")
    print(f"footprints: {len(FP_IDS)} (incl. 3 references) · outcomes {len(ALL_OUTCOMES)} × "
          f"horizons {HORIZONS} × variants {VARIANTS} · + incremental outcomes + placebos")

    ev = band_evidence(regimes["DISCOVERY"][["symbol", "ts", "close"]])
    with pd.option_context("display.float_format", lambda v: f"{v:.4f}", "display.width", 200):
        print("\n--- circuit band evidence (DISCOVERY): mass points of the daily return ---")
        print(ev.to_string(index=False))
    ev.to_csv(os.path.join(paths["results"], "DOORSTEP_BAND_EVIDENCE.csv"), index=False)

    all_res, all_cov, all_jac, all_rec, all_pre = [], [], [], [], []
    frames, fdr_all, date_idx = {}, {}, {}
    for regime, d in regimes.items():
        if d.empty:
            continue
        P.assert_single_panel(d, f"regime {regime}")
        fpf = build_footprints(d)
        d = d.join(fpf.drop(columns=["symbol", "ts"]))
        d = d.sort_values(["symbol", "ts"], kind="mergesort").reset_index(drop=True)
        fwd = forward_outcomes(d)
        date_idx[regime] = fwd.attrs["date_index"]
        d = d.merge(fwd, on=["symbol", "ts"], how="left")
        cov, jac = coverage(d, regime)
        res, info = analyse(d, regime, date_idx[regime])
        frames[regime] = info["frame"]
        fdr_all[regime] = info["fdr"]
        rec = recall_and_lead(d, regime)
        pre = predoor_profile(d, regime)
        all_res.append(res); all_cov.append(cov); all_jac.append(jac)
        all_rec.append(rec); all_pre.append(pre)
        print("\n" + "-" * 78)
        gd = d["guard"]
        print(f"REGIME {regime}: {len(d):,} rows · {d['symbol'].nunique()} symbols · "
              f"{d['ts'].min().date()} → {d['ts'].max().date()} · guarded {int(gd.sum()):,} · "
              f"up-door open {int((gd & d['recent_door_up'].fillna(False)).sum()):,} · "
              f"down-door open {int((gd & d['recent_door_down'].fillna(False)).sum()):,} · "
              f"corporate-action suspect in t-5..t {int((gd & d['recent_ca'].fillna(False)).sum()):,}")
        for v in VARIANTS:
            base = res[res["footprint"].str.startswith("ALL") & (res["variant"] == v)]
            print(f"  base rates [{v}]: " + " · ".join(
                f"{o} k5 {base[(base.outcome == o) & (base.horizon == 5)]['hit_rate'].iloc[0]:.2%}"
                for o in DOOR_OUTCOMES))
        print("  FDR (door, non-reference, NW t): " + ", ".join(
            f"{v}: {x['survivors']}/{x['tests']} cutoff p={x['bh_cutoff_p']}" for v, x in info["fdr"].items()))

    res = pd.concat(all_res, ignore_index=True)
    res.to_csv(os.path.join(paths["results"], "DOORSTEP_FOOTPRINT_ANALYSIS.csv"), index=False)
    pd.concat(all_cov).to_csv(os.path.join(paths["results"], "DOORSTEP_FOOTPRINT_COVERAGE.csv"), index=False)
    pd.concat(all_jac).to_csv(os.path.join(paths["results"], "DOORSTEP_FOOTPRINT_OVERLAP.csv"), index=False)
    rec = pd.concat(all_rec, ignore_index=True)
    rec.to_csv(os.path.join(paths["results"], "DOORSTEP_RECALL_LEADTIME.csv"), index=False)
    pre = pd.concat(all_pre, ignore_index=True)
    pre.to_csv(os.path.join(paths["results"], "DOORSTEP_PREDOOR_PROFILE.csv"), index=False)

    # ------------------------------------------------------------------ #
    # PLACEBO calibration: what do the gates pass when the footprint is stale?
    plc = res[(res["regime"] == "DISCOVERY") & res["placebo"]].copy()
    plc.to_csv(os.path.join(paths["results"], "DOORSTEP_PLACEBO.csv"), index=False)
    plc_ok = plc[plc["n_measured"] >= MIN_N]
    placebo_summary = {
        "rows": int(len(plc_ok)),
        "median_lift_vm": float(plc_ok["lift_volmatched"].median()),
        "p90_lift_vm": float(plc_ok["lift_volmatched"].quantile(0.9)),
        "share_t_nw_ge_3": float((plc_ok["t_volmatched_nw"] >= CAND_T).mean()),
        "share_pass_lift_and_t": float(((plc_ok["lift_volmatched"] >= CAND_LIFT)
                                        & (plc_ok["t_volmatched_nw"] >= CAND_T)).mean()),
        "max_t_nw": float(plc_ok["t_volmatched_nw"].max()),
    }

    # ------------------------------------------------------------------ #
    # THE FUNNEL. Steps 1-2 = v1 (frozen criterion; v1 tier A). Steps 3-6 = v2.
    disc = res[(res["regime"] == "DISCOVERY") & ~res["footprint"].str.startswith("ALL")
               & ~res["placebo"] & ~res["outcome"].str.endswith("(first)")].copy()
    door = disc["door_outcome"] & ~disc["footprint"].isin(REF_IDS)
    v1_gate = (door & (disc["n_measured"] >= MIN_N) & (disc["n_dates"] >= MIN_DATES)
               & (disc["excess_volmatched"] > 0) & (disc["t_volmatched"] >= CAND_T)
               & (disc["lift_volmatched"] >= CAND_LIFT))
    beats_pt = (disc["lift_vm_vs_ref_volume"] > 1) & (disc["lift_vm_vs_ref_moved"] > 1)
    v2_core = (door & disc["eligible"]
               & (disc["lift_volmatched"] >= CAND_LIFT) & (disc["t_volmatched_nw"] >= CAND_T)
               & (disc["lift_shockmatched"] >= CAND_LIFT) & (disc["t_shockmatched_nw"] >= CAND_T))
    fb = disc["variant"] == "fresh_both"
    funnel = [
        ("1 v1 pre-registered criterion, any-door", int((v1_gate & (disc["variant"] == "any")).sum())),
        ("2 v1 tier A: one-sided fresh + beats refs (point)", int((v1_gate & beats_pt & (disc["variant"] == "fresh")).sum())),
        ("3 same v1 gates on fresh_both", int((v1_gate & beats_pt & fb).sum())),
        ("4 v2: fresh_both · NW t≥3 · shock-matched too · distinct doors≥30", int((v2_core & fb).sum())),
    ]
    # step 5: bootstrap lower bound of the ratio vs both references > 1
    dfD = frames["DISCOVERY"]
    cand = disc[v2_core & fb & beats_pt].copy()
    lows_v, lows_m = [], []
    for _, r in cand.iterrows():
        o, k, fp = r["outcome"], int(r["horizon"]), r["footprint"]
        y = f"y_{o}_{k}"
        pop = population_mask(dfD, o, "fresh_both")
        ys = dfD[y]
        m = ys.notna() & pop
        bv = loo_base(dfD, ys, m, ["ts", "volq"])
        lows_v.append(boot_ratio_lower(dfD, pop, fp, ["F15_REF_abnormal_volume"], y, bv, date_idx["DISCOVERY"]))
        mref = ([ "F16u_REF_moved_up"] if fp == "F08u_idio_move_up" else
                ["F16d_REF_moved_down"] if fp == "F08d_idio_move_down" else
                ["F16u_REF_moved_up", "F16d_REF_moved_down"])
        lows_m.append(boot_ratio_lower(dfD, pop, fp, mref, y, bv, date_idx["DISCOVERY"]))
    cand["boot_lower_ratio_vs_volume"] = lows_v
    cand["boot_lower_ratio_vs_moved"] = lows_m
    cand.to_csv(os.path.join(paths["results"], "DOORSTEP_FUNNEL_STEP4.csv"), index=False)
    step5 = cand[(cand["boot_lower_ratio_vs_volume"] > 1) & (cand["boot_lower_ratio_vs_moved"] > 1)].copy()
    funnel.append(("5 = 4 and beats BOTH references with bootstrap lower bound > 1", int(len(step5))))
    # step 6: a horizon > 1 stands only if its INCREMENTAL window passes
    inc = res[(res["regime"] == "DISCOVERY") & (res["variant"] == "fresh_both")
              & res["outcome"].str.endswith("(first)")].copy()
    inc["outcome_base"] = inc["outcome"].str.replace("(first)", "", regex=False)
    inc_key = inc.set_index(["footprint", "outcome_base", "horizon"])
    def inc_pass(r):
        if int(r["horizon"]) == 1:
            return True
        try:
            x = inc_key.loc[(r["footprint"], r["outcome"], int(r["horizon"]))]
        except KeyError:
            return False
        return bool((x["lift_volmatched"] >= CAND_LIFT) and (x["t_volmatched_nw"] >= CAND_T))
    step5["incremental_passes"] = pd.Series([inc_pass(r) for _, r in step5.iterrows()],
                                            index=step5.index, dtype=bool)
    step5["incremental_lift_vm"] = [
        (inc_key.loc[(r["footprint"], r["outcome"], int(r["horizon"]))]["lift_volmatched"]
         if int(r["horizon"]) > 1 else np.nan) for _, r in step5.iterrows()]
    step5["incremental_t_nw"] = [
        (inc_key.loc[(r["footprint"], r["outcome"], int(r["horizon"]))]["t_volmatched_nw"]
         if int(r["horizon"]) > 1 else np.nan) for _, r in step5.iterrows()]
    tierA = step5[step5["incremental_passes"].astype(bool)].copy()
    funnel.append(("6 = 5 and (k=1 or the incremental window (k_prev,k] itself passes) → TIER A", int(len(tierA))))

    # context columns for the candidate file
    def ctx(row, regime, variant, col):
        x = res[(res["regime"] == regime) & (res["variant"] == variant)
                & (res["footprint"] == row["footprint"]) & (res["outcome"] == row["outcome"])
                & (res["horizon"] == row["horizon"])]
        return x[col].iloc[0] if len(x) else np.nan
    plc_idx = plc.set_index(["footprint", "outcome", "horizon"])
    def plc_get(r, col):
        try:
            return plc_idx.loc[("PLACEBO_" + r["footprint"], r["outcome"], int(r["horizon"]))][col]
        except KeyError:
            return np.nan
    ctx_cols = ([f"{tag}_{col}" for tag in ("floor", "postbreak")
                 for col in ("lift_volmatched", "t_volmatched_nw", "n_hit")]
                + ["v1_onesided_fresh_lift_vm", "anydoor_lift_vm", "placebo_lift_vm", "placebo_t_nw",
                   "n_years", "n_years_lift_ge_1", "min_year_lift_vm", "min_year_t_nw",
                   "lift_vm_price_10-50", "lift_vm_price_50-200", "lift_vm_price_>200"])
    stab = pd.DataFrame()
    if len(tierA):
        for reg, tag in (("FLOOR", "floor"), ("POSTBREAK", "postbreak")):
            for col in ("lift_volmatched", "t_volmatched_nw", "n_hit"):
                tierA[f"{tag}_{col}"] = [ctx(r, reg, "fresh_both", col) for _, r in tierA.iterrows()]
        tierA["v1_onesided_fresh_lift_vm"] = [ctx(r, "DISCOVERY", "fresh", "lift_volmatched") for _, r in tierA.iterrows()]
        tierA["anydoor_lift_vm"] = [ctx(r, "DISCOVERY", "any", "lift_volmatched") for _, r in tierA.iterrows()]
        tierA["placebo_lift_vm"] = [plc_get(r, "lift_volmatched") for _, r in tierA.iterrows()]
        tierA["placebo_t_nw"] = [plc_get(r, "t_volmatched_nw") for _, r in tierA.iterrows()]
        stab = stability(dfD, "DISCOVERY", "fresh_both", tierA, date_idx["DISCOVERY"])
        if len(stab):
            yr = stab[stab["split"] == "year"].groupby(["footprint", "outcome", "horizon"]).agg(
                n_years=("level", "size"),
                n_years_lift_ge_1=("lift_volmatched", lambda s: int((s >= 1).sum())),
                min_year_lift_vm=("lift_volmatched", "min"), min_year_t_nw=("t_volmatched_nw", "min"))
            pb = stab[stab["split"] == "price_bucket"].pivot_table(
                index=["footprint", "outcome", "horizon"], columns="level", values="lift_volmatched")
            pb.columns = [f"lift_vm_price_{c}" for c in pb.columns]
            tierA = tierA.merge(yr.reset_index(), on=["footprint", "outcome", "horizon"], how="left")
            tierA = tierA.merge(pb.reset_index(), on=["footprint", "outcome", "horizon"], how="left")
    for c in ctx_cols:
        if c not in tierA.columns:
            tierA[c] = np.nan
    stab.to_csv(os.path.join(paths["results"], "DOORSTEP_STABILITY.csv"), index=False)
    tierA["direction"] = np.where(tierA["outcome"].isin(UP_OUTCOMES), "up", "down")
    tierA["family"] = tierA["footprint"].map({f[0]: f[1] for f in FOOTPRINTS})
    tierA = tierA.sort_values(["footprint", "direction", "outcome", "horizon"])
    tierA["status"] = "DISCOVERY-WINDOW LEAD — fresh_both — NOT VALIDATED — Phase 5 holdout is the test"
    tierA.to_csv(os.path.join(paths["results"], "PHASE5_CANDIDATES_ALL_ROWS.csv"), index=False)
    # one primary row per (footprint, direction): the shortest passing horizon
    prim = (tierA.sort_values(["footprint", "direction", "horizon", "t_volmatched_nw"],
                              ascending=[True, True, True, False])
            .groupby(["footprint", "direction"]).head(1).copy())
    prim["horizons_outcomes_passing"] = [
        "; ".join(f"{o}@k{int(k)}" for o, k in tierA[(tierA["footprint"] == r["footprint"])
                                                   & (tierA["direction"] == r["direction"])]
                  [["outcome", "horizon"]].itertuples(index=False))
        for _, r in prim.iterrows()]
    prim["phase5_test_spec"] = (
        "On the SEALED holdout 2019-01-01..2022-07-27 (PRIMARY panel), fresh_both population, "
        "same footprint definition, same outcome and horizon: pre-registered pass = vol-matched "
        "AND shock-matched lift ≥ 1.5, Newey-West t ≥ 3, n_distinct_doors ≥ 30, bootstrap lower "
        "bound of the ratio vs both references > 1. One test per row; FDR over this file's rows.")
    keep_cols = ["footprint", "family", "direction", "outcome", "horizon", "horizons_outcomes_passing",
                 "n_occurrences", "n_measured", "unmeasurable_share", "n_dates", "n_hit",
                 "n_distinct_doors", "n_symbols_hit", "top3_symbol_share", "hit_rate", "fail_share",
                 "base_volmatched", "lift_volmatched", "t_volmatched", "t_volmatched_nw",
                 "base_shockmatched", "lift_shockmatched", "t_shockmatched_nw",
                 "lift_vm_vs_ref_volume", "boot_lower_ratio_vs_volume", "ref_moved_used",
                 "lift_vm_vs_ref_moved", "boot_lower_ratio_vs_moved",
                 "within_parent", "within_parent_test", "within_parent_t_nw", "within_parent_diff",
                 "incremental_lift_vm", "incremental_t_nw",
                 "anydoor_lift_vm", "v1_onesided_fresh_lift_vm", "placebo_lift_vm", "placebo_t_nw",
                 "n_years_lift_ge_1", "min_year_lift_vm", "min_year_t_nw",
                 "lift_vm_price_10-50", "lift_vm_price_50-200", "lift_vm_price_>200",
                 "floor_lift_volmatched", "floor_t_volmatched_nw", "floor_n_hit",
                 "postbreak_lift_volmatched", "postbreak_t_volmatched_nw", "postbreak_n_hit",
                 "p_volmatched_nw", "fdr10_volmatched_nw", "phase5_test_spec", "status"]
    prim = prim[[c for c in keep_cols if c in prim.columns]]
    prim.to_csv(os.path.join(paths["results"], "PHASE5_CANDIDATES.csv"), index=False)

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 78)
    print("candidate funnel (DISCOVERY):")
    for label, n in funnel:
        print(f"  {label:<78} {n:>4}")
    print(f"\nplacebo calibration (footprints shifted +{PLACEBO_SHIFT} sessions, fresh_both, door outcomes, k∈{{1,3,5}}): "
          f"{placebo_summary}")
    with pd.option_context("display.float_format", lambda v: f"{v:.2f}", "display.width", 260):
        print("\n--- funnel step 4 rows (fresh_both, all gates except references/increment) ---")
        print(cand[["footprint", "outcome", "horizon", "n_hit", "n_distinct_doors", "lift_volmatched",
                    "t_volmatched_nw", "lift_shockmatched", "t_shockmatched_nw",
                    "lift_vm_vs_ref_volume", "boot_lower_ratio_vs_volume",
                    "lift_vm_vs_ref_moved", "boot_lower_ratio_vs_moved"]].to_string(index=False)
              if len(cand) else "  → NONE.")
    show = ["footprint", "outcome", "horizon", "n_measured", "n_hit", "n_distinct_doors", "hit_rate",
            "base_volmatched", "lift_volmatched", "t_volmatched_nw", "lift_shockmatched",
            "t_shockmatched_nw", "boot_lower_ratio_vs_volume", "boot_lower_ratio_vs_moved",
            "within_parent_t_nw", "incremental_lift_vm", "placebo_lift_vm"]
    with pd.option_context("display.float_format", lambda v: f"{v:.2f}", "display.width", 260):
        print(f"\nTIER A (v2) rows: {len(tierA)} · primary rows (one per footprint × direction): {len(prim)}")
        print(tierA[show].to_string(index=False) if len(tierA) else "  → NONE.")
        fbq = disc[fb]
        for o, k in (("limit_up", 5), ("abn_up", 3), ("limit_down", 5), ("abn_down", 3)):
            print(f"\n--- fresh_both, {o}, k={k}: every footprint (DISCOVERY) ---")
            blk = fbq[(fbq["outcome"] == o) & (fbq["horizon"] == k)]
            print(blk[["footprint", "n_measured", "n_hit", "n_distinct_doors", "hit_rate",
                       "base_volmatched", "lift_volmatched", "t_volmatched_nw", "lift_shockmatched",
                       "t_shockmatched_nw", "lift_vm_vs_ref_volume", "lift_vm_vs_ref_moved",
                       "within_parent_t_nw"]].to_string(index=False))
        print("\n--- v1 tier-A down-door rows under the three populations (DISCOVERY, lift_vm / NW t) ---")
        for fp in ("F14_volume_no_range", "F12_closing_strength_10s", "F03_dip_recovered",
                   "F17_abnormal_volume_persistent"):
            for o in ("limit_down", "abn_down"):
                line = f"  {fp:<32} {o:<10}"
                for v in VARIANTS:
                    x = disc[(disc["footprint"] == fp) & (disc["outcome"] == o) & (disc["horizon"] == 5)
                             & (disc["variant"] == v)]
                    if len(x):
                        line += f"  {v}: {x['lift_volmatched'].iloc[0]:.2f} / {x['t_volmatched_nw'].iloc[0]:.1f} (hits {int(x['n_hit'].iloc[0])})"
                print(line)
        print("\n--- recall of FRESH limit-up doors, prior-5 window, base = pre-door rows (DISCOVERY) ---")
        r5 = rec[(rec["regime"] == "DISCOVERY") & (rec["door"] == "limit_up") & (rec["window"] == 5)]
        print(r5[["footprint", "n_fresh_doors", "n_doors_with_footprint", "recall",
                  "base_share_predoor_rows", "recall_lift", "lead_median"]].to_string(index=False))

    bio.write_manifest("phase45_manifest.json", {
        "phase": "4.5_doorstep_footprints_v2",
        "status": "DISCOVERY-WINDOW DESCRIPTIVE — holdout sealed, nothing validated",
        **meta,
        "pre_registration": "v1 design written before v1 run, but design+code+results committed "
                            "together (56fa8ab) after the run; rests on the author's statement. "
                            "All v2 changes were made after seeing v1 results and a five-lens "
                            "adversarial review. The sealed holdout is the only test.",
        "holdout_previously_inspected_by": ["Round 2 (P2 2018-01-01..2022-07-27, ledgered)",
                                            "Phase 4 (full-sample descriptive, ledgered)"],
        "footprints": [{"id": i, "family": fam, "definition": dfn} for i, fam, dfn in FOOTPRINTS],
        "footprint_params": FP, "outcomes": OUTCOME_NOTE, "horizons": list(HORIZONS),
        "variants": {"any": "guard & no corporate-action suspect in t-5..t",
                     "fresh": "+ no same-direction door in t-5..t (v1 one-sided rule)",
                     "fresh_both": "+ no door of either direction in t-5..t (v2 primary)"},
        "guards": {"min_price": MIN_PRICE, "sigma_defined": True, "rel_volume_z_defined": True},
        "limit_proxy": {"at_band": f"{C.CIRCUIT_PROXY_FRACTION}·band ≤ |R| ≤ band + {C.LIMIT_BAND_TOLERANCE}",
                        "beyond_band": "corporate-action suspect: never a hit; window unmeasurable"},
        "base_rates": ["same-day LOO", "same-day × σ_prev quintile LOO",
                       "same-day × σ_prev quintile × |ret_1|/σ_prev quintile LOO (shock-matched)"],
        "inference": f"date-paired excess; iid t and Newey-West t (Bartlett, L={NW_LAGS}); "
                     "distinct (symbol, door-date) events counted; incremental outcomes "
                     "(first door in (k_prev,k]); date-block bootstrap of lift ratios "
                     f"(block {BOOT_BLOCK}, {BOOT_REPS} reps); within-parent paired increment",
        "fdr": fdr_all, "placebo": placebo_summary, "candidate_funnel": funnel,
        "tierA_rows": int(len(tierA)), "tierA_primary_rows": int(len(prim)),
        "candidate_gates_v2": {"population": "fresh_both", "eligible": f"n≥{MIN_N}, dates≥{MIN_DATES}, distinct doors≥{MIN_DOORS}",
                               "lift": f"vol-matched AND shock-matched ≥ {CAND_LIFT}",
                               "t": f"Newey-West t ≥ {CAND_T} on both", "references": "bootstrap lower 2.5% of ratio > 1 vs F15 and vs signed/max F16",
                               "horizon": "k=1 or incremental window passes lift & t"},
        "circuit_bands": {"verified": C.TICK_RULES_VERIFIED, "schedule": list(C.CIRCUIT_BANDS_UNVERIFIED)},
        "turnover_derived": True, "uses_open_field": False, "emits_orders": False,
        "cost_layer_applied": False,
        "outputs": ["results/DOORSTEP_FOOTPRINT_ANALYSIS.csv", "results/DOORSTEP_FOOTPRINT_COVERAGE.csv",
                    "results/DOORSTEP_FOOTPRINT_OVERLAP.csv", "results/DOORSTEP_RECALL_LEADTIME.csv",
                    "results/DOORSTEP_PREDOOR_PROFILE.csv", "results/DOORSTEP_BAND_EVIDENCE.csv",
                    "results/DOORSTEP_STABILITY.csv", "results/DOORSTEP_PLACEBO.csv",
                    "results/PHASE5_CANDIDATES.csv", "results/PHASE5_CANDIDATES_ALL_ROWS.csv"],
    })
    print("\nNo BUY/SELL was produced. No cost layer was applied. The holdout was not read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
