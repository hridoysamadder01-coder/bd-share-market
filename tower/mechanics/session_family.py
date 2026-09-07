"""session_family — session-bound mechanisms (MECHANISMS.md #36, #16).

Same conventions as ``divergence_family``: causal windows over the current
``MarketState`` + ``StateHistory``, continuous scores, ``None`` for what is
not observable and ``evidence["missing"]`` when the mechanism needs it,
``evidence["direction"]`` ∈ {+1, −1, 0}, ``baseline`` from
``queue_family.baselines``.

Rules:
  close_session_pressure  inside the last 30 min of CONTINUOUS (session end
                          from ``seeing.clock``, Dhaka wall time of the state's
                          own timestamp — never a clock read) and during
                          POST_CLOSE: volume rate over the last 300 s against
                          the day's rate before the close window, and pressure
                          strength against the day's mean |pressure|.
  auction_imbalance       ``ms.auction['auction_pressure']`` (signed imbalance /
                          (matched + |imbalance|) from an auction feed, or the
                          pre-open book imbalance flagged as a proxy) × sign
                          persistence over 120 s × freshness; proxy readings
                          are damped and flagged.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from seeing.clock import CONTINUOUS_END, POST_CLOSE_END, to_dhaka

from ..state import MarketState
from ..windows import clamp01, safe_div, sign
from .base import Mechanism, MechanismReading, StateHistory, register
from .divergence_family import DirectedMechanism, _reading, combined_pressure_of, field_series
from .queue_family import Frame, baselines, missing_reading, ramp, visible_depth

_EPS = 1e-9
FAMILY = "session"
PROXY_SOURCE = "pre_open_book_proxy"


def minutes_to_close(t: datetime) -> float:
    """Minutes from the state's instant to the end of the continuous session on
    its own trading day (Dhaka wall time); negative once the session has ended."""
    local = to_dhaka(t)
    end = local.replace(hour=CONTINUOUS_END.hour, minute=CONTINUOUS_END.minute, second=0, microsecond=0)
    return (end - local).total_seconds() / 60.0


# ============================================================================ #36
@register
class CloseSessionPressure(DirectedMechanism):
    """#36 Close-session pressure.

    Rule: requires ``session_phase`` ∈ {CONTINUOUS, POST_CLOSE}; any other phase
    is score 0 with the phase reported (``in_close_window`` False).  Window
    factor: CONTINUOUS → ramp(30 − minutes to close, 0 → 5) (0 more than
    30 min before the close, 1 from 25 min before); POST_CLOSE → 1.
    Day baseline = the history states of the same trading day earlier than
    (close − 30 min): volume rate = Δ cumulative volume / span per minute
    (else Σ interval volumes / span), needs ≥ 5 states and ≥ 300 s of span,
    else missing; mean |combined pressure| over the same states when carried.
    Current: rate = volume over the last 300 s per minute; pressure = combined
    pressure (else the book / trade blend).  volume_rel = rate / base rate;
    pressure excess = |p| − base mean |p| (|p| when no pressure baseline).
    score = window × (0.5 × ramp(volume_rel, 1.2 → 3.0) + 0.5 ×
    ramp(pressure excess, 0.2 → 0.6)).  direction = sign(p) when |p| ≥ 0.2.
    """

    name = "close_session_pressure"
    family = FAMILY
    requires = ("session_phase", "trade_volume", "interval_volume", "combined_pressure")
    close_w_min = 30.0
    vol_s = 300.0
    base_min_s = 300.0
    base_min_points = 5

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        phase = ms.session_phase
        mtc = minutes_to_close(ms.t)
        ev: Dict[str, Any] = {"phase": phase, "minutes_to_close": mtc, "direction": 0}
        if phase not in ("CONTINUOUS", "POST_CLOSE"):
            ev.update({"in_close_window": False, "window_factor": 0.0})
            return _reading(self, 0.0, ev, base, f"phase {phase}: outside the continuous / post-close sessions")
        window = 1.0 if phase == "POST_CLOSE" else ramp(self.close_w_min - mtc, 0.0, 5.0)
        ev.update({"in_close_window": window > 0, "window_factor": window})
        # day baseline: states before the close window on the same trading day
        local_now = to_dhaka(ms.t)
        t_close_w = (local_now.replace(hour=CONTINUOUS_END.hour, minute=CONTINUOUS_END.minute, second=0, microsecond=0)
                     - timedelta(minutes=self.close_w_min))
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        earlier = [s for s in fr.past if day_start <= to_dhaka(s.t) < t_close_w]
        with_vol = [s for s in earlier if s.trade_volume is not None]
        base_rate: Optional[float] = None
        base_basis = None
        if len(with_vol) >= self.base_min_points:
            span = (with_vol[-1].t - with_vol[0].t).total_seconds()
            if span >= self.base_min_s:
                dv = with_vol[-1].trade_volume - with_vol[0].trade_volume
                if dv >= 0:
                    base_rate, base_basis = dv / (span / 60.0), "cum_volume"
        if base_rate is None:
            rows = [s for s in earlier if s.interval_volume is not None]
            if len(rows) >= self.base_min_points:
                span = (rows[-1].t - rows[0].t).total_seconds()
                if span >= self.base_min_s:
                    base_rate, base_basis = sum(s.interval_volume for s in rows) / (span / 60.0), "interval_volume"
        if ms.trade_volume is None and ms.interval_volume is None:
            return missing_reading(self, ["trade_volume"], base, ev)
        if base_rate is None:
            return missing_reading(self, ["day baseline before the close window (≥ 5 states over ≥ 300 s)"], base,
                                   dict(ev, baseline_states=len(earlier)))
        p_base = [abs(v) for v in (combined_pressure_of(s) for s in earlier) if v is not None]
        base_abs_p = sum(p_base) / len(p_base) if p_base else None
        vol_now = fr.volume_over(self.vol_s)
        span_now = min(self.vol_s, fr.span_s(self.vol_s)) or self.vol_s
        rate_now = (vol_now / (span_now / 60.0)) if vol_now is not None else None
        # a silent day baseline (rate 0) makes any positive rate unboundedly large relative to it; a
        # zero rate against a zero baseline is not a ratio at all (None, factor 0)
        if base_rate > _EPS:
            vol_rel = safe_div(rate_now, base_rate)
            vol_factor = ramp(vol_rel, 1.2, 3.0)
        elif rate_now is not None and rate_now > 0:
            vol_rel, vol_factor = float("inf"), 1.0
        else:
            vol_rel, vol_factor = None, 0.0
        p = combined_pressure_of(ms)
        excess = (abs(p) - (base_abs_p or 0.0)) if p is not None else None
        p_factor = ramp(excess, 0.2, 0.6)
        score = window * (0.5 * vol_factor + 0.5 * p_factor)
        direction = sign(p) if (p is not None and abs(p) >= 0.2 and score > 0) else 0
        ev.update({"baseline_states": len(earlier), "base_rate_per_min": base_rate, "base_rate_basis": base_basis,
                   "volume_300s": vol_now, "rate_now_per_min": rate_now, "volume_rel": vol_rel, "volume_factor": vol_factor,
                   "pressure": p, "base_abs_pressure": base_abs_p, "pressure_excess": excess, "pressure_factor": p_factor,
                   "direction": direction})
        if p is None:
            ev["unverified"] = ["pressure"]
        return _reading(self, score, ev, base, f"{phase} {mtc:.1f} min to close, vol_rel {vol_rel}, pressure {p}")


# ============================================================================ #16
@register
class AuctionImbalance(DirectedMechanism):
    """#16 Auction imbalance.

    Rule: p = ``auction['auction_pressure']`` (missing when None — no auction
    feed and not in PRE_OPEN); ``proxy`` = the source is the pre-open book
    proxy (flagged, damped × 0.7).  persistence = share of the states in the
    last 120 s whose auction pressure has the sign of p.  freshness = 1 −
    ramp(auction age, 600 → 1800 s) for feed data (a stale indicative fades),
    1 for the proxy.  size = |imbalance_qty| (feed) or the visible-depth gap
    |Vb − Va| (proxy), reported.
    score = ramp(|p|, 0.2 → 0.7) × (0.5 + 0.5 × ramp(persistence, 0.5 → 1.0))
    × freshness × (0.7 if proxy else 1).  direction = sign(p) when |p| ≥ 0.2.
    """

    name = "auction_imbalance"
    family = FAMILY
    requires = ("auction", "session_phase", "imb_topk")
    persist_s = 120.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        a = ms.auction if isinstance(ms.auction, dict) else {}
        p = a.get("auction_pressure")
        src = a.get("source")
        if p is None:
            return missing_reading(self, ["auction_pressure"], base,
                                   {"phase": ms.session_phase, "auction_source": src, "auction_missing": a.get("missing")})
        p = float(p)
        proxy = src == PROXY_SOURCE
        sgn = sign(p)
        n = same = 0
        for s in fr.states(self.persist_s):
            v = (s.auction or {}).get("auction_pressure") if isinstance(s.auction, dict) else None
            if s is ms:
                v = p
            if v is None:
                continue
            n += 1
            if sign(v) == sgn:
                same += 1
        share = same / n if n else 0.0
        age = a.get("auction_age_s")
        fresh = 1.0 if proxy else (1.0 - ramp(age, 600.0, 1800.0) if age is not None else 1.0)
        if proxy:
            vb, va = visible_depth(ms, "bid"), visible_depth(ms, "ask")
            size = abs((vb or 0.0) - (va or 0.0)) if (vb is not None or va is not None) else None
        else:
            size = abs(a["imbalance_qty"]) if a.get("imbalance_qty") is not None else None
        score = ramp(abs(p), 0.2, 0.7) * (0.5 + 0.5 * ramp(share, 0.5, 1.0)) * fresh * (0.7 if proxy else 1.0)
        direction = sgn if (abs(p) >= 0.2 and score > 0) else 0
        ev = {"auction_pressure": p, "auction_source": src, "proxy": proxy, "proxy_basis": a.get("proxy_basis"),
              "phase": ms.session_phase, "indicative_price": a.get("indicative_price"), "matched_qty": a.get("matched_qty"),
              "imbalance_qty": a.get("imbalance_qty"), "imbalance_side": a.get("imbalance_side"), "size": size,
              "auction_age_s": age, "freshness": fresh, "sign_share_120s": share, "points": n,
              "open_gap_ticks": a.get("open_gap_ticks"), "direction": direction}
        return _reading(self, score, ev, base, f"pressure {p:.2f} ({'proxy' if proxy else src}), persistence {share:.2f}")
