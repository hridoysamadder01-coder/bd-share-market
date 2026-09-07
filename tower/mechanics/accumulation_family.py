"""accumulation_family — absorption / accumulation / distribution mechanisms
(MECHANISMS.md #6, #7, #11, #12, #13, #28, #29, #32, #38, #39).

Same conventions as ``queue_family`` (whose ``Frame``, ``ramp``, ``geo_mean``,
``baselines`` and ``missing_reading`` helpers are reused): every score is a
continuous function of quantities measured on causal rolling windows over the
``StateHistory`` plus the current ``MarketState``; anything a source does not
deliver is ``None`` and, when the mechanism needs it, the reading is score 0
with ``evidence["missing"]`` naming the inputs; ``evidence["direction"]`` ∈
{+1, −1, 0} is the price direction the mechanism implies; ``baseline`` carries
the simple baselines (``imb_l1``, ``imb_topk``, ``imb_weighted``,
``depth_ratio``, ``price_only_response``, ``volume_only_response``) at the
same instant.

The tape is read through ``Frame.tape_rows`` (one row per distinct tape
interval: volume, trades, direction ∈ [−1, 1] from the aggressor / quote rule)
so a print feed and a cumulative-totals feed behave the same.  "Signed flow"
is Σ direction × volume over the classified rows; "one-sidedness" is
|signed flow| / Σ volume of the classified rows.

Rules in one line each (every mechanism docstring restates its rule fully):

  passive_accumulation  sell flow (≥ 55 % of classified volume) absorbed at a
                        bid touch that holds its price and refills what the
                        sells consumed, the mid flat (≤ 1 tick range) and the
                        visible bid depth share rising over 300 s.
  passive_distribution  mirror (buy flow into an ask that holds and refills).
  block_absorption      60-s traded volume vs the 900-s baseline rate (or the
                        largest interval vs the touch it hit), with the mid
                        moving ≤ 2 ticks.
  inventory_rebalancing flow-sign flips between consecutive classified rows
                        (300 s), buy / sell volume symmetry, mid net move small
                        against its path length (mean reversion).
  adverse_retreat       after the last classified trade, depth on the side it
                        hit pulled beyond the traded volume, spread widening,
                        fading 60 → 180 s after the trade.
  stealth_accumulation  600-s net buy share and buy-row persistence with low
                        trade intensity (vs the symbol's own longer history,
                        else absolute) and a flat mid.
  stealth_distribution  mirror.
  absorption            120-s one-sided aggressive flow large against the touch
                        it hits, the hit best price unchanged and the touch
                        refilled to its pre-flow size.
  accumulation_like     900-s composite: mean and persistence of the strongest
                        of passive_accumulation / stealth_accumulation /
                        absorption (bid-side), needing ≥ 60–300 s of span.
  distribution_like     mirror over the distribution mechanisms.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..state import MarketState
from ..windows import clamp01, safe_div, sign
from .base import Mechanism, MechanismReading, StateHistory, register
from .queue_family import (Frame, _EPS, _cv, _median, baselines, best_of, geo_mean, levels_of, mid_of,
                           missing_reading, queue_counters, ramp, spread_ticks_of, topk_depth, visible_depth)


# ============================================================================ lifecycle base
class DirectedMechanism(Mechanism):
    """``Mechanism`` that remembers the direction its episode carried.

    Readings report ``direction`` 0 once the score is 0, so at the moment an
    episode releases the base ``_resolve`` would see no direction and mark
    every episode "resolved".  Here the last non-zero direction seen while the
    episode was building / active / confirmed is kept (``episode_direction``
    in the evidence) and ``outcome_positive`` judges the realised mid move
    since the episode start against it: (mid − start mid) × direction > 0.
    None (→ the base rule) when no direction, start mid or mid is known.
    """

    _EPISODE_STATES = ("building", "active", "confirmed")

    def __init__(self) -> None:
        super().__init__()
        self._episode_dir = 0

    def outcome_positive(self, ms: MarketState) -> Optional[bool]:
        if self._episode_dir == 0 or self._start_mid is None or ms.mid is None:
            return None
        return (ms.mid - self._start_mid) * self._episode_dir > 0

    def update(self, ms: MarketState, hist: StateHistory):
        prev = self._state
        st = super().update(ms, hist)
        d = st.evidence.get("direction")
        if st.state in self._EPISODE_STATES:
            if prev not in self._EPISODE_STATES:
                self._episode_dir = 0            # a new episode: the previous one's direction is not inherited
            if d in (1, -1):
                self._episode_dir = d
        elif st.state == "inactive":
            self._episode_dir = 0
        st.evidence["episode_direction"] = self._episode_dir
        return st


# ============================================================================ helpers
def reading(mech: Mechanism, score: float, ev: Dict[str, Any], base: Dict[str, Any], note: str) -> MechanismReading:
    """The family's reading: whenever ``ev["missing"]`` names an input the mechanism needs, the
    score is 0 and no direction is implied (CONTRACTS: never a computed score on a substituted
    constant) — the measured evidence is kept so the reader sees what *was* observable."""
    if ev.get("missing"):
        score = 0.0
        ev["direction"] = 0
    return MechanismReading(mech.name, mech.family, clamp01(score), "inactive", ev, base, note=note)


def all_states(fr: Frame) -> List[MarketState]:
    """The whole causal history (states at or before now) followed by the current state."""
    return fr.past + [fr.ms]


def state_before(fr: Frame, state: MarketState) -> Optional[MarketState]:
    """The state that precedes ``state`` in the causal history (by identity, so states sharing a
    timestamp are told apart); None when ``state`` is the oldest one held."""
    sts = all_states(fr)
    for i in range(len(sts) - 1, -1, -1):
        if sts[i] is state:
            return sts[i - 1] if i > 0 else None
    return None


def _tape_identity(s: MarketState) -> Tuple[Any, ...]:
    return (s.trade_volume, s.interval_volume, s.interval_trades, s.interval_vwap)


def _tape_key(s: MarketState) -> Any:
    tp = s.session_state.get("tape") if isinstance(s.session_state, dict) else None
    clock = tp.get("tape_clock") if isinstance(tp, dict) else None
    return clock if clock is not None else (s.trade_count, s.trade_volume, s.interval_volume, s.interval_trades)


def _tape_flagged(s: MarketState) -> bool:
    tp = s.session_state.get("tape") if isinstance(s.session_state, dict) else None
    return isinstance(tp, dict) and bool(tp.get("last_first_row") or tp.get("last_monotone_break"))


def tape_rows(fr: Frame, seconds: float) -> List[Dict[str, Any]]:
    """Distinct tape intervals that *started inside the window*, oldest first, each row carrying
    the state it was first seen in (``state``) — the same identity rule as ``Frame.tape_rows``
    (the tape clock when carried, else the (trade_count, trade_volume, interval_volume,
    interval_trades) tuple) with three more rules:

    (1) Re-polls: the tape clock advances when a cumulative feed re-serves unchanged totals
        while the state keeps carrying the last real interval, so a row whose cumulative identity
        (cum_volume, interval volume, interval trades, vwap) equals the previous row's is the
        same interval polled again and is kept once (only when a cumulative volume is carried).
    (2) Window boundary: the walk starts before the window so that an interval first seen
        *before* the cutoff and still re-polled inside it is not counted as a new row at the
        window's first state (a trade must leave the window ``seconds`` after it happened).
    (3) Rows the tape engine flagged as the day's first row (its "interval" is the whole day so
        far) or as a monotone break (a source-side reset) carry no interval information and are
        dropped (``session_state.tape.last_first_row`` / ``last_monotone_break``); so is any row
        whose interval volume is negative (a reset the engine did not flag) — a negative volume
        would otherwise poison every flow sum (Σ v ≤ 0, one-sidedness undefined)."""
    cutoff = fr.ms.t - timedelta(seconds=seconds)
    sts = all_states(fr)
    # start the walk at the last tape-carrying state before the window (its identity seeds the dedupe)
    start = 0
    for i in range(len(sts) - 1, -1, -1):
        s = sts[i]
        if s.t < cutoff and (s.interval_volume is not None or s.trade_volume is not None):
            start = i
            break
    out: List[Dict[str, Any]] = []
    last_key: Any = object()
    last_ident: Optional[Tuple[Any, ...]] = None
    for s in sts[start:]:
        if s.interval_volume is None and s.trade_volume is None:
            continue
        key = _tape_key(s)
        if key == last_key:
            continue
        last_key = key
        ident = _tape_identity(s)
        if s.trade_volume is not None and ident == last_ident:
            continue                                     # the same interval polled again
        last_ident = ident
        if s.t < cutoff or _tape_flagged(s) or (s.interval_volume is not None and s.interval_volume < 0):
            continue
        out.append({"t": s.t, "volume": s.interval_volume, "trades": s.interval_trades,
                    "direction": s.trade_flow_direction, "vwap": s.interval_vwap, "cum_volume": s.trade_volume,
                    "state": s})
    return out


def classified_rows(fr: Frame, seconds: float) -> List[Dict[str, Any]]:
    """Tape rows inside the window that carry a volume > 0 and a direction; each row also
    carries the mid of the state it appeared in (``mid``) and that state (``state``)."""
    out: List[Dict[str, Any]] = []
    for r in tape_rows(fr, seconds):
        if not r["volume"] or r["volume"] < 0 or r["direction"] is None:
            continue
        row = dict(r)
        row["mid"] = mid_of(r["state"])
        out.append(row)
    return out


def cum_delta(states: Sequence[MarketState]) -> Optional[float]:
    """Δ cumulative day volume from the first to the last of ``states`` carrying it — only when
    the total never decreases across the states in between: a reset (day roll / feed restart)
    inside the span would otherwise turn into a phantom volume (1000 → 100 → 2200 reads as
    +1200 when 2100 traded) or a negative one.  None when < 2 states carry it or a decrease
    is seen (the caller falls back to the distinct tape intervals, which drop the reset row)."""
    cum = [float(s.trade_volume) for s in states if s.trade_volume is not None]
    if len(cum) < 2 or any(b < a for a, b in zip(cum, cum[1:])):
        return None
    return cum[-1] - cum[0]


def volume_since(fr: Frame, pre: MarketState) -> Optional[float]:
    """Traded volume after ``pre`` up to now: Δ cumulative day volume when both carry it and it
    never decreased in between, else Σ of the distinct tape intervals first seen after ``pre``
    (None when the tape is not observable)."""
    ms = fr.ms
    sts = all_states(fr)
    idx = next((i for i in range(len(sts) - 1, -1, -1) if sts[i] is pre), None)
    if idx is None:
        return None
    delta = cum_delta(sts[idx:]) if pre.trade_volume is not None else None
    if delta is not None:
        return delta
    after = {id(s) for s in sts[idx + 1:]}
    rows = [r for r in tape_rows(fr, (ms.t - pre.t).total_seconds() + 1.0)
            if id(r["state"]) in after and r["volume"] is not None]
    return float(sum(r["volume"] for r in rows)) if rows else (0.0 if ms.trade_volume is not None else None)


def flow_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """signed = Σ d·v, total = Σ v, buy = Σ v·max(0, d), sell = Σ v·max(0, −d), one_sided = |signed| / total,
    buy_rows / sell_rows = row counts by sign (|d| > 0.1 counts as a signed row)."""
    if not rows:
        return {"signed": None, "total": None, "buy": None, "sell": None, "one_sided": None,
                "buy_rows": 0, "sell_rows": 0, "rows": 0}
    signed = float(sum(r["direction"] * r["volume"] for r in rows))
    total = float(sum(r["volume"] for r in rows))
    buy = float(sum(r["volume"] * max(0.0, r["direction"]) for r in rows))
    sell = float(sum(r["volume"] * max(0.0, -r["direction"]) for r in rows))
    return {"signed": signed, "total": total, "buy": buy, "sell": sell,
            "one_sided": (abs(signed) / total) if total > 0 else None,
            "buy_rows": sum(1 for r in rows if r["direction"] > 0.1),
            "sell_rows": sum(1 for r in rows if r["direction"] < -0.1), "rows": len(rows)}


def touch_series(fr: Frame, side: str, seconds: float) -> List[Tuple[datetime, float, float]]:
    """[(t, best price, touch qty)] of one side over the window."""
    out = []
    for s in fr.states(seconds):
        p, q = best_of(s, side)
        if p is None or q is None:
            continue
        out.append((s.t, float(p), float(q)))
    return out


def side_replenishment(ser: Sequence[Tuple[datetime, float, float]], side: str) -> Dict[str, Any]:
    """Replenishment counters of one side's touch series: at an unchanged best price a fall of
    the touch qty is *consumed* and a rise is *refilled*; a best-price improvement adds the new
    touch qty to *refilled* (fresh liquidity in front); a retreat adds the old touch qty to
    *consumed*.  refills = number of rises after a fall; refill_ratio = min(1, refilled /
    consumed), None when nothing was consumed."""
    consumed = refilled = 0.0
    refills = 0
    fell = False
    for (t0, p0, q0), (t1, p1, q1) in zip(ser, ser[1:]):
        if abs(p1 - p0) < _EPS:
            d = q1 - q0
            if d < -_EPS:
                consumed += -d
                fell = True
            elif d > _EPS:
                refilled += d
                if fell:
                    refills += 1
                    fell = False
            continue
        better = (p1 > p0) if side == "bid" else (p1 < p0)
        if better:
            refilled += q1
            if fell:
                refills += 1
                fell = False
        else:
            consumed += q0
            fell = True
    ratio = (min(1.0, refilled / consumed) if consumed > 0 else None)
    return {"consumed": consumed, "refilled": refilled, "refills": refills, "refill_ratio": ratio}


def mid_range_ticks(fr: Frame, seconds: float) -> Optional[float]:
    pts = fr.series(mid_of, seconds)
    if len(pts) < 2 or not fr.tick:
        return None
    vs = [v for _, v in pts]
    return (max(vs) - min(vs)) / fr.tick


def flat_factor(range_ticks: Optional[float], free_ticks: float = 1.0, span_ticks: float = 2.0) -> Optional[float]:
    """1 while the mid range is ≤ ``free_ticks``, falling linearly to 0 ``span_ticks`` beyond it."""
    if range_ticks is None:
        return None
    return clamp01(1.0 - max(0.0, range_ticks - free_ticks) / span_ticks)


def depth_share_series(fr: Frame, side: str, seconds: float) -> List[Tuple[datetime, float]]:
    """visible depth of ``side`` / (bid + ask) per state."""
    out = []
    for s in fr.states(seconds):
        vb, va = visible_depth(s, "bid"), visible_depth(s, "ask")
        if vb is None or va is None or vb + va <= 0:
            continue
        out.append((s.t, (vb if side == "bid" else va) / (vb + va)))
    return out


def share_change(pts: Sequence[Tuple[datetime, float]]) -> Optional[float]:
    """mean of the last quarter of the points − mean of the first quarter (≥ 2 points)."""
    if len(pts) < 2:
        return None
    k = max(1, len(pts) // 4)
    first = [v for _, v in pts[:k]]
    last = [v for _, v in pts[-k:]]
    return sum(last) / len(last) - sum(first) / len(first)


def tape_missing(ms: MarketState) -> List[str]:
    miss = []
    if ms.trade_volume is None and ms.interval_volume is None:
        miss.append("trade_volume/interval_volume")
    if ms.trade_flow_direction is None:
        miss.append("trade_flow_direction")
    return miss or ["classified tape rows in window"]


# ============================================================================ #6 / #7
def _passive(mech: Mechanism, ms: MarketState, hist: StateHistory, side: str) -> MechanismReading:
    """Shared body of passive_accumulation (side = bid, absorbing sells) and passive_distribution
    (side = ask, absorbing buys); see the class docstrings for the rule."""
    fr = Frame(ms, hist)
    base = baselines(fr)
    w = mech.window_s
    rows = classified_rows(fr, w)
    if not rows:
        return missing_reading(mech, tape_missing(ms), base)
    fs = flow_summary(rows)
    absorbed = fs["sell"] if side == "bid" else fs["buy"]
    absorbed_share = safe_div(absorbed, fs["total"])
    ser = touch_series(fr, side, w)
    if len(ser) < 2:
        miss = [("best_bid" if side == "bid" else "best_ask"), ("bid_qty1" if side == "bid" else "ask_qty1")]
        return missing_reading(mech, [k for k in miss if getattr(ms, k) is None] or ["touch series (< 2 states)"],
                               base, {"flow": {k: v for k, v in fs.items()}})
    rep = side_replenishment(ser, side)
    p0, q0 = ser[0][1], ser[0][2]
    p1, q1 = ser[-1][1], ser[-1][2]
    tick = fr.tick
    retreat = None
    if tick:
        retreat = ((p0 - p1) if side == "bid" else (p1 - p0)) / tick
    hold = clamp01(1.0 - retreat) if retreat is not None else None
    mean_touch = sum(q for _, _, q in ser) / len(ser)
    absorbed_vs_touch = safe_div(absorbed, mean_touch) if mean_touch > 0 else None
    refill_ratio = rep["refill_ratio"]
    refill_observed = refill_ratio is not None
    if refill_ratio is None:
        # the queue never dipped between polls: it held or grew while the flow arrived
        refill_ratio = 1.0 if (absorbed and absorbed > 0 and q1 >= q0) else 0.0
    rng = mid_range_ticks(fr, w)
    flat = flat_factor(rng)
    shares = depth_share_series(fr, side, w)
    d_share = share_change(shares)
    s_flow = ramp(absorbed_share, 0.55, 0.85)
    s_abs = ramp(absorbed_vs_touch, 0.3, 1.5)
    s_ref = ramp(refill_ratio, 0.3, 0.9) * (1.0 if refill_observed else 0.8)
    s_share = ramp(d_share, 0.0, 0.15)
    core = geo_mean([s_flow, s_abs, s_ref])
    # hold / flat / share are None only when their inputs are unobservable (no tick, no mid in
    # the window, no two-sided visible depth): named in ``missing`` below, so the reading is 0
    # rather than a constant standing in for the factor
    score = core * (hold if hold is not None else 0.0) * (flat if flat is not None else 0.0) \
        * (0.5 + 0.5 * s_share)
    direction = (1 if side == "bid" else -1) if score > 0 else 0
    ev = {"side": side, "absorbed_volume": absorbed, "absorbed_share": absorbed_share, "signed_flow": fs["signed"],
          "total_volume": fs["total"], "rows": fs["rows"], "absorbed_vs_touch": absorbed_vs_touch,
          "mean_touch_qty": mean_touch, "touch_qty_start": q0, "touch_qty_now": q1, "touch_price_start": p0,
          "touch_price_now": p1, "retreat_ticks": retreat, "hold": hold, "consumed_qty": rep["consumed"],
          "refilled_qty": rep["refilled"], "refills": rep["refills"], "refill_ratio": refill_ratio,
          "refill_observed": refill_observed, "mid_range_ticks": rng, "flat": flat,
          "depth_share_change": d_share, "depth_share_now": (shares[-1][1] if shares else None),
          "components": {"flow": s_flow, "absorbed_vs_touch": s_abs, "refill": s_ref, "share": s_share},
          "direction": direction, "window_s": w}
    q = queue_counters(ms)
    if q and isinstance(q.get(side), dict):
        ev["engine_replenished"] = q[side].get("replenished")
        ev["engine_stacks_120s"] = q[side].get("stacks_120s")
    missing = []
    if not tick:
        missing.append("tick_size")
    if flat is None and tick:
        missing.append("mid (< 2 states with a mid in window)")
    if d_share is None:
        missing.append("visible depth on both sides (< 2 states in window)")
    if missing:
        ev["missing"] = missing
    return reading(mech, score, ev, base,
                   f"{side} absorbed {absorbed:.0f} ({absorbed_share:.2f} of flow), refill {refill_ratio:.2f}")


@register
class PassiveAccumulation(DirectedMechanism):
    """#6 Passive accumulation.

    Rule (window 300 s): classified tape rows give sell volume (Σ v·max(0, −d))
    and its share of the classified volume; the bid touch series gives the
    replenishment counters (falls at the same best price are *consumed*, rises
    are *refilled*, an improvement adds the new touch, a retreat consumes the
    old touch; refill_ratio = refilled / consumed, 1.0 with a 0.8 damp when the
    queue never dipped but held or grew while sells arrived), hold = 1 −
    (best_bid start − best_bid now) in ticks (clipped), absorbed_vs_touch = sell
    volume / mean touch qty; flat = 1 − max(0, mid range − 1 tick) / 2 ticks;
    depth-share change = mean visible-bid share over the last quarter of the
    window − over the first quarter.  score = geometric mean(ramp(sell share,
    0.55 → 0.85), ramp(absorbed_vs_touch, 0.3 → 1.5), ramp(refill_ratio, 0.3 →
    0.9)) × hold × flat × (0.5 + 0.5 × ramp(Δshare, 0 → 0.15)).  direction +1.
    """

    name = "passive_accumulation"
    family = "accumulation"
    requires = ("best_bid", "bid_qty1", "bids", "asks", "trade_flow_direction", "interval_volume", "mid", "tick_size")
    window_s = 300.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        return _passive(self, ms, hist, "bid")


@register
class PassiveDistribution(DirectedMechanism):
    """#7 Passive distribution — the mirror of #6: buy volume absorbed at an ask
    touch that holds its price and refills, flat mid, rising visible-ask share.
    Same windows and ramps as ``PassiveAccumulation``; direction −1.
    """

    name = "passive_distribution"
    family = "accumulation"
    requires = ("best_ask", "ask_qty1", "bids", "asks", "trade_flow_direction", "interval_volume", "mid", "tick_size")
    window_s = 300.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        return _passive(self, ms, hist, "ask")


# ============================================================================ #11
@register
class BlockAbsorption(DirectedMechanism):
    """#11 Large-print / block absorption.

    Rule: burst = the last 60 s, baseline = [now − 900 s, now − 60 s] (span ≥
    120 s with the tape observable).  burst_volume = traded volume in the burst
    (Δ cumulative, else Σ interval volumes); baseline_rate = baseline volume /
    baseline span; ratio = burst_volume / (baseline_rate × 60 s) — None when the
    baseline carried no trades (then the size is judged against the book:
    largest_interval / touch qty of the side it hit at the previous state).
    s_size = ramp(ratio, 3 → 10) when the baseline ratio exists, else
    ramp(largest / touch, 1 → 4) (which one is named in ``size_source``).
    price factor = 1 − |mid now − mid at burst start| / 2 ticks (clipped).
    one-sidedness of the burst flow → (0.6 + 0.4 × ramp(one_sided, 0.3 → 0.8)).
    score = s_size × price factor × that.  direction = −sign(burst signed flow)
    when the burst is ≥ 50 % one-sided (the absorbing side wins), else 0.
    """

    name = "block_absorption"
    family = "accumulation"
    requires = ("trade_volume", "interval_volume", "trade_flow_direction", "mid", "tick_size", "bid_qty1", "ask_qty1")
    burst_s = 60.0
    baseline_s = 900.0
    min_baseline_span_s = 120.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        # burst volume: Δ cumulative over the burst states (monotone, else a reset inside would be
        # a phantom burst), else the distinct tape intervals of the burst
        burst_vol = cum_delta(fr.states(self.burst_s))
        if burst_vol is None:
            b_rows = [r for r in tape_rows(fr, self.burst_s) if r["volume"] is not None]
            burst_vol = float(sum(r["volume"] for r in b_rows)) if b_rows else None
        if burst_vol is None:
            return missing_reading(self, ["trade_volume/interval_volume"], base)
        t_pre = ms.t - timedelta(seconds=self.burst_s)
        # baseline volume rate before the burst
        base_states = [s for s in fr.past if t_pre - timedelta(seconds=self.baseline_s) <= s.t <= t_pre]
        base_vol = base_rate = base_span = None
        cum = [s for s in base_states if s.trade_volume is not None]
        cum_vol = cum_delta(cum)
        if cum_vol is not None and (cum[-1].t - cum[0].t).total_seconds() >= self.min_baseline_span_s:
            base_span = (cum[-1].t - cum[0].t).total_seconds()
            base_vol = cum_vol
            base_rate = base_vol / base_span
        elif len(base_states) >= 2 and (base_states[-1].t - base_states[0].t).total_seconds() >= self.min_baseline_span_s:
            rows = [r for r in tape_rows(fr, self.baseline_s + self.burst_s) if r["t"] <= t_pre and r["volume"] is not None]
            if rows:
                base_span = (base_states[-1].t - base_states[0].t).total_seconds()
                base_vol = float(sum(r["volume"] for r in rows))
                base_rate = base_vol / base_span
        ratio = None
        if base_rate is not None and base_rate > 0:
            ratio = burst_vol / (base_rate * self.burst_s)
        # largest interval in the burst against the touch it hit
        burst_rows = [r for r in tape_rows(fr, self.burst_s) if r["volume"]]
        largest = max((r["volume"] for r in burst_rows), default=None)
        vs_touch = None
        hit_side = None
        if largest is not None:
            big = max(burst_rows, key=lambda r: r["volume"])
            d = big["direction"]
            hit_side = "ask" if (d is not None and d > 0) else ("bid" if (d is not None and d < 0) else None)
            prev = state_before(fr, big["state"])
            if hit_side is not None and prev is not None:
                _, tq = best_of(prev, hit_side)
                vs_touch = safe_div(largest, tq) if tq else None
        if ratio is not None:
            s_size, size_source = ramp(ratio, 3.0, 10.0), "baseline_rate"
        elif vs_touch is not None:
            s_size, size_source = ramp(vs_touch, 1.0, 4.0), "touch_qty"
        else:
            miss = ["volume baseline (span < %.0f s or no trades)" % self.min_baseline_span_s]
            if hit_side is None:
                miss.append("trade_flow_direction")
            return missing_reading(self, miss, base, {"burst_volume": burst_vol, "largest_interval": largest})
        pre = fr.at_or_before(t_pre)
        m_pre = mid_of(pre) if pre is not None else None
        m_now = mid_of(ms)
        move = (abs(m_now - m_pre) / tick) if (m_pre is not None and m_now is not None and tick) else None
        pf = clamp01(1.0 - move / 2.0) if move is not None else 0.5
        fs = flow_summary([r for r in classified_rows(fr, self.burst_s)])
        one = fs["one_sided"]
        s_one = 0.6 + 0.4 * ramp(one, 0.3, 0.8)
        score = s_size * pf * s_one
        direction = 0
        if score > 0 and one is not None and one >= 0.5:
            direction = -sign(fs["signed"])
        ev = {"burst_volume": burst_vol, "baseline_volume": base_vol, "baseline_span_s": base_span,
              "baseline_rate_per_s": base_rate, "volume_ratio": ratio, "largest_interval": largest,
              "largest_vs_touch": vs_touch, "hit_side": hit_side, "size_source": size_source,
              "mid_move_ticks": move, "price_factor": pf, "one_sided": one, "signed_flow": fs["signed"],
              "components": {"size": s_size, "price": pf, "one_sided": s_one},
              "direction": direction, "burst_s": self.burst_s, "baseline_s": self.baseline_s}
        missing = []
        if move is None:
            missing.append("mid/tick_size")
        if one is None:
            missing.append("trade_flow_direction")
        if missing:
            ev["missing"] = missing
        return reading(self, score, ev, base, f"burst {burst_vol:.0f} ratio {ratio} move {move}")


# ============================================================================ #12
@register
class InventoryRebalancing(DirectedMechanism):
    """#12 Inventory rebalancing.

    Rule (window 300 s): the classified tape rows with |direction| > 0.1 give
    a sign sequence; flips = number of sign changes between consecutive rows,
    flip_rate = flips / (rows − 1); symmetry = 1 − |buy − sell| / (buy + sell)
    over the row volumes; mean reversion: over the mids at the rows (plus the
    current mid) path = Σ |Δmid|, net = |mid now − mid first|, reversion = 1 −
    net / path when path ≥ ½ tick, else 1 (the price never drifted).
    score = ramp(rows, 3 → 8) × ramp(flip_rate, 0.3 → 0.8) × ramp(symmetry,
    0.5 → 0.95) × (0.4 + 0.6 × reversion).  No implied direction (0).
    """

    name = "inventory_rebalancing"
    family = "accumulation"
    requires = ("trade_flow_direction", "interval_volume", "mid", "tick_size")
    window_s = 300.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        rows = [r for r in classified_rows(fr, self.window_s) if abs(r["direction"]) > 0.1]
        if len(rows) < 2:
            return missing_reading(self, tape_missing(ms) if not rows else ["classified rows (< 2 in window)"],
                                   base, {"rows": len(rows)})
        signs = [1 if r["direction"] > 0 else -1 for r in rows]
        flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
        flip_rate = flips / (len(signs) - 1)
        fs = flow_summary(rows)
        buy, sell = fs["buy"], fs["sell"]
        symmetry = (1.0 - abs(buy - sell) / (buy + sell)) if (buy + sell) > 0 else None
        tick = fr.tick
        mids = [r["mid"] for r in rows if r["mid"] is not None]
        m_now = mid_of(ms)
        if m_now is not None:
            mids.append(m_now)
        path = net = reversion = None
        if len(mids) >= 2 and tick:
            path = sum(abs(b - a) for a, b in zip(mids, mids[1:])) / tick
            net = abs(mids[-1] - mids[0]) / tick
            reversion = (1.0 - net / path) if path >= 0.5 else 1.0
        s_rows = ramp(len(rows), 3.0, 8.0)
        s_flip = ramp(flip_rate, 0.3, 0.8)
        s_sym = ramp(symmetry, 0.5, 0.95)
        rev = reversion if reversion is not None else 0.5
        score = s_rows * s_flip * s_sym * (0.4 + 0.6 * rev)
        ev = {"rows": len(rows), "flips": flips, "flip_rate": flip_rate, "buy_volume": buy, "sell_volume": sell,
              "symmetry": symmetry, "mid_path_ticks": path, "mid_net_ticks": net, "reversion": reversion,
              "signs": signs[:60], "components": {"rows": s_rows, "flips": s_flip, "symmetry": s_sym},
              "direction": 0, "window_s": self.window_s}
        if reversion is None:
            ev["missing"] = ["mid/tick_size"]
        return reading(self, score, ev, base, f"{flips} flips over {len(rows)} rows, symmetry {symmetry}")


# ============================================================================ #13
@register
class AdverseRetreat(DirectedMechanism):
    """#13 Adverse-selection retreat.

    Rule: the last classified tape row (volume > 0, direction ≠ 0) inside 180 s
    names the side it hit (ask for buys, bid for sells) and t_trade.  pre = the
    state before the one that carried that row (else that state).  Depth on
    the hit side: top-K (5 levels) and touch, pre vs now; traded = traded
    volume since pre (Δ cumulative, else Σ intervals) bounds what trades can
    account for: pulled = max(0, pre − now − traded), pulled_share = pulled /
    pre for both series, the larger one is used (named).  Δspread = spread
    ticks now − at pre; retreat = best-price retreat of the hit side in ticks.
    recency = 1 up to 60 s after the trade, then linear to 0 at 180 s.
    score = ramp(pulled_share, 0.2 → 0.6) × (0.4 + 0.6 × ramp(Δspread, 0.5 →
    2)) × recency.  direction = the trade's direction (ask pulled after buys →
    +1, bid pulled after sells → −1).
    """

    name = "adverse_retreat"
    family = "accumulation"
    requires = ("trade_flow_direction", "interval_volume", "bids", "asks", "bid_qty1", "ask_qty1", "spread_ticks",
                "tick_size")
    window_s = 180.0
    fresh_s = 60.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        rows = [r for r in classified_rows(fr, self.window_s) if abs(r["direction"]) > 0.1]
        if not rows:
            return missing_reading(self, tape_missing(ms), base)
        last = rows[-1]
        d = last["direction"]
        side = "ask" if d > 0 else "bid"
        # the book before the trade: the state preceding the one that first carried the row (by
        # identity — states sharing a timestamp are told apart); the trade's own state when it is
        # the oldest held (then its volume is already inside that state's cumulative total)
        pre = state_before(fr, last["state"]) or last["state"]
        # trades after ``pre`` (when ``pre`` is the trade's own state its print is already inside
        # that state's total, so only later prints — classified or not — are subtracted)
        traded = volume_since(fr, pre) or 0.0
        k_pre, k_now = topk_depth(pre, side), topk_depth(ms, side)
        _, t_pre = best_of(pre, side)
        _, t_now = best_of(ms, side)
        book_now = bool(levels_of(ms, "bid") or levels_of(ms, "ask") or ms.best_bid is not None
                        or ms.best_ask is not None)
        if book_now:
            k_now = k_now if k_now is not None else 0.0
            t_now = t_now if t_now is not None else 0.0
        cands: Dict[str, Optional[float]] = {}
        for name, a, b in (("topk", k_pre, k_now), ("touch", t_pre, t_now)):
            if a is None or b is None or a <= 0:
                cands[name] = None
                continue
            pulled = max(0.0, a - b - traded)
            cands[name] = pulled / a
        if all(v is None for v in cands.values()):
            miss = [("ask_qty1" if side == "ask" else "bid_qty1")]
            return missing_reading(self, miss, base, {"hit_side": side, "t_trade": last["t"].isoformat()})
        src = max((k for k in cands if cands[k] is not None), key=lambda k: cands[k])
        pulled_share = cands[src]
        sp_pre, sp_now = spread_ticks_of(pre, tick), spread_ticks_of(ms, tick)
        d_spread = (sp_now - sp_pre) if (sp_pre is not None and sp_now is not None) else None
        p_pre, _ = best_of(pre, side)
        p_now, _ = best_of(ms, side)
        # the hit side vacated entirely (a book is displayed, but nothing on that side): the
        # spread is unbounded, the strongest widening there is — not an unobservable one
        vacated = book_now and p_pre is not None and p_now is None
        retreat = None
        if p_pre is not None and p_now is not None and tick:
            retreat = ((p_now - p_pre) if side == "ask" else (p_pre - p_now)) / tick
        dt = (ms.t - last["t"]).total_seconds()
        recency = 1.0 if dt <= self.fresh_s else clamp01(1.0 - (dt - self.fresh_s) / (self.window_s - self.fresh_s))
        s_pull = ramp(pulled_share, 0.2, 0.6)
        s_sp = 1.0 if (vacated and d_spread is None) else ramp(d_spread, 0.5, 2.0)
        score = s_pull * (0.4 + 0.6 * s_sp) * recency
        direction = sign(d) if score > 0 else 0
        ev = {"hit_side": side, "trade_direction": d, "trade_volume_row": last["volume"], "t_trade": last["t"].isoformat(),
              "seconds_since_trade": dt, "recency": recency, "depth_topk_pre": k_pre, "depth_topk_now": k_now,
              "touch_pre": t_pre, "touch_now": t_now, "traded_since_pre": traded, "pulled_share": pulled_share,
              "pulled_share_topk": cands["topk"], "pulled_share_touch": cands["touch"], "pull_source": src,
              "spread_ticks_pre": sp_pre, "spread_ticks_now": sp_now, "spread_widening_ticks": d_spread,
              "best_retreat_ticks": retreat, "hit_side_vacated": vacated,
              "components": {"pull": s_pull, "spread": s_sp},
              "direction": direction, "window_s": self.window_s}
        if d_spread is None and not vacated:
            ev["missing"] = ["spread_ticks/tick_size"]
        return reading(self, score, ev, base, f"{side} pulled {pulled_share:.2f} after trade, spread +{d_spread}")


# ============================================================================ #28 / #29
def _intensity(fr: Frame, seconds: float, long_s: float) -> Dict[str, Optional[float]]:
    """Window trade intensity (mean ``trade_intensity``, else Σ interval trades / window minutes)
    and the symbol's own longer-history median intensity (points older than the window, ≥ 5)."""
    ints = fr.series(lambda s: s.trade_intensity, seconds)
    now_i: Optional[float] = None
    src = "trade_intensity"
    if ints:
        now_i = sum(v for _, v in ints) / len(ints)
    else:
        rows = [r for r in tape_rows(fr, seconds) if r["trades"] is not None]
        span = fr.span_s(seconds)
        if rows and span > 0:
            now_i = float(sum(r["trades"] for r in rows)) / (span / 60.0)
            src = "interval_trades"
    older = [s.trade_intensity for s in fr.past
             if s.trade_intensity is not None and (fr.ms.t - s.t).total_seconds() > seconds
             and (fr.ms.t - s.t).total_seconds() <= long_s]
    ref = _median(older) if len(older) >= 5 else None
    rel = (now_i / ref) if (now_i is not None and ref) else None
    return {"intensity": now_i, "source": src, "reference": ref, "relative": rel, "reference_points": len(older)}


def _stealth(mech: Mechanism, ms: MarketState, hist: StateHistory, want: int) -> MechanismReading:
    fr = Frame(ms, hist)
    base = baselines(fr)
    w = mech.window_s
    rows = classified_rows(fr, w)
    if not rows:
        return missing_reading(mech, tape_missing(ms), base)
    fs = flow_summary(rows)
    net_share = safe_div(fs["signed"] * want, fs["total"])
    signed_rows = fs["buy_rows"] + fs["sell_rows"]
    persistence = safe_div((fs["buy_rows"] if want > 0 else fs["sell_rows"]), signed_rows) if signed_rows else None
    inten = _intensity(fr, w, mech.long_s)
    if inten["relative"] is not None:
        low = 1.0 - ramp(inten["relative"], 1.0, 2.5)
        low_src = "relative"
    elif inten["intensity"] is not None:
        low = 1.0 - ramp(inten["intensity"], 4.0, 16.0)
        low_src = "absolute"
    else:
        low, low_src = None, "none"
    rng = mid_range_ticks(fr, w)
    flat = flat_factor(rng)
    s_net = ramp(net_share, 0.3, 0.8)
    s_per = ramp(persistence, 0.55, 0.9)
    s_rows = ramp(fs["rows"], 3.0, 8.0)
    score = s_net * s_per * s_rows * (low if low is not None else 0.5) * (flat if flat is not None else 0.5)
    direction = want if score > 0 else 0
    ev = {"net_share": net_share, "signed_flow": fs["signed"], "total_volume": fs["total"], "rows": fs["rows"],
          "buy_rows": fs["buy_rows"], "sell_rows": fs["sell_rows"], "persistence": persistence,
          "intensity": inten["intensity"], "intensity_source": inten["source"], "intensity_reference": inten["reference"],
          "intensity_relative": inten["relative"], "low_intensity": low, "low_intensity_source": low_src,
          "mid_range_ticks": rng, "flat": flat,
          "components": {"net": s_net, "persistence": s_per, "rows": s_rows}, "direction": direction, "window_s": w}
    missing = []
    if low is None:
        missing.append("trade_intensity/interval_trades")
    if flat is None:
        missing.append("mid/tick_size")
    if missing:
        ev["missing"] = missing
    return reading(mech, score, ev, base,
                   f"net {net_share:.2f} over {fs['rows']} rows, low-intensity {low}, flat {flat}")


@register
class StealthAccumulation(DirectedMechanism):
    """#28 Stealth accumulation.

    Rule (window 600 s): classified tape rows → net_share = Σ d·v / Σ v (buy
    positive), persistence = buy rows / signed rows; intensity = mean
    ``trade_intensity`` over the window (else Σ interval trades per window
    minute), compared with the median ``trade_intensity`` of the symbol's own
    history older than the window (≤ 1800 s, ≥ 5 points): low = 1 −
    ramp(intensity / reference, 1.0 → 2.5), or without a reference low = 1 −
    ramp(intensity, 4 → 16 trades/min) (source named); flat = 1 − max(0, mid
    range − 1 tick) / 2 ticks.  score = ramp(net_share, 0.3 → 0.8) ×
    ramp(persistence, 0.55 → 0.9) × ramp(rows, 3 → 8) × low × flat.
    direction +1.
    """

    name = "stealth_accumulation"
    family = "accumulation"
    requires = ("trade_flow_direction", "interval_volume", "interval_trades", "trade_intensity", "mid", "tick_size")
    window_s = 600.0
    long_s = 1800.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        return _stealth(self, ms, hist, +1)


@register
class StealthDistribution(DirectedMechanism):
    """#29 Stealth distribution — mirror of #28 (net sell share, sell-row
    persistence, low intensity, flat mid); direction −1."""

    name = "stealth_distribution"
    family = "accumulation"
    requires = ("trade_flow_direction", "interval_volume", "interval_trades", "trade_intensity", "mid", "tick_size")
    window_s = 600.0
    long_s = 1800.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        return _stealth(self, ms, hist, -1)


# ============================================================================ #32
@register
class Absorption(DirectedMechanism):
    """#32 Absorption.

    Rule (window 120 s): signed flow over the classified rows names the
    aggressor side (buys hit the ask, sells hit the bid) and one_sided =
    |signed| / total.  On the hit side the touch series gives touch_ref = the
    largest touch qty in the window (the full queue — a reference independent
    of the poll phase), touch_now, the replenishment counters (consumed /
    refilled qty, refills = rises after falls at the same best price) and the
    best-price retreat in ticks (start → now).  flow_vs_touch = |signed flow|
    / touch_ref; refill_ratio = max(touch_now / touch_ref, min(1, refilled /
    consumed)) — the queue is back, or has been rebuilt as much as it was
    eaten; price factor = 1 − retreat (clipped: the hit best price unchanged).
    score = ramp(flow_vs_touch, 0.5 → 2) × price factor × ramp(refill_ratio,
    0.4 → 0.9) × (0.5 + 0.5 × ramp(one_sided, 0.3 → 0.8)).
    direction = −sign(signed flow) (the absorbing passive side wins).
    """

    name = "absorption"
    family = "accumulation"
    requires = ("trade_flow_direction", "interval_volume", "best_bid", "best_ask", "bid_qty1", "ask_qty1", "tick_size")
    window_s = 120.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        rows = classified_rows(fr, self.window_s)
        if not rows:
            return missing_reading(self, tape_missing(ms), base)
        fs = flow_summary(rows)
        signed = fs["signed"]
        if signed is None or abs(signed) <= _EPS:
            ev = {"signed_flow": signed, "total_volume": fs["total"], "one_sided": fs["one_sided"], "direction": 0,
                  "rows": fs["rows"], "window_s": self.window_s}
            return MechanismReading(self.name, self.family, 0.0, "inactive", ev, base, note="balanced flow")
        side = "ask" if signed > 0 else "bid"
        ser = touch_series(fr, side, self.window_s)
        if len(ser) < 2:
            miss = [("best_ask" if side == "ask" else "best_bid"), ("ask_qty1" if side == "ask" else "bid_qty1")]
            return missing_reading(self, [k for k in miss if getattr(ms, k) is None] or ["touch series (< 2 states)"],
                                   base, {"hit_side": side, "signed_flow": signed})
        p0, q0 = ser[0][1], ser[0][2]
        p1, q1 = ser[-1][1], ser[-1][2]
        q_ref = max(q for _, _, q in ser)
        rep = side_replenishment(ser, side)
        tick = fr.tick
        retreat = (((p1 - p0) if side == "ask" else (p0 - p1)) / tick) if tick else None
        pf = clamp01(1.0 - retreat) if retreat is not None else 0.5
        flow_vs_touch = (abs(signed) / q_ref) if q_ref > 0 else None
        refill_now = (q1 / q_ref) if q_ref > 0 else None
        refill_window = rep["refill_ratio"]
        refill_ratio = max(x for x in (refill_now, refill_window) if x is not None) \
            if (refill_now is not None or refill_window is not None) else None
        s_flow = ramp(flow_vs_touch, 0.5, 2.0)
        s_ref = ramp(refill_ratio, 0.4, 0.9)
        s_one = ramp(fs["one_sided"], 0.3, 0.8)
        score = s_flow * pf * s_ref * (0.5 + 0.5 * s_one)
        direction = -sign(signed) if score > 0 else 0
        ev = {"hit_side": side, "signed_flow": signed, "total_volume": fs["total"], "one_sided": fs["one_sided"],
              "rows": fs["rows"], "touch_ref": q_ref, "touch_start": q0, "touch_now": q1, "touch_price_start": p0,
              "touch_price_now": p1, "retreat_ticks": retreat, "price_factor": pf, "flow_vs_touch": flow_vs_touch,
              "refill_ratio": refill_ratio, "refill_now": refill_now, "refill_window": refill_window,
              "refills": rep["refills"], "consumed_qty": rep["consumed"], "refilled_qty": rep["refilled"],
              "components": {"flow": s_flow, "refill": s_ref, "one_sided": s_one},
              "direction": direction, "window_s": self.window_s}
        if q_ref <= 0:
            ev["missing"] = ["touch qty (zero throughout the window)"]
        if retreat is None:
            ev.setdefault("missing", []).append("tick_size")
        return reading(self, score, ev, base,
                       f"{side} absorbed flow {abs(signed):.0f} vs touch {q_ref:.0f}, refill {refill_ratio}")


# ============================================================================ #38 / #39
class _CompositeState(DirectedMechanism):
    """Shared body of accumulation_like / distribution_like.

    The three component mechanisms are evaluated at every update (their
    readings are stateless functions of the same causal window); the strongest
    component score — absorption counted only when its implied direction is the
    composite's — is kept as a (t, value) point over the composite window
    (900 s; one point per distinct state time).  mean = mean of the points,
    persistence = share of the points ≥ 0.35 (the build threshold), span =
    time covered by the points.  score = mean × (0.5 + 0.5 × persistence) ×
    ramp(span, 60 → 300 s).  The raw net buy share (Σ d·v / Σ v: negative when
    sells dominate, as they do when a bid absorbs them) and the mid change over
    the composite window are reported as evidence.
    """

    want: int = 0
    window_s = 900.0
    components: Tuple[type, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self._subs: List[Mechanism] = [c() for c in self.components]
        self._pts: List[Tuple[datetime, float]] = []

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        subs: Dict[str, Dict[str, Any]] = {}
        missing_all = True
        best = 0.0
        for sub in self._subs:
            r = sub.compute(ms, hist)
            eff = r.score
            if sub.name == "absorption" and r.evidence.get("direction") != self.want:
                eff = 0.0
            subs[sub.name] = {"score": r.score, "effective": eff, "direction": r.evidence.get("direction"),
                              "missing": r.evidence.get("missing")}
            if not r.evidence.get("missing"):
                missing_all = False
            best = max(best, eff)
        # roll the composite window (one point per state time; a repeated time is replaced).  An
        # instant at which every component lacks its inputs carries no information and adds no
        # point (a 0 there would be a silent zero dragging the mean down).
        cutoff = ms.t - timedelta(seconds=self.window_s)
        if not missing_all:
            if self._pts and self._pts[-1][0] == ms.t:
                self._pts[-1] = (ms.t, best)
            elif self._pts and ms.t < self._pts[-1][0]:
                pass                                 # out-of-order state: keep the window monotone
            else:
                self._pts.append((ms.t, best))
        self._pts = [p for p in self._pts if p[0] >= cutoff]
        if missing_all or not self._pts:
            miss = sorted({m for s in subs.values() for m in (s["missing"] or [])})
            return missing_reading(self, miss or ["component inputs"], base, {"components": subs})
        vals = [v for _, v in self._pts]
        mean = sum(vals) / len(vals)
        persistence = sum(1 for v in vals if v >= self.build_threshold) / len(vals)
        span = (self._pts[-1][0] - self._pts[0][0]).total_seconds()
        s_span = ramp(span, 60.0, 300.0)
        score = mean * (0.5 + 0.5 * persistence) * s_span
        rows = classified_rows(fr, self.window_s)
        fs = flow_summary(rows)
        net_buy_share = safe_div(fs["signed"], fs["total"])
        direction = self.want if score > 0 else 0
        ev = {"components": subs, "strongest_now": best, "mean_strongest": mean, "persistence": persistence,
              "points": len(vals), "span_s": span, "span_factor": s_span, "net_buy_share": net_buy_share,
              "total_volume": fs["total"], "mid_change_ticks": fr.mid_change_ticks(self.window_s),
              "direction": direction, "window_s": self.window_s}
        return reading(self, score, ev, base, f"mean {mean:.2f} persistence {persistence:.2f} over {span:.0f} s")


@register
class AccumulationLike(_CompositeState):
    """#38 Accumulation-like state: composite of passive_accumulation,
    stealth_accumulation and absorption (bid side, direction +1) over 900 s —
    rule in ``_CompositeState``.  direction +1."""

    name = "accumulation_like"
    family = "accumulation"
    requires = ("trade_flow_direction", "interval_volume", "best_bid", "bid_qty1", "bids", "asks", "mid", "tick_size")
    want = 1
    components = (PassiveAccumulation, StealthAccumulation, Absorption)


@register
class DistributionLike(_CompositeState):
    """#39 Distribution-like state: composite of passive_distribution,
    stealth_distribution and absorption (ask side, direction −1) over 900 s —
    rule in ``_CompositeState``.  direction −1."""

    name = "distribution_like"
    family = "accumulation"
    requires = ("trade_flow_direction", "interval_volume", "best_ask", "ask_qty1", "bids", "asks", "mid", "tick_size")
    want = -1
    components = (PassiveDistribution, StealthDistribution, Absorption)
