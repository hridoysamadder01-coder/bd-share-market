"""ofi_shape_family — order-flow imbalance, deep-book shape and the recovery
curve (MECHANISMS.md #40, #41, #42).

Same conventions as ``divergence_family``: rolling causal windows over the
current ``MarketState`` + ``StateHistory``, continuous scores, ``None`` for
what is not observable and ``evidence["missing"]`` when the mechanism needs
it, ``evidence["direction"]`` ∈ {+1, −1, 0}, ``baseline`` from
``queue_family.baselines``.

Rules:
  ofi_state             rolling OFI (``ofi_window``, else Σ ``ofi`` over 60 s)
                        as a z against the trailing 900 s that precede the
                        120-s persistence window (depth-normalised magnitude
                        when that baseline is flat or short) × sign persistence
                        over the last 120 s.
  deep_book_shape       per side the cumulative depth over the displayed
                        levels: load index = (A_u − A)/(1 − A_u), A = mean
                        cumulative share, A_u its uniform-book value (+1 =
                        back-loaded, −1 = front-loaded; slope / quadratic
                        curvature reported); asymmetry = ``side_asymmetry`` (else the
                        visible-depth share); signal = 0.5 asym + 0.25 conv_ask
                        − 0.25 conv_bid; regime label + 120-s persistence.
  recovery_curve_state  exponential fit of the depth deficit 1 − share(s) along
                        the resilience engine's recovery curve: τ = −1/slope
                        of ln(deficit) vs s, R² of that fit, progress towards
                        the pre-shock depth.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..state import MarketState
from ..windows import clamp01, curvature as quad_curvature, safe_div, sign, slope as ls_slope
from .base import Mechanism, MechanismReading, StateHistory, register
from .divergence_family import DirectedMechanism, _reading, clip1, field_series, mean_std, zscore_against
from .queue_family import Frame, baselines, levels_of, missing_reading, ramp, topk_depth, visible_depth

_EPS = 1e-9
FAMILY = "ofi_shape"


# ============================================================================ #40
@register
class OfiState(DirectedMechanism):
    """#40 Order Flow Imbalance state.

    Rule: ofi_w = ``ofi_window`` (the book engine's rolling sum), else Σ of the
    per-update ``ofi`` over the last 60 s (``ofi_basis`` names which); missing
    when neither exists.  Baseline = ``ofi_window`` of the states in
    [now − 900 s, now − 120 s) — the stretch *before* the persistence window,
    so a persistent imbalance does not dilute its own z.  magnitude =
    ramp(|z|, 1.0 → 2.5) when the baseline has ≥ 4 points and spread; else the
    depth-normalised |ofi_w| / (top-K bid + top-K ask) with ramp(0.15 → 0.5)
    (``magnitude_basis``).  persistence = share of the states in the last
    120 s whose OFI has the sign of ofi_w (run seconds also reported).
    score = magnitude × (0.4 + 0.6 × ramp(persistence, 0.5 → 0.9)).
    direction = sign(ofi_w).
    """

    name = "ofi_state"
    family = FAMILY
    requires = ("ofi", "ofi_window", "bids", "asks")
    persist_s = 120.0
    baseline_s = 900.0
    sum_s = 60.0

    @staticmethod
    def _ofi_of(s: MarketState) -> Optional[float]:
        if s.ofi_window is not None:
            return float(s.ofi_window)
        return None if s.ofi is None else float(s.ofi)

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        ofi_w = ms.ofi_window
        basis = "ofi_window"
        if ofi_w is None:
            pts = field_series(fr, "ofi", self.sum_s)
            if pts:
                ofi_w = float(sum(v for _, v in pts))
                basis = "sum_ofi_60s"
        if ofi_w is None:
            return missing_reading(self, ["ofi"], base)
        t_p = ms.t - timedelta(seconds=self.persist_s)
        prior = [v for t, v in field_series(fr, "ofi_window", self.baseline_s, before_now=True) if t < t_p]
        z = zscore_against(ofi_w, prior)
        tb, ta = topk_depth(ms, "bid"), topk_depth(ms, "ask")
        depth = (tb or 0.0) + (ta or 0.0)
        norm = ofi_w / depth if depth > 0 else None
        if z is not None:
            magnitude, mbasis = ramp(abs(z), 1.0, 2.5), "z"
        elif norm is not None:
            magnitude, mbasis = ramp(abs(norm), 0.15, 0.5), "depth_normalised"
        else:
            return missing_reading(self, ["ofi baseline (900 s) or displayed depth"], base,
                                   {"ofi_window": ofi_w, "baseline_points": len(prior)})
        sgn = sign(ofi_w)
        win = [(s.t, self._ofi_of(s) if s is not ms else ofi_w) for s in fr.states(self.persist_s)]
        win = [(t, v) for t, v in win if v is not None]
        same = [t for t, v in win if sign(v) == sgn]
        share = len(same) / len(win) if win else 0.0
        run_start = ms.t
        for t, v in reversed(win):
            if sign(v) == sgn:
                run_start = t
            else:
                break
        run_s = (ms.t - run_start).total_seconds()
        score = magnitude * (0.4 + 0.6 * ramp(share, 0.5, 0.9)) if sgn != 0 else 0.0
        m, sd = mean_std(prior)
        ev = {"ofi_window": ofi_w, "ofi_basis": basis, "ofi_last": ms.ofi, "baseline_mean": m, "baseline_std": sd,
              "baseline_points": len(prior), "ofi_z": z, "ofi_depth_normalised": norm, "magnitude": magnitude,
              "magnitude_basis": mbasis, "sign_share_120s": share, "sign_run_s": run_s, "points": len(win),
              "direction": sgn if score > 0 else 0}
        return _reading(self, score, ev, base, f"ofi {ofi_w:.0f}, z {z}, sign share {share:.2f}")


# ============================================================================ #41
def side_shape(s: MarketState, side: str, tick: float) -> Optional[Dict[str, Any]]:
    """Shape of one side's displayed depth: cumulative qty y against distance
    d (ticks) from the touch over ≥ 3 levels.  slope = least-squares dy/dd
    (qty per tick; the book engine's ``depth_slope_*`` when carried), curvature
    = 2a of the quadratic fit (``depth_curvature_*`` when carried) — both
    reported as evidence.  The regime measure is the bounded load index
    convexity = (A_u − A) / (1 − A_u) with A = mean over the n displayed levels
    of the cumulative depth share y_i / Y and A_u = (n + 1) / (2n) the same
    mean for a uniform side: −1 = all size at the touch (front-loaded), 0 =
    uniform, +1 = all size at the deepest level (back-loaded)."""
    lv = levels_of(s, side)
    n = len(lv)
    if n < 3:
        return None
    touch = lv[0][0]
    xs = [abs(p - touch) / tick for p, _ in lv]
    ys: List[float] = []
    cum = 0.0
    for _, q in lv:
        cum += q
        ys.append(cum)
    total, span = cum, xs[-1]
    sl = getattr(s, f"depth_slope_{side}", None)
    cv = getattr(s, f"depth_curvature_{side}", None)
    sl = float(sl) if sl is not None else ls_slope(xs, ys)
    cv = float(cv) if cv is not None else quad_curvature(xs, ys)
    conv = None
    if total > 0:
        a_mean = sum(y / total for y in ys) / n
        a_uni = (n + 1) / (2.0 * n)
        conv = (a_uni - a_mean) / (1.0 - a_uni)
    return {"levels": n, "total": total, "span_ticks": span, "slope": sl, "curvature": cv, "convexity": conv,
            "touch_share": (lv[0][1] / total) if total > 0 else None}


def _shape_label(conv: Optional[float], thr: float = 0.15) -> str:
    if conv is None:
        return "unknown"
    if conv <= -thr:
        return "front_loaded"
    if conv >= thr:
        return "back_loaded"
    return "linear"


@register
class DeepBookShape(DirectedMechanism):
    """#41 Deep-book shape / curvature regime.

    Rule: both sides need ≥ 3 displayed levels (else missing).  Per side the
    load index of the cumulative depth over the displayed levels (see
    ``side_shape``; −1 … +1): front-loaded (< −0.15) = size at the touch,
    back-loaded (> 0.15) = size deep; the least-squares slope and quadratic
    curvature of cumulative depth vs distance are reported alongside.
    asymmetry = ``side_asymmetry`` (else (Vb − Va) / (Vb + Va) of
    the visible depth).  signal = 0.5 × asymmetry + 0.25 × conv_ask − 0.25 ×
    conv_bid, so a heavier, front-loaded bid against a thin, back-loaded ask
    is +1-ward.  regime = "<bid_heavy|ask_heavy|balanced>/<bid shape>/<ask
    shape>".  persistence = share of the states in the last 120 s whose signal
    has the same sign and |signal| ≥ 0.1.
    score = ramp(|signal|, 0.15 → 0.5) × (0.5 + 0.5 × ramp(persistence, 0.5 → 1.0)).
    direction = sign(signal).
    """

    name = "deep_book_shape"
    family = FAMILY
    requires = ("bids", "asks", "depth_slope_bid", "depth_curvature_bid", "side_asymmetry", "tick_size")
    persist_s = 120.0
    asym_thr = 0.15

    def _signal(self, s: MarketState, tick: float) -> Optional[Tuple[float, Dict[str, Any]]]:
        b, a = side_shape(s, "bid", tick), side_shape(s, "ask", tick)
        if b is None or a is None:
            return None
        asym = s.side_asymmetry
        if asym is None:
            vb, va = visible_depth(s, "bid") or 0.0, visible_depth(s, "ask") or 0.0
            asym = (vb - va) / (vb + va) if vb + va > 0 else None
        if asym is None:
            return None
        cb, ca = clip1(b["convexity"]) or 0.0, clip1(a["convexity"]) or 0.0
        sig = 0.5 * float(asym) + 0.25 * ca - 0.25 * cb
        return sig, {"bid": b, "ask": a, "asymmetry": float(asym), "conv_bid": cb, "conv_ask": ca}

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        cur = self._signal(ms, tick)
        if cur is None:
            return missing_reading(self, ["≥ 3 displayed levels per side"], base,
                                   {"bid_levels": len(levels_of(ms, "bid")), "ask_levels": len(levels_of(ms, "ask"))})
        sig, parts = cur
        sgn = sign(sig)
        n = same = 0
        for s in fr.states(self.persist_s):
            r = cur if s is ms else self._signal(s, tick)
            if r is None:
                continue
            n += 1
            if sign(r[0]) == sgn and abs(r[0]) >= 0.1:
                same += 1
        share = same / n if n else 0.0
        heavy = "bid_heavy" if parts["asymmetry"] >= self.asym_thr else ("ask_heavy" if parts["asymmetry"] <= -self.asym_thr else "balanced")
        regime = f"{heavy}/bid_{_shape_label(parts['conv_bid'])}/ask_{_shape_label(parts['conv_ask'])}"
        score = ramp(abs(sig), 0.15, 0.5) * (0.5 + 0.5 * ramp(share, 0.5, 1.0))
        ev = {"signal": sig, "asymmetry": parts["asymmetry"], "convexity_bid": parts["conv_bid"], "convexity_ask": parts["conv_ask"],
              "slope_bid": parts["bid"]["slope"], "slope_ask": parts["ask"]["slope"], "curvature_bid": parts["bid"]["curvature"],
              "curvature_ask": parts["ask"]["curvature"], "touch_share_bid": parts["bid"]["touch_share"],
              "touch_share_ask": parts["ask"]["touch_share"], "levels_bid": parts["bid"]["levels"], "levels_ask": parts["ask"]["levels"],
              "regime": regime, "persistence": share, "points": n, "concentration_bid": ms.depth_concentration_bid,
              "concentration_ask": ms.depth_concentration_ask, "direction": sgn if score > 0 else 0}
        return _reading(self, score, ev, base, f"{regime}, signal {sig:.2f}, persistence {share:.2f}")


# ============================================================================ #42
def fit_exponential_recovery(curve: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
    """Fit share(s) = 1 − d0 · exp(−s/τ) to [(s, share)] by least squares on
    ln(1 − share) vs s over the points with a positive deficit.  Returns
    tau_s (None unless the deficit shrinks), r2, d0, points_fit."""
    pts = [(float(s), 1.0 - float(sh)) for s, sh in curve if sh is not None and 1.0 - float(sh) > 1e-6]
    out: Dict[str, Any] = {"tau_s": None, "r2": None, "d0": None, "points_fit": len(pts), "slope": None}
    if len(pts) < 3:
        return out
    xs = [s for s, _ in pts]
    ys = [math.log(d) for _, d in pts]
    b = ls_slope(xs, ys)
    if b is None:
        return out
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > _EPS else None
    out.update({"slope": b, "d0": math.exp(a), "r2": r2, "tau_s": (-1.0 / b) if b < -_EPS else None})
    return out


@register
class RecoveryCurveState(DirectedMechanism):
    """#42 Resiliency recovery curve.

    Rule: ``recovery_curve`` = [(seconds since the shock, recovered share)]
    from the resilience engine (missing when no shock has been observed).
    With ≥ 3 points: progress = (share_now − share_0) / (1 − share_0);
    exponential fit of the deficit (``fit_exponential_recovery``) gives τ and
    R²; τ is None when the deficit is not shrinking (then the fit factor is 0).
    speed class: fast τ ≤ 60 s, moderate ≤ 300 s, else slow.
    score = ramp(points, 3 → 8) × ramp(R², 0.4 → 0.85) × ramp(progress, 0.2 → 0.7).
    direction = −sign(shock mid move) from the resilience record (a recovering
    book implies the shock move reverts), else +1 after a bid-side shock, −1
    after an ask-side shock.
    """

    name = "recovery_curve_state"
    family = FAMILY
    requires = ("recovery_curve", "resilience_state", "recovery_speed")

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        curve = ms.recovery_curve
        rec = ms.session_state.get("resilience") if isinstance(ms.session_state, dict) else None
        rec = rec if isinstance(rec, dict) else {}
        if not curve:
            return missing_reading(self, ["recovery_curve (no shock observed)"], base,
                                   {"resilience_state": ms.resilience_state, "curves_completed": rec.get("curves_completed")})
        pts = [(float(s), float(sh)) for s, sh in curve if sh is not None]
        shock = rec.get("shock") if isinstance(rec.get("shock"), dict) else {}
        move = shock.get("move_ticks")
        side = rec.get("side")
        if move:
            direction = -sign(move)
        elif side == "bid":
            direction = 1
        elif side == "ask":
            direction = -1
        else:
            direction = 0
        ev: Dict[str, Any] = {"points": len(pts), "resilience_state": ms.resilience_state, "recovery_speed": ms.recovery_speed,
                              "shock_side": side, "shock_move_ticks": move, "elapsed_s": rec.get("elapsed_s"),
                              "curve_state": rec.get("state"), "direction": 0}
        if len(pts) < 3:
            ev.update({"progress": None, "tau_s": None, "r2": None})
            return _reading(self, 0.0, ev, base, f"curve too short ({len(pts)} points)")
        s0, s_now = pts[0][1], pts[-1][1]
        progress = ((s_now - s0) / (1.0 - s0)) if s0 < 1.0 - _EPS else None
        fit = fit_exponential_recovery(pts)
        tau = fit["tau_s"]
        r2 = fit["r2"] if tau is not None else 0.0
        speed = None if tau is None else ("fast" if tau <= 60 else ("moderate" if tau <= 300 else "slow"))
        score = ramp(len(pts), 3, 8) * ramp(r2, 0.4, 0.85) * ramp(progress, 0.2, 0.7)
        ev.update({"share_at_shock": s0, "share_now": s_now, "progress": progress, "tau_s": tau, "r2": r2, "d0": fit["d0"],
                   "points_fit": fit["points_fit"], "speed_class": speed, "direction": direction if score > 0 else 0})
        return _reading(self, score, ev, base, f"tau {tau}, r2 {r2}, progress {progress}")
