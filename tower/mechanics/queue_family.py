"""queue_family — touch-queue mechanisms (MECHANISMS.md #1, #2, #30, #31, #33).

Every mechanism here is a ``Mechanism`` subclass computed from rolling windows
over the current ``MarketState`` plus the causal ``StateHistory`` (states at or
before the current one — the engine pushes the current state *after* the
mechanisms run, so the window is assembled as history + current).  Scores are
continuous functions of the measured quantities (linear ramps of shares,
counts and rates, multiplied together); no constant scores.  Whatever cannot be
measured is ``None`` and, when the mechanism needs it, the reading is score 0
with ``evidence["missing"]`` naming the inputs.  ``evidence["direction"]`` ∈
{+1, −1, 0} states the price direction the mechanism implies (0 = none).
``baseline`` carries the simple baselines at the same instant (``imb_l1``,
``imb_topk``, ``imb_weighted``, ``depth_ratio``, ``price_only_response``,
``volume_only_response``), computed from the displayed book / the window when
the state does not carry them.

Shared helpers (``Frame``, ``ramp``, ``baselines`` …) live here and are reused
by ``sweep_family``.

Rules (window lengths are class attributes, every rule is restated in the
mechanism docstring):

  queue_pull_stack     touch qty at an unchanged best price over 120 s: the fall
                       not accounted for by traded volume is a *pull*, the rise
                       is a *stack*; score = ramp(share, 0.15 → 0.75) ×
                       (0.6 + 0.4 × step consistency).
  quote_refresh_churn  best (price, qty) changes per minute over 60 s, damped by
                       the net best-price drift (1 / (1 + |drift ticks|)) and by
                       the net qty change; score = ramp(rate, 2 → 10 /min) × …
  layering_like        per-price level diffs over 120 s: qty appearing and
                       cancelling ≥ 2 ticks away from the touch, appear→cancel
                       cycles at the same price, cancel-away share of removals.
  hidden_replenishment same-price touch queue consumed (≤ 70 % of its reference
                       size, trade volume present) and refilled (≥ 75 %)
                       repeatedly over 300 s; similarity of the refilled sizes.
  order_splitting      repeated same-size prints (5 % tolerance) over 300 s from
                       the print / single-trade-interval / touch-consumption
                       series, with the regularity of their cadence.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..state import MarketState
from ..windows import clamp01, safe_div, sign
from .base import Mechanism, MechanismReading, StateHistory, register

TOP_K = 5
_EPS = 1e-9


# ============================================================================ helpers
def ramp(x: Optional[float], lo: float, hi: float) -> float:
    """Linear ramp: 0 at ``lo`` and below, 1 at ``hi`` and above, linear between; None → 0."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.0
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return clamp01((float(x) - lo) / (hi - lo))


def geo_mean(parts: Sequence[float]) -> float:
    """Geometric mean of scores in [0, 1] (one zero component → 0)."""
    if not parts:
        return 0.0
    p = 1.0
    for x in parts:
        p *= max(0.0, float(x))
    return p ** (1.0 / len(parts))


def _cv(xs: Sequence[float]) -> Optional[float]:
    """Coefficient of variation (sample std / mean); None with < 2 points or mean 0."""
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    if abs(m) < _EPS:
        return None
    return statistics.stdev(xs) / abs(m)


def _median(xs: Sequence[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def levels_of(ms: MarketState, side: str) -> List[Tuple[float, float]]:
    rows = ms.bids if side == "bid" else ms.asks
    return [(float(r[0]), float(r[1])) for r in (rows or [])]


def best_of(ms: MarketState, side: str) -> Tuple[Optional[float], Optional[float]]:
    """(best price, touch qty) of a side from the L1 fields, else the first displayed level."""
    p = ms.best_bid if side == "bid" else ms.best_ask
    q = ms.bid_qty1 if side == "bid" else ms.ask_qty1
    lv = levels_of(ms, side)
    if p is None and lv:
        p = lv[0][0]
    if q is None and lv:
        q = lv[0][1]
    return p, q


def topk_depth(ms: MarketState, side: str, k: int = TOP_K) -> Optional[float]:
    lv = levels_of(ms, side)
    if lv:
        return float(sum(q for _, q in lv[:k]))
    _, q = best_of(ms, side)
    return q


def visible_depth(ms: MarketState, side: str) -> Optional[float]:
    v = ms.visible_bid_liq if side == "bid" else ms.visible_ask_liq
    if v is not None:
        return float(v)
    lv = levels_of(ms, side)
    if lv:
        return float(sum(q for _, q in lv))
    _, q = best_of(ms, side)
    return q


def mid_of(ms: MarketState) -> Optional[float]:
    if ms.mid is not None:
        return float(ms.mid)
    b, _ = best_of(ms, "bid")
    a, _ = best_of(ms, "ask")
    if b is not None and a is not None:
        return (b + a) / 2.0
    return None


def tick_of(ms: MarketState) -> Optional[float]:
    """Tick size: the state's ``tick_size``, else the smallest positive gap between displayed prices."""
    if ms.tick_size:
        return float(ms.tick_size)
    gaps: List[float] = []
    for side in ("bid", "ask"):
        ps = [p for p, _ in levels_of(ms, side)]
        gaps += [abs(a - b) for a, b in zip(ps, ps[1:]) if abs(a - b) > _EPS]
    return min(gaps) if gaps else None


def spread_ticks_of(ms: MarketState, tick: Optional[float]) -> Optional[float]:
    if ms.spread_ticks is not None:
        return float(ms.spread_ticks)
    if ms.spread is not None and tick:
        return float(ms.spread) / tick
    b, _ = best_of(ms, "bid")
    a, _ = best_of(ms, "ask")
    if b is not None and a is not None and tick:
        return (a - b) / tick
    return None


class Frame:
    """The causal window: history states at or before ``ms`` plus ``ms`` itself."""

    def __init__(self, ms: MarketState, hist: Optional[StateHistory]) -> None:
        self.ms = ms
        buf = list(hist.buf) if hist is not None else []
        if buf and buf[-1] is ms:
            buf = buf[:-1]
        self.past: List[MarketState] = [s for s in buf if s.t <= ms.t]
        self.tick = tick_of(ms)

    def states(self, seconds: float) -> List[MarketState]:
        cutoff = self.ms.t - timedelta(seconds=seconds)
        return [s for s in self.past if s.t >= cutoff] + [self.ms]

    def at_or_before(self, t: datetime) -> Optional[MarketState]:
        if self.ms.t <= t:
            return self.ms
        for s in reversed(self.past):
            if s.t <= t:
                return s
        return None

    def series(self, fn, seconds: float) -> List[Tuple[datetime, float]]:
        """[(t, fn(state))] over the window for states where fn is not None."""
        out = []
        for s in self.states(seconds):
            v = fn(s)
            if v is not None:
                out.append((s.t, float(v)))
        return out

    def span_s(self, seconds: float) -> float:
        st = self.states(seconds)
        return (st[-1].t - st[0].t).total_seconds() if len(st) > 1 else 0.0

    # ---- tape helpers -----------------------------------------------------------
    def tape_rows(self, seconds: float) -> List[Dict[str, Any]]:
        """Distinct tape intervals inside the window: one row per change of the tape identity
        (tape clock when carried, else (trade_count, trade_volume, interval_volume))."""
        rows: List[Dict[str, Any]] = []
        last_key: Any = object()
        for s in self.states(seconds):
            if s.interval_volume is None and s.trade_volume is None:
                continue
            tp = s.session_state.get("tape") if isinstance(s.session_state, dict) else None
            clock = tp.get("tape_clock") if isinstance(tp, dict) else None
            key = clock if clock is not None else (s.trade_count, s.trade_volume, s.interval_volume, s.interval_trades)
            if key == last_key:
                continue
            last_key = key
            rows.append({"t": s.t, "volume": s.interval_volume, "trades": s.interval_trades,
                         "direction": s.trade_flow_direction, "vwap": s.interval_vwap, "cum_volume": s.trade_volume})
        return rows

    def volume_over(self, seconds: float) -> Optional[float]:
        """Traded volume inside the window: Δ cumulative day volume when both ends carry it,
        else the sum of distinct interval volumes; None when the tape is not observable."""
        st = self.states(seconds)
        cur = st[-1].trade_volume
        first = None
        for s in st:
            if s.trade_volume is not None:
                first = s.trade_volume
                break
        if cur is not None and first is not None and cur >= first:
            # the first state's own interval belongs to the window when it starts inside it
            return float(cur - first)
        rows = [r for r in self.tape_rows(seconds) if r["volume"] is not None]
        if rows:
            return float(sum(r["volume"] for r in rows))
        return None

    def signed_volume_over(self, seconds: float) -> Tuple[Optional[float], Optional[float]]:
        """(Σ direction × volume, Σ volume of classified rows) over the window; None when no classified row."""
        rows = [r for r in self.tape_rows(seconds) if r["volume"] and r["direction"] is not None]
        if not rows:
            return None, None
        return (float(sum(r["direction"] * r["volume"] for r in rows)), float(sum(r["volume"] for r in rows)))

    def mid_change_ticks(self, seconds: float) -> Optional[float]:
        pts = self.series(mid_of, seconds)
        if len(pts) < 2 or not self.tick:
            return None
        return (pts[-1][1] - pts[0][1]) / self.tick


def baselines(fr: Frame, response_s: float = 120.0) -> Dict[str, Optional[float]]:
    """Simple baselines at this instant (state fields when carried, else computed from the displayed
    book / the window): imb_l1, imb_topk, imb_weighted, depth_ratio, price_only_response,
    volume_only_response."""
    ms = fr.ms
    bids, asks = levels_of(ms, "bid"), levels_of(ms, "ask")
    bq1 = ms.bid_qty1 if ms.bid_qty1 is not None else (bids[0][1] if bids else None)
    aq1 = ms.ask_qty1 if ms.ask_qty1 is not None else (asks[0][1] if asks else None)

    def imb(b: Optional[float], a: Optional[float]) -> Optional[float]:
        if b is None or a is None or b + a <= 0:
            return None
        return (b - a) / (b + a)

    out: Dict[str, Optional[float]] = {}
    out["imb_l1"] = ms.imb_l1 if ms.imb_l1 is not None else imb(bq1, aq1)
    out["imb_topk"] = ms.imb_topk if ms.imb_topk is not None else imb(topk_depth(ms, "bid"), topk_depth(ms, "ask"))
    if ms.imb_weighted is not None:
        out["imb_weighted"] = ms.imb_weighted
    else:
        tick = fr.tick
        bb, aa = (bids[0][0] if bids else None), (asks[0][0] if asks else None)
        if tick and bb is not None and aa is not None:
            wb = sum(q / (1.0 + (bb - p) / tick) for p, q in bids)
            wa = sum(q / (1.0 + (p - aa) / tick) for p, q in asks)
            out["imb_weighted"] = imb(wb, wa)
        else:
            out["imb_weighted"] = None
    vb, va = visible_depth(ms, "bid"), visible_depth(ms, "ask")
    out["depth_ratio"] = ms.depth_ratio if ms.depth_ratio is not None else (
        vb / (vb + va) if (vb is not None and va is not None and vb + va > 0) else None)
    out["price_only_response"] = ms.price_only_response if ms.price_only_response is not None \
        else fr.mid_change_ticks(response_s)
    out["volume_only_response"] = ms.volume_only_response if ms.volume_only_response is not None \
        else fr.volume_over(response_s)
    return out


def missing_reading(mech: "Mechanism", missing: Sequence[str], base: Dict[str, Any],
                    extra: Optional[Dict[str, Any]] = None) -> MechanismReading:
    ev: Dict[str, Any] = {"missing": list(missing), "direction": 0}
    if extra:
        ev.update(extra)
    return MechanismReading(name=mech.name, family=mech.family, score=0.0, state="inactive",
                            evidence=ev, baseline=base, note="missing inputs: " + ", ".join(missing))


def queue_counters(ms: MarketState) -> Optional[Dict[str, Any]]:
    q = ms.session_state.get("queue") if isinstance(ms.session_state, dict) else None
    return q if isinstance(q, dict) and ("bid" in q or "ask" in q) else None


def _touch_series(fr: Frame, side: str, seconds: float) -> List[Tuple[datetime, float, float, Optional[int]]]:
    """[(t, best price, touch qty, orders)] over the window (states with a displayed touch on that side)."""
    out = []
    for s in fr.states(seconds):
        p, q = best_of(s, side)
        if p is None or q is None:
            continue
        orders = None
        oc = s.bid_orders if side == "bid" else s.ask_orders
        if oc:
            orders = oc[0]
        out.append((s.t, float(p), float(q), orders))
    return out


def _step_consistency(vals: Sequence[float], down: bool) -> Optional[float]:
    """Share of the non-zero consecutive steps going in the stated direction."""
    steps = [b - a for a, b in zip(vals, vals[1:]) if abs(b - a) > _EPS]
    if not steps:
        return None
    n = sum(1 for d in steps if (d < 0) == down)
    return n / len(steps)


# ============================================================================ #1
@register
class QueuePullStack(Mechanism):
    """#1 Queue pull / stack.

    Rule: per side, take the trailing run of states whose best price equals the
    current best (window ≤ 120 s); q0 = touch qty at the start of that run, q1 =
    now.  fall = max(0, q0 − q1), rise = max(0, q1 − q0).  Traded volume over the
    same span (Δ cumulative volume, else Σ interval volumes) bounds how much of
    the fall can be consumption: pull_qty = fall − min(fall, volume);
    pull_share = pull_qty / q0; stack_share = rise / q1.  When the queue engine's
    120-s pull counter is present (``pulled_qty_120s``, tape-budgeted) its
    share pulled / (touch qty + pulled) is a second pull estimate and the larger
    one is used (which one is named in the evidence); the engine's
    ``added_qty_120s`` is evidence only because it also counts levels that
    merely appeared.  The winning (side, kind) sets
    score = ramp(share, 0.15 → 0.75) × (0.6 + 0.4 × consistency), consistency =
    share of the non-zero qty steps in the run going the same way.  With no tape
    at all a fall cannot be split into trades and pulls: the pull estimate is
    marked ``unverified`` and damped by 0.75.
    direction: bid pull −1, bid stack +1, ask pull +1, ask stack −1.
    """

    name = "queue_pull_stack"
    family = "queue"
    requires = ("best_bid", "best_ask", "bid_qty1", "ask_qty1", "trade_volume", "bids", "asks")
    window_s = 120.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        counters = queue_counters(ms)
        cands: List[Dict[str, Any]] = []
        sides_ev: Dict[str, Any] = {}
        tape_seen = False
        for side in ("bid", "ask"):
            ser = _touch_series(fr, side, self.window_s)
            if len(ser) < 2:
                sides_ev[side] = {"points": len(ser)}
                continue
            p1, q1 = ser[-1][1], ser[-1][2]
            run = [ser[-1]]
            for rec in reversed(ser[:-1]):
                if abs(rec[1] - p1) < _EPS:
                    run.append(rec)
                else:
                    break
            run.reverse()
            if len(run) < 2:
                sides_ev[side] = {"points": len(ser), "same_price_points": 1, "touch_price": p1, "touch_qty": q1}
                continue
            t0, q0 = run[0][0], run[0][2]
            span = (ms.t - t0).total_seconds()
            fall, rise = max(0.0, q0 - q1), max(0.0, q1 - q0)
            vol = fr.volume_over(span) if span > 0 else None
            if vol is not None:
                tape_seen = True
            traded_bound = min(fall, vol) if vol is not None else None
            pull_qty = fall - (traded_bound or 0.0)
            pull_share = safe_div(pull_qty, q0) if q0 > 0 else None
            stack_share = safe_div(rise, q1) if q1 > 0 else None
            qs = [r[2] for r in run]
            cons_down = _step_consistency(qs, down=True)
            cons_up = _step_consistency(qs, down=False)
            o0, o1 = run[0][3], run[-1][3]
            d_orders = (o1 - o0) if (o0 is not None and o1 is not None) else None
            c_pull = None
            c_added = None
            if counters and isinstance(counters.get(side), dict):
                c = counters[side]
                pq, tq = c.get("pulled_qty_120s"), c.get("touch_qty")
                c_added = c.get("added_qty_120s")          # evidence only: it also counts appearing levels
                if pq is not None and tq is not None and tq + pq > 0:
                    c_pull = pq / (tq + pq)
            sides_ev[side] = {"touch_price": p1, "touch_qty_start": q0, "touch_qty_now": q1, "span_s": span,
                              "fall": fall, "rise": rise, "volume_in_span": vol, "pull_qty": pull_qty,
                              "pull_share": pull_share, "stack_share": stack_share,
                              "consistency_down": cons_down, "consistency_up": cons_up,
                              "orders_change": d_orders, "same_price_points": len(run),
                              "counter_pull_share": c_pull, "counter_added_qty_120s": c_added}
            for kind, share, cons, src in (("pull", pull_share, cons_down, "series"),
                                            ("stack", stack_share, cons_up, "series"),
                                            ("pull", c_pull, cons_down, "counters")):
                if share is None:
                    continue
                cands.append({"side": side, "kind": kind, "share": share, "consistency": cons, "source": src,
                              "verified": (vol is not None) or kind == "stack" or src == "counters"})
        if not cands:
            miss = [k for k in ("best_bid", "bid_qty1", "best_ask", "ask_qty1")
                    if getattr(ms, k) is None] or ["touch series (< 2 same-price states)"]
            return missing_reading(self, miss, base, {"sides": sides_ev})
        best = max(cands, key=lambda c: c["share"])
        cons = best["consistency"] if best["consistency"] is not None else 0.5
        score = ramp(best["share"], 0.15, 0.75) * (0.6 + 0.4 * cons)
        unverified: List[str] = []
        if not best["verified"]:
            score *= 0.75
            unverified.append("trade_volume")
        direction = 0
        if best["side"] == "bid":
            direction = -1 if best["kind"] == "pull" else 1
        else:
            direction = 1 if best["kind"] == "pull" else -1
        ev: Dict[str, Any] = {"side": best["side"], "kind": best["kind"], "share": best["share"],
                              "consistency": cons, "estimate_source": best["source"], "tape_observed": tape_seen,
                              "direction": direction, "sides": sides_ev, "window_s": self.window_s}
        if unverified:
            ev["unverified"] = unverified
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"{best['kind']} on {best['side']} share={best['share']:.2f}")


# ============================================================================ #2
@register
class QuoteRefreshChurn(Mechanism):
    """#2 Quote refresh churn.

    Rule: over the last 60 s, count per side the consecutive states whose best
    (price, qty) differs from the previous one; rate = (bid changes + ask
    changes) / span in minutes (span ≥ 10 s required).  drift = max over sides
    of |best(now) − best(window start)| in ticks; qty_net = max over sides of
    |q(now) − q(start)| / max(q(now), q(start)).  score = ramp(rate, 2 → 10 per
    min) × 1 / (1 + drift) × (0.5 + 0.5 × (1 − qty_net)): many refreshes that
    leave price and size where they were.  With < 3 window points the queue
    engine's ``best_changes_per_min`` / ``net_drift_ticks`` counters are used
    when present.  No price direction (direction 0).
    """

    name = "quote_refresh_churn"
    family = "queue"
    requires = ("best_bid", "best_ask", "bid_qty1", "ask_qty1", "tick_size")
    window_s = 60.0
    min_span_s = 10.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        counters = queue_counters(ms)
        changes = 0
        drift = None
        qty_net = None
        pts_total = 0
        sides_ev: Dict[str, Any] = {}
        span = fr.span_s(self.window_s)
        for side in ("bid", "ask"):
            ser = _touch_series(fr, side, self.window_s)
            pts_total += len(ser)
            if len(ser) < 2:
                continue
            n = sum(1 for a, b in zip(ser, ser[1:]) if abs(a[1] - b[1]) > _EPS or abs(a[2] - b[2]) > _EPS)
            changes += n
            d = abs(ser[-1][1] - ser[0][1]) / tick if tick else None
            qmax = max(ser[-1][2], ser[0][2])
            qn = abs(ser[-1][2] - ser[0][2]) / qmax if qmax > 0 else 0.0
            drift = d if drift is None else (max(drift, d) if d is not None else drift)
            qty_net = qn if qty_net is None else max(qty_net, qn)
            sides_ev[side] = {"changes": n, "drift_ticks": d, "qty_net_share": qn, "points": len(ser)}
        rate = (changes / (span / 60.0)) if span >= self.min_span_s else None
        source = "series"
        if (rate is None or pts_total < 3) and counters:
            rates = [counters[s].get("best_changes_per_min") for s in ("bid", "ask")
                     if isinstance(counters.get(s), dict) and counters[s].get("best_changes_per_min") is not None]
            drifts = [abs(counters[s].get("net_drift_ticks")) for s in ("bid", "ask")
                      if isinstance(counters.get(s), dict) and counters[s].get("net_drift_ticks") is not None]
            if rates:
                rate, source = float(sum(rates)), "counters"
                drift = max(drifts) if drifts else drift
        if rate is None:
            miss = ["touch series (span < %.0f s)" % self.min_span_s] if pts_total >= 2 else \
                [k for k in ("best_bid", "bid_qty1", "best_ask", "ask_qty1") if getattr(ms, k) is None] or ["history"]
            return missing_reading(self, miss, base, {"span_s": span, "points": pts_total})
        if drift is None:
            miss = ["tick_size"] if sides_ev else \
                ([k for k in ("best_bid", "bid_qty1", "best_ask", "ask_qty1") if getattr(ms, k) is None] or ["history"])
            return missing_reading(self, miss, base, {"rate_per_min": rate})
        qn = qty_net if qty_net is not None else 0.0
        score = ramp(rate, 2.0, 10.0) * (1.0 / (1.0 + drift)) * (0.5 + 0.5 * (1.0 - min(1.0, qn)))
        ev = {"rate_per_min": rate, "changes": changes, "span_s": span, "drift_ticks": drift, "qty_net_share": qn,
              "estimate_source": source, "sides": sides_ev, "direction": 0, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"{rate:.1f} changes/min, drift {drift:.1f} ticks")


# ============================================================================ #30
def _level_diffs(prev: MarketState, cur: MarketState, side: str, tick: float, away_ticks: float = 2.0
                 ) -> Dict[str, Any]:
    """Per-price qty differences between two displayed books of one side, split into
    additions / removals at the touch (< away_ticks from the best) and away from it."""
    pm = {p: q for p, q in levels_of(prev, side)}
    cm = {p: q for p, q in levels_of(cur, side)}
    pb, _ = best_of(prev, side)
    cb, _ = best_of(cur, side)
    out = {"added_away": 0.0, "added_touch": 0.0, "cancel_away": 0.0, "removed_touch": 0.0,
           "appear": [], "cancel": []}

    def dist(p: float, best: Optional[float]) -> Optional[float]:
        if best is None:
            return None
        return (best - p) / tick if side == "bid" else (p - best) / tick

    for p in set(pm) | set(cm):
        q0, q1 = pm.get(p, 0.0), cm.get(p, 0.0)
        d = q1 - q0
        if abs(d) < _EPS:
            continue
        if d > 0:
            dc = dist(p, cb)
            if dc is not None and dc >= away_ticks:
                out["added_away"] += d
                if q0 <= _EPS:
                    out["appear"].append(p)
            else:
                out["added_touch"] += d
        else:
            dp = dist(p, pb)
            dc = dist(p, cb)
            still_away = dc is not None and dc >= away_ticks       # the touch never reached the level
            if dp is not None and dp >= away_ticks and still_away:
                out["cancel_away"] += -d
                if q1 <= _EPS:
                    out["cancel"].append(p)
            else:
                out["removed_touch"] += -d
    return out


@register
class LayeringLike(Mechanism):
    """#30 Displayed-liquidity instability / layering-like.

    Rule: over 120 s, diff consecutive displayed books per side and per price.
    Qty added at a price ≥ 2 ticks behind the touch is *added_away*; qty removed
    from a price that was and still is ≥ 2 ticks behind the touch (the best
    never reached it, so trades cannot have consumed it) is *cancel_away*;
    everything else is touch activity.  cycles = Σ over prices of
    min(appearances, cancellations) of a whole level away from the touch;
    cancel_away_share = cancel_away / (cancel_away + removed_touch);
    away_churn_share = (added_away + cancel_away) / mean visible depth of the
    side.  Per side score = ramp(cycles, 0.5 → 3.5) × (0.4 + 0.6 ×
    ramp(cancel_away_share, 0.3 → 0.9)) × (0.5 + 0.5 × ramp(away_churn_share,
    0.1 → 0.6)); the larger side wins.  direction: an unstable bid layer means
    the displayed support is unreliable → −1; unstable ask layer → +1.
    """

    name = "layering_like"
    family = "queue"
    requires = ("bids", "asks", "tick_size")
    window_s = 120.0
    away_ticks = 2.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        states = [s for s in fr.states(self.window_s) if levels_of(s, "bid") or levels_of(s, "ask")]
        if len(states) < 2:
            return missing_reading(self, ["bids/asks history (< 2 displayed books in window)"], base,
                                   {"books_in_window": len(states)})
        best: Optional[Dict[str, Any]] = None
        sides_ev: Dict[str, Any] = {}
        for side in ("bid", "ask"):
            agg = {"added_away": 0.0, "added_touch": 0.0, "cancel_away": 0.0, "removed_touch": 0.0}
            appear: Dict[float, int] = {}
            cancel: Dict[float, int] = {}
            for a, b in zip(states, states[1:]):
                d = _level_diffs(a, b, side, tick, self.away_ticks)
                for k in agg:
                    agg[k] += d[k]
                for p in d["appear"]:
                    appear[p] = appear.get(p, 0) + 1
                for p in d["cancel"]:
                    cancel[p] = cancel.get(p, 0) + 1
            cycles = sum(min(appear.get(p, 0), cancel.get(p, 0)) for p in set(appear) | set(cancel))
            vis = [visible_depth(s, side) for s in states]
            vis = [v for v in vis if v is not None and v > 0]
            mean_vis = (sum(vis) / len(vis)) if vis else None
            denom = agg["cancel_away"] + agg["removed_touch"]
            cancel_share = (agg["cancel_away"] / denom) if denom > 0 else None
            churn_share = ((agg["added_away"] + agg["cancel_away"]) / mean_vis) if mean_vis else None
            s_side = ramp(cycles, 0.5, 3.5) * (0.4 + 0.6 * ramp(cancel_share, 0.3, 0.9)) * \
                (0.5 + 0.5 * ramp(churn_share, 0.1, 0.6))
            rec = {"cycles": cycles, "cancel_away_share": cancel_share, "away_churn_share": churn_share,
                   "mean_visible": mean_vis, "prices_cycled": sorted(p for p in set(appear) & set(cancel)),
                   "score": s_side, **agg}
            sides_ev[side] = rec
            if best is None or s_side > best["score"]:
                best = dict(rec, side=side)
        assert best is not None
        direction = -1 if best["side"] == "bid" else 1
        if best["score"] <= 0.0:
            direction = 0
        ev = {"side": best["side"], "cycles": best["cycles"], "cancel_away_share": best["cancel_away_share"],
              "away_churn_share": best["away_churn_share"], "added_away": best["added_away"],
              "cancel_away": best["cancel_away"], "removed_touch": best["removed_touch"],
              "books_in_window": len(states), "direction": direction, "sides": sides_ev, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(best["score"]), "inactive", ev, base,
                                note=f"{best['cycles']} appear/cancel cycles on {best['side']}")


# ============================================================================ #31
@register
class HiddenReplenishment(Mechanism):
    """#31 Repeated hidden-liquidity-like replenishment.

    Rule: over 300 s walk the touch (price, qty) per side.  A reference size is
    the qty when the queue is full; at the same price a fall to ≤ 70 % of the
    reference opens a consumption; a later rise at the same price back to
    ≥ 75 % of the reference closes it as a *refill* (the refilled qty becomes
    the new reference).  A price change resets the reference.  cycles = number
    of refills; similarity = 1 − min(1, cv(refilled sizes) / 0.5) (with one
    refill: 1 − min(1, |refill/reference − 1| / 0.5)); traded_frac =
    min(1, traded volume in the window / Σ consumed qty) — consumption has to
    be trade-backed, so the tape is required.  score = ramp(cycles, 0.5 → 3.5)
    × (0.5 + 0.5 × similarity) × (0.25 + 0.75 × traded_frac) — refills of a
    queue that was pulled rather than traded keep at most a quarter of the
    score; the larger side wins.  direction: bid refills (hidden buyer) +1,
    ask refills −1.
    """

    name = "hidden_replenishment"
    family = "queue"
    requires = ("best_bid", "best_ask", "bid_qty1", "ask_qty1", "trade_volume")
    window_s = 300.0
    consume_share = 0.70
    refill_share = 0.75

    def _cycles(self, ser: List[Tuple[datetime, float, float, Optional[int]]]) -> Dict[str, Any]:
        ref_p: Optional[float] = None
        ref_q: Optional[float] = None
        low: Optional[float] = None
        refills: List[float] = []
        refill_ratio: List[float] = []
        consumed = 0.0
        times: List[datetime] = []
        for t, p, q, _ in ser:
            if ref_p is None or abs(p - ref_p) > _EPS:
                ref_p, ref_q, low = p, q, None
                continue
            assert ref_q is not None
            if low is None:
                if ref_q > 0 and q <= self.consume_share * ref_q:
                    low = q
                elif q > ref_q:
                    ref_q = q                                  # queue grew without consumption: new reference
            else:
                low = min(low, q)
                if ref_q > 0 and q >= self.refill_share * ref_q:
                    refills.append(q)
                    refill_ratio.append(q / ref_q)
                    consumed += ref_q - low
                    times.append(t)
                    ref_q, low = q, None
        sim = None
        if len(refills) >= 2:
            cv = _cv(refills)
            sim = 1.0 - min(1.0, (cv if cv is not None else 0.0) / 0.5)
        elif len(refills) == 1:
            sim = 1.0 - min(1.0, abs(refill_ratio[0] - 1.0) / 0.5)
        return {"cycles": len(refills), "refill_sizes": refills, "similarity": sim, "consumed_qty": consumed,
                "refill_times": [t.isoformat() for t in times]}

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        vol = fr.volume_over(self.window_s)
        counters = queue_counters(ms)
        sides_ev: Dict[str, Any] = {}
        best: Optional[Dict[str, Any]] = None
        n_pts = 0
        for side in ("bid", "ask"):
            ser = _touch_series(fr, side, self.window_s)
            n_pts += len(ser)
            if len(ser) < 3:
                sides_ev[side] = {"points": len(ser)}
                continue
            rec = self._cycles(ser)
            if counters and isinstance(counters.get(side), dict):
                c = counters[side]
                rec["engine_depletion_episodes"] = c.get("depletion_episodes")
                rec["engine_replenished"] = c.get("replenished")
                rec["engine_mean_time_to_replenish_s"] = c.get("mean_time_to_replenish_s")
            traded_frac = None
            if vol is not None and rec["consumed_qty"] > 0:
                traded_frac = min(1.0, vol / rec["consumed_qty"])
            rec["traded_frac"] = traded_frac
            sim = rec["similarity"] if rec["similarity"] is not None else 0.0
            s_side = ramp(rec["cycles"], 0.5, 3.5) * (0.5 + 0.5 * sim) * (0.25 + 0.75 * (traded_frac or 0.0))
            rec["score"] = s_side
            sides_ev[side] = rec
            if best is None or s_side > best["score"]:
                best = dict(rec, side=side)
        if best is None:
            miss = [k for k in ("best_bid", "bid_qty1", "best_ask", "ask_qty1") if getattr(ms, k) is None] \
                or ["touch series (< 3 states in window)"]
            return missing_reading(self, miss, base, {"points": n_pts})
        if vol is None:
            return missing_reading(self, ["trade_volume"], base, {"sides": sides_ev, "side": best["side"],
                                                                  "cycles": best["cycles"]})
        direction = 0 if best["score"] <= 0 else (1 if best["side"] == "bid" else -1)
        ev = {"side": best["side"], "cycles": best["cycles"], "refill_sizes": best["refill_sizes"],
              "similarity": best["similarity"], "consumed_qty": best["consumed_qty"], "traded_frac": best["traded_frac"],
              "volume_in_window": vol, "direction": direction, "sides": sides_ev, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(best["score"]), "inactive", ev, base,
                                note=f"{best['cycles']} refills on {best['side']}")


# ============================================================================ #33
def _print_sizes(fr: Frame, seconds: float) -> Tuple[List[Dict[str, Any]], str]:
    """Print-size events in the window, from the best available representation:
    distinct ``last_print`` records → single-trade tape intervals → same-price touch drops
    (when the window carries traded volume).  Returns (events, source)."""
    seen = set()
    ev: List[Dict[str, Any]] = []
    for s in fr.states(seconds):
        lp = s.last_print
        if not isinstance(lp, dict) or lp.get("qty") is None:
            continue
        key = (lp.get("t"), lp.get("price"), lp.get("qty"), lp.get("trade_id"))
        if key in seen:
            continue
        seen.add(key)
        t = lp.get("t")
        try:
            tt = datetime.fromisoformat(str(t).replace("Z", "+00:00")) if t else s.t
        except ValueError:
            tt = s.t
        if tt.tzinfo is None:
            tt = s.t
        ev.append({"t": tt, "qty": float(lp["qty"]), "direction": lp.get("direction"), "price": lp.get("price")})
    if ev:
        return ev, "prints"
    rows = [r for r in fr.tape_rows(seconds) if r["trades"] == 1 and r["volume"]]
    if rows:
        return [{"t": r["t"], "qty": float(r["volume"]), "direction": r["direction"], "price": r["vwap"]}
                for r in rows], "single_trade_intervals"
    vol = fr.volume_over(seconds)
    if vol is not None and vol > 0:
        out = []
        for side in ("bid", "ask"):
            ser = _touch_series(fr, side, seconds)
            for a, b in zip(ser, ser[1:]):
                if abs(a[1] - b[1]) < _EPS and b[2] < a[2] - _EPS:
                    out.append({"t": b[0], "qty": a[2] - b[2], "direction": (-1.0 if side == "bid" else 1.0),
                                "price": b[1]})
        out.sort(key=lambda e: e["t"])
        if out:
            return out, "touch_consumption"
    return [], "none"


@register
class OrderSplitting(Mechanism):
    """#33 Order splitting.

    Rule: sizes of the prints in the last 300 s (distinct ``last_print`` records;
    else single-trade tape intervals; else same-price touch-qty drops while the
    window carries traded volume).  Sizes are clustered with a 5 % relative
    tolerance around the smallest member; the largest cluster is the modal size:
    n_mode repeats, mode_share = n_mode / n.  regularity = 1 − min(1, cv of the
    inter-arrival times of the modal prints) (0.5 when fewer than three).
    score = ramp(n_mode, 1.5 → 5.5) × ramp(mode_share, 0.3 → 0.8) × (0.6 + 0.4
    × regularity), needing ≥ 3 prints.  direction = sign of the mean carried /
    inferred direction of the modal prints (0 when unknown).
    """

    name = "order_splitting"
    family = "queue"
    requires = ("last_print", "interval_volume", "interval_trades", "trade_volume", "bid_qty1", "ask_qty1")
    window_s = 300.0
    tol = 0.05

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        events, source = _print_sizes(fr, self.window_s)
        if len(events) < 3:
            miss = ["last_print"] if ms.last_print is None else []
            if ms.interval_volume is None:
                miss.append("interval_volume")
            if not miss:
                miss = ["prints (< 3 in window)"]
            return missing_reading(self, miss, base, {"prints": len(events), "size_source": source})
        sizes = sorted(e["qty"] for e in events)
        clusters: List[List[float]] = []
        for s in sizes:
            if clusters and s <= clusters[-1][0] * (1.0 + self.tol) + _EPS:
                clusters[-1].append(s)
            else:
                clusters.append([s])
        modal = max(clusters, key=len)
        lo, hi = modal[0], modal[-1]
        modal_events = [e for e in events if lo - _EPS <= e["qty"] <= hi + _EPS]
        n_mode, n = len(modal_events), len(events)
        mode_share = n_mode / n
        ts = sorted(e["t"] for e in modal_events)
        gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:]) if (b - a).total_seconds() > 0]
        cv_dt = _cv(gaps) if len(gaps) >= 2 else None
        regularity = (1.0 - min(1.0, cv_dt)) if cv_dt is not None else 0.5
        dirs = [e["direction"] for e in modal_events if e["direction"] is not None]
        mean_dir = (sum(dirs) / len(dirs)) if dirs else None
        score = ramp(n_mode, 1.5, 5.5) * ramp(mode_share, 0.3, 0.8) * (0.6 + 0.4 * regularity)
        direction = sign(mean_dir) if score > 0 else 0
        ev = {"prints": n, "modal_size": sum(modal) / len(modal), "modal_repeats": n_mode, "mode_share": mode_share,
              "cadence_cv": cv_dt, "regularity": regularity, "mean_gap_s": (sum(gaps) / len(gaps)) if gaps else None,
              "mean_direction": mean_dir, "size_source": source, "direction": direction, "window_s": self.window_s,
              "sizes": sizes[:50]}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"{n_mode}/{n} prints of ~{ev['modal_size']:.0f} ({source})")
