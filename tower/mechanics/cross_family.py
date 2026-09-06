"""cross_family — cross-symbol mechanisms (MECHANISMS.md #17, #18).

Both mechanisms read the ``cross`` / ``sector`` context the global
``tower.cross.CrossEngine`` writes on every state *before* the mechanics run
(``ms.cross``, ``ms.sector`` — see the contract in ``tower/cross.py``), plus
the symbol's own causal window (``queue_family.Frame`` = history states at or
before the current one + the current one).  Scores are continuous functions of
the measured quantities (linear ramps blended with fixed weights, missing
components dropped with the weights renormalised and named in
``evidence["unverified"]``); nothing is a constant.  Whatever the context does
not deliver is ``None`` and, when the mechanism needs it, the reading is score
0 with ``evidence["missing"]`` naming the inputs.  ``evidence["direction"]``
∈ {+1, −1, 0}; ``evidence["inputs"]`` carries the raw context values the
score was computed from; ``baseline`` is ``queue_family.baselines``.

Rules (window lengths are class attributes; restated in each docstring):

  basket_rebalance   inside the last 30 min of CONTINUOUS (session end from
                     ``seeing.clock`` on the state's own timestamp) and during
                     POST_CLOSE: the symbol's own volume rate over the last
                     300 s against its rate over the preceding 1800 s, blended
                     with the basket-wide simultaneity the cross engine
                     measures (share of symbols whose visible liquidity changed
                     ≥ 20 % over 60 s — ``simultaneous_liquidity_change`` —
                     and share whose |price velocity| sits in its own top
                     decile — ``synchronized_expansion``) and the sector
                     basket synchronisation (``basket_sync``).
  cross_lead_lag     leaders / laggers from the cross engine's lagged
                     correlation of 10-s mid returns: strength of the best
                     pair, number of qualifying pairs, persistence of the
                     relation over the last 120 s; a leader implies the symbol
                     catches up with its peers (direction from the peer /
                     market 60-s return gap).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..state import MarketState
from ..windows import sign
from .base import MechanismReading, StateHistory, register
from .divergence_family import DirectedMechanism, _reading, combined_pressure_of
from .queue_family import Frame, baselines, mid_of, missing_reading, ramp
from .session_family import minutes_to_close

_EPS = 1e-9
FAMILY = "cross"


# ============================================================================ helpers
def weighted_blend(parts: Sequence[Tuple[str, float, Optional[float]]]) -> Tuple[Optional[float], List[str]]:
    """Weighted mean of the components that are observable (``(name, weight, value)``;
    value None = not observable).  Returns (blend, names of the dropped components);
    blend None when nothing is observable.  The weights of the present components
    are renormalised so a missing input never counts as zero evidence."""
    used = [(w, float(v)) for _, w, v in parts if v is not None]
    dropped = [n for n, _, v in parts if v is None]
    if not used:
        return None, dropped
    return sum(w * v for w, v in used) / sum(w for w, _ in used), dropped


def volume_between(fr: Frame, t0: datetime, t1: datetime) -> Tuple[Optional[float], int, float]:
    """(traded volume, states, span s) over the states with t0 ≤ t < t1: Δ cumulative day
    volume when the first and last such state carry it, else the sum of the distinct
    interval volumes inside; volume None when the tape is not observable there."""
    st = [s for s in fr.past if t0 <= s.t < t1]
    if fr.ms.t < t1 and fr.ms.t >= t0:
        st.append(fr.ms)
    if not st:
        return None, 0, 0.0
    span = (st[-1].t - st[0].t).total_seconds()
    cum = [s.trade_volume for s in st if s.trade_volume is not None]
    if len(cum) >= 2 and cum[-1] >= cum[0]:
        return float(cum[-1] - cum[0]), len(st), span
    rows, last_key = [], object()
    for s in st:
        if s.interval_volume is None:
            continue
        key = (s.trade_count, s.trade_volume, s.interval_volume, s.interval_trades)
        if key == last_key:
            continue
        last_key = key
        rows.append(float(s.interval_volume))
    if rows:
        return float(sum(rows)), len(st), span
    return None, len(st), span


def _share_of(d: Any) -> Optional[float]:
    if not isinstance(d, dict):
        return None
    v = d.get("share")
    return None if v is None else float(v)


def _pairs(v: Any) -> Optional[List[Tuple[str, float, float]]]:
    """Normalise a leaders / laggers list (tuples or JSON lists) to [(symbol, lag_s, corr)]."""
    if v is None:
        return None
    out: List[Tuple[str, float, float]] = []
    for row in v:
        try:
            sym, lag, corr = row[0], float(row[1]), float(row[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if math.isnan(corr):
            continue
        out.append((str(sym), lag, corr))
    return out


# ============================================================================ #17
@register
class BasketRebalance(DirectedMechanism):
    """#17 Index / basket rebalance footprint.

    Rule: requires ``session_phase`` ∈ {CONTINUOUS, POST_CLOSE}; any other
    phase is score 0 with ``in_close_window`` False (the phase is reported, not
    "missing").  window = 1 in POST_CLOSE, else ramp(30 − minutes to close,
    0 → 5) (0 more than 30 min before the close, 1 from 25 min before).
    Own burst: rate_now = volume over the last 300 s per minute; base rate =
    volume over [now − 2100 s, now − 300 s) per minute, needing ≥ 5 states
    over ≥ 300 s (else missing); burst = ramp(rate_now / base, 1.5 → 4.0)
    (a positive rate against a zero base counts as a full burst).
    Basket simultaneity (cross engine, market-wide, ≥ 2 symbols): simult =
    max(ramp(share of symbols whose visible liquidity changed ≥ 20 % over
    60 s, 0.2 → 0.6), ramp(share of symbols in their own velocity top decile,
    0.2 → 0.6)); missing when the engine delivers neither.  Sector sync =
    ramp(basket_sync, 0.6 → 1.0) over ≥ 2 peers (dropped when there is no
    sector).  score = window × blend(0.40 burst, 0.35 simult, 0.25 sync).
    direction = sign of the symbol's combined pressure (|p| ≥ 0.2), else of
    the sector pressure (|p| ≥ 0.2), else of the sector breadth net (|net| ≥
    0.5), else 0.
    """

    name = "basket_rebalance"
    family = FAMILY
    requires = ("cross", "sector", "session_phase", "trade_volume", "interval_volume")
    close_w_min = 30.0
    vol_s = 300.0
    base_s = 1800.0
    base_min_points = 5
    base_min_s = 300.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        cross = ms.cross if isinstance(ms.cross, dict) else {}
        sector = ms.sector if isinstance(ms.sector, dict) else {}
        phase = ms.session_phase
        mtc = minutes_to_close(ms.t)
        slc = cross.get("simultaneous_liquidity_change")
        sx = cross.get("synchronized_expansion")
        inputs = {"simultaneous_liquidity_change": slc, "synchronized_expansion": sx,
                  "basket_sync": cross.get("basket_sync"), "basket_sync_n": cross.get("basket_sync_n"),
                  "sector": sector.get("sector"), "sector_pressure": sector.get("sector_pressure"),
                  "sector_breadth": sector.get("sector_breadth")}
        ev: Dict[str, Any] = {"phase": phase, "minutes_to_close": mtc, "direction": 0, "inputs": inputs}
        if phase not in ("CONTINUOUS", "POST_CLOSE"):
            ev.update({"in_close_window": False, "window_factor": 0.0})
            return _reading(self, 0.0, ev, base, f"phase {phase}: outside the continuous / post-close sessions")
        window = 1.0 if phase == "POST_CLOSE" else ramp(self.close_w_min - mtc, 0.0, 5.0)
        ev.update({"in_close_window": window > 0, "window_factor": window})
        # ---- own volume burst
        if ms.trade_volume is None and ms.interval_volume is None:
            return missing_reading(self, ["trade_volume"], base, ev)
        vol_now = fr.volume_over(self.vol_s)
        span_now = min(self.vol_s, fr.span_s(self.vol_s)) or self.vol_s
        rate_now = (vol_now / (span_now / 60.0)) if vol_now is not None else None
        t1 = ms.t - timedelta(seconds=self.vol_s)
        vol_base, n_base, span_base = volume_between(fr, t1 - timedelta(seconds=self.base_s), t1)
        base_rate: Optional[float] = None
        if vol_base is not None and n_base >= self.base_min_points and span_base >= self.base_min_s:
            base_rate = vol_base / (span_base / 60.0)
        if rate_now is None:
            return missing_reading(self, ["volume over the last 300 s"], base, ev)
        if base_rate is None:
            return missing_reading(self, ["volume baseline (≥ 5 states over ≥ 300 s before the last 300 s)"], base,
                                   dict(ev, baseline_states=n_base, baseline_span_s=span_base, rate_now_per_min=rate_now))
        if base_rate > _EPS:
            vol_rel: Optional[float] = rate_now / base_rate
            burst = ramp(vol_rel, 1.5, 4.0)
        else:
            vol_rel = float("inf") if rate_now > 0 else None
            burst = 1.0 if rate_now > 0 else 0.0
        # ---- basket-wide simultaneity from the cross engine
        if not isinstance(slc, dict) and not isinstance(sx, dict):
            return missing_reading(self, ["cross.simultaneous_liquidity_change"], base,
                                   dict(ev, rate_now_per_min=rate_now, base_rate_per_min=base_rate, volume_rel=vol_rel,
                                        burst=burst))
        liq_share, exp_share = _share_of(slc), _share_of(sx)
        f_liq = ramp(liq_share, 0.2, 0.6) if liq_share is not None else None
        f_exp = ramp(exp_share, 0.2, 0.6) if exp_share is not None else None
        cands = [(v, b) for v, b in ((f_liq, "liquidity_change"), (f_exp, "synchronized_expansion")) if v is not None]
        simult, simult_basis = max(cands, key=lambda x: x[0])
        # ---- sector basket synchronisation
        bsync, bsn = cross.get("basket_sync"), cross.get("basket_sync_n")
        sync = ramp(float(bsync), 0.6, 1.0) if (bsync is not None and bsn is not None and bsn >= 2) else None
        blend, dropped = weighted_blend([("burst", 0.40, burst), ("simultaneity", 0.35, simult), ("basket_sync", 0.25, sync)])
        score = window * (blend or 0.0)
        # ---- direction
        p = combined_pressure_of(ms)
        sp = sector.get("sector_pressure")
        net = (sector.get("sector_breadth") or {}).get("net") if isinstance(sector.get("sector_breadth"), dict) else None
        direction, basis = 0, None
        if p is not None and abs(p) >= 0.2:
            direction, basis = sign(p), "combined_pressure"
        elif sp is not None and abs(float(sp)) >= 0.2:
            direction, basis = sign(float(sp)), "sector_pressure"
        elif net is not None and abs(float(net)) >= 0.5:
            direction, basis = sign(float(net)), "sector_breadth_net"
        if score <= 0:
            direction = 0
        ev.update({"volume_300s": vol_now, "rate_now_per_min": rate_now, "base_rate_per_min": base_rate,
                   "baseline_states": n_base, "baseline_span_s": span_base, "volume_rel": vol_rel, "burst": burst,
                   "liquidity_change_share": liq_share, "liquidity_change_n": slc.get("n") if isinstance(slc, dict) else None,
                   "own_liquidity_rel_change": slc.get("own_rel_change") if isinstance(slc, dict) else None,
                   "expansion_share": exp_share, "own_in_top_decile": sx.get("own_in_top_decile") if isinstance(sx, dict) else None,
                   "simultaneity": simult, "simultaneity_basis": simult_basis, "basket_sync": bsync, "basket_sync_n": bsn,
                   "sync": sync, "blend": blend, "pressure": p, "direction_basis": basis, "direction": direction})
        if dropped:
            ev["unverified"] = dropped
        return _reading(self, score, ev, base,
                        f"{phase} {mtc:.1f} min to close, vol_rel {vol_rel}, simultaneity {simult:.2f} ({simult_basis})")


# ============================================================================ #18
@register
class CrossLeadLag(DirectedMechanism):
    """#18 Cross-stock lead / lag.

    Rule: ``cross.leaders`` / ``cross.laggers`` are [(symbol, lag s, corr)]
    from the cross engine (missing when both are None — no pair reached the
    overlap threshold; ``lead_lag_pairs_evaluated`` reported).  mode = "led"
    when a leader exists whose correlation is at least the best lagger's,
    "leading" when only laggers qualify, "none" otherwise (score 0, not
    missing).  strength = ramp(best corr of the mode's list, 0.3 → 0.8);
    breadth = 0.7 + 0.3 × ramp(number of qualifying pairs, 1 → 3);
    persistence = share of the states over the last 120 s (with an evaluated
    lead/lag context) whose list for the mode was non-empty, factor 0.5 + 0.5 ×
    ramp(persistence, 0.5 → 1.0).  score = strength × breadth × persistence
    factor.  direction ("led" only — a leader implies the symbol follows its
    peers): sign of (peer 60-s return, else market 60-s return) − own 60-s
    return when the gap is at least half a tick of log price; 0 in "leading"
    mode (no implication for the symbol's own price).
    """

    name = "cross_lead_lag"
    family = FAMILY
    requires = ("cross", "sector", "mid", "tick_size")
    persist_s = 120.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        cross = ms.cross if isinstance(ms.cross, dict) else {}
        sector = ms.sector if isinstance(ms.sector, dict) else {}
        leaders, laggers = _pairs(cross.get("leaders")), _pairs(cross.get("laggers"))
        evaluated = int(cross.get("lead_lag_pairs_evaluated") or 0)
        inputs = {"leaders": cross.get("leaders"), "laggers": cross.get("laggers"), "pairs_evaluated": evaluated,
                  "symbol_return_60s": cross.get("symbol_return_60s"), "market_return_60s": cross.get("market_return_60s"),
                  "peer_return_60s": sector.get("peer_return_60s"), "sector": sector.get("sector")}
        ev: Dict[str, Any] = {"direction": 0, "inputs": inputs, "pairs_evaluated": evaluated}
        if leaders is None and laggers is None:
            return missing_reading(self, ["cross.leaders (no pair with enough overlap)"], base, ev)
        leaders, laggers = leaders or [], laggers or []
        top_lead = max(leaders, key=lambda x: x[2]) if leaders else None
        top_lag = max(laggers, key=lambda x: x[2]) if laggers else None
        if top_lead is not None and (top_lag is None or top_lead[2] >= top_lag[2]):
            mode, top, n = "led", top_lead, len(leaders)
        elif top_lag is not None:
            mode, top, n = "leading", top_lag, len(laggers)
        else:
            mode, top, n = "none", None, 0
        ev.update({"mode": mode, "n_leaders": len(leaders), "n_laggers": len(laggers),
                   "top_leader": {"symbol": top_lead[0], "lag_s": top_lead[1], "corr": top_lead[2]} if top_lead else None,
                   "top_lagger": {"symbol": top_lag[0], "lag_s": top_lag[1], "corr": top_lag[2]} if top_lag else None})
        # persistence of the relation over the window (states carrying an evaluated context)
        key = "leaders" if mode == "led" else "laggers"
        n_ctx = n_same = 0
        for s in fr.states(self.persist_s):
            cx = s.cross if isinstance(s.cross, dict) else {}
            if s is ms:
                cx = cross
            if not (cx.get("lead_lag_pairs_evaluated") or 0):
                continue
            n_ctx += 1
            lst = _pairs(cx.get(key)) or []
            if lst:
                n_same += 1
        persistence = (n_same / n_ctx) if n_ctx else 0.0
        if mode == "none":
            ev.update({"strength": 0.0, "breadth": 0.0, "persistence": persistence, "persistence_states": n_ctx})
            return _reading(self, 0.0, ev, base, f"{evaluated} pairs evaluated, none qualified")
        strength = ramp(top[2], 0.3, 0.8)
        breadth = 0.7 + 0.3 * ramp(float(n), 1.0, 3.0)
        p_factor = 0.5 + 0.5 * ramp(persistence, 0.5, 1.0)
        score = strength * breadth * p_factor
        # direction: the symbol catches up with its peers (led mode only)
        direction, basis, gap, thr = 0, None, None, None
        if mode == "led":
            own = cross.get("symbol_return_60s")
            ref, basis = sector.get("peer_return_60s"), "peer_return_gap"
            if ref is None:
                ref, basis = cross.get("market_return_60s"), "market_return_gap"
            if own is not None and ref is not None:
                gap = float(ref) - float(own)
                m, tick = mid_of(ms), fr.tick
                thr = (0.5 * tick / m) if (m and tick) else 0.0
                direction = sign(gap) if abs(gap) >= thr else 0
            else:
                basis = None
        if score <= 0:
            direction = 0
        ev.update({"strength": strength, "breadth": breadth, "persistence": persistence, "persistence_states": n_ctx,
                   "persistence_factor": p_factor, "return_gap": gap, "gap_threshold": thr, "direction_basis": basis,
                   "direction": direction})
        return _reading(self, score, ev, base, f"{mode}: {top[0]} lag {top[1]:.0f} s corr {top[2]:.2f}, {n} pairs")
