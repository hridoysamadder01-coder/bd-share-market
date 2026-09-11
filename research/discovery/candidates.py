"""A candidate library, and an evaluator that measures conditional structure.

## What a candidate is

A candidate is a **boolean state or sequence** on `(symbol, day)`, computable at
the close of day *t* from information available then. It is not a prediction and
it is not a rule — it is a question of the form *"when the market is in this
state, what tends to happen next?"*

## Why a raw hit rate is not the measurement

`REJECTED_CANDIDATES.md` records how this goes wrong. **I-013**: "lift ratio > 1"
as a gate passed 21 of 63 rows by under 10 %. **I-012**: 102 "hits" were 28
distinct events on 25 symbols. **P45-4**: a footprint's edge collapsed 1.84 → 1.29
once *today's own move* entered the comparison — most of what looked like signal
was just "this stock moved today".

So every candidate is measured against a **same-day, same-volatility control**:
the other symbols trading on the same date in the same realized-volatility
quintile. That removes the market's own day and the stock's own restlessness,
which is what P45-4 showed is otherwise doing the work. Distinct `(symbol, date)`
events are counted, never occurrences.

This is discovery, so nothing here returns KEEP. It returns *structure worth
mutating* — a lift that survives the matched control on a real denominator — and
Stage 11 is where survivors get the full falsification battery.

## The families deliberately included

`P45-1 … P45-8` rejected every **single** price/volume footprint against a
limit-up outcome on daily bars. `ROADMAP.md` Stage 7 therefore bars re-asking
that question. What is licensed, and what this library is built from:

* **multi-day sequences** rather than single-day footprints;
* **circuit room** (distance to the daily band) — an input no P45 candidate had;
* **regime-conditional** states, evaluated per panel and per era, never pooled;
* **combinations** where each leg is individually weak.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .panel import assert_pit

# Outcome columns available per horizon.
def ret_col(h: int) -> str: return f"fwd_ret_{h}"
def mfe_col(h: int) -> str: return f"fwd_mfe_{h}"
def mae_col(h: int) -> str: return f"fwd_mae_{h}"


@dataclass
class Candidate:
    """One measurable state, its inputs, and the reasoning behind it."""

    cid: str
    logic: str
    inputs: Tuple[str, ...]
    build: Callable[[pd.DataFrame], pd.Series]
    family: str
    sequence: bool = False
    truth_note: str = ""

    def mask(self, d: pd.DataFrame) -> pd.Series:
        assert_pit(self.inputs)
        m = self.build(d)
        return m.fillna(False).astype(bool)


# --------------------------------------------------------------------------- helpers
def _g(d: pd.DataFrame, col: str):
    return d.groupby("symbol", sort=False)[col]


def roll_mean(d: pd.DataFrame, col: str, n: int) -> pd.Series:
    """Trailing mean INCLUDING today — today is known at today's close."""
    return _g(d, col).transform(lambda s: s.rolling(n, min_periods=n).mean())


def prior(d: pd.DataFrame, col: str, k: int = 1) -> pd.Series:
    return _g(d, col).shift(k)


def rose_for(d: pd.DataFrame, col: str, n: int, thresh: float) -> pd.Series:
    """True when `col` has been above `thresh` on each of the last n days."""
    ind = (d[col] > thresh).astype(float)
    return _g(d.assign(_i=ind), "_i").transform(
        lambda s: s.rolling(n, min_periods=n).sum()) == n


def delta(d: pd.DataFrame, col: str, k: int) -> pd.Series:
    return d[col] - prior(d, col, k)


# --------------------------------------------------------------------------- library
def build_library() -> List[Candidate]:
    """Materially different families, not variations on one idea."""
    L: List[Candidate] = []

    # ---- family A: compression → expansion SEQUENCE (not a single-day footprint)
    L.append(Candidate(
        "A1", "range compressed for 5 straight days, then turnover expands today",
        ("range_compression", "rel_turnover_z"),
        lambda d: rose_for(d, "range_compression", 5, 0.5) & (d["rel_turnover_z"] > 1.0),
        family="compression_expansion", sequence=True))
    L.append(Candidate(
        "A2", "range compressed 10 days and volume still quiet — coil, no trigger yet",
        ("range_compression", "rel_volume_z"),
        lambda d: rose_for(d, "range_compression", 10, 0.5) & (d["rel_volume_z"] < 0.0),
        family="compression_expansion", sequence=True))
    L.append(Candidate(
        "A3", "compression 5d + turnover expansion + close in the upper third of the day",
        ("range_compression", "rel_turnover_z", "close_location"),
        lambda d: rose_for(d, "range_compression", 5, 0.5) & (d["rel_turnover_z"] > 1.0)
                  & (d["close_location"] > 0.66),
        family="compression_expansion", sequence=True))

    # ---- family B: turnover ACCELERATION (second difference, not level)
    L.append(Candidate(
        "B1", "turnover z rising on each of 3 days and accelerating",
        ("rel_turnover_z",),
        lambda d: (delta(d, "rel_turnover_z", 1) > 0) & (prior(d, "rel_turnover_z", 1)
                  - prior(d, "rel_turnover_z", 2) > 0)
                  & (delta(d, "rel_turnover_z", 1) > prior(d, "rel_turnover_z", 1)
                     - prior(d, "rel_turnover_z", 2)),
        family="turnover_acceleration", sequence=True))
    L.append(Candidate(
        "B2", "volume persistent AND activity concentrated — few days carry the flow",
        ("volume_persistence", "activity_concentration"),
        lambda d: (d["volume_persistence"] > 0.6) & (d["activity_concentration"] > 0.3),
        family="participation_concentration"))

    # ---- family C: CIRCUIT ROOM — the input no P45 candidate had
    L.append(Candidate(
        "C1", "closed at the daily band with room to run tomorrow",
        ("closed_at_band", "room_up_frac"),
        lambda d: d["closed_at_band"] & (d["room_up_frac"] > 0.0),
        family="circuit_room", truth_note="band ladder INFERRED (panel.BAND_LADDER_TRUTH)"))
    L.append(Candidate(
        "C2", "approached the band today (>80% of the way) but did NOT close there",
        ("approach_up", "closed_at_band"),
        lambda d: (d["approach_up"] > 0.80) & (~d["closed_at_band"]),
        family="circuit_room", truth_note="band ladder INFERRED"))
    L.append(Candidate(
        "C3", "band approach on abnormal volume, after a quiet 5-day coil",
        ("approach_up", "rel_volume_z", "range_compression"),
        lambda d: (d["approach_up"] > 0.80) & (d["rel_volume_z"] > 1.5)
                  & rose_for(d, "range_compression", 5, 0.4),
        family="circuit_room", sequence=True, truth_note="band ladder INFERRED"))
    L.append(Candidate(
        "C4", "two band approaches within 5 days — repeated pressure on the ceiling",
        ("approach_up",),
        lambda d: _g(d.assign(_a=(d["approach_up"] > 0.80).astype(float)), "_a").transform(
            lambda s: s.rolling(5, min_periods=5).sum()) >= 2,
        family="circuit_room", sequence=True, truth_note="band ladder INFERRED"))

    # ---- family D: relative strength PERSISTENCE while the market is weak
    L.append(Candidate(
        "D1", "outperformed the market on each of 3 days while the market fell",
        ("market_relative_ret", "market_ret"),
        lambda d: rose_for(d, "market_relative_ret", 3, 0.0)
                  & (roll_mean(d, "market_ret", 3) < 0),
        family="relative_strength", sequence=True))
    L.append(Candidate(
        "D2", "5-day relative strength positive and market breadth abnormal-low",
        ("market_relative_ret", "xs_breadth_abnormal"),
        lambda d: (roll_mean(d, "market_relative_ret", 5) > 0) & (d["xs_breadth_abnormal"] < 0.02),
        family="relative_strength", sequence=True))

    # ---- family E: liquidity depletion / illiquidity
    L.append(Candidate(
        "E1", "illiquidity persistent and Amihud spiking — depth withdrawing",
        ("illiquidity_persistence", "amihud_z"),
        lambda d: (d["illiquidity_persistence"] > 0.15) & (d["amihud_z"] > 1.0),
        family="liquidity"))
    L.append(Candidate(
        "E2", "volume up but price not responding — absorption proxy",
        ("volume_price_divergence", "rel_volume_z"),
        lambda d: (d["volume_price_divergence"] > 0.5) & (d["rel_volume_z"] > 1.0),
        family="absorption"))

    # ---- family F: cross-sectional standout
    L.append(Candidate(
        "F1", "top 5% of the market by relative volume today",
        ("xs_rank_rel_volume",),
        lambda d: d["xs_rank_rel_volume"] > 0.95, family="cross_sectional"))
    L.append(Candidate(
        "F2", "top 5% by relative volume on a day when few others are abnormal",
        ("xs_rank_rel_volume", "xs_breadth_abnormal"),
        lambda d: (d["xs_rank_rel_volume"] > 0.95) & (d["xs_breadth_abnormal"] < 0.05),
        family="cross_sectional"))

    # ---- family G: combinations of individually weak legs
    L.append(Candidate(
        "G1", "coil + band room + relative strength — three weak legs together",
        ("range_compression", "room_up_frac", "market_relative_ret"),
        lambda d: rose_for(d, "range_compression", 5, 0.4) & (d["room_up_frac"] > 0.5)
                  & (roll_mean(d, "market_relative_ret", 3) > 0),
        family="combination", sequence=True, truth_note="band ladder INFERRED"))
    L.append(Candidate(
        "G2", "absorption + concentration + band approach",
        ("volume_price_divergence", "activity_concentration", "approach_up"),
        lambda d: (d["volume_price_divergence"] > 0.3) & (d["activity_concentration"] > 0.25)
                  & (d["approach_up"] > 0.6),
        family="combination", truth_note="band ladder INFERRED"))

    # ---- family H: the P45 baselines, carried ON PURPOSE as the reference
    # If a new candidate cannot beat these, it has found nothing new. P45-8's
    # best was F08u 1.84 / F07 1.72 / F15 1.55 against limit_up.
    L.append(Candidate(
        "H1_baseline", "plain abnormal volume (the P45 F15 reference)",
        ("rel_volume_z",), lambda d: d["rel_volume_z"] > 2.0, family="baseline"))
    L.append(Candidate(
        "H2_baseline", "abnormal volume on a market-quiet day (the P45 F07 reference)",
        ("rel_volume_z", "xs_breadth_abnormal"),
        lambda d: (d["rel_volume_z"] > 2.0) & (d["xs_breadth_abnormal"] <= 0.05),
        family="baseline"))
    return L


# --------------------------------------------------------------------------- evaluation
def vol_quintile(d: pd.DataFrame) -> pd.Series:
    """Realized-volatility quintile WITHIN each date — the control dimension.

    Rank-based rather than `qcut`: a date with fewer than five distinct volatility
    values makes qcut raise or return uneven bins, and a per-group function that
    returns a differently-shaped object breaks the transform outright. A within-date
    percentile rank is always defined and always aligns.
    """
    r = d.groupby("ts", sort=False)["realized_vol"].rank(pct=True, method="average")
    return (r * 5).clip(upper=4.999).astype("float").round(0).clip(0, 4)


def evaluate(d: pd.DataFrame, cand: Candidate, horizon: int,
             min_events: int = 30) -> Dict[str, Any]:
    """Conditional structure for one candidate at one horizon, against a matched control.

    The control is every OTHER row on the same date in the same volatility
    quintile — the comparison P45-4 showed is the one that matters, because it
    removes both the market's day and the stock's own restlessness.
    """
    rc, fc, ac = ret_col(horizon), mfe_col(horizon), mae_col(horizon)
    need = [rc, fc, ac, "realized_vol", "ts", "symbol"]
    frame = d.dropna(subset=[c for c in need if c in d.columns]).copy()
    if frame.empty:
        return {"cid": cand.cid, "horizon": horizon, "status": "NOT_OBSERVABLE",
                "note": "no rows with this outcome"}

    try:
        m = cand.mask(frame)
    except KeyError as e:
        return {"cid": cand.cid, "horizon": horizon, "status": "NOT_OBSERVABLE",
                "note": f"input missing: {e}"}

    if "_vq" not in frame.columns:                     # precomputed once per slice
        frame["_vq"] = vol_quintile(frame)
    hit = frame[m]
    n = len(hit)
    if n == 0:
        return {"cid": cand.cid, "horizon": horizon, "status": "KILLED",
                "n": 0, "note": "state never occurs on this slice"}

    # Matched control: same (date, vol quintile) cells the events actually live in,
    # excluding the events themselves. Weighted so each cell counts as its events do.
    cells = hit.groupby(["ts", "_vq"], sort=False).size().rename("w").reset_index()
    pool = frame[~m].merge(cells, on=["ts", "_vq"], how="inner")
    if pool.empty:
        ctrl_ret = ctrl_mfe = ctrl_mae = np.nan
        ctrl_n = 0
    else:
        cm = pool.groupby(["ts", "_vq"], sort=False)[[rc, fc, ac]].mean()
        w = cells.set_index(["ts", "_vq"])["w"].reindex(cm.index).fillna(0)
        ctrl_ret = float(np.average(cm[rc], weights=w)) if w.sum() else np.nan
        ctrl_mfe = float(np.average(cm[fc], weights=w)) if w.sum() else np.nan
        ctrl_mae = float(np.average(cm[ac], weights=w)) if w.sum() else np.nan
        ctrl_n = int(len(pool))

    fwd = float(hit[rc].mean())
    mfe = float(hit[fc].mean())
    mae = float(hit[ac].mean())
    # "Useful move": beats the round-trip cost bracket Round 2 used (1.0 %).
    thr = 0.01
    hit_rate = float((hit[rc] > thr).mean())
    ctrl_hit = float((pool[rc] > thr).mean()) if ctrl_n else np.nan
    lift = (hit_rate / ctrl_hit) if (ctrl_hit and ctrl_hit > 0) else np.nan
    excess = fwd - ctrl_ret if ctrl_ret == ctrl_ret else np.nan

    # Distinct events, not occurrences (I-012).
    events = int(hit.groupby(["symbol", "ts"], sort=False).ngroups)
    symbols = int(hit["symbol"].nunique())

    if events < min_events:
        status = "WEAK"
        why = f"only {events} distinct events (< {min_events})"
    elif not np.isfinite(lift):
        status = "WEAK"
        why = "no matched control available"
    elif lift >= 1.25 and excess > 0:
        status = "PROMISING"
        why = f"hit-rate lift {lift:.2f} and excess forward move {excess * 100:+.2f} pp vs matched control"
    elif lift <= 1.05:
        status = "KILLED"
        why = f"hit-rate lift {lift:.2f} — no better than the same-day same-volatility control"
    else:
        status = "WEAK"
        why = f"lift {lift:.2f} — positive but under the 1.25 discovery bar"

    return {
        "cid": cand.cid, "family": cand.family, "sequence": cand.sequence,
        "logic": cand.logic, "inputs": ",".join(cand.inputs), "horizon": horizon,
        "n_events": events, "n_rows": n, "symbols": symbols,
        "coverage_pct": round(100.0 * n / len(frame), 3),
        "fwd_move_pct": round(fwd * 100, 3), "adverse_pct": round(mae * 100, 3),
        "mfe_pct": round(mfe * 100, 3),
        "ctrl_fwd_pct": round(ctrl_ret * 100, 3) if ctrl_ret == ctrl_ret else None,
        "excess_pct": round(excess * 100, 3) if excess == excess else None,
        "hit_rate": round(hit_rate, 4), "ctrl_hit_rate": round(ctrl_hit, 4) if ctrl_hit == ctrl_hit else None,
        "lift": round(lift, 3) if np.isfinite(lift) else None,
        "ctrl_n": ctrl_n, "status": status, "why": why,
        "truth_note": cand.truth_note,
    }


def build_mutations() -> List[Candidate]:
    """Round 2: mutate the one family that survived the matched control.

    C1 (closed AT the daily band) showed lift 1.33 with +3.3 pp excess forward
    move on PRIMARY/free, while C2 (went 80 % of the way to the band and did NOT
    close there) came back at lift 0.97 on 11,944 events — KILLED. The edge is
    therefore not "this stock moved a lot today", which is the confound that
    collapsed P45-4 from 1.84 to 1.29. These mutations ask what *kind* of band
    close carries the structure, and which conditions destroy it.
    """
    M: List[Candidate] = []
    band = lambda d: d["closed_at_band"]

    M.append(Candidate(
        "M1", "band close that is the FIRST in 20 days — fresh, not already extended",
        ("closed_at_band",),
        lambda d: band(d) & (_g(d.assign(_b=band(d).astype(float)), "_b").transform(
            lambda s: s.shift(1).rolling(19, min_periods=1).sum()).fillna(0) == 0),
        family="circuit_room_mut", sequence=True, truth_note="band ladder INFERRED"))
    M.append(Candidate(
        "M2", "band close on abnormal volume",
        ("closed_at_band", "rel_volume_z"),
        lambda d: band(d) & (d["rel_volume_z"] > 2.0),
        family="circuit_room_mut", truth_note="band ladder INFERRED"))
    M.append(Candidate(
        "M3", "band close on QUIET volume — the opposite leg, to see if volume matters at all",
        ("closed_at_band", "rel_volume_z"),
        lambda d: band(d) & (d["rel_volume_z"] < 0.5),
        family="circuit_room_mut", truth_note="band ladder INFERRED"))
    M.append(Candidate(
        "M4", "band close after a 5-day compression coil",
        ("closed_at_band", "range_compression"),
        lambda d: band(d) & rose_for(d, "range_compression", 5, 0.4),
        family="circuit_room_mut", sequence=True, truth_note="band ladder INFERRED"))
    M.append(Candidate(
        "M5", "SECOND consecutive band close — is continuation still there, or spent?",
        ("closed_at_band",),
        lambda d: band(d) & (prior(d.assign(_b=band(d)), "_b", 1) == True),
        family="circuit_room_mut", sequence=True, truth_note="band ladder INFERRED"))
    M.append(Candidate(
        "M6", "band close while the MARKET fell — idiosyncratic, not a market-wide up day",
        ("closed_at_band", "market_ret"),
        lambda d: band(d) & (d["market_ret"] < 0),
        family="circuit_room_mut", truth_note="band ladder INFERRED"))
    M.append(Candidate(
        "M7", "band close while the market ROSE — the control for M6",
        ("closed_at_band", "market_ret"),
        lambda d: band(d) & (d["market_ret"] > 0),
        family="circuit_room_mut", truth_note="band ladder INFERRED"))
    M.append(Candidate(
        "M8", "band close on a day when few other symbols were abnormal — lonely strength",
        ("closed_at_band", "xs_breadth_abnormal"),
        lambda d: band(d) & (d["xs_breadth_abnormal"] < 0.03),
        family="circuit_room_mut", truth_note="band ladder INFERRED"))
    M.append(Candidate(
        "M9", "band close with illiquidity elevated — thin book carried it up",
        ("closed_at_band", "amihud_z"),
        lambda d: band(d) & (d["amihud_z"] > 1.0),
        family="circuit_room_mut", truth_note="band ladder INFERRED"))
    return M
