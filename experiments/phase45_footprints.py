#!/usr/bin/env python3
"""PHASE 4.5 — doorstep footprint research.

THE CORRECTED QUESTION
----------------------
Phase 4 asked "does a state carry tradeable alpha after costs?" and answered no.
That was the wrong question for this track. The actual question is:

    From PUBLIC end-of-day data alone, do certain footprints appear BEFORE an
    abnormal price event (a limit-up/limit-down proxy hit, an abnormal run)
    more often than chance — by how much, with what lead time, and how often
    does the footprint fire with nothing following it?

No trade, no cost layer, no BUY/SELL. A footprint that predicts a door is a
research lead for Phase 5 walk-forward; a footprint that does not is a counted
failure. Both are written down.

DISCIPLINE
----------
1. FOOTPRINTS ARE FIXED BEFORE ANY OUTCOME IS SEEN. The list below is the
   pre-registered list; nothing is added after looking at a result. A footprint
   suggested by the post-hoc pre-door profile is NOT a candidate — it is a note
   for a fresh test on fresh data.
2. THE HOLDOUT IS SEALED. Rows in C.HOLDOUT_WINDOW are dropped at load and an
   assertion refuses to continue if any survive. Discovery is
   C.DISCOVERY_WINDOW only. The floor era and the POSTBREAK panel are separate
   regimes, reported apart, never pooled.
3. FULL DENOMINATOR. Every footprint occurrence is counted; the ones nothing
   followed are the failed footprints, reported per hypothesis.
4. TWO BASE RATES. Excess over (a) the SAME-DAY base rate across all guarded
   names, and (b) the same-day base rate within the occurrence's VOLATILITY
   QUINTILE — because volatility clusters, and "abnormal now ⇒ abnormal soon"
   is not a doorstep, it is variance. Both are date-paired and tested across
   DATES (occurrences cluster on market-wide days).
5. TWO REFERENCE FOOTPRINTS. "Plain abnormal volume" and "already moved" are
   tested exactly like the others, so "quiet volume" has to beat plain volume
   and momentum, not merely beat chance.
6. MULTIPLE TESTING. Every footprint × outcome × horizon is a hypothesis; the
   count is printed and BH-FDR applied per regime.
7. NO `open` FIELD ANYWHERE — its provenance is an open question (D-7).

  python3 bd_research/experiments/phase45_footprints.py --tag dse_eod
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
DOOR_OUTCOMES = ("limit_up", "abn_up", "run20", "limit_down", "abn_down")
ALL_OUTCOMES = DOOR_OUTCOMES + ("activity",)
OUTCOME_NOTE = {
    "limit_up": "any session in (t,t+k] with close/close ≥ 95% of the UNVERIFIED upper band",
    "limit_down": "any session in (t,t+k] with close/close ≤ −95% of the band",
    "abn_up": "max cumulative log return over (t,t+k] ≥ max(2.5·σ_prev·√k, 5%)",
    "abn_down": "min cumulative log return over (t,t+k] ≤ −max(2.5·σ_prev·√k, 5%)",
    "run20": "max cumulative log return over (t,t+k] ≥ ln(1.20)",
    "activity": "any session in (t,t+k] with rel_volume_z ≥ 2 — MECHANICAL "
                "(volume autocorrelates); reported, never a door",
}

MIN_N, MIN_DATES, FDR_Q = 200, 30, 0.10
MIN_PRICE = 10.0            # Tk; one tick (0.10) is 1% here — below it a "limit" is a few ticks
CAND_LIFT = 1.5             # vol-matched lift a candidate must reach
CAND_T_MATCHED = 3.0        # vol-matched date-clustered |t| a candidate must reach

# Footprint parameters — every threshold is against the symbol's OWN trailing
# baseline (the Phase-2 z features) except the two market-context cuts.
FP = dict(
    z_abn=2.0,               # abnormal activity (= FeatureParams.abnormal_z)
    z_mid=1.5,
    z_elev=1.0,
    calm_mult=1.0,           # |ret| ≤ calm_mult·σ_prev ⇒ price "calm"
    dip_mult=1.0,            # low ≤ prev_close·(1 − dip_mult·σ_prev) ⇒ sellers pushed
    win=5,                   # short persistence window (sessions the symbol traded)
    persist_min=3,           # ≥ this many elevated sessions in `win`
    absorb_min=2,            # ≥ this many absorption sessions in `win`
    market_quiet_breadth=0.05,   # ≤ 5% of names abnormal-volume that day ⇒ market quiet
    coil_z=-1.0,             # mean range_z over the prior `win` ≤ this ⇒ coiled
    close_strength=0.8,      # close_location ≥ this ⇒ buyers finished in control
    accum_thresh=0.5,        # accumulation_proxy ≥ this
    vpd_thresh=2.0,          # volume_price_divergence ≥ this (= rung1_quiet_accumulation)
    move_mult=2.0,           # |ret| ≥ move_mult·σ_prev ⇒ "already moved"
    out_move_mult=2.5,       # outcome: abnormal run threshold multiplier
    out_move_floor=0.05,     # outcome: absolute floor on the abnormal-run threshold
    run_thresh=0.20,         # outcome: "big run"
)

# (id, family, one-line definition) — the PRE-REGISTERED list. Order is fixed.
FOOTPRINTS = [
    ("F01_quiet_volume", "quiet volume",
     "rel_volume_z ≥ 2 and |ret_1| ≤ σ_prev — abnormal volume, price calm"),
    ("F02_quiet_volume_persistent", "quiet volume",
     "≥3 of the last 5 sessions with rel_volume_z ≥ 1 and |ret_5| ≤ σ_prev·√5"),
    ("F03_absorption", "absorption",
     "low ≤ prev_close·(1−σ_prev) and close ≥ prev_close and rel_volume_z ≥ 1 — "
     "sellers pushed, price did not fall by the close"),
    ("F04_absorption_persistent", "absorption",
     "≥2 absorption sessions within the last 5"),
    ("F05_departure_calm", "own-baseline departure",
     "rung-2 state ∈ {DEPARTURE, EXTREME} and |ret_1| ≤ σ_prev"),
    ("F06_departure_any", "own-baseline departure",
     "rung-2 state ∈ {DEPARTURE, EXTREME} (Phase-4 cohort, for comparison)"),
    ("F07_idio_activity", "idiosyncratic vs market",
     "rel_volume_z ≥ 2 while ≤5% of names are abnormal-volume that day"),
    ("F08_idio_move", "idiosyncratic vs market",
     "|market_relative_ret| ≥ 2σ_prev while |market_ret| ≤ σ_market_prev"),
    ("F09_idio_quiet_volume", "idiosyncratic vs market",
     "F01 while ≤5% of names are abnormal-volume that day"),
    ("F10_coil_then_volume", "compression → activity",
     "mean range_z over the prior 5 sessions ≤ −1 and rel_volume_z ≥ 1.5 today"),
    ("F11_closing_strength", "absorption",
     "close_location ≥ 0.8 and rel_volume_z ≥ 1.5 and |ret_1| ≤ σ_prev"),
    ("F12_accumulation_proxy", "quiet volume",
     "accumulation_proxy ≥ 0.5 (10-session mean of (close_location−½)·rel_volume_z)"),
    ("F13_volume_price_divergence", "quiet volume",
     "volume_price_divergence ≥ 2 (= rung1_quiet_accumulation)"),
    ("F14_turnover_no_range", "quiet volume",
     "rel_turnover_z ≥ 2 and range_z ≤ 0 — money in, range not expanding"),
    ("F15_REF_abnormal_volume", "REFERENCE",
     "rel_volume_z ≥ 2 — plain abnormal volume; does 'quiet' add anything?"),
    ("F16_REF_already_moved", "REFERENCE",
     "|ret_1| ≥ 2σ_prev — the price already moved; the momentum / vol-clustering reference. "
     "NOT a doorstep footprint by construction"),
    ("F17_abnormal_volume_persistent", "persistence",
     "abnormal_persistence ≥ 2 — second consecutive abnormal-volume session"),
    ("F18_quiet_volume_repeat", "quiet volume",
     "F01 today and F01 on ≥1 of the prior 5 sessions"),
]
FP_IDS = [f[0] for f in FOOTPRINTS]
PREDOOR_FEATURES = ["rel_volume_z", "rel_turnover_z", "range_z", "close_location",
                    "market_relative_ret", "volume_price_divergence",
                    "accumulation_proxy", "amihud_z", "ret_1"]


# --------------------------------------------------------------------------- #
def band_of(price: pd.Series) -> pd.Series:
    """± daily limit band for a previous close, from the UNVERIFIED schedule."""
    edges = [0.0] + [cap for cap, _ in C.CIRCUIT_BANDS_UNVERIFIED]
    bands = [b for _, b in C.CIRCUIT_BANDS_UNVERIFIED]
    cut = pd.cut(price, bins=edges, labels=False, right=True, include_lowest=False)
    arr = np.array(bands + [np.nan])
    idx = cut.fillna(len(bands)).astype(int).to_numpy()
    return pd.Series(arr[idx], index=price.index)


def _roll_sum(f: pd.DataFrame, s: pd.Series, w: int, shift: int = 0) -> pd.Series:
    """Per-symbol trailing rolling sum over the last `w` rows (optionally shifted)."""
    return (s.astype(float).groupby(f["symbol"], sort=False)
            .transform(lambda x: x.shift(shift).rolling(w, min_periods=w).sum()))


def build_footprints(f: pd.DataFrame) -> pd.DataFrame:
    """Pre-registered footprints at the CLOSE of row t. Causal by construction:
    every input is a Phase-2 feature (proved causal), a Phase-3 state (proved
    causal), or a strictly trailing per-symbol window computed here.

    `f` must be one regime, sorted by (symbol, ts). Returns a frame aligned to
    f.index with one bool column per footprint plus the guard and helper cols.
    """
    f = f.sort_values(["symbol", "ts"], kind="mergesort")
    g = f.groupby("symbol", sort=False)
    p = C.DEFAULT.features

    close = f["close"]
    prev_close = g["close"].shift(1)
    sigma = g["realized_vol"].shift(1)                      # STRICTLY trailing σ
    sigma = sigma.where(sigma > p.min_meaningful_vol)
    ret1 = f["ret_1"]
    ret5 = np.log(close / g["close"].shift(FP["win"]))
    vz, tz, rz = f["rel_volume_z"], f["rel_turnover_z"], f["range_z"]

    # market volatility: trailing σ of the same-day median return, by DATE
    mkt = f.groupby("ts")["market_ret"].first().sort_index()
    mkt_sigma_prev = mkt.shift(1).rolling(p.vol_window, min_periods=20).std()
    msig = f["ts"].map(mkt_sigma_prev)

    calm1 = ret1.abs() <= FP["calm_mult"] * sigma
    calm5 = ret5.abs() <= FP["calm_mult"] * sigma * np.sqrt(FP["win"])
    dip = f["low"] / prev_close - 1.0
    market_quiet = f["xs_breadth_abnormal"] <= FP["market_quiet_breadth"]

    out = pd.DataFrame(index=f.index)
    out["symbol"], out["ts"] = f["symbol"], f["ts"]
    out["sigma_prev"] = sigma
    out["guard"] = (close >= MIN_PRICE) & sigma.notna() & vz.notna()

    F = {}
    F["F01_quiet_volume"] = (vz >= FP["z_abn"]) & calm1
    elev5 = _roll_sum(f, (vz >= FP["z_elev"]), FP["win"])
    F["F02_quiet_volume_persistent"] = (elev5 >= FP["persist_min"]) & calm5
    absorb = ((dip <= -FP["dip_mult"] * sigma) & (close >= prev_close)
              & (vz >= FP["z_elev"]))
    F["F03_absorption"] = absorb
    F["F04_absorption_persistent"] = _roll_sum(f, absorb, FP["win"]) >= FP["absorb_min"]
    elevated = f["state"].isin(("DEPARTURE", "EXTREME"))
    F["F05_departure_calm"] = elevated & calm1
    F["F06_departure_any"] = elevated
    F["F07_idio_activity"] = (vz >= FP["z_abn"]) & market_quiet
    F["F08_idio_move"] = ((f["market_relative_ret"].abs() >= FP["move_mult"] * sigma)
                          & (f["market_ret"].abs() <= msig))
    F["F09_idio_quiet_volume"] = F["F01_quiet_volume"] & market_quiet
    coil = (rz.groupby(f["symbol"], sort=False)
            .transform(lambda x: x.shift(1).rolling(FP["win"], min_periods=FP["win"]).mean()))
    F["F10_coil_then_volume"] = (coil <= FP["coil_z"]) & (vz >= FP["z_mid"])
    F["F11_closing_strength"] = ((f["close_location"] >= FP["close_strength"])
                                 & (vz >= FP["z_mid"]) & calm1)
    F["F12_accumulation_proxy"] = f["accumulation_proxy"] >= FP["accum_thresh"]
    F["F13_volume_price_divergence"] = f["volume_price_divergence"] >= FP["vpd_thresh"]
    F["F14_turnover_no_range"] = (tz >= FP["z_abn"]) & (rz <= 0)
    F["F15_REF_abnormal_volume"] = vz >= FP["z_abn"]
    F["F16_REF_already_moved"] = ret1.abs() >= FP["move_mult"] * sigma
    F["F17_abnormal_volume_persistent"] = f["abnormal_persistence"] >= 2
    prior_qv = _roll_sum(f, F["F01_quiet_volume"], FP["win"], shift=1)
    F["F18_quiet_volume_repeat"] = F["F01_quiet_volume"] & (prior_qv >= 1)

    for k in FP_IDS:                     # fixed order; NaN ⇒ did not fire
        out[k] = (F[k].fillna(False).astype(bool) & out["guard"])
    return out


# --------------------------------------------------------------------------- #
def forward_outcomes(f: pd.DataFrame) -> pd.DataFrame:
    """Binary outcomes over sessions (t, t+k], DATE-ALIGNED on the regime's
    trading calendar (a missing session ⇒ outcome not measurable, never
    interpolated). Uses close only — never `open`."""
    piv = f.pivot_table(index="ts", columns="symbol", values="close", aggfunc="last")
    vzp = f.pivot_table(index="ts", columns="symbol", values="rel_volume_z", aggfunc="last")
    sig = f.pivot_table(index="ts", columns="symbol", values="sigma_prev", aggfunc="last")
    sig = sig.reindex(index=piv.index, columns=piv.columns)
    vzp = vzp.reindex(index=piv.index, columns=piv.columns)

    logC = np.log(piv)
    R = piv / piv.shift(1) - 1.0                       # simple, vs the PRIOR SESSION
    band = piv.shift(1).apply(band_of)
    LU = (R >= C.CIRCUIT_PROXY_FRACTION * band).astype(float).where(R.notna())
    LD = (R <= -C.CIRCUIT_PROXY_FRACTION * band).astype(float).where(R.notna())
    AB = (vzp >= FP["z_abn"]).astype(float).where(vzp.notna())
    present = piv.notna().astype(float)

    cols = {}
    for k in HORIZONS:
        n_fwd = present.rolling(k).sum().shift(-k)     # rows t+1..t+k all present?
        ok = n_fwd == k
        maxcum = logC.rolling(k, min_periods=k).max().shift(-k) - logC
        mincum = logC.rolling(k, min_periods=k).min().shift(-k) - logC
        thresh = np.maximum(FP["out_move_mult"] * sig * np.sqrt(k), FP["out_move_floor"])
        cols[f"y_limit_up_{k}"] = (LU.rolling(k, min_periods=k).sum().shift(-k) >= 1)
        cols[f"y_limit_down_{k}"] = (LD.rolling(k, min_periods=k).sum().shift(-k) >= 1)
        cols[f"y_abn_up_{k}"] = maxcum >= thresh
        cols[f"y_abn_down_{k}"] = mincum <= -thresh
        cols[f"y_run20_{k}"] = maxcum >= np.log1p(FP["run_thresh"])
        cols[f"y_activity_{k}"] = (AB.rolling(k, min_periods=k).sum().shift(-k) >= 1)
        # measurable ⇔ every session t+1..t+k is present (and, for the
        # σ-relative outcomes, the threshold itself is defined)
        for name in [n for n in cols if n.endswith(f"_{k}")]:
            m = cols[name].astype(float).where(ok)
            if name.startswith("y_abn"):
                m = m.where(thresh.notna())
            cols[name] = m
    frames = [mat.stack(future_stack=True).rename(n) for n, mat in cols.items()]
    out = pd.concat(frames, axis=1).reset_index()
    out.columns = ["ts", "symbol"] + list(cols)
    # single-session door flags at row t itself (for recall / lead-time) —
    # same thresholds as the k=1 outcomes
    thresh1 = np.maximum(FP["out_move_mult"] * sig, FP["out_move_floor"])
    abn_up_day = (np.log1p(R) >= thresh1).astype(float).where(R.notna() & thresh1.notna())
    abn_dn_day = (np.log1p(R) <= -thresh1).astype(float).where(R.notna() & thresh1.notna())
    # DOOR-ALREADY-OPEN guard: an up-door (limit-up or abnormal-up day) anywhere
    # in rows t-5..t means the door is open AT t; a footprint there is not a
    # doorstep, it is a continuation. Same for down-doors. The "fresh" variant
    # removes these rows from BOTH the occurrences and the base population.
    up_day = ((LU == 1) | (abn_up_day == 1)).astype(float)
    dn_day = ((LD == 1) | (abn_dn_day == 1)).astype(float)
    recent_up = up_day.rolling(FP["win"] + 1, min_periods=1).sum() >= 1
    recent_dn = dn_day.rolling(FP["win"] + 1, min_periods=1).sum() >= 1
    d = pd.concat([LU.stack(future_stack=True).rename("door_limit_up_day"),
                   abn_up_day.stack(future_stack=True).rename("door_abn_up_day"),
                   recent_up.stack(future_stack=True).rename("recent_door_up"),
                   recent_dn.stack(future_stack=True).rename("recent_door_down")],
                  axis=1).reset_index()
    d.columns = ["ts", "symbol", "door_limit_up_day", "door_abn_up_day",
                 "recent_door_up", "recent_door_down"]
    return out.merge(d, on=["ts", "symbol"], how="left")


UP_OUTCOMES = ("limit_up", "abn_up", "run20")
DOWN_OUTCOMES = ("limit_down", "abn_down")
VARIANTS = ("any", "fresh")


def population_mask(df: pd.DataFrame, outcome: str, variant: str) -> pd.Series:
    """Rows on which an outcome is a DOORSTEP question under a variant.
    any   : every guarded row (the door may already be open — pre-registered)
    fresh : guarded rows with no door of that direction in t-5..t."""
    m = df["guard"].copy()
    if variant == "fresh":
        if outcome in UP_OUTCOMES:
            m &= ~df["recent_door_up"].fillna(False).astype(bool)
        elif outcome in DOWN_OUTCOMES:
            m &= ~df["recent_door_down"].fillna(False).astype(bool)
    return m


# --------------------------------------------------------------------------- #
def _t_over_dates(per_date: pd.Series) -> tuple[float, int]:
    per_date = per_date.dropna()
    n = len(per_date)
    if n < 2:
        return np.nan, n
    sd = per_date.std(ddof=1)
    return (float(per_date.mean() / (sd / np.sqrt(n))) if sd > 0 else np.nan), n


def bh_fdr(p: np.ndarray, q: float) -> np.ndarray:
    ok = np.isfinite(p)
    idx = np.where(ok)[0]
    keep = np.zeros_like(p, dtype=bool)
    if len(idx) == 0:
        return keep
    order = idx[np.argsort(p[idx])]
    m = len(order)
    passed = p[order] <= q * (np.arange(1, m + 1) / m)
    if passed.any():
        keep[order[:np.max(np.where(passed)[0]) + 1]] = True
    return keep


def two_sided_p(t: np.ndarray) -> np.ndarray:
    from math import erfc, sqrt
    return np.array([erfc(abs(v) / sqrt(2)) if np.isfinite(v) else np.nan for v in t])


def add_base_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Per outcome: same-day base rate and same-day-within-volatility-quintile
    base rate, both EXCLUDING the row itself (leave-one-out)."""
    df = df.copy()
    gd = df["guard"]
    r = df.loc[gd].groupby("ts")["sigma_prev"].rank(pct=True)
    df["volq"] = np.minimum((r * 5).fillna(0).astype(int), 4).reindex(df.index)
    new = {}
    for variant in VARIANTS:
        for o in ALL_OUTCOMES:
            pop = population_mask(df, o, variant)
            for k in HORIZONS:
                y = df[f"y_{o}_{k}"]
                m = y.notna() & pop       # base populations: guarded (+ fresh) rows only
                for key, tag in (("ts", "b"), (["ts", "volq"], "bv")):
                    grp = df[m].groupby(key)[f"y_{o}_{k}"]
                    S = grp.transform("sum")
                    N = grp.transform("size")
                    loo = ((S - y[m]) / (N - 1)).where(N > 1)
                    new[f"{tag}_{o}_{k}__{variant}"] = loo.reindex(df.index)
    return pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)


def analyse(df: pd.DataFrame, regime: str, variant: str) -> pd.DataFrame:
    rows = []
    n_guard = int(df["guard"].sum())
    fps = FP_IDS + ["ALL (base rate)"]
    for o in ALL_OUTCOMES:
        pop = population_mask(df, o, variant)
        dpop = df[pop]
        for fp in fps:
            sub = dpop if fp.startswith("ALL") else dpop[dpop[fp]]
            n_occ = len(sub)
            for k in HORIZONS:
                y = f"y_{o}_{k}"
                b, bv = f"b_{o}_{k}__{variant}", f"bv_{o}_{k}__{variant}"
                d = sub[[y, b, bv, "ts"]].dropna(subset=[y])
                n = len(d)
                hit = float(d[y].mean()) if n else np.nan
                pd_ = d.groupby("ts")[[y, b, bv]].mean()
                t_ex, n_dates = _t_over_dates(pd_[y] - pd_[b])
                t_vm, _ = _t_over_dates(pd_[y] - pd_[bv])
                base = float(d[b].mean()) if n else np.nan
                base_vm = float(d[bv].mean()) if n else np.nan
                rows.append({
                    "regime": regime, "variant": variant, "footprint": fp,
                    "outcome": o, "horizon": k,
                    "door_outcome": o in DOOR_OUTCOMES,
                    "n_guarded_rows": n_guard, "n_population": int(pop.sum()),
                    "n_occurrences": n_occ, "n_no_outcome": n_occ - n,
                    "n_measured": n, "n_dates": n_dates,
                    "n_hit": int(d[y].sum()) if n else 0,
                    "n_failed": int(n - d[y].sum()) if n else 0,
                    "hit_rate": hit, "fail_share": (1 - hit) if n else np.nan,
                    "base_sameday": base, "excess_sameday": (hit - base) if n else np.nan,
                    "lift_sameday": (hit / base) if (n and base and base > 0) else np.nan,
                    "t_sameday": t_ex,
                    "base_volmatched": base_vm,
                    "excess_volmatched": (hit - base_vm) if n else np.nan,
                    "lift_volmatched": (hit / base_vm) if (n and base_vm and base_vm > 0) else np.nan,
                    "t_volmatched": t_vm,
                })
    res = pd.DataFrame(rows)
    # FDR per (regime, variant) across every real hypothesis (base-rate rows are not tests)
    test = ~res["footprint"].str.startswith("ALL")
    for tag in ("sameday", "volmatched"):
        p = two_sided_p(res.loc[test, f"t_{tag}"].to_numpy(dtype=float))
        res.loc[test, f"p_{tag}"] = p
        res.loc[test, f"fdr10_{tag}"] = bh_fdr(p, FDR_Q)
    res["eligible"] = (res["n_measured"] >= MIN_N) & (res["n_dates"] >= MIN_DATES)
    # incremental information: lift relative to the two references at the same
    # outcome and horizon. Below 1 ⇒ the extra condition SUBTRACTS information.
    key = list(zip(res["outcome"], res["horizon"]))
    for ref_id, col in (("F15_REF_abnormal_volume", "lift_vm_vs_ref_volume"),
                        ("F16_REF_already_moved", "lift_vm_vs_ref_moved")):
        ref = res[res["footprint"] == ref_id].set_index(["outcome", "horizon"])
        res[col] = res["lift_volmatched"].to_numpy() / \
            ref["lift_volmatched"].reindex(key).to_numpy()
    return res


def stability(df: pd.DataFrame, regime: str, variant: str,
              rows: pd.DataFrame) -> pd.DataFrame:
    """Does a candidate's vol-matched lift hold in EVERY calendar year and in
    every price bucket, or does one year / one bucket carry it?"""
    out = []
    df = df.assign(year=df["ts"].dt.year,
                   price_bucket=pd.cut(df["close"], [MIN_PRICE, 50, 200, np.inf],
                                       labels=["10-50", "50-200", ">200"]))
    for _, r in rows.iterrows():
        fp, o, k = r["footprint"], r["outcome"], int(r["horizon"])
        y, bv = f"y_{o}_{k}", f"bv_{o}_{k}__{variant}"
        pop = population_mask(df, o, variant)
        d = df[pop & df[fp]][[y, bv, "year", "price_bucket", "ts"]].dropna(subset=[y])
        for by in ("year", "price_bucket"):
            for lvl, g in d.groupby(by, observed=True):
                n = len(g)
                hit = float(g[y].mean()) if n else np.nan
                base = float(g[bv].mean()) if n else np.nan
                pdm = g.groupby("ts")[[y, bv]].mean()
                t, nd = _t_over_dates(pdm[y] - pdm[bv])
                out.append({"regime": regime, "variant": variant, "footprint": fp,
                            "outcome": o, "horizon": k, "split": by, "level": str(lvl),
                            "n_measured": n, "n_dates": nd, "hit_rate": hit,
                            "base_volmatched": base,
                            "lift_volmatched": (hit / base) if (base and base > 0) else np.nan,
                            "t_volmatched": t})
    return pd.DataFrame(out)


def recall_and_lead(df: pd.DataFrame, regime: str) -> pd.DataFrame:
    """The other direction: of the doors that happened, how many had the
    footprint in the prior 5 / 10 sessions — against how often that footprint
    sits in ANY prior-5 / prior-10 window. Row-based per symbol (sessions the
    symbol traded). A door is FRESH when no door of the same kind occurred in
    the prior 5 sessions (a continuation is not a doorstep)."""
    df = df.sort_values(["symbol", "ts"], kind="mergesort")
    g = df.groupby("symbol", sort=False)
    rows = []
    for door in ("door_limit_up_day", "door_abn_up_day"):
        dd = df[door].fillna(0).astype(float)
        prior5 = _roll_sum(df, dd, 5, shift=1).fillna(0)
        fresh = (dd == 1) & (prior5 == 0) & df["guard"]
        n_doors = int(fresh.sum())
        for fp in FP_IDS:
            fpv = df[fp].astype(float)
            for w in (5, 10):
                inw = _roll_sum(df, fpv, w, shift=1) >= 1
                base_pop = df["guard"] & inw.notna()
                recall = float(inw[fresh].mean()) if n_doors else np.nan
                base = float(inw[base_pop].mean()) if base_pop.any() else np.nan
                # lead time: sessions since the most recent footprint before the door
                pos = g.cumcount()
                last_fp = pos.where(df[fp]).groupby(df["symbol"], sort=False).ffill()
                lead = (pos - last_fp.groupby(df["symbol"], sort=False).shift(1))
                lead_at_door = lead[fresh & (inw == True)]  # noqa: E712
                lead_at_door = lead_at_door[lead_at_door <= w]
                rows.append({
                    "regime": regime, "door": door.replace("door_", "").replace("_day", ""),
                    "footprint": fp, "window": w,
                    "n_fresh_doors": n_doors,
                    "n_doors_with_footprint": int((inw[fresh] == True).sum()),  # noqa: E712
                    "recall": recall, "base_share_any_window": base,
                    "recall_lift": (recall / base) if (base and base > 0) else np.nan,
                    "lead_median": float(lead_at_door.median()) if len(lead_at_door) else np.nan,
                    "lead_p25": float(lead_at_door.quantile(0.25)) if len(lead_at_door) else np.nan,
                    "lead_p75": float(lead_at_door.quantile(0.75)) if len(lead_at_door) else np.nan,
                })
    return pd.DataFrame(rows)


def predoor_profile(df: pd.DataFrame, regime: str) -> pd.DataFrame:
    """POST-HOC, CONDITIONED ON THE OUTCOME. What the ten sessions before a
    fresh limit-up door look like on average, as excess over the same-day
    cross-section. This is a description, not evidence for any footprint above,
    and any footprint it suggests must be tested fresh on the sealed holdout."""
    df = df.sort_values(["symbol", "ts"], kind="mergesort")
    dd = df["door_limit_up_day"].fillna(0).astype(float)
    prior5 = _roll_sum(df, dd, 5, shift=1).fillna(0)
    fresh = (dd == 1) & (prior5 == 0) & df["guard"]
    rows = []
    for feat in PREDOOR_FEATURES:
        xs_mean = df[feat].where(df["guard"]).groupby(df["ts"]).transform("mean")
        ex = df[feat] - xs_mean
        for off in range(1, 11):
            lagged = ex.groupby(df["symbol"], sort=False).shift(off)
            v = lagged[fresh]
            ts = df["ts"][fresh]
            per_date = v.groupby(ts).mean()
            t, nd = _t_over_dates(per_date)
            rows.append({"regime": regime, "feature": feat, "sessions_before_door": off,
                         "n_events": int(v.notna().sum()), "n_dates": nd,
                         "mean_excess_vs_xs": float(v.mean()) if v.notna().any() else np.nan,
                         "t_over_dates": t})
    return pd.DataFrame(rows)


def coverage(df: pd.DataFrame, regime: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    g = df[df["guard"]]
    rows = [{"regime": regime, "footprint": fp, "n_fired": int(g[fp].sum()),
             "share_of_guarded_rows": float(g[fp].mean()),
             "n_symbols": int(g.loc[g[fp], "symbol"].nunique()),
             "n_dates": int(g.loc[g[fp], "ts"].nunique())} for fp in FP_IDS]
    M = g[FP_IDS].astype(float).to_numpy()
    inter = M.T @ M
    sz = M.sum(axis=0)
    union = sz[:, None] + sz[None, :] - inter
    jac = pd.DataFrame(np.where(union > 0, inter / np.maximum(union, 1), np.nan),
                       index=FP_IDS, columns=FP_IDS)
    jac.insert(0, "regime", regime)
    return pd.DataFrame(rows), jac.reset_index().rename(columns={"index": "footprint"})


def band_evidence(bars: pd.DataFrame) -> pd.DataFrame:
    """Empirical mass points of the daily return by UNVERIFIED band bucket."""
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
        at_up = float(((r[m] - bb).abs() <= 0.0025).mean())
        at_dn = float(((r[m] + bb).abs() <= 0.0025).mean())
        beyond = float((r[m].abs() > bb + 0.0025).mean())
        rows.append({"band_bucket_prev_close_le": cap, "band": bb,
                     "n_moves_ge_2pct": n, "share_at_+band": at_up,
                     "share_at_-band": at_dn, "share_beyond_band": beyond})
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

    # --- SEAL THE HOLDOUT. Dropped here; nothing below ever sees it. ---
    h0, h1 = (pd.Timestamp(x) for x in C.HOLDOUT_WINDOW)
    in_holdout = d["ts"].between(h0, h1)
    n_sealed = int(in_holdout.sum())
    d = d[~in_holdout]
    assert not d["ts"].between(h0, h1).any(), "holdout rows survived the seal"

    d0, d1 = (pd.Timestamp(x) for x in C.DISCOVERY_WINDOW)
    f0, f1 = (pd.Timestamp(x) for x in C.FLOOR_ERA)
    prim = P.select(d, "PRIMARY")
    regimes = {
        "DISCOVERY": prim[prim["ts"].between(d0, d1)],
        "FLOOR": prim[prim["ts"].between(f0, f1)],
        "POSTBREAK": P.select(d, "POSTBREAK"),
    }
    leftover = len(prim) - sum(len(v) for k, v in regimes.items() if k != "POSTBREAK")
    meta = {"sealed_holdout_rows": n_sealed, "holdout_window": list(C.HOLDOUT_WINDOW),
            "discovery_window": list(C.DISCOVERY_WINDOW),
            "primary_rows_outside_all_regimes": int(leftover)}
    return regimes, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="dse_eod")
    a = ap.parse_args()
    paths = bio.paths()

    print("=" * 78)
    print("PHASE 4.5 — doorstep footprint research (DISCOVERY window only; holdout SEALED)")
    print("=" * 78)
    regimes, meta = load_regimes(a.tag)
    print(f"sealed holdout {meta['holdout_window']}: {meta['sealed_holdout_rows']:,} rows "
          "dropped at load — never read below")
    print(f"discovery window {meta['discovery_window']}")
    print(f"footprints pre-registered: {len(FP_IDS)} · outcomes: {len(ALL_OUTCOMES)} "
          f"× horizons {HORIZONS} ⇒ {len(FP_IDS) * len(ALL_OUTCOMES) * len(HORIZONS)} "
          "hypotheses per regime")
    print(f"guards: close ≥ Tk {MIN_PRICE:.0f}, trailing σ defined, rel_volume_z defined")

    print("\n--- circuit band evidence (DISCOVERY, non-floor): mass points of daily return ---")
    ev = band_evidence(regimes["DISCOVERY"][["symbol", "ts", "close"]])
    with pd.option_context("display.float_format", lambda v: f"{v:.4f}", "display.width", 160):
        print(ev.to_string(index=False))
    ev.to_csv(os.path.join(paths["results"], "DOORSTEP_BAND_EVIDENCE.csv"), index=False)

    all_res, all_cov, all_jac, all_rec, all_pre, all_stab = [], [], [], [], [], []
    frames = {}
    for regime, d in regimes.items():
        if d.empty:
            continue
        P.assert_single_panel(d, f"regime {regime}")
        fpf = build_footprints(d)
        d = d.join(fpf.drop(columns=["symbol", "ts"]))          # index-aligned
        d = d.sort_values(["symbol", "ts"], kind="mergesort").reset_index(drop=True)
        fwd = forward_outcomes(d)
        d = d.merge(fwd, on=["symbol", "ts"], how="left")
        d = add_base_rates(d)
        frames[regime] = d

        cov, jac = coverage(d, regime)
        res = pd.concat([analyse(d, regime, v) for v in VARIANTS], ignore_index=True)
        rec = recall_and_lead(d, regime)
        pre = predoor_profile(d, regime)
        all_res.append(res); all_cov.append(cov); all_jac.append(jac)
        all_rec.append(rec); all_pre.append(pre)

        print("\n" + "-" * 78)
        print(f"REGIME {regime}: {len(d):,} rows · {d['symbol'].nunique()} symbols · "
              f"{d['ts'].min().date()} → {d['ts'].max().date()} · guarded rows "
              f"{int(d['guard'].sum()):,} ({d['guard'].mean():.1%}) · door already open "
              f"(up) on {int((d['guard'] & d['recent_door_up'].fillna(False)).sum()):,} of them")
        for v in VARIANTS:
            base = res[res["footprint"].str.startswith("ALL") & (res["variant"] == v)]
            print(f"  base rates [{v}] (share of population rows followed by the outcome):")
            for o in ALL_OUTCOMES:
                print("   " + f"{o:<11}" + "  ".join(
                    f"k={k}:{base[(base.outcome == o) & (base.horizon == k)]['hit_rate'].iloc[0]:.3%}"
                    for k in HORIZONS))
        print("  footprint fire counts:")
        for _, r in cov.iterrows():
            print(f"    {r['footprint']:<32} {r['n_fired']:>7,}  "
                  f"({r['share_of_guarded_rows']:.2%} of guarded rows, "
                  f"{r['n_symbols']} symbols, {r['n_dates']} dates)")

    res = pd.concat(all_res, ignore_index=True)
    res.to_csv(os.path.join(paths["results"], "DOORSTEP_FOOTPRINT_ANALYSIS.csv"), index=False)
    pd.concat(all_cov).to_csv(os.path.join(paths["results"], "DOORSTEP_FOOTPRINT_COVERAGE.csv"),
                              index=False)
    pd.concat(all_jac).to_csv(os.path.join(paths["results"], "DOORSTEP_FOOTPRINT_OVERLAP.csv"),
                              index=False)
    rec = pd.concat(all_rec, ignore_index=True)
    rec.to_csv(os.path.join(paths["results"], "DOORSTEP_RECALL_LEADTIME.csv"), index=False)
    pre = pd.concat(all_pre, ignore_index=True)
    pre.to_csv(os.path.join(paths["results"], "DOORSTEP_PREDOOR_PROFILE.csv"), index=False)

    # ---------------- the candidate funnel ----------------
    # Step 1 is the PRE-REGISTERED criterion. Steps 2–3 were added after the
    # first run showed the pre-registered criterion passes ~200 hypotheses
    # because (a) it counted doors that were ALREADY OPEN at t and (b) a
    # footprint that merely restates "abnormal volume" or "already moved" was
    # not asked to add anything. Every step's count is printed and written.
    def prereg(x: pd.DataFrame) -> pd.Series:
        return (x["eligible"] & x["door_outcome"]
                & ~x["footprint"].eq("F16_REF_already_moved")
                & (x["excess_sameday"] > 0) & x["fdr10_sameday"].fillna(False)
                & (x["excess_volmatched"] > 0) & (x["t_volmatched"] >= CAND_T_MATCHED)
                & (x["lift_volmatched"] >= CAND_LIFT))

    disc = res[(res["regime"] == "DISCOVERY") & ~res["footprint"].str.startswith("ALL")].copy()
    disc["pass_prereg"] = prereg(disc)
    disc["beats_both_refs"] = ((disc["lift_vm_vs_ref_volume"] > 1.0)
                               & (disc["lift_vm_vs_ref_moved"] > 1.0))
    d_any = disc[disc["variant"] == "any"]
    d_fresh = disc[disc["variant"] == "fresh"]
    funnel = [
        ("1 pre-registered criterion, any-door (as frozen)", int(d_any["pass_prereg"].sum())),
        ("2 same criterion, FRESH-door population", int(d_fresh["pass_prereg"].sum())),
        ("3 = 2 and beats BOTH references (plain volume, already moved)",
         int((d_fresh["pass_prereg"] & d_fresh["beats_both_refs"]).sum())),
    ]
    tierA = d_fresh[d_fresh["pass_prereg"] & d_fresh["beats_both_refs"]].copy()
    tierA = tierA.sort_values("t_volmatched", ascending=False)
    tierA["tier"] = "A"
    tierA["status"] = ("PHASE5_CANDIDATE — DISCOVERY WINDOW ONLY, fresh-door, "
                       "beats both references, NOT VALIDATED")
    tierA.to_csv(os.path.join(paths["results"], "PHASE5_CANDIDATES.csv"), index=False)

    stab = stability(frames["DISCOVERY"], "DISCOVERY", "fresh", tierA) if len(tierA) \
        else pd.DataFrame()
    stab.to_csv(os.path.join(paths["results"], "DOORSTEP_STABILITY.csv"), index=False)

    print("\n" + "=" * 78)
    for v in VARIANTS:
        dv = disc[disc["variant"] == v]
        print(f"DISCOVERY [{v}] hypotheses: {len(dv)} · eligible: {int(dv['eligible'].sum())} · "
              f"same-day FDR survivors: {int(dv['fdr10_sameday'].fillna(False).sum())} · "
              f"vol-matched FDR survivors: {int(dv['fdr10_volmatched'].fillna(False).sum())}")
    print("\ncandidate funnel (DISCOVERY):")
    for label, n in funnel:
        print(f"  {label:<70} {n:>4}")
    show = ["footprint", "outcome", "horizon", "n_measured", "n_dates", "hit_rate",
            "base_volmatched", "lift_volmatched", "t_volmatched", "fail_share",
            "lift_vm_vs_ref_volume", "lift_vm_vs_ref_moved"]
    with pd.option_context("display.float_format", lambda v: f"{v:.3f}", "display.width", 240):
        print(f"\nPHASE 5 CANDIDATES (tier A): {len(tierA)}")
        print(tierA[show].to_string(index=False) if len(tierA) else "  → NONE.")
        print("\n--- FRESH-door, limit_up, k=5: every footprint (DISCOVERY) ---")
        blk = d_fresh[(d_fresh["outcome"] == "limit_up") & (d_fresh["horizon"] == 5)]
        print(blk[show].to_string(index=False))
        print("\n--- FRESH-door, abn_up, k=3: every footprint (DISCOVERY) ---")
        blk = d_fresh[(d_fresh["outcome"] == "abn_up") & (d_fresh["horizon"] == 3)]
        print(blk[show].to_string(index=False))
        if len(stab):
            print("\n--- stability of tier-A candidates by year / price bucket (fresh) ---")
            print(stab.to_string(index=False))
        print("\n--- recall: of fresh limit-up doors, share with the footprint in the prior "
              "5 sessions vs any window (DISCOVERY) ---")
        r5 = rec[(rec["regime"] == "DISCOVERY") & (rec["door"] == "limit_up") & (rec["window"] == 5)]
        print(r5[["footprint", "n_fresh_doors", "n_doors_with_footprint", "recall",
                  "base_share_any_window", "recall_lift", "lead_median"]].to_string(index=False))

    bio.write_manifest("phase45_manifest.json", {
        "phase": "4.5_doorstep_footprints",
        "status": "DISCOVERY-WINDOW DESCRIPTIVE — holdout sealed, nothing validated",
        **meta,
        "footprints": [{"id": i, "family": fam, "definition": dfn} for i, fam, dfn in FOOTPRINTS],
        "footprint_params": FP,
        "outcomes": OUTCOME_NOTE, "horizons": list(HORIZONS),
        "guards": {"min_price": MIN_PRICE, "sigma_defined": True, "rel_volume_z_defined": True},
        "base_rates": "same-day leave-one-out; same-day within volatility quintile leave-one-out",
        "inference": "date-paired excess, t across dates; BH-FDR per regime",
        "hypotheses_per_regime": len(FP_IDS) * len(ALL_OUTCOMES) * len(HORIZONS),
        "candidate_criterion": {
            "regime": "DISCOVERY", "door_outcome": True, "eligible": f"n≥{MIN_N}, dates≥{MIN_DATES}",
            "sameday": f"excess>0 and BH-FDR {FDR_Q}",
            "volmatched": f"excess>0 and t≥{CAND_T_MATCHED} and lift≥{CAND_LIFT}",
            "excluded": "F16_REF_already_moved (post-move by construction); activity outcome"},
        "candidate_funnel": funnel,
        "post_first_run_amendments": [
            "fresh-door variant: rows with an up/down door in t-5..t removed from "
            "occurrences AND base population (the pre-registered analysis counted "
            "already-open doors)",
            "tier A requires vol-matched lift above BOTH references at the same "
            "outcome/horizon",
            "circuit band schedule replaced by the one the data's mass points sit on",
        ],
        "n_candidates_tierA": int(len(tierA)),
        "circuit_bands": {"verified": C.TICK_RULES_VERIFIED,
                          "schedule": list(C.CIRCUIT_BANDS_UNVERIFIED),
                          "proxy_fraction": C.CIRCUIT_PROXY_FRACTION},
        "uses_open_field": False, "emits_orders": False, "cost_layer_applied": False,
        "outputs": ["results/DOORSTEP_FOOTPRINT_ANALYSIS.csv",
                    "results/DOORSTEP_FOOTPRINT_COVERAGE.csv",
                    "results/DOORSTEP_FOOTPRINT_OVERLAP.csv",
                    "results/DOORSTEP_RECALL_LEADTIME.csv",
                    "results/DOORSTEP_PREDOOR_PROFILE.csv",
                    "results/DOORSTEP_BAND_EVIDENCE.csv",
                    "results/DOORSTEP_STABILITY.csv",
                    "results/PHASE5_CANDIDATES.csv"],
    })
    print("\nNo BUY/SELL was produced. No cost layer was applied. The holdout was not read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
