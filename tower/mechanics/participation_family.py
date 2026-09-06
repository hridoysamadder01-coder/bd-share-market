"""participation_family — passive repricing / participation / metaorder mechanisms
(MECHANISMS.md #8, #9, #10, #43).

Same conventions as ``queue_family`` / ``accumulation_family``: causal windows
over history + current state, continuous scores, ``None`` for the
unobservable, ``evidence["missing"]`` naming absent inputs,
``evidence["direction"]`` for the implied price direction and the simple
baselines at the same instant.

Rules in one line each (the mechanism docstrings restate them fully):

  pegged_repricing        a side's best price moves (≥ 1 tick) in the same
                          direction as its reference (the last traded price
                          when carried, else the opposite best) and lands ≤ 2
                          ticks behind it, repeatedly, re-posting similar
                          sizes (300 s).
  participation_footprint 60-s buckets of symbol volume over 600 s as a share
                          of the market's 60-s volume (``cross['market_volume_60s']``
                          when present) else of the symbol's own window mean:
                          low coefficient of variation, full coverage.
  metaorder_trajectory    persistent one-sided flow (600 s) whose normalised
                          impact path (cumulative signed flow → mid move) is
                          concave: ``windows.curvature`` < 0 with a positive
                          ``windows.slope``.
  metaorder_impact        |Δmid| (ticks) against |cumulative signed flow|:
                          concave quadratic fit (normalised curvature), a
                          log–log exponent near ½ with a good R², compared
                          with the tape engine's linear ``price_impact``.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..state import MarketState
from ..windows import clamp01, curvature, safe_div, sign, slope
from .base import Mechanism, MechanismReading, StateHistory, register
from .queue_family import Frame, _EPS, _cv, baselines, best_of, geo_mean, mid_of, missing_reading, ramp
from .accumulation_family import (DirectedMechanism, all_states, classified_rows, cum_delta, flow_summary, reading,
                                  state_before, tape_missing, tape_rows)


# ============================================================================ #8
@register
class PeggedRepricing(DirectedMechanism):
    """#8 Pegged / passive repricing.

    Rule (window 300 s): per side, consecutive states where the side's best
    price moved by ≥ 1 tick are *moves*.  The reference price is the last
    traded price (``ltp``) when the state carries one, else the opposite best.
    A move is a *follow* when the reference moved ≥ 1 tick in the same
    direction over the same step or the step before it (a re-post may lag the
    reference by one poll) and the new best lands ≤ 2 ticks behind the
    reference (a bid below it, an ask above it).  With the opposite best as
    the reference a step in which both sides moved by the same amount is a
    parallel shift — each side would be the other's "leader" — and counts as a
    move but not a follow (``parallel_moves``).  consistency = follows / moves;
    similarity = 1 − min(1, cv(re-posted touch sizes) / 0.5) (0.5 with a single
    follow).  Per side score = ramp(follows, 1.5 → 5) × ramp(consistency, 0.5 →
    0.9) × (0.3 + 0.7 × similarity); the stronger side wins.  direction = sign
    of the pegged side's net price drift over the window when |drift| ≥ 2
    ticks, else 0.
    """

    name = "pegged_repricing"
    family = "participation"
    requires = ("best_bid", "best_ask", "bid_qty1", "ask_qty1", "ltp", "tick_size")
    window_s = 300.0
    behind_ticks = 2.0

    def _side(self, fr: Frame, side: str, tick: float) -> Dict[str, Any]:
        opp = "ask" if side == "bid" else "bid"
        ser = []
        for s in fr.states(self.window_s):
            p, q = best_of(s, side)
            if p is None or q is None:
                continue
            ref = s.ltp if s.ltp is not None else best_of(s, opp)[0]
            ser.append((s.t, float(p), float(q), (float(ref) if ref is not None else None),
                        ("ltp" if s.ltp is not None else "opposite_best")))
        moves = follows = parallel = 0
        sizes: List[float] = []
        ref_sources = set()
        for i in range(1, len(ser)):
            a, b = ser[i - 1], ser[i]
            dp = (b[1] - a[1]) / tick
            if abs(dp) < 0.5:
                continue
            moves += 1
            if a[3] is None or b[3] is None:
                continue
            d_now = (b[3] - a[3]) / tick
            prev = ser[i - 2] if i >= 2 else None
            d_prev = ((a[3] - prev[3]) / tick) if (prev is not None and prev[3] is not None) else 0.0
            behind = ((b[3] - b[1]) if side == "bid" else (b[1] - b[3])) / tick
            if not (-0.5 <= behind <= self.behind_ticks + _EPS):
                continue
            if b[4] == "opposite_best" and abs(d_now) >= 0.5 and abs(d_now - dp) < 0.5:
                parallel += 1                    # both sides moved together: neither led the other
                continue
            led_now = abs(d_now) >= 0.5 and sign(d_now) == sign(dp)
            led_prev = abs(d_prev) >= 0.5 and sign(d_prev) == sign(dp)
            if led_now or led_prev:
                follows += 1
                sizes.append(b[2])
                ref_sources.add(b[4])
        consistency = (follows / moves) if moves else None
        if len(sizes) >= 2:
            cv = _cv(sizes)
            sim = 1.0 - min(1.0, (cv if cv is not None else 0.0) / 0.5)
        elif len(sizes) == 1:
            sim = 0.5
        else:
            sim = None
        drift = ((ser[-1][1] - ser[0][1]) / tick) if len(ser) >= 2 else None
        s_side = ramp(follows, 1.5, 5.0) * ramp(consistency, 0.5, 0.9) * (0.3 + 0.7 * (sim if sim is not None else 0.0))
        return {"points": len(ser), "moves": moves, "follows": follows, "parallel_moves": parallel,
                "consistency": consistency, "size_similarity": sim, "sizes": sizes[:40], "drift_ticks": drift,
                "reference": sorted(ref_sources), "score": s_side}

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        sides = {sd: self._side(fr, sd, tick) for sd in ("bid", "ask")}
        if all(v["points"] < 2 for v in sides.values()):
            miss = [k for k in ("best_bid", "bid_qty1", "best_ask", "ask_qty1") if getattr(ms, k) is None] \
                or ["touch series (< 2 states)"]
            return missing_reading(self, miss, base, {"sides": sides})
        best_side = max(sides, key=lambda k: sides[k]["score"])
        rec = sides[best_side]
        score = rec["score"]
        direction = 0
        if score > 0 and rec["drift_ticks"] is not None and abs(rec["drift_ticks"]) >= 2.0:
            direction = sign(rec["drift_ticks"])
        ev = {"side": best_side, "moves": rec["moves"], "follows": rec["follows"],
              "parallel_moves": rec["parallel_moves"], "consistency": rec["consistency"],
              "size_similarity": rec["size_similarity"], "drift_ticks": rec["drift_ticks"],
              "reference": rec["reference"], "sides": sides, "direction": direction, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"{rec['follows']}/{rec['moves']} follows on {best_side}")


# ============================================================================ #9
def bucket_volumes(fr: Frame, window_s: float, bucket_s: float) -> List[Dict[str, Any]]:
    """Symbol traded volume per ``bucket_s`` bucket counted back from now over ``window_s``,
    oldest first; only buckets whose start lies inside the observed history are formed.
    Volume = Δ cumulative day volume between the states at or before the bucket ends when
    both carry it and the total never decreases across the states in between (a reset inside
    the bucket would otherwise be a phantom volume — ``cum_delta``), else Σ tape-row volumes
    inside the bucket.  Each bucket also carries the market 60-s volume from
    ``cross['market_volume_60s']`` of the state at its end (None when the state does not carry
    it)."""
    states = fr.states(window_s)
    if not states:
        return []
    t_first = states[0].t
    n = int(window_s // bucket_s)
    rows = tape_rows(fr, window_s)
    every = all_states(fr)
    index = {id(s): i for i, s in enumerate(every)}
    out = []
    for k in range(n - 1, -1, -1):
        end = fr.ms.t - timedelta(seconds=k * bucket_s)
        start = end - timedelta(seconds=bucket_s)
        if start < t_first:
            continue
        s_end = fr.at_or_before(end)
        s_start = fr.at_or_before(start)
        vol: Optional[float] = None
        src = "none"
        delta = None
        reset_inside = False
        if s_end is None or s_end.t <= start:
            src = "no_state"                       # no state landed inside the bucket: unobserved, not 0
        elif s_start is not None and s_end.trade_volume is not None and s_start.trade_volume is not None:
            seg = every[index[id(s_start)]:index[id(s_end)] + 1]
            delta = cum_delta(seg)
            cum = [s.trade_volume for s in seg if s.trade_volume is not None]
            reset_inside = delta is None and any(b < a for a, b in zip(cum, cum[1:]))
        if delta is not None:
            vol, src = delta, "cum_volume"
        elif src != "no_state":
            rs = [r for r in rows if start < r["t"] <= end and r["volume"] is not None]
            if rs:
                vol, src = float(sum(r["volume"] for r in rs)), "interval_volume"
            elif reset_inside:
                vol, src = None, "reset"           # the reset destroyed the bucket's only interval: unobservable
            elif s_end.interval_volume is not None:
                vol, src = 0.0, "interval_volume"
        mkt = None
        if s_end is not None and isinstance(s_end.cross, dict):
            mkt = s_end.cross.get("market_volume_60s")
        out.append({"start": start, "end": end, "volume": vol, "source": src,
                    "market_volume": (float(mkt) if mkt is not None else None)})
    return out


@register
class ParticipationFootprint(DirectedMechanism):
    """#9 Participation-style footprint.

    Rule: 60-s buckets of symbol volume over the last 600 s (``bucket_volumes``).
    When ≥ 3 buckets carry ``cross['market_volume_60s']`` > 0 the series is
    ratio_k = symbol_k / market_k (mode ``market``); otherwise ratio_k =
    symbol_k / mean(symbol over the buckets) (mode ``self``: participation
    at a steady rate of the symbol's own baseline).  stability = 1 − min(1,
    cv(ratio) / 0.6); coverage = share of buckets with volume > 0.
    score = ramp(buckets, 2.5 → 6) × stability × ramp(coverage, 0.6 → 1).
    direction = sign of the window's signed flow when ≥ 50 % one-sided, else 0.
    """

    name = "participation_footprint"
    family = "participation"
    requires = ("trade_volume", "interval_volume", "trade_flow_direction", "cross")
    window_s = 600.0
    bucket_s = 60.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        buckets = [b for b in bucket_volumes(fr, self.window_s, self.bucket_s) if b["volume"] is not None]
        if len(buckets) < 2:
            miss = ["trade_volume/interval_volume"] if (ms.trade_volume is None and ms.interval_volume is None) \
                else ["volume buckets (< 2 × %.0f s of history)" % self.bucket_s]
            return missing_reading(self, miss, base, {"buckets": len(buckets)})
        vols = [b["volume"] for b in buckets]
        with_mkt = [b for b in buckets if b["market_volume"] is not None and b["market_volume"] > 0]
        if len(with_mkt) >= 3:
            mode = "market"
            ratios = [b["volume"] / b["market_volume"] for b in with_mkt]
            used = with_mkt
        else:
            mode = "self"
            mean_v = sum(vols) / len(vols)
            ratios = [v / mean_v for v in vols] if mean_v > 0 else []
            used = buckets
        coverage = sum(1 for b in used if b["volume"] > 0) / len(used)
        if not ratios or sum(ratios) <= 0:
            return missing_reading(self, ["traded volume in window (all buckets empty)"], base,
                                   {"buckets": len(buckets), "mode": mode, "coverage": coverage})
        cv = _cv(ratios)
        stability = (1.0 - min(1.0, cv / 0.6)) if cv is not None else None
        s_n = ramp(len(used), 2.5, 6.0)
        s_cov = ramp(coverage, 0.6, 1.0)
        score = s_n * (stability if stability is not None else 0.0) * s_cov
        fs = flow_summary(classified_rows(fr, self.window_s))
        direction = 0
        if score > 0 and fs["one_sided"] is not None and fs["one_sided"] >= 0.5:
            direction = sign(fs["signed"])
        ev = {"mode": mode, "buckets": len(used), "bucket_volumes": vols, "ratios": ratios,
              "mean_ratio": sum(ratios) / len(ratios), "ratio_cv": cv, "stability": stability, "coverage": coverage,
              "market_volumes": [b["market_volume"] for b in used], "volume_source": buckets[-1]["source"],
              "signed_flow": fs["signed"], "one_sided": fs["one_sided"],
              "components": {"buckets": s_n, "coverage": s_cov}, "direction": direction,
              "window_s": self.window_s, "bucket_s": self.bucket_s}
        if stability is None:
            ev["missing"] = ["ratio series (< 2 buckets)"]
        return reading(self, score, ev, base, f"{mode}: {len(used)} buckets, cv {cv}, coverage {coverage:.2f}")


# ============================================================================ #10 / #43
def flow_path(fr: Frame, seconds: float, tick: float) -> Dict[str, Any]:
    """Impact path of the window: classified tape rows in order → cumulative signed flow x_i
    and the mid move y_i (ticks) from the origin mid — the mid of the state before the first
    row (else the first row's own mid).  The origin (0, 0) is the first point.  Also the flow
    summary and the majority direction (sign of Σ d·v)."""
    rows = [r for r in classified_rows(fr, seconds) if r["mid"] is not None]
    fs = flow_summary(rows)
    if not rows:
        return {"rows": [], "flow": fs, "dir": 0, "x": [], "y": [], "mid0": None}
    origin = state_before(fr, rows[0]["state"])
    mid0 = mid_of(origin) if origin is not None else None
    if mid0 is None:
        mid0 = rows[0]["mid"]
    d = sign(fs["signed"])
    xs = [0.0]
    ys = [0.0]
    cum = 0.0
    for r in rows:
        cum += r["direction"] * r["volume"]
        xs.append(cum)
        ys.append((r["mid"] - mid0) / tick)
    m_now = mid_of(fr.ms)
    if m_now is not None and (m_now - mid0) / tick != ys[-1]:
        xs.append(cum)
        ys.append((m_now - mid0) / tick)
    return {"rows": rows, "flow": fs, "dir": d, "x": xs, "y": ys, "mid0": mid0}


@register
class MetaorderTrajectory(DirectedMechanism):
    """#10 Metaorder / participation trajectory.

    Rule (window 600 s): classified rows → one_sided = |Σ d·v| / Σ v,
    consistency = share of signed rows on the majority side, dir = sign(Σ d·v).
    Impact path (``flow_path``): X_i = cumulative signed flow × dir / final,
    Y_i = mid move × dir / final move (both normalised to [0, 1], with the
    origin), requiring the final flow and the final move along dir to be
    positive.  slope = ``windows.slope``(X, Y) (> 0 required), concavity =
    −``windows.curvature``(X, Y) (a √-like path gives ≈ 1.6, a linear one 0,
    a convex one < 0).  move_along = final mid move × dir in ticks.
    score = ramp(one_sided, 0.4 → 0.85) × ramp(consistency, 0.6 → 0.95) ×
    ramp(rows, 3 → 8) × ramp(move_along, 0.5 → 3) × ramp(concavity, 0.3 → 1.2)
    (0 when slope ≤ 0).  direction = dir.
    """

    name = "metaorder_trajectory"
    family = "participation"
    requires = ("trade_flow_direction", "interval_volume", "mid", "tick_size")
    window_s = 600.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        path = flow_path(fr, self.window_s, tick)
        rows, fs, d = path["rows"], path["flow"], path["dir"]
        if not rows:
            return missing_reading(self, tape_missing(ms), base)
        signed_rows = fs["buy_rows"] + fs["sell_rows"]
        majority_rows = fs["buy_rows"] if d > 0 else fs["sell_rows"]
        consistency = (majority_rows / signed_rows) if signed_rows else None
        xs, ys = path["x"], path["y"]
        x_final = xs[-1] * d
        y_final = ys[-1] * d
        slope_n = curv_n = concavity = None
        if x_final > 0 and y_final > 0 and len(xs) >= 3:
            X = [x * d / x_final for x in xs]
            Y = [y * d / y_final for y in ys]
            slope_n = slope(X, Y)
            curv_n = curvature(X, Y)
            concavity = (-curv_n) if curv_n is not None else None
        s_one = ramp(fs["one_sided"], 0.4, 0.85)
        s_cons = ramp(consistency, 0.6, 0.95)
        s_rows = ramp(fs["rows"], 3.0, 8.0)
        s_move = ramp(y_final, 0.5, 3.0)
        s_conc = ramp(concavity, 0.3, 1.2) if (slope_n is not None and slope_n > 0) else 0.0
        score = s_one * s_cons * s_rows * s_move * s_conc
        direction = d if score > 0 else 0
        ev = {"rows": fs["rows"], "signed_flow": fs["signed"], "total_volume": fs["total"], "one_sided": fs["one_sided"],
              "consistency": consistency, "flow_direction": d, "cum_flow_final": xs[-1], "move_along_ticks": y_final,
              "path_slope": slope_n, "path_curvature": curv_n, "concavity": concavity, "mid_origin": path["mid0"],
              "points": len(xs), "components": {"one_sided": s_one, "consistency": s_cons, "rows": s_rows,
                                                 "move": s_move, "concavity": s_conc},
              "direction": direction, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"one-sided {fs['one_sided']:.2f}, move {y_final:.1f} ticks, concavity {concavity}")


@register
class MetaorderImpact(DirectedMechanism):
    """#43 Metaorder impact trajectory.

    Rule (window 600 s): points (x_i, y_i) = (cumulative signed flow × dir,
    mid move × dir in ticks) from ``flow_path`` (origin included), dir =
    sign(Σ d·v).  Fits: slope_lin = ``windows.slope``(x, y) (ticks per share,
    must be > 0), curvature = ``windows.curvature``(x, y), normalised as
    curvature × x_final² / y_final so that concavity = −normalised ≈ 1.6 for a
    √ path and 0 for a linear one; log–log exponent β = slope(ln x, ln y) over
    the points with x > 0 and y > 0 (≥ 4) with its R²; sqrt_like = 1 − |β −
    0.5| / 0.4 (clipped).  score = ramp(points, 4 → 10) × ramp(y_final, 0.5 →
    3) × geometric mean(ramp(concavity, 0.3 → 1.2), sqrt_like, ramp(R², 0.5 →
    0.9)) × (0.6 + 0.4 × ramp(|Σ d·v| / Σ v, 0.3 → 0.8)).  The tape engine's
    linear ``price_impact`` is reported next to slope_lin.  direction = dir.
    """

    name = "metaorder_impact"
    family = "participation"
    requires = ("trade_flow_direction", "interval_volume", "mid", "tick_size", "price_impact")
    window_s = 600.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        path = flow_path(fr, self.window_s, tick)
        rows, fs, d = path["rows"], path["flow"], path["dir"]
        if not rows:
            return missing_reading(self, tape_missing(ms), base)
        xs = [x * d for x in path["x"]]
        ys = [y * d for y in path["y"]]
        x_final, y_final = xs[-1], ys[-1]
        slope_lin = slope(xs, ys) if len(xs) >= 2 else None
        curv = curvature(xs, ys) if len(xs) >= 3 else None
        concavity = None
        if curv is not None and x_final > 0 and y_final > 0:
            concavity = -curv * (x_final ** 2) / y_final
        pos = [(x, y) for x, y in zip(xs, ys) if x > 0 and y > 0]
        beta = r2 = None
        if len(pos) >= 4:
            lx = [math.log(x) for x, _ in pos]
            ly = [math.log(y) for _, y in pos]
            beta = slope(lx, ly)
            if beta is not None:
                mx, my = sum(lx) / len(lx), sum(ly) / len(ly)
                b0 = my - beta * mx
                ss_res = sum((y - (b0 + beta * x)) ** 2 for x, y in zip(lx, ly))
                ss_tot = sum((y - my) ** 2 for y in ly)
                r2 = (1.0 - ss_res / ss_tot) if ss_tot > _EPS else None
        sqrt_like = clamp01(1.0 - abs(beta - 0.5) / 0.4) if beta is not None else 0.0
        s_pts = ramp(len(xs), 4.0, 10.0)
        s_move = ramp(y_final, 0.5, 3.0)
        s_conc = ramp(concavity, 0.3, 1.2) if (slope_lin is not None and slope_lin > 0) else 0.0
        s_r2 = ramp(r2, 0.5, 0.9)
        s_one = ramp(fs["one_sided"], 0.3, 0.8)
        score = s_pts * s_move * geo_mean([s_conc, sqrt_like, s_r2]) * (0.6 + 0.4 * s_one)
        direction = d if score > 0 else 0
        ev = {"rows": fs["rows"], "points": len(xs), "signed_flow": fs["signed"], "total_volume": fs["total"],
              "one_sided": fs["one_sided"], "flow_direction": d, "cum_flow_final": x_final, "move_ticks": y_final,
              "slope_linear_ticks_per_unit": slope_lin, "engine_price_impact": ms.price_impact, "curvature": curv,
              "concavity": concavity, "loglog_exponent": beta, "loglog_r2": r2, "sqrt_like": sqrt_like,
              "loglog_points": len(pos),
              "components": {"points": s_pts, "move": s_move, "concavity": s_conc, "r2": s_r2, "one_sided": s_one},
              "direction": direction, "window_s": self.window_s}
        if len(pos) < 4:
            ev["missing"] = ["impact points with flow > 0 and move > 0 (< 4)"]
        return reading(self, score, ev, base, f"beta {beta} r2 {r2} concavity {concavity}")
