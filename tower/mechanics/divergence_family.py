"""divergence_family — mechanisms whose evidence is a *conflict* between two
observables (MECHANISMS.md #19, #22, #23, #24, #25, #26, #27, #35, #37).

Every mechanism is a ``Mechanism`` subclass computed from rolling windows over
the current ``MarketState`` plus the causal ``StateHistory`` (states at or
before the current one; the engine pushes the current state *after* the
mechanisms run, so the window is history + current — see
``queue_family.Frame``).  Scores are continuous functions of the measured
quantities (linear ramps multiplied together); nothing is a constant.
Whatever cannot be measured is ``None`` and, when the mechanism needs it, the
reading is score 0 with ``evidence["missing"]`` naming the inputs.
``evidence["direction"]`` ∈ {+1, −1, 0} is the price direction the mechanism
implies (0 = none).  ``baseline`` carries the simple baselines at the same
instant (``queue_family.baselines``).

Shared helpers (``field_series``, ``book_pressure_of``, ``trade_pressure_of``,
``combined_pressure_of``, ``pearson``, ``mean_std``) live here and are reused
by ``ofi_shape_family`` and ``session_family``.

Rules (window lengths are class attributes; every rule is restated in the
mechanism docstring):

  churn_anomaly            trade-intensity z against the symbol's own trailing
                           900 s (ratio to the mean when the baseline is flat)
                           × (1 − price progress in ticks/min over 120 s).
  book_trade_divergence    book pressure and trade pressure of opposite sign,
                           strength = min(|book|, |trade|), persistence = share
                           of the last 120 s in conflict.
  depth_price_divergence   depth share / migration moving one way over 120 s
                           while the mid moved the other way.
  flow_impact_divergence   |signed flow| vs its 900-s baseline against the mid
                           move vs the move the 900-s impact baseline predicts.
  resilience_asymmetry     normalised bid-vs-ask recovery-speed difference of
                           the same sign persisting over the recovery window.
  compression_expansion    mid range / spread std / velocity std of the
                           compression phase [−300, −60 s) shrinking against
                           the reference phase [−900, −300 s), then the last
                           60 s moving by multiples of the compressed range.
  false_breakout           mid beyond the [−600, −180 s) range by ≥ 1 tick
                           inside the last 180 s, back inside now, pressure at
                           the excursion peak reversed since.
  trap_pressure            displayed one-side pressure at the start of 300 s
                           whose depth is withdrawn (not traded) while the mid
                           approaches its centre of mass.
  trade_churn_repetition   identical (volume, trade-count) tape intervals
                           repeating over 600 s at a regular cadence with a
                           flat mid.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..state import MarketState
from ..windows import clamp01, safe_div, sign
from .base import Mechanism, MechanismReading, StateHistory, register
from .queue_family import (Frame, baselines, best_of, geo_mean, levels_of, mid_of, missing_reading, ramp,
                           spread_ticks_of, topk_depth, visible_depth, _cv, _median)

_EPS = 1e-9
FAMILY = "divergence"


# ============================================================================ shared helpers
def field_series(fr: Frame, attr: str, seconds: float, before_now: bool = False) -> List[Tuple[datetime, float]]:
    """[(t, state.attr)] over the window for states carrying a numeric value;
    ``before_now`` drops the current state (the trailing baseline)."""
    out: List[Tuple[datetime, float]] = []
    for s in fr.states(seconds):
        if before_now and s is fr.ms:
            continue
        v = getattr(s, attr, None)
        if v is None or isinstance(v, (bool, str, dict, list)):
            continue
        fv = float(v)
        if math.isnan(fv):
            continue
        out.append((s.t, fv))
    return out


def mean_std(xs: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """(mean, sample std); std None with < 2 points."""
    if not xs:
        return None, None
    m = sum(xs) / len(xs)
    if len(xs) < 2:
        return m, None
    return m, statistics.stdev(xs)


def zscore_against(cur: Optional[float], base: Sequence[float], min_points: int = 4) -> Optional[float]:
    """z of ``cur`` against ``base`` (which must not contain ``cur``); None with a
    short (< min_points) or degenerate (std ≈ 0) baseline — unknown, never normal."""
    if cur is None or len(base) < min_points:
        return None
    m, sd = mean_std(list(base))
    if sd is None or sd <= _EPS:
        return None
    return (cur - m) / sd


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Pearson correlation; None with < 3 points or a constant series."""
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    xs, ys = list(xs[:n]), list(ys[:n])
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= _EPS or syy <= _EPS:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def clip1(x: Optional[float]) -> Optional[float]:
    return None if x is None else max(-1.0, min(1.0, float(x)))


def book_pressure_of(s: MarketState) -> Optional[float]:
    """Book pressure of one state: the pressure layer's ``book_pressure``; else the
    same blend (0.5 imb_weighted + 0.3 imb_topk + 0.2 imb_l1, re-weighted over
    the parts present); else the top-K imbalance of the displayed levels."""
    if s.book_pressure is not None:
        return float(s.book_pressure)
    parts = [(w, v) for w, v in ((0.5, s.imb_weighted), (0.3, s.imb_topk), (0.2, s.imb_l1)) if v is not None]
    if parts:
        return sum(w * float(v) for w, v in parts) / sum(w for w, _ in parts)
    tb, ta = topk_depth(s, "bid"), topk_depth(s, "ask")
    if tb is None and ta is None:
        return None
    tb, ta = tb or 0.0, ta or 0.0
    return (tb - ta) / (tb + ta) if tb + ta > 0 else None


def trade_pressure_of(s: MarketState) -> Optional[float]:
    """Trade pressure of one state: ``trade_pressure``; else ``signed_flow_window`` over the
    tape's volume of the *same* 300-s window (``session_state["tape"]["volume_300s"]``), so the
    ratio is a share in [−1, 1].  None when neither the pressure layer nor the tape's window
    volume is carried — a signed flow without its window volume is not a pressure (dividing by
    the 120-s response volume or by |flow| itself would read any non-zero flow as ±1)."""
    if s.trade_pressure is not None:
        return float(s.trade_pressure)
    sfw = s.signed_flow_window
    if sfw is None:
        return None
    tp = s.session_state.get("tape") if isinstance(s.session_state, dict) else None
    vol = tp.get("volume_300s") if isinstance(tp, dict) else None
    if vol is None or float(vol) <= 0:
        return None
    return max(-1.0, min(1.0, float(sfw) / float(vol)))


def trade_pressure_now(fr: Frame, seconds: float = 300.0) -> Optional[float]:
    """Trade pressure of the current state, falling back to the classified tape rows of the window."""
    tp = trade_pressure_of(fr.ms)
    if tp is not None:
        return tp
    sv, vol = fr.signed_volume_over(seconds)
    if sv is None or not vol:
        return None
    return sv / vol


def combined_pressure_of(s: MarketState) -> Optional[float]:
    """``combined_pressure`` else the mean of the available book / trade pressures."""
    if s.combined_pressure is not None:
        return float(s.combined_pressure)
    parts = [x for x in (book_pressure_of(s), trade_pressure_of(s)) if x is not None]
    return sum(parts) / len(parts) if parts else None


def depth_ratio_of(s: MarketState) -> Optional[float]:
    """Bid share of the visible depth (state field else from the displayed levels)."""
    if s.depth_ratio is not None:
        return float(s.depth_ratio)
    vb, va = visible_depth(s, "bid"), visible_depth(s, "ask")
    if vb is None and va is None:
        return None
    vb, va = vb or 0.0, va or 0.0
    return vb / (vb + va) if vb + va > 0 else None


def price_velocity_of(fr: Frame, seconds: float = 60.0) -> Optional[float]:
    """Mid velocity in ticks per minute: the state field, else Δmid over ``seconds`` from the window."""
    if fr.ms.price_velocity is not None:
        return float(fr.ms.price_velocity)
    if not fr.tick:
        return None
    pts = fr.series(mid_of, seconds)
    if len(pts) < 2:
        return None
    span = (pts[-1][0] - pts[0][0]).total_seconds()
    if span <= 0:
        return None
    return (pts[-1][1] - pts[0][1]) / fr.tick / (span / 60.0)


def _reading(mech: Mechanism, score: float, ev: Dict[str, Any], base: Dict[str, Any], note: str) -> MechanismReading:
    return MechanismReading(mech.name, mech.family, clamp01(score), "inactive", ev, base, note=note)


class DirectedMechanism(Mechanism):
    """Mechanism whose episode outcome is judged against the direction its
    readings carried *while the episode was building / active / confirmed*.

    Readings here carry ``direction`` 0 once the evidence is gone, and the base
    lifecycle judges the outcome from the releasing reading — so every episode
    would resolve.  ``update`` remembers the last non-zero direction seen
    during the episode (``evidence["episode_direction"]``) and
    ``outcome_positive`` compares the mid move since the episode start against
    it: resolved when the mid moved that way, failed otherwise; None (→
    resolved) for mechanisms that imply no direction."""

    def __init__(self) -> None:
        super().__init__()
        self._episode_direction = 0

    def outcome_positive(self, ms: MarketState) -> Optional[bool]:
        d = self._episode_direction
        if d in (1, -1) and self._start_mid is not None and ms.mid is not None:
            return (ms.mid - self._start_mid) * d > 0
        return None

    def update(self, ms: MarketState, hist: StateHistory):
        prev = self._state
        st = super().update(ms, hist)
        if prev in ("inactive", "failed", "resolved") and st.state in ("building", "active"):
            self._episode_direction = 0          # a new episode: the previous episode's direction must not leak
        d = st.evidence.get("direction")
        if st.state in ("building", "active", "confirmed") and d in (1, -1):
            self._episode_direction = d
        elif st.state == "inactive":
            self._episode_direction = 0
        st.evidence["episode_direction"] = self._episode_direction
        return st


# ============================================================================ #19
@register
class ChurnAnomaly(DirectedMechanism):
    """#19 High-activity low-progress churn.

    Rule: intensity = ``trade_intensity`` (trades/min; else trades in the last
    120 s of tape rows per minute of span).  Baseline = ``trade_intensity`` of
    the states in [now − 900 s, now − 120 s) — the stretch *before* the burst
    window, so a sustained burst does not dilute its own z (≥ 5 points, else
    missing).  burst =
    ramp(z, 1.5 → 3.5) when the baseline has spread; when it is flat (std ≈ 0)
    the z is unknown and burst = ramp(intensity / mean, 2 → 4) is used instead;
    when it is silent (mean ≈ 0, no trades at all) the ratio is unbounded and
    the intensity is compared against a 1 trade/min floor (``burst_basis``
    names which: ``z`` / ``ratio_flat_baseline`` / ``ratio_silent_baseline``).  progress = max(|price_velocity| ticks/min,
    |Δmid over 120 s| / 2 min) — the velocity is taken from the state or from the
    mid series; low_progress = 1 − ramp(progress, 0.5 → 2.5).
    score = burst × low_progress.  direction 0 (churn implies no direction).
    """

    name = "churn_anomaly"
    family = FAMILY
    requires = ("trade_intensity", "price_velocity", "mid", "tick_size")
    baseline_s = 900.0
    progress_s = 120.0
    min_points = 5

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        cur = ms.trade_intensity
        basis = "state"
        if cur is None:
            rows = [r for r in fr.tape_rows(self.progress_s) if r["trades"] is not None]
            span = fr.span_s(self.progress_s)
            if rows and span > 0:
                cur = float(sum(r["trades"] for r in rows)) / (span / 60.0)
                basis = "tape_rows"
        if cur is None:
            return missing_reading(self, ["trade_intensity"], base)
        t_p = ms.t - timedelta(seconds=self.progress_s)
        prior = [v for t, v in field_series(fr, "trade_intensity", self.baseline_s, before_now=True) if t < t_p]
        if len(prior) < self.min_points:
            return missing_reading(self, [f"trade_intensity baseline (< {self.min_points} points in [−900 s, −120 s))"],
                                   base, {"intensity": cur, "baseline_points": len(prior)})
        m, sd = mean_std(prior)
        z = zscore_against(cur, prior, self.min_points)
        if z is not None:
            ratio = safe_div(cur, m) if m > _EPS else None
            burst, burst_basis = ramp(z, 1.5, 3.5), "z"
        elif m > _EPS:
            ratio = cur / m
            burst, burst_basis = ramp(ratio, 2.0, 4.0), "ratio_flat_baseline"
        else:
            # a silent baseline (no trades at all for the whole stretch): the ratio is unbounded, so
            # the intensity is compared against a 1 trade/min floor instead of a zero mean
            ratio = cur / 1.0
            burst, burst_basis = ramp(ratio, 2.0, 4.0), "ratio_silent_baseline"
        if not fr.tick:
            return missing_reading(self, ["tick_size"], base, {"intensity": cur, "intensity_z": z})
        vel = price_velocity_of(fr)
        net = fr.mid_change_ticks(self.progress_s)
        if vel is None and net is None:
            return missing_reading(self, ["mid"], base, {"intensity": cur, "intensity_z": z})
        progress = max(abs(vel) if vel is not None else 0.0,
                       abs(net) / (self.progress_s / 60.0) if net is not None else 0.0)
        low = 1.0 - ramp(progress, 0.5, 2.5)
        score = burst * low
        ev = {"intensity": cur, "intensity_basis": basis, "baseline_mean": m, "baseline_std": sd,
              "baseline_points": len(prior), "intensity_z": z, "intensity_ratio": ratio, "burst": burst,
              "burst_basis": burst_basis, "velocity_ticks_per_min": vel, "net_move_ticks_120s": net,
              "progress_ticks_per_min": progress, "low_progress": low, "direction": 0}
        return _reading(self, score, ev, base, f"z {z}, ratio {ratio}, progress {progress:.2f} t/min")


# ============================================================================ #22
@register
class BookTradeDivergence(DirectedMechanism):
    """#22 Book-vs-trade pressure divergence.

    Rule: book = ``book_pressure`` (else the imbalance blend), trade =
    ``trade_pressure`` (else signed flow / volume over 300 s of tape rows).
    conflict now = book × trade < 0; strength = min(|book|, |trade|) when in
    conflict, else 0.  Persistence = share of the states in the last 120 s that
    carry both pressures and are in conflict with min(|book|, |trade|) ≥ 0.15;
    with fewer than 3 such states it is unknown (None, ``unverified`` =
    ["persistence"]) and its factor stays neutral (0.5) — never 1.0 from the
    current point alone.
    score = ramp(strength, 0.15 → 0.5) × (0.5 + 0.5 × ramp(persistence, 0.3 → 0.9)).
    direction = sign(trade pressure): the transactions, not the display, are
    taken as the side that will carry the price.
    """

    name = "book_trade_divergence"
    family = FAMILY
    requires = ("book_pressure", "trade_pressure", "imb_topk", "signed_flow_window")
    window_s = 120.0
    min_conflict = 0.15
    min_persist_points = 3

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        bp = book_pressure_of(ms)
        tp = trade_pressure_now(fr)
        missing = [n for n, v in (("book_pressure", bp), ("trade_pressure", tp)) if v is None]
        if missing:
            return missing_reading(self, missing, base, {"book_pressure": bp, "trade_pressure": tp})
        conflict = bp * tp < 0
        strength = min(abs(bp), abs(tp)) if conflict else 0.0
        n_both = n_conf = 0
        for s in fr.states(self.window_s):
            b = book_pressure_of(s)
            t = trade_pressure_of(s) if s is not ms else tp
            if b is None or t is None:
                continue
            n_both += 1
            if b * t < 0 and min(abs(b), abs(t)) >= self.min_conflict:
                n_conf += 1
        # persistence is unknown (never 1.0 from the current point alone) with < 3 points carrying
        # both pressures: the factor stays neutral and the reading is flagged unverified
        share = n_conf / n_both if n_both >= self.min_persist_points else None
        persist = ramp(share, 0.3, 0.9) if share is not None else 0.5
        score = ramp(strength, 0.15, 0.5) * (0.5 + 0.5 * persist)
        direction = sign(tp) if score > 0 else 0
        ev = {"book_pressure": bp, "trade_pressure": tp, "conflict": conflict, "strength": strength,
              "conflict_share": share, "points": n_both, "engine_divergence": ms.pressure_divergence,
              "direction": direction, "window_s": self.window_s}
        if share is None:
            ev["unverified"] = ["persistence"]
        return _reading(self, score, ev, base, f"book {bp:.3f} vs trade {tp:.3f}, conflict share {share}")


# ============================================================================ #23
@register
class DepthPriceDivergence(DirectedMechanism):
    """#23 Depth–price divergence.

    Rule (window 120 s): depth signal = mean of the available components, each
    clipped to [−1, 1]:  Δ(bid share of visible depth) / 0.25 over the window
    and (ask migration − bid migration) / 4 ticks (the book engine's 60-s
    mean-distance changes: bid depth moving nearer and ask depth moving away
    both count as bid-ward).  price signal = Δmid over the window / 4 ticks,
    clipped.  Divergence when the two signals have opposite signs:
    score = ramp(|depth|, 0.2 → 0.8) × ramp(|price|, 0.25 → 1.0), else 0.
    direction = sign(depth signal) — depth is the pressure the price has not
    yet followed.
    """

    name = "depth_price_divergence"
    family = FAMILY
    requires = ("depth_ratio", "depth_migration_bid", "depth_migration_ask", "mid", "tick_size")
    window_s = 120.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        if not fr.tick:
            return missing_reading(self, ["tick_size"], base)
        rows = [(s.t, depth_ratio_of(s), mid_of(s)) for s in fr.states(self.window_s)]
        rows = [r for r in rows if r[1] is not None and r[2] is not None]
        if len(rows) < 3:
            return missing_reading(self, ["depth ratio / mid history (< 3 points in 120 s)"], base,
                                   {"points": len(rows)})
        d_ratio = rows[-1][1] - rows[0][1]
        mid_ticks = (rows[-1][2] - rows[0][2]) / fr.tick
        comps: List[float] = [clip1(d_ratio / 0.25)]
        mig_b, mig_a = ms.depth_migration_bid, ms.depth_migration_ask
        mig = None
        if mig_b is not None or mig_a is not None:
            mig = (mig_a or 0.0) - (mig_b or 0.0)
            comps.append(clip1(mig / 4.0))
        depth_signal = sum(comps) / len(comps)
        price_signal = clip1(mid_ticks / 4.0)
        conflict = depth_signal * price_signal < 0
        score = ramp(abs(depth_signal), 0.2, 0.8) * ramp(abs(price_signal), 0.25, 1.0) if conflict else 0.0
        direction = sign(depth_signal) if score > 0 else 0
        ev = {"depth_ratio_start": rows[0][1], "depth_ratio_now": rows[-1][1], "depth_ratio_change": d_ratio,
              "migration_bid": mig_b, "migration_ask": mig_a, "migration_net_ticks": mig,
              "depth_signal": depth_signal, "mid_change_ticks": mid_ticks, "price_signal": price_signal,
              "conflict": conflict, "points": len(rows), "direction": direction, "window_s": self.window_s}
        return _reading(self, score, ev, base, f"depth {depth_signal:.2f} vs price {price_signal:.2f}")


# ============================================================================ #24
@register
class FlowImpactDivergence(DirectedMechanism):
    """#24 Flow–impact divergence.

    Rule: flow = ``signed_flow_window`` (300 s; else Σ direction × volume of the
    window's tape rows); move = Δmid over 300 s in ticks.  Baselines from the
    trailing 900 s *before* now: base_impact = median |price_impact| (≥ 5
    points, else missing) and base_flow = median |signed_flow_window| (≥ 5
    points, else the tape's ``abs_flow_baseline``).  expected move =
    base_impact × |flow|; flow_rel = |flow| / base_flow (None with
    ``base_flow_silent`` when the baseline flow is 0: any non-zero flow is then
    unboundedly large relative to it and the flow ramps take their limits);
    ratio = |move| / expected (None when the baseline impact is 0 — a flow
    that moves nothing is then normal, not divergent, and A is 0).
      A  flow without impact:  ramp(flow_rel, 1.2 → 2.5) × (1 − ramp(ratio, 0.2 → 0.8))
      B  impact without flow:  ramp(|move| / max(base_impact × base_flow, 1 tick), 2 → 4)
                               × (1 − ramp(flow_rel, 0.3 → 1.0))
    score = max(A, B); ``mode`` names the winner.  direction = −sign(flow) in
    A (flow absorbed without a move) and −sign(move) in B (a move without flow
    lacks support).
    """

    name = "flow_impact_divergence"
    family = FAMILY
    requires = ("signed_flow_window", "price_impact", "mid", "tick_size")
    flow_s = 300.0
    baseline_s = 900.0
    min_points = 5

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        if not fr.tick:
            return missing_reading(self, ["tick_size"], base)
        flow = ms.signed_flow_window
        flow_basis = "state"
        if flow is None:
            flow, _ = fr.signed_volume_over(self.flow_s)
            flow_basis = "tape_rows"
        if flow is None:
            return missing_reading(self, ["signed_flow_window"], base)
        move = fr.mid_change_ticks(self.flow_s)
        if move is None:
            return missing_reading(self, ["mid history (300 s)"], base, {"signed_flow": flow})
        imp_hist = [abs(v) for _, v in field_series(fr, "price_impact", self.baseline_s, before_now=True)]
        if len(imp_hist) < self.min_points:
            return missing_reading(self, [f"price_impact baseline (< {self.min_points} points in 900 s)"], base,
                                   {"signed_flow": flow, "mid_change_ticks": move, "impact_points": len(imp_hist)})
        base_impact = _median(imp_hist)
        flow_hist = [abs(v) for _, v in field_series(fr, "signed_flow_window", self.baseline_s, before_now=True)]
        flow_basis_b = "history"
        if len(flow_hist) >= self.min_points:
            base_flow = _median(flow_hist)
        else:
            tp = ms.session_state.get("tape") if isinstance(ms.session_state, dict) else None
            base_flow = tp.get("abs_flow_baseline") if isinstance(tp, dict) else None
            flow_basis_b = "tape_abs_flow_baseline"
        if base_flow is None:
            return missing_reading(self, ["signed flow baseline (900 s)"], base,
                                   {"signed_flow": flow, "mid_change_ticks": move, "base_impact": base_impact})
        expected = base_impact * abs(flow)
        # a silent flow baseline (median |flow| = 0) makes any non-zero flow unboundedly large relative
        # to it: flow_rel is reported None with ``base_flow_silent`` and the ramps take their limits
        silent = base_flow <= _EPS
        flow_rel = abs(flow) / base_flow if not silent else None
        rel_hi = ramp(flow_rel, 1.2, 2.5) if not silent else (1.0 if abs(flow) > _EPS else 0.0)
        rel_lo = ramp(flow_rel, 0.3, 1.0) if not silent else (1.0 if abs(flow) > _EPS else 0.0)
        ratio = abs(move) / expected if expected > _EPS else None
        a = rel_hi * (1.0 - ramp(ratio, 0.2, 0.8)) if ratio is not None else 0.0
        normal_move = max(base_impact * base_flow, 1.0)
        b = ramp(abs(move) / normal_move, 2.0, 4.0) * (1.0 - rel_lo)
        if a >= b:
            mode, score, direction = "flow_without_impact", a, (-sign(flow) if a > 0 else 0)
        else:
            mode, score, direction = "impact_without_flow", b, (-sign(move) if b > 0 else 0)
        ev = {"signed_flow": flow, "flow_basis": flow_basis, "mid_change_ticks": move, "base_impact": base_impact,
              "impact_points": len(imp_hist), "base_flow": base_flow, "base_flow_basis": flow_basis_b,
              "expected_move_ticks": expected, "flow_rel": flow_rel, "base_flow_silent": silent, "impact_ratio": ratio,
              "normal_move_ticks": normal_move,
              "score_flow_without_impact": a, "score_impact_without_flow": b, "mode": mode,
              "engine_failed_response": ms.failed_response, "direction": direction}
        return _reading(self, score, ev, base, f"{mode}: flow_rel {flow_rel}, ratio {ratio}")


# ============================================================================ #25
@register
class ResilienceAsymmetry(DirectedMechanism):
    """#25 Resilience asymmetry.

    Rule (window 600 s): for every state carrying ``recovery_asymmetry`` (bid
    speed − ask speed, shares/s) the normalised asymmetry r = asym /
    (|speed_bid| + |speed_ask|) from the resilience record, else asym /
    max(|recovery_speed|, |asym|), so r ∈ [−1, 1]; a state carrying the
    asymmetry without any recovery speed cannot be normalised and is skipped.  The trailing run of states
    whose r has the sign of the current one gives run_share = run points /
    window points, run_span = seconds covered by the run, mean_r over the run.
    score = ramp(|mean_r|, 0.3 → 0.8) × ramp(run_share, 0.6 → 1.0) ×
    ramp(run_span, 20 → 90 s).  direction = sign(r): the side that rebuilds
    faster is the side that holds (+1 when the bid recovers faster).
    Missing when no recovery has been observed inside the window.
    """

    name = "resilience_asymmetry"
    family = FAMILY
    requires = ("recovery_asymmetry", "recovery_speed")
    window_s = 600.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        pts: List[Tuple[datetime, float, float]] = []
        for s in fr.states(self.window_s):
            asym = s.recovery_asymmetry
            if asym is None:
                continue
            rec = s.session_state.get("resilience") if isinstance(s.session_state, dict) else None
            sb = rec.get("recovery_speed_bid") if isinstance(rec, dict) else None
            sa = rec.get("recovery_speed_ask") if isinstance(rec, dict) else None
            if sb is not None and sa is not None:
                denom = abs(sb) + abs(sa)
            elif s.recovery_speed is not None:
                denom = max(abs(float(s.recovery_speed)), abs(asym))
            else:
                continue          # an asymmetry without any recovery speed cannot be normalised (it is not ±1)
            if denom <= _EPS:
                continue
            pts.append((s.t, float(asym), max(-1.0, min(1.0, float(asym) / denom))))
        if not pts:
            return missing_reading(self, ["recovery_asymmetry (no recovery observed in 600 s)"], base)
        r_now = pts[-1][2]
        sgn = sign(r_now)
        run = []
        for p in reversed(pts):
            if sign(p[2]) == sgn and sgn != 0:
                run.append(p)
            else:
                break
        run.reverse()
        run_share = len(run) / len(pts)
        run_span = (ms.t - run[0][0]).total_seconds() if run else 0.0
        mean_r = sum(p[2] for p in run) / len(run) if run else 0.0
        score = ramp(abs(mean_r), 0.3, 0.8) * ramp(run_share, 0.6, 1.0) * ramp(run_span, 20.0, 90.0)
        rec = ms.session_state.get("resilience") if isinstance(ms.session_state, dict) else None
        ev = {"asymmetry_now": pts[-1][1], "normalised_now": r_now, "mean_normalised": mean_r,
              "run_points": len(run), "points": len(pts), "run_share": run_share, "run_span_s": run_span,
              "resilience_state": ms.resilience_state,
              "curves_completed": rec.get("curves_completed") if isinstance(rec, dict) else None,
              "direction": sgn if score > 0 else 0, "window_s": self.window_s}
        return _reading(self, score, ev, base, f"mean r {mean_r:.2f} over {run_span:.0f} s")


# ============================================================================ #26
@register
class CompressionExpansion(DirectedMechanism):
    """#26 Compression → expansion.

    Rule: three phases of the mid / spread / velocity series: reference R =
    [now − 900, now − 300 s), compression C = [now − 300, now − 60 s),
    expansion E = [now − 60 s, now].  compression = mean of the available
    components, each clipped to [0, 1]:  1 − range_C / range_R of the mid (ticks),
    1 − std_C / std_R of spread_ticks, 1 − std_C / std_R of price_velocity — a
    component whose reference is flat is unknown and dropped; all unknown →
    missing.  expansion = max(|Δmid over E| / max(range_C, 1 tick),
    (spread now − mean spread_C) / 2 ticks).
    score = ramp(compression, 0.3 → 0.8) × ramp(expansion, 1.0 → 3.0).
    direction = sign(Δmid over E).
    """

    name = "compression_expansion"
    family = FAMILY
    requires = ("mid", "spread_ticks", "price_velocity", "tick_size")
    ref_s = 900.0
    comp_s = 300.0
    exp_s = 60.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        t_c = ms.t - timedelta(seconds=self.comp_s)
        t_e = ms.t - timedelta(seconds=self.exp_s)
        mids = fr.series(mid_of, self.ref_s)
        spreads = fr.series(lambda s: spread_ticks_of(s, tick), self.ref_s)
        vels = field_series(fr, "price_velocity", self.ref_s)

        def phase(pts, lo_excl: Optional[datetime], hi: datetime):
            return [v for t, v in pts if (lo_excl is None or t >= lo_excl) and t < hi]

        r_mid, c_mid = phase(mids, None, t_c), phase(mids, t_c, t_e)
        e_mid = [v for t, v in mids if t >= t_e]
        if len(r_mid) < 3 or len(c_mid) < 3 or len(e_mid) < 2:
            return missing_reading(self, ["mid history (900 s: reference / compression / expansion phases)"], base,
                                   {"reference_points": len(r_mid), "compression_points": len(c_mid),
                                    "expansion_points": len(e_mid)})
        range_r = (max(r_mid) - min(r_mid)) / tick
        range_c = (max(c_mid) - min(c_mid)) / tick
        comps: Dict[str, Optional[float]] = {}
        comps["mid_range"] = clamp01(1.0 - range_c / range_r) if range_r > _EPS else None
        r_sp, c_sp = phase(spreads, None, t_c), phase(spreads, t_c, t_e)
        _, sd_r = mean_std(r_sp)
        _, sd_c = mean_std(c_sp)
        comps["spread_std"] = clamp01(1.0 - sd_c / sd_r) if (sd_r is not None and sd_r > _EPS and sd_c is not None) else None
        r_v, c_v = phase(vels, None, t_c), phase(vels, t_c, t_e)
        _, vd_r = mean_std(r_v)
        _, vd_c = mean_std(c_v)
        comps["velocity_std"] = clamp01(1.0 - vd_c / vd_r) if (vd_r is not None and vd_r > _EPS and vd_c is not None) else None
        avail = [v for v in comps.values() if v is not None]
        if not avail:
            return missing_reading(self, ["reference-phase variation (flat 900-s history)"], base,
                                   {"range_reference_ticks": range_r, "range_compression_ticks": range_c})
        compression = sum(avail) / len(avail)
        move_e = (e_mid[-1] - e_mid[0]) / tick
        exp_mid = abs(move_e) / max(range_c, 1.0)
        mean_c_sp = (sum(c_sp) / len(c_sp)) if c_sp else None
        sp_now = spread_ticks_of(ms, tick)
        exp_spread = ((sp_now - mean_c_sp) / 2.0) if (sp_now is not None and mean_c_sp is not None) else None
        expansion = max(exp_mid, exp_spread if exp_spread is not None else 0.0)
        score = ramp(compression, 0.3, 0.8) * ramp(expansion, 1.0, 3.0)
        direction = sign(move_e) if score > 0 else 0
        ev = {"range_reference_ticks": range_r, "range_compression_ticks": range_c, "spread_std_reference": sd_r,
              "spread_std_compression": sd_c, "velocity_std_reference": vd_r, "velocity_std_compression": vd_c,
              "components": comps, "compression": compression, "move_expansion_ticks": move_e,
              "expansion_mid": exp_mid, "expansion_spread": exp_spread, "expansion": expansion,
              "direction": direction, "phases_s": [self.ref_s, self.comp_s, self.exp_s]}
        return _reading(self, score, ev, base, f"compression {compression:.2f}, expansion {expansion:.2f}")


# ============================================================================ #27
@register
class FalseBreakout(DirectedMechanism):
    """#27 False breakout / failed pressure.

    Rule: reference range [lo, hi] = min / max mid over [now − 600, now − 180 s)
    (≥ 5 points); the breakout phase is the last 180 s (≥ 3 points).  The
    excursion is the larger of (max mid − hi) and (lo − min mid) inside the
    breakout phase strictly before now, in ticks, with its direction d and
    time t_peak.  re-entry = (peak − mid now) / (peak − boundary) along d,
    clipped to [0, 1] (1 = back at the range edge or inside).  Pressure at the
    peak vs now (``combined_pressure`` else the book/trade blend): reversal =
    clamp01(((p_peak − p_now) × d) / 0.5); when neither is carried the pressure
    layer's ``pressure_reversal`` flag counts as 1, else the factor is 0.5 and
    ``unverified`` = ["pressure"].  time factor = 1 − ramp(seconds since the
    peak, 90 → 180).
    score = ramp(excursion, 1 → 4 ticks) × ramp(re-entry, 0.6 → 1.0) ×
    (0.4 + 0.6 × reversal) × time factor.  direction = −d.
    """

    name = "false_breakout"
    family = FAMILY
    requires = ("mid", "tick_size", "combined_pressure", "pressure_reversal")
    window_s = 600.0
    breakout_s = 180.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        t_b = ms.t - timedelta(seconds=self.breakout_s)
        pts = fr.series(mid_of, self.window_s)
        ref = [(t, v) for t, v in pts if t < t_b]
        brk = [(t, v) for t, v in pts if t >= t_b]
        if len(ref) < 5 or len(brk) < 3:
            return missing_reading(self, ["mid history (600 s reference + 180 s breakout phase)"], base,
                                   {"reference_points": len(ref), "breakout_points": len(brk)})
        lo, hi = min(v for _, v in ref), max(v for _, v in ref)
        body = brk[:-1]
        t_up, peak_up = max(body, key=lambda p: p[1])
        t_dn, peak_dn = min(body, key=lambda p: p[1])
        exc_up, exc_dn = (peak_up - hi) / tick, (lo - peak_dn) / tick
        if exc_up >= exc_dn:
            d, exc, peak, boundary, t_peak = 1, exc_up, peak_up, hi, t_up
        else:
            d, exc, peak, boundary, t_peak = -1, exc_dn, peak_dn, lo, t_dn
        mid_now = brk[-1][1]
        ev: Dict[str, Any] = {"range_lo": lo, "range_hi": hi, "excursion_ticks": max(0.0, exc), "breakout_direction": d,
                              "peak": peak, "t_peak": t_peak.isoformat(), "mid_now": mid_now, "direction": 0,
                              "reference_points": len(ref), "breakout_points": len(brk)}
        if exc <= 0:
            ev.update({"reentry": None, "reversal": None, "time_factor": None})
            return _reading(self, 0.0, ev, base, "no excursion beyond the reference range")
        reentry = clamp01(((peak - mid_now) * d) / ((peak - boundary) * d))
        s_peak = fr.at_or_before(t_peak)
        p_peak = combined_pressure_of(s_peak) if s_peak is not None else None
        p_now = combined_pressure_of(ms)
        unverified: List[str] = []
        if p_peak is not None and p_now is not None:
            reversal = clamp01(((p_peak - p_now) * d) / 0.5)
            rev_basis = "pressure_delta"
        elif ms.pressure_reversal:
            reversal, rev_basis = 1.0, "engine_flag"
        else:
            reversal, rev_basis = 0.5, "unverified"
            unverified.append("pressure")
        age = (ms.t - t_peak).total_seconds()
        tf = 1.0 - ramp(age, 90.0, 180.0)
        score = ramp(exc, 1.0, 4.0) * ramp(reentry, 0.6, 1.0) * (0.4 + 0.6 * reversal) * tf
        ev.update({"reentry": reentry, "pressure_at_peak": p_peak, "pressure_now": p_now, "reversal": reversal,
                   "reversal_basis": rev_basis, "seconds_since_peak": age, "time_factor": tf,
                   "direction": -d if score > 0 else 0})
        if unverified:
            ev["unverified"] = unverified
        return _reading(self, score, ev, base, f"excursion {exc:.1f} t, reentry {reentry:.2f}, reversal {reversal:.2f}")


# ============================================================================ #35
@register
class TrapPressure(DirectedMechanism):
    """#35 Trap-like pressure.

    Rule (window 300 s, ≥ 4 two-sided states): the reference state is the
    oldest in the window; its top-K imbalance names the pressure side (bid when
    > 0) and must be ≥ 0.15 in magnitude, else score 0.  centre_ref = Σ price
    × qty / Σ qty of that side *at the reference state* (fixed: the distance
    must measure the price approaching the displayed pressure, not the centre
    drifting towards the touch as the wall is withdrawn).  Per state on that
    side: depth = visible depth, dist = |mid − centre_ref| in ticks.
    withdrawal = (depth_ref − depth_now − volume traded over
    the span) / depth_ref, i.e. the part of the fall the tape cannot explain
    (no tape → unverified, damped × 0.75); approach = dist_ref − dist_now
    (ticks); co-movement = Pearson(dist, depth) over the window (depth falling
    as the distance shrinks → +1).
    score = ramp(|imb_ref|, 0.2 → 0.5) × ramp(withdrawal, 0.3 → 0.8) ×
    ramp(approach, 1 → 3) × (0.5 + 0.5 × ramp(co-movement, 0.3 → 0.9)).
    direction = −1 for a bid trap, +1 for an ask trap (the displayed wall was
    not there to hold).
    """

    name = "trap_pressure"
    family = FAMILY
    requires = ("bids", "asks", "mid", "tick_size", "trade_volume")
    window_s = 300.0
    min_imb = 0.15

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        states = [s for s in fr.states(self.window_s) if levels_of(s, "bid") and levels_of(s, "ask") and mid_of(s) is not None]
        if len(states) < 4:
            return missing_reading(self, ["two-sided book history (< 4 points in 300 s)"], base, {"points": len(states)})
        ref = states[0]
        tb, ta = topk_depth(ref, "bid") or 0.0, topk_depth(ref, "ask") or 0.0
        imb_ref = (tb - ta) / (tb + ta) if tb + ta > 0 else 0.0
        side = "bid" if imb_ref > 0 else "ask"
        ev: Dict[str, Any] = {"imb_ref": imb_ref, "side": side, "points": len(states), "direction": 0,
                              "t_ref": ref.t.isoformat(), "window_s": self.window_s}
        if abs(imb_ref) < self.min_imb:
            ev.update({"withdrawal": None, "approach_ticks": None, "comovement": None})
            return _reading(self, 0.0, ev, base, "no one-sided displayed pressure at the window start")
        lv_ref = levels_of(ref, side)
        tot_ref = sum(q for _, q in lv_ref)
        if tot_ref <= 0:
            return missing_reading(self, [f"{side} depth at the window start"], base, ev)
        centre_ref = sum(p * q for p, q in lv_ref) / tot_ref     # the displayed pressure's centre, fixed at t_ref
        dists: List[float] = []
        depths: List[float] = []
        for s in states:
            tot = sum(q for _, q in levels_of(s, side))
            if tot <= 0:
                continue
            dists.append(abs(mid_of(s) - centre_ref) / tick)
            depths.append(tot)
        if len(depths) < 4:
            return missing_reading(self, [f"{side} depth history"], base, ev)
        ev["centre_ref"] = centre_ref
        depth_ref, depth_now = depths[0], depths[-1]
        span = (ms.t - ref.t).total_seconds()
        traded = fr.volume_over(span) if span > 0 else None
        fall = max(0.0, depth_ref - depth_now)
        unexplained = fall - min(fall, traded or 0.0)
        withdrawal = unexplained / depth_ref if depth_ref > 0 else 0.0
        approach = dists[0] - dists[-1]
        comove = pearson(dists, depths)
        damp = 1.0 if traded is not None else 0.75
        score = (ramp(abs(imb_ref), 0.2, 0.5) * ramp(withdrawal, 0.3, 0.8) * ramp(approach, 1.0, 3.0)
                 * (0.5 + 0.5 * ramp(comove, 0.3, 0.9)) * damp)
        direction = (-1 if side == "bid" else 1) if score > 0 else 0
        ev.update({"depth_ref": depth_ref, "depth_now": depth_now, "traded_volume": traded, "unexplained_removal": unexplained,
                   "withdrawal": withdrawal, "dist_ref_ticks": dists[0], "dist_now_ticks": dists[-1],
                   "approach_ticks": approach, "comovement": comove, "direction": direction})
        if traded is None:
            ev["unverified"] = ["tape"]
        return _reading(self, score, ev, base, f"{side} withdrawal {withdrawal:.2f}, approach {approach:.1f} t")


# ============================================================================ #37
@register
class TradeChurnRepetition(DirectedMechanism):
    """#37 Repetitive trade-churn anomaly.

    Rule (window 600 s): distinct tape intervals with volume > 0 (≥ 4, else
    missing) are grouped by identical (round(volume), trade count); g = size
    of the largest group, max_share = g / n, repeat_share = share of rows in
    groups of ≥ 2; regularity = 1 − cv of the inter-arrival times inside the
    largest group (needs g ≥ 3, else the cadence factor is neutral 0.6).
    flat = 1 − ramp(mid range over the window in ticks, 1 → 3).
    score = ramp(max_share, 0.3 → 0.75) × ramp(g, 3 → 6) × (0.6 + 0.4 ×
    regularity) × flat.  direction 0.
    """

    name = "trade_churn_repetition"
    family = FAMILY
    requires = ("interval_volume", "interval_trades", "trade_volume", "mid", "tick_size")
    window_s = 600.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        rows = [r for r in fr.tape_rows(self.window_s) if r["volume"] is not None and r["volume"] > 0]
        if len(rows) < 4:
            what = "interval_volume" if not fr.tape_rows(self.window_s) else "tape intervals (< 4 rows with volume in 600 s)"
            return missing_reading(self, [what], base, {"rows": len(rows)})
        groups: Dict[Tuple[float, Optional[float]], List[datetime]] = {}
        for r in rows:
            key = (round(float(r["volume"])), None if r["trades"] is None else round(float(r["trades"])))
            groups.setdefault(key, []).append(r["t"])
        key_max, times = max(groups.items(), key=lambda kv: len(kv[1]))
        g = len(times)
        n = len(rows)
        max_share = g / n
        repeat_share = sum(len(v) for v in groups.values() if len(v) >= 2) / n
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
        cv = _cv(gaps) if len(gaps) >= 2 else None
        regularity = clamp01(1.0 - cv) if cv is not None else None
        cadence = 0.6 + 0.4 * regularity if regularity is not None else 0.6
        mids = fr.series(mid_of, self.window_s)
        if mids and fr.tick:
            rng = (max(v for _, v in mids) - min(v for _, v in mids)) / fr.tick
            flat = 1.0 - ramp(rng, 1.0, 3.0)
        else:
            rng, flat = None, 0.5
        score = ramp(max_share, 0.3, 0.75) * ramp(g, 3, 6) * cadence * flat
        ev = {"rows": n, "groups": len(groups), "largest_group": g, "largest_key": {"volume": key_max[0], "trades": key_max[1]},
              "max_share": max_share, "repeat_share": repeat_share, "cadence_cv": cv, "regularity": regularity,
              "mid_range_ticks": rng, "flat": flat, "direction": 0, "window_s": self.window_s}
        if rng is None:
            ev["unverified"] = ["mid"]
        return _reading(self, score, ev, base, f"{g}/{n} identical intervals, flat {flat:.2f}")
