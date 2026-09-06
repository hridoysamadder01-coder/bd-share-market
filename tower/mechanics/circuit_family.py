"""circuit_family — price-limit (circuit) mechanisms (MECHANISMS.md #44–#49).

Every mechanism reads the ``circuit`` dict the per-symbol
``tower.circuit.CircuitEngine`` writes on the state before the mechanics run
(limits, distances, approach velocity / acceleration, hit / lock flags, queue
at the limit with its 60-s growth / decay and persistence, unlock / relock
counts, shares to the door / floor, the frozen pre-hit state, cross-session
streaks, break-day and next-session verdicts) plus the symbol's own causal
window (``queue_family.Frame``).  Scores are continuous functions of those
measurements (linear ramps blended with fixed weights; components the
sources do not deliver are dropped with the weights renormalised and named
in ``evidence["unverified"]``).  Whatever is not observable is ``None`` and,
when the mechanism needs it, the reading is score 0 with
``evidence["missing"]`` naming the inputs.  ``evidence["direction"]`` ∈
{+1, −1, 0} (+1 = toward / beyond the upper limit); ``evidence["inputs"]``
carries the raw circuit values used; ``baseline`` is
``queue_family.baselines``.

Session gate (all six): the circuit is a live-trading phenomenon, so the
final score = phase factor × core, phase factor = 1 in CONTINUOUS and
POST_CLOSE, 0.5 in PRE_OPEN (a book is forming), 0 in CLOSED.  The ungated
core is always computed and reported as ``evidence["score_ungated"]`` so a
closed market still shows the regime it closed in.

Rules (restated in each docstring):

  circuit_regime           locked: 0.8 + 0.2 × ramp(time locked, 0 → 600 s);
                           hit (touching, asks/bids remain): 0.6 + 0.4 × queue
                           at the limit / (queue + shares to the door); else
                           proximity (1 − ramp(nearer distance %, 0 → 3)) ×
                           (0.5 + 0.5 × ramp(approach velocity, 0 → 3 ticks/min)).
  circuit_streak           ramp(consecutive limit sessions incl. today, 1 → 3)
                           × (0.5 + 0.5 × continuation strength) × 0.7 when
                           weakening.
  circuit_prehit_pressure  before the first contact: (1 − ramp(distance %, 1 → 4))
                           × blend(0.40 approach velocity [max of its z against the
                           symbol's own trailing 900 s and its absolute ramp
                           0.5 → 3 ticks/min], 0.35 pressure toward the limit
                           (ramp 0.2 → 0.7), 0.25 shares-to-door in minutes of the
                           current volume rate (1 − ramp(minutes, 2 → 30))).
  circuit_lock_strength    at the limit: lock base (1 locked, 0.6 hit) × blend(0.40
                           queue growth [0.5 + 0.5 × clip(Δqueue 60 s / queue 60 s
                           ago, ±1)], 0.30 ramp(queue persistence, 0 → 300 s), 0.30
                           queue size in minutes of the volume rate ramp(1 → 30))
                           × integrity (1 − 0.5 × open unlocks − 0.2 × ramp(unlocks,
                           1 → 4), floor 0.3).
  circuit_break_weakness   on a break day: blend(0.35 queue decay [1 − queue / day
                           max, or the 60-s decay rate], 0.35 reversal ticks below
                           the prior limit ramp(0 → 5), 0.20 price velocity away
                           ramp(0 → 3 ticks/min), 0.10 ramp(unlocks today, 0 → 2)).
  circuit_next_session     continuation: blend(0.5 ramp(open gap beyond the prior
                           limit, −0.5 → 2 ticks), 0.3 locked now / locked share,
                           0.2 ramp(follow-through since the open, 0 → 3 ticks));
                           reversal: blend(0.6 ramp(open gap inside, 0.5 → 5 ticks),
                           0.4 ramp(retreat since the open, 0 → 3 ticks)); both ×
                           (1 − 0.5 × ramp(session elapsed, 1800 → 5400 s)).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..state import MarketState
from ..windows import clamp01
from .base import MechanismReading, StateHistory, register
from .divergence_family import DirectedMechanism, _reading, combined_pressure_of, price_velocity_of, zscore_against
from .queue_family import Frame, baselines, missing_reading, ramp
from .cross_family import weighted_blend

_EPS = 1e-9
FAMILY = "circuit"
PHASE_FACTOR = {"CONTINUOUS": 1.0, "POST_CLOSE": 1.0, "PRE_OPEN": 0.5}


# ============================================================================ helpers
def phase_factor(phase: Optional[str]) -> float:
    """Session gate: 1 while trading (CONTINUOUS / POST_CLOSE), 0.5 in PRE_OPEN, 0 otherwise."""
    return PHASE_FACTOR.get(phase or "", 0.0)


def side_sign(side: Optional[str]) -> int:
    return 1 if side == "up" else (-1 if side == "down" else 0)


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def circuit_series(fr: Frame, key: str, seconds: float, before_now: bool = True) -> List[Tuple[datetime, float]]:
    """[(t, circuit[key])] over the window for states whose circuit dict carries a number."""
    out: List[Tuple[datetime, float]] = []
    for s in fr.states(seconds):
        if before_now and s is fr.ms:
            continue
        c = s.circuit if isinstance(s.circuit, dict) else {}
        v = _num(c.get(key))
        if v is not None:
            out.append((s.t, v))
    return out


def volume_rate(fr: Frame, seconds: float) -> Tuple[Optional[float], Optional[float], float]:
    """(volume per minute, volume, span s) over the last ``seconds``; None when the tape is not observable."""
    vol = fr.volume_over(seconds)
    span = min(seconds, fr.span_s(seconds)) or seconds
    if vol is None:
        return None, None, span
    return vol / (span / 60.0), vol, span


def price_ticks_from(px: Optional[float], ref: Optional[float], tick: Optional[float]) -> Optional[float]:
    if px is None or ref is None or not tick:
        return None
    return (px - ref) / tick


class CircuitMechanism(DirectedMechanism):
    """Shared prelude: the frame, the baselines and the circuit dict (missing
    when no limits are known — nothing about the circuit is observable then)."""

    family = FAMILY

    def _prelude(self, ms: MarketState, hist: StateHistory):
        fr = Frame(ms, hist)
        base = baselines(fr)
        c = ms.circuit if isinstance(ms.circuit, dict) else {}
        if not c or c.get("upper_limit") is None or c.get("lower_limit") is None:
            miss = missing_reading(self, ["circuit.upper_limit (no reference and no yclose to derive limits)"], base,
                                   {"rule_source": c.get("rule_source"), "phase": ms.session_phase, "inputs": {}})
            return fr, base, c, miss
        return fr, base, c, None

    def _finish(self, score_core: Optional[float], ms: MarketState, ev: Dict[str, Any], base: Dict[str, Any],
                note: str, direction: int) -> MechanismReading:
        pf = phase_factor(ms.session_phase)
        core = clamp01(score_core) if score_core is not None else 0.0
        score = pf * core
        ev.update({"phase": ms.session_phase, "phase_factor": pf, "score_ungated": core,
                   "direction": direction if score > 0 else 0})
        return _reading(self, score, ev, base, note)


def nearer_side(c: Dict[str, Any]) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """(side, distance %, distance ticks) of the nearer limit; the lock / hit side overrides."""
    if c.get("locked_up"):
        side = "up"
    elif c.get("locked_down"):
        side = "down"
    elif c.get("hit_up") and not c.get("hit_down"):
        side = "up"
    elif c.get("hit_down") and not c.get("hit_up"):
        side = "down"
    else:
        side = c.get("nearer_limit")
    if side is None:
        return None, None, None
    pct = _num(c.get("dist_up_pct" if side == "up" else "dist_down_pct"))
    ticks = _num(c.get("dist_up_ticks" if side == "up" else "dist_down_ticks"))
    return side, pct, ticks


# ============================================================================ #44
@register
class CircuitRegime(CircuitMechanism):
    """#44 Circuit / price-limit regime.

    Rule: side = the locked side, else the hit side, else the nearer limit.
    proximity = 1 − ramp(distance to that limit in % of price, 0 → 3) (1 at
    the limit); approach = ramp(approach velocity, 0 → 3 ticks/min) (0 when
    moving away or unknown).  locked → core = 0.8 + 0.2 × ramp(time locked
    today, 0 → 600 s), regime "locked_<side>"; hit but not locked → core =
    0.6 + 0.4 × lock pressure where lock pressure = queue at the limit /
    (queue + shares still displayed to the door) (0 when unknown, flagged
    unverified), regime "hit_<side>"; otherwise core = proximity × (0.5 +
    0.5 × approach), regime "near_<side>" while proximity > 0 else "free".
    Missing when no price / distance exists and the symbol is not at a
    limit.  direction = +1 (up side) / −1 (down side) while the score is > 0.
    """

    name = "circuit_regime"
    requires = ("circuit", "session_phase")

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr, base, c, miss = self._prelude(ms, hist)
        if miss is not None:
            return miss
        side, pct, ticks = nearer_side(c)
        locked = bool(c.get("locked_up") or c.get("locked_down"))
        hit = bool(c.get("hit_up") or c.get("hit_down"))
        vel = _num(c.get("approach_velocity"))
        inputs = {"upper_limit": c.get("upper_limit"), "lower_limit": c.get("lower_limit"), "price": c.get("price"),
                  "price_basis": c.get("price_basis"), "nearer_limit": c.get("nearer_limit"), "dist_pct": pct,
                  "dist_ticks": ticks, "approach_velocity": vel, "approach_acceleration": c.get("approach_acceleration"),
                  "hit_up": c.get("hit_up"), "hit_down": c.get("hit_down"), "locked_up": c.get("locked_up"),
                  "locked_down": c.get("locked_down"), "time_locked_s": c.get("time_locked_s"),
                  "rule_source": c.get("rule_source"), "unverified_limits": c.get("unverified")}
        ev: Dict[str, Any] = {"inputs": inputs, "side": side}
        if side is None or (pct is None and not locked and not hit):
            ev["missing"] = ["circuit.dist_up_pct (no price to measure the distance)"]
            ev.update({"regime": "unknown", "proximity": None, "approach": None})
            return self._finish(0.0, ms, ev, base, "no price: distance to the limits not observable", 0)
        proximity = (1.0 - ramp(pct, 0.0, 3.0)) if pct is not None else 1.0
        approach = ramp(vel, 0.0, 3.0)
        unverified: List[str] = []
        if locked:
            tl = _num(c.get("time_locked_s")) or 0.0
            lock_time = ramp(tl, 0.0, 600.0)
            core = 0.8 + 0.2 * lock_time
            regime = f"locked_{side}"
            ev.update({"lock_time_factor": lock_time, "lock_pressure": None})
        elif hit:
            q = _num(c.get("queue_at_upper" if side == "up" else "queue_at_lower"))
            door = _num(c.get("shares_to_door" if side == "up" else "shares_to_floor"))
            lp = (q / (q + door)) if (q is not None and door is not None and q + door > 0) else None
            if lp is None:
                unverified.append("lock_pressure")
            core = 0.6 + 0.4 * (lp or 0.0)
            regime = f"hit_{side}"
            ev.update({"lock_pressure": lp, "queue_at_limit": q, "shares_to_door": door, "lock_time_factor": None})
        else:
            core = proximity * (0.5 + 0.5 * approach)
            regime = f"near_{side}" if proximity > 0 else "free"
            if vel is None:
                unverified.append("approach_velocity")
            ev.update({"lock_pressure": None, "lock_time_factor": None})
        ev.update({"regime": regime, "proximity": proximity, "approach": approach, "locked": locked, "hit": hit})
        if unverified:
            ev["unverified"] = unverified
        return self._finish(core, ms, ev, base, f"{regime}: dist {pct} %, approach {vel} ticks/min", side_sign(side))


# ============================================================================ #45
@register
class CircuitStreak(CircuitMechanism):
    """#45 Circuit streak behaviour.

    Rule: length = ``consecutive_upper_streak`` / ``consecutive_lower_streak``
    (prior sessions locked at close + 1 when locked now; the larger side
    wins, upper on ties); no streak on either side → score 0, regime "none"
    (observed, not missing).  length factor = ramp(length, 1 → 3) (a single
    session is not a streak).  continuation = ``streak_continuation_strength``
    (today's locked share of the elapsed session ÷ the previous session's
    share, capped at 1); when the engine has none, 1 if locked now else 0;
    factor 0.5 + 0.5 × continuation.  weakening (queue at the streak limit
    decaying over 60 s or unlocks exceeding the previous session's) × 0.7.
    core = length × continuation × weakening factors.  direction = the streak
    side while the score is > 0.
    """

    name = "circuit_streak"
    requires = ("circuit", "session_phase")

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr, base, c, miss = self._prelude(ms, hist)
        if miss is not None:
            return miss
        cu, cl = _num(c.get("consecutive_upper_streak")), _num(c.get("consecutive_lower_streak"))
        inputs = {"consecutive_upper_streak": cu, "consecutive_lower_streak": cl,
                  "prior_upper_streak": c.get("prior_upper_streak"), "prior_lower_streak": c.get("prior_lower_streak"),
                  "streak_continuation_strength": c.get("streak_continuation_strength"),
                  "streak_weakening": c.get("streak_weakening"), "locked_share_today": c.get("locked_share_today"),
                  "locked_up": c.get("locked_up"), "locked_down": c.get("locked_down"),
                  "unlock_count": c.get("unlock_count"), "time_locked_s": c.get("time_locked_s")}
        ev: Dict[str, Any] = {"inputs": inputs}
        if cu is None and cl is None:
            ev["missing"] = ["circuit.consecutive_upper_streak (no streak bookkeeping)"]
            ev.update({"side": None, "length": None, "regime": "unknown"})
            return self._finish(0.0, ms, ev, base, "streak bookkeeping not observable", 0)
        cu, cl = cu or 0.0, cl or 0.0
        if cu <= 0 and cl <= 0:
            ev.update({"side": None, "length": 0, "regime": "none", "length_factor": 0.0})
            return self._finish(0.0, ms, ev, base, "no limit streak on either side", 0)
        side = "up" if cu >= cl else "down"
        length = cu if side == "up" else cl
        locked_now = bool(c.get("locked_up") if side == "up" else c.get("locked_down"))
        cont = _num(c.get("streak_continuation_strength"))
        cont_basis = "streak_continuation_strength"
        if cont is None:
            cont, cont_basis = (1.0 if locked_now else 0.0), "locked_now"
        weakening = c.get("streak_weakening")
        len_factor = ramp(length, 1.0, 3.0)
        cont_factor = 0.5 + 0.5 * clamp01(cont)
        weak_factor = 0.7 if weakening else 1.0
        core = len_factor * cont_factor * weak_factor
        ev.update({"side": side, "length": length, "regime": f"streak_{side}", "locked_now": locked_now,
                   "length_factor": len_factor, "continuation": cont, "continuation_basis": cont_basis,
                   "continuation_factor": cont_factor, "weakening": weakening, "weakening_factor": weak_factor})
        if weakening is None:
            ev["unverified"] = ["streak_weakening"]
        return self._finish(core, ms, ev, base, f"{side} streak of {length:.0f}, continuation {cont:.2f}, weakening {weakening}",
                            side_sign(side))


# ============================================================================ #46
@register
class CircuitPrehitPressure(CircuitMechanism):
    """#46 Circuit pre-hit pressure.

    Rule: applies before the first contact with the nearer limit; once the
    symbol has hit or locked, core = 0 with regime "post_hit" and the frozen
    ``pre_hit_state`` (and the same factors recomputed on it) reported.
    gate = 1 − ramp(distance to the nearer limit in %, 1 → 4).
    velocity factor = max(ramp(z of the approach velocity against the
    symbol's own approach velocities over the trailing 900 s, 0.5 → 2.5),
    ramp(approach velocity, 0.5 → 3 ticks/min)) (z None with < 6 or constant
    samples — the absolute ramp alone then).  pressure toward the limit =
    pressure strength when the pressure direction points at that side (−
    strength when it points away), else the combined pressure signed toward
    the side; factor ramp(0.2 → 0.7).  door: shares to the door (up) /
    floor (down) in minutes of the last-300-s volume rate; factor 1 −
    ramp(minutes, 2 → 30), × 0.85 when the displayed side does not reach the
    limit (the sum is a lower bound).  core = gate × blend(0.40 velocity, 0.35
    pressure, 0.25 door) — components not observable are dropped and named
    in ``unverified``; missing when neither velocity nor pressure exists.
    direction = the side while the score is > 0.
    """

    name = "circuit_prehit_pressure"
    requires = ("circuit", "pressure_direction", "pressure_strength", "trade_volume", "session_phase")
    z_window_s = 900.0
    vol_s = 300.0
    z_min_points = 6

    def _factors(self, vel: Optional[float], z: Optional[float], p_toward: Optional[float],
                 door: Optional[float], rate: Optional[float], visible: Optional[bool]) -> Dict[str, Any]:
        f_abs = ramp(vel, 0.5, 3.0) if vel is not None else None
        f_z = ramp(z, 0.5, 2.5) if z is not None else None
        f_vel = max(x for x in (f_abs, f_z) if x is not None) if (f_abs is not None or f_z is not None) else None
        f_p = ramp(p_toward, 0.2, 0.7) if p_toward is not None else None
        minutes = None
        f_door = None
        if door is not None and rate is not None:
            if rate > _EPS:
                minutes = door / rate
                f_door = 1.0 - ramp(minutes, 2.0, 30.0)
            else:
                minutes, f_door = (float("inf") if door > 0 else 0.0), (0.0 if door > 0 else 1.0)
            if visible is False:
                f_door *= 0.85
        return {"velocity_abs_factor": f_abs, "velocity_z_factor": f_z, "velocity_factor": f_vel,
                "pressure_factor": f_p, "minutes_to_door": minutes, "door_factor": f_door}

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr, base, c, miss = self._prelude(ms, hist)
        if miss is not None:
            return miss
        side, pct, ticks = nearer_side(c)
        locked = bool(c.get("locked_up") or c.get("locked_down"))
        hit = bool(c.get("hit_up") or c.get("hit_down"))
        vel = _num(c.get("approach_velocity"))
        ss = side_sign(side)
        inputs = {"side": side, "dist_pct": pct, "dist_ticks": ticks, "approach_velocity": vel,
                  "approach_acceleration": c.get("approach_acceleration"), "hit": hit, "locked": locked,
                  "pressure_direction": ms.pressure_direction, "pressure_strength": ms.pressure_strength,
                  "shares_to_door": c.get("shares_to_door") if side == "up" else c.get("shares_to_floor"),
                  "door_visible": c.get("door_visible") if side == "up" else c.get("floor_visible"),
                  "volume_approaching": c.get("volume_approaching"), "first_hit_time": c.get("first_hit_time")}
        ev: Dict[str, Any] = {"inputs": inputs, "side": side}
        if hit or locked:
            pre = c.get("pre_hit_state") if isinstance(c.get("pre_hit_state"), dict) else None
            frozen = None
            if pre:
                pd_, ps_ = pre.get("pressure_direction"), _num(pre.get("pressure_strength"))
                p_t = (ps_ * (1 if pd_ == ss else -1 if pd_ == -ss else 0)) if (ps_ is not None and pd_ is not None) else None
                frozen = self._factors(_num(pre.get("approach_velocity")), None, p_t, _num(pre.get("shares_to_door")), None, None)
                blend, _ = weighted_blend([("velocity", 0.40, frozen["velocity_factor"]), ("pressure", 0.35, frozen["pressure_factor"])])
                frozen["blend"] = blend
            ev.update({"regime": "post_hit", "pre_hit_state": pre, "pre_hit_factors": frozen, "gate": 0.0})
            return self._finish(0.0, ms, ev, base, f"limit already {'locked' if locked else 'hit'} ({side}): pre-hit phase over", ss)
        if side is None or pct is None:
            ev["missing"] = ["circuit.nearer_limit (no price to measure the distance)"]
            ev.update({"regime": "unknown", "gate": None})
            return self._finish(0.0, ms, ev, base, "no price: distance not observable", 0)
        gate = 1.0 - ramp(pct, 1.0, 4.0)
        # velocity z against the symbol's own trailing approach velocities (before now)
        vals = [v for _, v in circuit_series(fr, "approach_velocity", self.z_window_s, before_now=True)]
        z = zscore_against(vel, vals, self.z_min_points)
        # pressure toward the limit
        pd_, ps_ = ms.pressure_direction, _num(ms.pressure_strength)
        if pd_ is not None and ps_ is not None:
            p_toward = ps_ * (1 if pd_ == ss else -1 if pd_ == -ss else 0)
            p_basis = "pressure_layer"
        else:
            p = combined_pressure_of(ms)
            p_toward, p_basis = ((p * ss) if p is not None else None), ("combined_pressure" if p is not None else None)
        door = _num(inputs["shares_to_door"])
        visible = inputs["door_visible"]
        rate, vol, span = volume_rate(fr, self.vol_s)
        f = self._factors(vel, z, p_toward, door, rate, visible)
        if f["velocity_factor"] is None and f["pressure_factor"] is None:
            ev["missing"] = [m for m, v in (("circuit.approach_velocity", vel), ("pressure", p_toward)) if v is None]
            ev.update(f, regime="pre_hit", gate=gate, velocity_z=z, pressure_toward=p_toward, pressure_basis=p_basis,
                      volume_rate_per_min=rate)
            return self._finish(0.0, ms, ev, base, "neither approach velocity nor pressure observable", 0)
        blend, dropped = weighted_blend([("velocity", 0.40, f["velocity_factor"]), ("pressure", 0.35, f["pressure_factor"]),
                                         ("door", 0.25, f["door_factor"])])
        core = gate * (blend or 0.0)
        ev.update(f)
        ev.update({"regime": "pre_hit", "gate": gate, "velocity_z": z, "velocity_z_points": len(vals),
                   "pressure_toward": p_toward, "pressure_basis": p_basis, "shares_to_door": door, "door_visible": visible,
                   "volume_300s": vol, "volume_rate_per_min": rate, "blend": blend})
        if dropped:
            ev["unverified"] = dropped
        return self._finish(core, ms, ev, base,
                            f"pre-hit {side}: dist {pct:.2f} %, vel {vel} (z {z}), pressure {p_toward}, door {door} in {f['minutes_to_door']} min", ss)


# ============================================================================ #47
@register
class CircuitLockStrength(CircuitMechanism):
    """#47 Circuit lock / unlock strength.

    Rule: applies at a limit — locked (lock base 1) or hit with the other side
    still displayed (lock base 0.6); off the limit → score 0, regime
    "off_limit".  queue = displayed qty at the limit price on the lock side
    (missing when no displayed book).  growth = 0.5 + 0.5 × clip(Δqueue over
    60 s / queue 60 s ago, −1 … 1) (1 when the queue appeared from nothing);
    persistence = ramp(seconds the queue has been non-zero, 0 → 300); size =
    ramp(queue in minutes of the last-300-s volume rate, 1 → 30) — when the
    tape is not observable, queue / the day's max queue at the limit instead
    (basis reported).  integrity = 1 − 0.5 × ramp(unlocks not followed by a
    relock, 0 → 1) − 0.2 × ramp(unlock count today, 1 → 4), floored at 0.3.
    core = lock base × blend(0.40 growth, 0.30 persistence, 0.30 size) ×
    integrity.  direction = the lock side while the score is > 0.
    """

    name = "circuit_lock_strength"
    requires = ("circuit", "bids", "asks", "trade_volume", "session_phase")
    vol_s = 300.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr, base, c, miss = self._prelude(ms, hist)
        if miss is not None:
            return miss
        locked_up, locked_down = bool(c.get("locked_up")), bool(c.get("locked_down"))
        hit_up, hit_down = bool(c.get("hit_up")), bool(c.get("hit_down"))
        locked, hit = locked_up or locked_down, hit_up or hit_down
        side = "up" if locked_up else "down" if locked_down else "up" if hit_up else "down" if hit_down else None
        unlocks, relocks = int(_num(c.get("unlock_count")) or 0), int(_num(c.get("relock_count")) or 0)
        inputs = {"locked_up": c.get("locked_up"), "locked_down": c.get("locked_down"), "hit_up": c.get("hit_up"),
                  "hit_down": c.get("hit_down"), "queue_at_upper": c.get("queue_at_upper"),
                  "queue_at_lower": c.get("queue_at_lower"), "queue_side": c.get("queue_side"),
                  "queue_delta_60s": c.get("queue_delta_60s"), "queue_growth": c.get("queue_growth"),
                  "queue_decay": c.get("queue_decay"), "queue_persistence_s": c.get("queue_persistence_s"),
                  "max_queue_at_limit": c.get("max_queue_at_limit"), "unlock_count": unlocks, "relock_count": relocks,
                  "time_between_unlock_relock_s": c.get("time_between_unlock_relock_s"),
                  "time_locked_s": c.get("time_locked_s"), "volume_while_locked": c.get("volume_while_locked")}
        ev: Dict[str, Any] = {"inputs": inputs, "side": side, "locked": locked, "hit": hit}
        if c.get("hit_up") is None and c.get("locked_up") is None:
            ev["missing"] = ["circuit.hit_up (no price / book to test the limit)"]
            ev["regime"] = "unknown"
            return self._finish(0.0, ms, ev, base, "limit contact not observable", 0)
        if side is None:
            ev.update({"regime": "off_limit", "lock_base": 0.0, "integrity": None})
            return self._finish(0.0, ms, ev, base, f"off the limit ({unlocks} unlocks, {relocks} relocks today)", 0)
        q = _num(c.get("queue_at_upper" if side == "up" else "queue_at_lower"))
        if q is None:
            ev["missing"] = [f"circuit.queue_at_{'upper' if side == 'up' else 'lower'} (no displayed book)"]
            ev["regime"] = f"{'locked' if locked else 'hit'}_{side}"
            return self._finish(0.0, ms, ev, base, "queue at the limit not observable", side_sign(side))
        delta = _num(c.get("queue_delta_60s")) if c.get("queue_side") == side else None
        rel = None
        growth = None
        if delta is not None:
            q_prev = q - delta
            if q_prev > _EPS:
                rel = delta / q_prev
            else:
                rel = 1.0 if delta > 0 else 0.0
            growth = 0.5 + 0.5 * max(-1.0, min(1.0, rel))
        persistence = ramp(_num(c.get("queue_persistence_s")), 0.0, 300.0) \
            if c.get("queue_persistence_s") is not None else None
        rate, vol, span = volume_rate(fr, self.vol_s)
        qmax = _num(c.get("max_queue_at_limit"))
        minutes = None
        if rate is not None and rate > _EPS:
            minutes = q / rate
            size, size_basis = ramp(minutes, 1.0, 30.0), "minutes_of_volume"
        elif rate is not None:
            minutes = float("inf") if q > 0 else 0.0
            size, size_basis = (1.0 if q > 0 else 0.0), "minutes_of_volume"
        elif qmax is not None and qmax > 0:
            size, size_basis = clamp01(q / qmax), "share_of_day_max"
        else:
            size, size_basis = None, None
        open_unlocks = max(0, unlocks - relocks)
        integrity = max(0.3, 1.0 - 0.5 * ramp(float(open_unlocks), 0.0, 1.0) - 0.2 * ramp(float(unlocks), 1.0, 4.0))
        lock_base = 1.0 if locked else 0.6
        blend, dropped = weighted_blend([("growth", 0.40, growth), ("persistence", 0.30, persistence), ("size", 0.30, size)])
        core = lock_base * (blend or 0.0) * integrity
        ev.update({"regime": f"{'locked' if locked else 'hit'}_{side}", "queue": q, "queue_delta_60s": delta,
                   "queue_rel_change": rel, "growth": growth, "persistence": persistence, "queue_minutes_of_volume": minutes,
                   "volume_rate_per_min": rate, "size": size, "size_basis": size_basis, "open_unlocks": open_unlocks,
                   "integrity": integrity, "lock_base": lock_base, "blend": blend})
        if dropped:
            ev["unverified"] = dropped
        return self._finish(core, ms, ev, base,
                            f"{ev['regime']}: queue {q:.0f} (rel Δ60 {rel}), persistence {persistence}, integrity {integrity:.2f}",
                            side_sign(side))


# ============================================================================ #48
@register
class CircuitBreakWeakness(CircuitMechanism):
    """#48 Circuit break-day weakness.

    Rule: applies on a break day (``break_day`` True: a prior streak exists
    and the symbol is off that limit now); no prior streak → score 0, regime
    "no_prior_streak"; streak holding → score 0, regime "streak_holding";
    prior streak but no price yet → missing.  side = the prior streak side,
    S = its sign.  queue decay = max(1 − queue at the streak limit / the
    day's max queue, ramp(−Δqueue 60 s / queue 60 s ago, 0 → 0.5)); the
    engine's boolean ``break_behaviour.queue_decay`` (1 / 0) when neither is
    numeric.  reversal = ramp(ticks the price sits inside the prior limit,
    0 → 5) where inside ticks = −S × (open gap ticks + (price − open) /
    tick); the boolean ``break_behaviour.reversal`` when the ticks are not
    computable.  velocity away = ramp(−S × price velocity, 0 → 3 ticks/min).
    unlocks = ramp(unlock count today, 0 → 2).  core = blend(0.35 queue
    decay, 0.35 reversal, 0.20 velocity away, 0.10 unlocks).  direction = −S
    while the score is > 0 (weakness implies the reversal continues).
    """

    name = "circuit_break_weakness"
    requires = ("circuit", "price_velocity", "session_phase")

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr, base, c, miss = self._prelude(ms, hist)
        if miss is not None:
            return miss
        pu, pl = int(_num(c.get("prior_upper_streak")) or 0), int(_num(c.get("prior_lower_streak")) or 0)
        side = "up" if pu > 0 else "down" if pl > 0 else None
        ss = side_sign(side)
        bd = c.get("break_day")
        beh = c.get("break_behaviour") if isinstance(c.get("break_behaviour"), dict) else {}
        vel = ms.price_velocity if ms.price_velocity is not None else price_velocity_of(fr)
        inputs = {"prior_upper_streak": pu, "prior_lower_streak": pl, "break_day": bd, "break_behaviour": beh,
                  "queue_at_upper": c.get("queue_at_upper"), "queue_at_lower": c.get("queue_at_lower"),
                  "queue_side": c.get("queue_side"), "queue_delta_60s": c.get("queue_delta_60s"),
                  "max_queue_at_limit": c.get("max_queue_at_limit"), "unlock_count": c.get("unlock_count"),
                  "open_price": c.get("open_price"), "price": c.get("price"), "tick": c.get("tick"),
                  "price_velocity": vel, "streak_weakening": c.get("streak_weakening")}
        ev: Dict[str, Any] = {"inputs": inputs, "side": side}
        if side is None:
            ev["regime"] = "no_prior_streak"
            return self._finish(0.0, ms, ev, base, "no prior limit streak: nothing to break", 0)
        if bd is None:
            ev["missing"] = ["circuit.break_day (no price observed yet today)"]
            ev["regime"] = "unknown"
            return self._finish(0.0, ms, ev, base, "break day not decidable without a price", 0)
        if not bd:
            ev["regime"] = "streak_holding"
            return self._finish(0.0, ms, ev, base, f"{side} streak of {pu or pl} still holding", 0)
        # queue decay
        q = _num(c.get("queue_at_upper" if side == "up" else "queue_at_lower"))
        qmax = _num(c.get("max_queue_at_limit"))
        delta = _num(c.get("queue_delta_60s")) if c.get("queue_side") == side else None
        parts: List[float] = []
        if q is not None and qmax is not None and qmax > 0:
            parts.append(clamp01(1.0 - q / qmax))
        if q is not None and delta is not None and (q - delta) > _EPS:
            parts.append(ramp(-delta / (q - delta), 0.0, 0.5))
        if parts:
            queue_decay, qd_basis = max(parts), "queue_series"
        elif beh.get("queue_decay") is not None:
            queue_decay, qd_basis = (1.0 if beh.get("queue_decay") else 0.0), "engine_bool"
        else:
            queue_decay, qd_basis = None, None
        # reversal: ticks inside the prior limit
        gap = _num(beh.get("gap_open_ticks"))
        follow = price_ticks_from(_num(c.get("price")), _num(c.get("open_price")), _num(c.get("tick")))
        inside = (-ss * (gap + follow)) if (gap is not None and follow is not None) else None
        if inside is not None:
            reversal, rev_basis = ramp(inside, 0.0, 5.0), "ticks_inside_prior_limit"
        elif beh.get("reversal") is not None:
            reversal, rev_basis = (1.0 if beh.get("reversal") else 0.0), "engine_bool"
        else:
            reversal, rev_basis = None, None
        vel_away = ramp(-ss * vel, 0.0, 3.0) if vel is not None else None
        unlocks = ramp(float(_num(c.get("unlock_count")) or 0.0), 0.0, 2.0) if c.get("unlock_count") is not None else None
        if queue_decay is None and reversal is None:
            ev["missing"] = ["circuit.queue_at_limit / break_behaviour (neither queue decay nor reversal observable)"]
            ev.update({"regime": "break_day", "queue_decay": None, "reversal": None, "velocity_away": vel_away})
            return self._finish(0.0, ms, ev, base, "break day, but no queue or price evidence", 0)
        blend, dropped = weighted_blend([("queue_decay", 0.35, queue_decay), ("reversal", 0.35, reversal),
                                         ("velocity_away", 0.20, vel_away), ("unlocks", 0.10, unlocks)])
        ev.update({"regime": "break_day", "prior_streak": pu or pl, "queue_decay": queue_decay, "queue_decay_basis": qd_basis,
                   "queue": q, "queue_day_max": qmax, "queue_delta_60s": delta, "gap_open_ticks": gap, "follow_ticks": follow,
                   "ticks_inside_prior_limit": inside, "reversal": reversal, "reversal_basis": rev_basis,
                   "velocity_away": vel_away, "unlocks": unlocks, "blend": blend})
        if dropped:
            ev["unverified"] = dropped
        return self._finish(blend, ms, ev, base,
                            f"break day after {pu or pl} {side} sessions: queue decay {queue_decay}, inside {inside} ticks", -ss)


# ============================================================================ #49
@register
class CircuitNextSession(CircuitMechanism):
    """#49 Circuit next-session continuation / reversal.

    Rule: needs a prior locked session (``prior_*_streak`` > 0, side S; none →
    score 0, regime "no_prior_lock") and the engine's ``next_session``
    verdict (None → missing: no open observed yet).  gap = S × open gap ticks
    vs the prior limit (positive = opened beyond it in the streak direction;
    missing when not computable); follow = S × (price − open) / tick.
    continuation: blend(0.5 ramp(gap, −0.5 → 2), 0.3 lock factor [1 when
    locked on that side now, else ramp(today's locked share, 0 → 0.5)], 0.2
    ramp(follow, 0 → 3)), direction S.  reversal: blend(0.6 ramp(−gap, 0.5 →
    5), 0.4 ramp(−follow, 0 → 3)), direction −S.  Both × time factor 1 − 0.5 ×
    ramp(session elapsed, 1800 → 5400 s) (the opening state fades through the
    session).
    """

    name = "circuit_next_session"
    requires = ("circuit", "session_phase")

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr, base, c, miss = self._prelude(ms, hist)
        if miss is not None:
            return miss
        pu, pl = int(_num(c.get("prior_upper_streak")) or 0), int(_num(c.get("prior_lower_streak")) or 0)
        side = "up" if pu > 0 else "down" if pl > 0 else None
        ss = side_sign(side)
        ns = c.get("next_session")
        beh = c.get("break_behaviour") if isinstance(c.get("break_behaviour"), dict) else {}
        gap_raw = _num(beh.get("gap_open_ticks"))
        follow_raw = price_ticks_from(_num(c.get("price")), _num(c.get("open_price")), _num(c.get("tick")))
        elapsed = _num(c.get("session_elapsed_s"))
        inputs = {"prior_upper_streak": pu, "prior_lower_streak": pl, "next_session": ns, "gap_open_ticks": gap_raw,
                  "open_price": c.get("open_price"), "price": c.get("price"), "tick": c.get("tick"),
                  "locked_up": c.get("locked_up"), "locked_down": c.get("locked_down"),
                  "locked_share_today": c.get("locked_share_today"), "session_elapsed_s": elapsed}
        ev: Dict[str, Any] = {"inputs": inputs, "side": side}
        if side is None:
            ev["regime"] = "no_prior_lock"
            return self._finish(0.0, ms, ev, base, "no prior locked session", 0)
        if ns is None:
            ev["missing"] = ["circuit.next_session (open not observed yet)"]
            ev["regime"] = "unknown"
            return self._finish(0.0, ms, ev, base, "next-session verdict needs an observed open", 0)
        if gap_raw is None:
            ev["missing"] = ["circuit.break_behaviour.gap_open_ticks (open / prior limit / tick)"]
            ev["regime"] = ns
            return self._finish(0.0, ms, ev, base, "open gap vs the prior limit not computable", 0)
        gap = ss * gap_raw
        follow = (ss * follow_raw) if follow_raw is not None else None
        time_factor = (1.0 - 0.5 * ramp(elapsed, 1800.0, 5400.0)) if elapsed is not None else 1.0
        locked_now = bool(c.get("locked_up") if side == "up" else c.get("locked_down"))
        lshare = _num(c.get("locked_share_today"))
        if ns == "continuation":
            f_gap = ramp(gap, -0.5, 2.0)
            f_lock = 1.0 if locked_now else (ramp(lshare, 0.0, 0.5) if lshare is not None else 0.0)
            f_follow = ramp(follow, 0.0, 3.0) if follow is not None else None
            blend, dropped = weighted_blend([("gap", 0.5, f_gap), ("lock", 0.3, f_lock), ("follow", 0.2, f_follow)])
            direction = ss
            ev.update({"gap_factor": f_gap, "lock_factor": f_lock, "follow_factor": f_follow})
        else:
            f_gap = ramp(-gap, 0.5, 5.0)
            f_follow = ramp(-follow, 0.0, 3.0) if follow is not None else None
            blend, dropped = weighted_blend([("gap", 0.6, f_gap), ("follow", 0.4, f_follow)])
            direction = -ss
            ev.update({"gap_factor": f_gap, "lock_factor": None, "follow_factor": f_follow})
        core = time_factor * (blend or 0.0)
        ev.update({"regime": ns, "gap_ticks": gap, "follow_ticks": follow, "locked_now": locked_now,
                   "locked_share_today": lshare, "time_factor": time_factor, "blend": blend})
        if dropped:
            ev["unverified"] = dropped
        return self._finish(core, ms, ev, base, f"{ns} after {pu or pl} {side} sessions: gap {gap:+.1f} ticks, follow {follow}",
                            direction)
