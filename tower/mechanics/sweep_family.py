"""sweep_family — sweep / vacuum / momentum mechanisms (MECHANISMS.md #3, #4, #5, #14, #15, #20, #21, #34).

Same conventions as ``queue_family`` (whose ``Frame``, ``ramp``, ``baselines``
helpers are reused): causal windows over history + current state, continuous
scores, None for the unobservable, ``evidence["missing"]`` when a required
input is absent, ``evidence["direction"]`` for the implied price direction and
the simple baselines at the same instant.

Rules in one line each (the mechanism docstrings restate them fully):

  liquidity_sweep      best retreated ≥ ticks inside the 30-s burst, displayed
                       levels of the pre-burst book now beyond the best
                       (consumed through), mid jump, volume-rate burst vs the
                       300-s baseline (or the share of top-K depth taken).
  failed_sweep         a mid / best excursion inside 180 s whose mid came back
                       (return share) with the swept side's levels restored.
  exhaustion           trade intensity peak (z vs the window) × velocity decay
                       from its peak × top-K depth rebuilding on the side the
                       move ran into (geometric mean).
  liquidity_vacuum     visible depth per side vs its baseline median (300 s
                       excluding the last 30 s), collapse share per side,
                       damped by the qty added in the last 60 s.
  vacuum_snapback      depth trough share inside 300 s, mid excursion at the
                       trough reverted (share) and depth returned (share),
                       geometric mean × a time factor (fast = within 60 s).
  liquidity_run        mid run in ticks and speed, directional flow
                       consistency, thinness of the consumed side before the
                       run, then a stall at the extreme (≥ 10–40 s, ≤ 1.5 ticks).
  ignition             velocity × acceleration (same sign) × relative trade
                       acceleration, geometric mean, × spread expansion.
  liquidity_depletion  touch / top-K depth decline share over 120 s × step
                       consistency × (1 − |mid move| / 2 ticks).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..state import MarketState
from ..windows import clamp01, safe_div, sign
from .base import Mechanism, MechanismReading, StateHistory, register
from .queue_family import (Frame, _EPS, _median, _step_consistency, baselines, best_of, geo_mean, levels_of,
                           mid_of, missing_reading, ramp, spread_ticks_of, topk_depth, visible_depth)


def _extreme(pts: List[Tuple[datetime, float]], want_max: bool) -> Tuple[datetime, float, int]:
    idx = max(range(len(pts)), key=lambda i: pts[i][1]) if want_max else min(range(len(pts)), key=lambda i: pts[i][1])
    return pts[idx][0], pts[idx][1], idx


def _velocity_series(fr: Frame, seconds: float, tick: float, vel_w: float = 60.0) -> List[Tuple[datetime, float]]:
    """price_velocity per state when carried, else mid change over 60 s in ticks per minute."""
    out = []
    states = fr.states(seconds + vel_w)
    mids = [(s.t, mid_of(s)) for s in states]
    for i, s in enumerate(states):
        if s.t < fr.ms.t - timedelta(seconds=seconds):
            continue
        if s.price_velocity is not None:
            out.append((s.t, float(s.price_velocity)))
            continue
        m = mids[i][1]
        if m is None:
            continue
        prev = None
        for j in range(i, -1, -1):
            if mids[j][0] <= s.t - timedelta(seconds=vel_w) and mids[j][1] is not None:
                prev = mids[j][1]
                break
        if prev is not None:
            out.append((s.t, (m - prev) / tick))
    return out


# ============================================================================ #3
@register
class LiquiditySweep(Mechanism):
    """#3 Liquidity sweep.

    Rule: pre = the state at or before now − 30 s (else the oldest inside the
    burst when it is ≥ 5 s old).  bid retreat = (pre.best_bid − best_bid) /
    tick, ask retreat = (best_ask − pre.best_ask) / tick; the side with the
    larger positive retreat is the swept side.  levels_consumed = number of
    pre-burst displayed levels on that side whose price is better than the new
    best (bid: > best_bid; ask: < best_ask); qty_consumed = their qty;
    taken_share = qty_consumed / pre top-K depth.  mid_jump = (mid − pre.mid) /
    tick.  volume burst = (Δ cumulative volume over the burst / 30 s) / (Δ over
    the 300-s baseline before the burst / its span); when the tape is not
    observable the volume component is replaced by taken_share and the tape
    fields are listed under ``missing``.  score = max(ramp(levels, 0.5 → 3),
    ramp(retreat, 0.5 → 3)) × (0.4 + 0.3 × ramp(|mid_jump| in the sweep
    direction, 0.5 → 3) + 0.3 × ramp(volume ratio, 1 → 5 | taken_share,
    0.2 → 0.8)).  A side displayed before the burst and empty now was swept
    through entirely: retreat = distance to its deepest pre-burst level + 1
    tick and every pre-burst level is consumed (``side_emptied``).  When the
    tape is observed and no volume traded inside the burst the vanished levels
    were pulled, not swept: the score keeps a quarter (``no_trades_in_burst``).
    direction: ask swept +1, bid swept −1.
    """

    name = "liquidity_sweep"
    family = "sweep"
    requires = ("best_bid", "best_ask", "bids", "asks", "mid", "tick_size", "trade_volume")
    burst_s = 30.0
    baseline_s = 300.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        pre = fr.at_or_before(ms.t - timedelta(seconds=self.burst_s))
        if pre is None:
            st = fr.states(self.burst_s)
            pre = st[0] if len(st) > 1 and (ms.t - st[0].t).total_seconds() >= 5.0 else None
        if pre is None or pre is ms:
            return missing_reading(self, ["history (no state ≥ 5 s before now)"], base)
        retreat: Dict[str, Optional[float]] = {}
        emptied: Dict[str, bool] = {"bid": False, "ask": False}
        pb, _ = best_of(pre, "bid")
        cb, _ = best_of(ms, "bid")
        pa, _ = best_of(pre, "ask")
        ca, _ = best_of(ms, "ask")
        retreat["bid"] = (pb - cb) / tick if (pb is not None and cb is not None) else None
        retreat["ask"] = (ca - pa) / tick if (pa is not None and ca is not None) else None
        for sd, p_pre, p_cur in (("bid", pb, cb), ("ask", pa, ca)):
            # a side displayed before the burst and empty now was swept through entirely: the best
            # retreated beyond every displayed level (deepest pre-burst price + 1 tick)
            lv = levels_of(pre, sd)
            if p_pre is not None and p_cur is None and lv:
                retreat[sd] = abs(p_pre - lv[-1][0]) / tick + 1.0
                emptied[sd] = True
        if retreat["bid"] is None and retreat["ask"] is None:
            miss = [k for k in ("best_bid", "best_ask") if getattr(ms, k) is None and not levels_of(ms, k[5:])]
            return missing_reading(self, miss or ["best prices in pre-burst state"], base)
        side = max(("bid", "ask"), key=lambda s: (retreat[s] if retreat[s] is not None else -math.inf))
        r = retreat[side] or 0.0
        new_best = cb if side == "bid" else ca
        pre_levels = levels_of(pre, side)
        if emptied[side]:
            consumed = list(pre_levels)
        else:
            consumed = [(p, q) for p, q in pre_levels
                        if new_best is not None and ((p > new_best + _EPS) if side == "bid" else (p < new_best - _EPS))]
        levels = len(consumed)
        qty_consumed = float(sum(q for _, q in consumed))
        topk_pre = topk_depth(pre, side)
        taken_share = safe_div(qty_consumed, topk_pre) if topk_pre else None
        pm, cm = mid_of(pre), mid_of(ms)
        mid_jump = (cm - pm) / tick if (pm is not None and cm is not None) else None
        sdir = 1 if side == "ask" else -1
        mid_along = (mid_jump * sdir) if mid_jump is not None else None
        # volume burst against the baseline rate
        vol_burst = fr.volume_over((ms.t - pre.t).total_seconds())
        vol_ratio = None
        missing: List[str] = []
        if vol_burst is None:
            missing.append("trade_volume")
        else:
            base_states = [s for s in fr.past if pre.t - timedelta(seconds=self.baseline_s) <= s.t <= pre.t
                           and s.trade_volume is not None]
            if len(base_states) >= 2 and (base_states[-1].t - base_states[0].t).total_seconds() > 0:
                span_b = (base_states[-1].t - base_states[0].t).total_seconds()
                vol_b = base_states[-1].trade_volume - base_states[0].trade_volume
                burst_span = max((ms.t - pre.t).total_seconds(), 1.0)
                rate_b = vol_b / span_b
                if rate_b > 0:
                    vol_ratio = (vol_burst / burst_span) / rate_b
                elif rate_b == 0:
                    vol_ratio = math.inf if vol_burst > 0 else 0.0
                else:
                    vol_ratio = None                          # cumulative volume went backwards: no baseline
            else:
                vol_ratio = None
        s_flow = ramp(vol_ratio, 1.0, 5.0) if vol_ratio is not None else ramp(taken_share, 0.2, 0.8)
        s_lv = ramp(levels, 0.5, 3.0)
        s_move = ramp(r, 0.5, 3.0)
        s_mid = ramp(mid_along, 0.5, 3.0)
        score = max(s_lv, s_move) * (0.4 + 0.3 * s_mid + 0.3 * s_flow)
        # the tape is observed and nothing traded inside the burst: the levels were pulled, not swept
        no_trades = bool(vol_burst is not None and vol_burst <= 0.0)
        if no_trades:
            score *= 0.25
        direction = sdir if score > 0 else 0
        ev = {"side": side, "retreat_ticks": r, "levels_consumed": levels, "qty_consumed": qty_consumed,
              "side_emptied": emptied[side],
              "taken_share": taken_share, "mid_jump_ticks": mid_jump, "volume_burst": vol_burst,
              "volume_ratio": (None if vol_ratio is None or math.isinf(vol_ratio) else vol_ratio),
              "volume_ratio_inf": bool(vol_ratio is not None and math.isinf(vol_ratio)),
              "no_trades_in_burst": no_trades,
              "pre_t": pre.t.isoformat(), "burst_s": (ms.t - pre.t).total_seconds(), "direction": direction,
              "retreat": retreat}
        if missing:
            ev["missing"] = missing
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"{side} swept {r:.1f} ticks, {levels} levels")


# ============================================================================ #4
@register
class FailedSweep(Mechanism):
    """#4 Failed sweep / rejection.

    Rule: over 180 s, pre = the oldest mid in the window; trough = the minimum
    mid (down excursion = (pre − trough)/tick) and peak = the maximum (up
    excursion); the larger excursion (≥ ½ tick, reached strictly before now)
    is the sweep.  It must show as a best-price retreat on the swept side at
    the extreme (bid: min best_bid ≤ pre best_bid − 1 tick; ask mirrored),
    otherwise the excursion is a quote drift and the score is 0.  return_share
    = (mid_now − trough) / (pre − trough) (mirrored for up sweeps);
    depth_return = displayed levels on the swept side now / at the extreme.
    score = ramp(excursion, 1 → 3 ticks) × ramp(return_share, 0.3 → 0.9) ×
    (0.7 + 0.3 × ramp(depth_return, 0.5 → 1)).  direction: a failed down sweep
    → +1 (price returning up), a failed up sweep → −1.
    """

    name = "failed_sweep"
    family = "sweep"
    requires = ("mid", "best_bid", "best_ask", "bids", "asks", "tick_size")
    window_s = 180.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        with_mid = [(s, mid_of(s)) for s in fr.states(self.window_s)]
        with_mid = [(s, m) for s, m in with_mid if m is not None]
        pts = [(s.t, m) for s, m in with_mid]
        if len(pts) < 3:
            return missing_reading(self, ["mid history (< 3 points)"], base, {"points": len(pts)})
        pre = pts[0][1]
        now = pts[-1][1]
        t_lo, lo, i_lo = _extreme(pts, want_max=False)
        t_hi, hi, i_hi = _extreme(pts, want_max=True)
        exc_down, exc_up = (pre - lo) / tick, (hi - pre) / tick
        if exc_down >= exc_up:
            side, exc, t_ext, ext, i_ext, direction = "bid", exc_down, t_lo, lo, i_lo, 1
        else:
            side, exc, t_ext, ext, i_ext, direction = "ask", exc_up, t_hi, hi, i_hi, -1
        # best-price retreat on the swept side at the extreme (relative to the window start); the
        # states are taken by index so duplicate timestamps cannot substitute a neighbour
        st_pre = with_mid[0][0]
        st_ext = with_mid[i_ext][0]
        retreat = None
        if st_pre is not None and st_ext is not None:
            p0, _ = best_of(st_pre, side)
            p1, _ = best_of(st_ext, side)
            if p0 is not None and p1 is not None:
                retreat = ((p0 - p1) if side == "bid" else (p1 - p0)) / tick
        if exc <= 0.5 or i_ext == len(pts) - 1:
            score = 0.0
            ret_share = 0.0
            depth_return = None
        else:
            ret_share = ((now - ext) / (pre - ext)) if side == "bid" else ((ext - now) / (ext - pre))
            n_now = len(levels_of(ms, side))
            n_ext = len(levels_of(st_ext, side)) if st_ext is not None else 0
            depth_return = (n_now / n_ext) if n_ext > 0 else (1.0 if n_now > 0 else None)
            gate = ramp(retreat, 0.5, 1.0) if retreat is not None else 0.0
            score = gate * ramp(exc, 1.0, 3.0) * ramp(ret_share, 0.3, 0.9) * (0.7 + 0.3 * ramp(depth_return, 0.5, 1.0))
        ev = {"side": side, "excursion_ticks": exc, "return_share": ret_share, "retreat_ticks_at_extreme": retreat,
              "depth_return": depth_return, "mid_pre": pre, "mid_extreme": ext, "mid_now": now,
              "t_extreme": t_ext.isoformat(), "seconds_since_extreme": (ms.t - t_ext).total_seconds(),
              "points": len(pts), "direction": direction if score > 0 else 0, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"{side} excursion {exc:.1f} ticks, returned {ret_share:.2f}")


# ============================================================================ #5
@register
class Exhaustion(Mechanism):
    """#5 Exhaustion.

    Rule (window 300 s):
      intensity  peak trade_intensity over the last 60 s against the earlier
                 part of the window: z = (peak − mean) / std (≥ 4 earlier
                 points, std > 0) → ramp(z, 0.5 → 2.5); with a degenerate std
                 the ratio peak / mean → ramp(ratio, 1.2 → 3); a positive peak
                 over an all-zero earlier window is the strongest spike (1);
      decay      velocity series (``price_velocity`` or mid change over 60 s in
                 ticks/min): v_peak = the largest |v| in the last 180 s
                 (≥ 0.5 ticks/min), v_now = the latest; decay = 1 − |v_now| /
                 |v_peak| → ramp(decay, 0.4 → 0.95);
      rebuild    the side the move ran into (ask for an up move, bid for down):
                 top-K depth now − its minimum since the velocity peak, as a
                 share of the side's window median → ramp(share, 0.15 → 0.6).
    score = geometric mean of the three (any zero → 0); direction = −sign(v_peak).
    """

    name = "exhaustion"
    family = "sweep"
    requires = ("trade_intensity", "price_velocity", "mid", "bids", "asks", "tick_size")
    window_s = 300.0
    peak_s = 60.0
    vel_s = 180.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        missing: List[str] = []
        if not tick:
            missing.append("tick_size")
        ints = fr.series(lambda s: s.trade_intensity, self.window_s)
        if len(ints) < 2:
            missing.append("trade_intensity")
        vels = _velocity_series(fr, self.vel_s, tick) if tick else []
        if len(vels) < 2:
            missing.append("price_velocity/mid history")
        if missing:
            return missing_reading(self, missing, base)
        # intensity peak vs the earlier window
        recent = [v for t, v in ints if (ms.t - t).total_seconds() <= self.peak_s]
        earlier = [v for t, v in ints if (ms.t - t).total_seconds() > self.peak_s]
        peak = max(recent) if recent else ints[-1][1]
        z = ratio = None
        from_zero = False                                     # a burst out of a silent tape: strongest spike
        if len(earlier) >= 4:
            m = sum(earlier) / len(earlier)
            sd = math.sqrt(sum((x - m) ** 2 for x in earlier) / (len(earlier) - 1))
            if sd > _EPS:
                z = (peak - m) / sd
            elif m > _EPS:
                ratio = peak / m
            else:
                from_zero = peak > _EPS
        elif earlier:
            m = sum(earlier) / len(earlier)
            if m > _EPS:
                ratio = peak / m
            else:
                from_zero = peak > _EPS
        s_int = ramp(z, 0.5, 2.5) if z is not None else (1.0 if from_zero else ramp(ratio, 1.2, 3.0))
        # velocity decay (the peak is taken by index: duplicate timestamps cannot swap its sign)
        t_pk, v_pk, i_pk = _extreme([(t, abs(v)) for t, v in vels], want_max=True)
        v_peak_signed = vels[i_pk][1]
        v_now = vels[-1][1]
        decay = (1.0 - abs(v_now) / v_pk) if v_pk > _EPS else None
        s_dec = ramp(decay, 0.4, 0.95) if v_pk >= 0.5 else 0.0
        # depth rebuilding on the side the move ran into
        mdir = sign(v_peak_signed)
        against = "ask" if mdir > 0 else "bid"
        dep = [(s.t, topk_depth(s, against)) for s in fr.states(self.window_s)]
        dep = [(t, d) for t, d in dep if d is not None]
        since = [d for t, d in dep if t >= t_pk]
        med = _median([d for _, d in dep])
        rebuild_share = None
        if since and med:
            rebuild_share = (dep[-1][1] - min(since)) / med
        s_reb = ramp(rebuild_share, 0.15, 0.6)
        score = geo_mean([s_int, s_dec, s_reb])
        direction = -mdir if score > 0 else 0
        ev = {"intensity_peak": peak, "intensity_z": z, "intensity_ratio": ratio, "intensity_from_zero": from_zero,
              "intensity_now": ints[-1][1],
              "velocity_peak": v_peak_signed, "velocity_now": v_now, "velocity_decay": decay,
              "t_velocity_peak": t_pk.isoformat(), "against_side": against, "rebuild_share": rebuild_share,
              "depth_against_now": dep[-1][1] if dep else None, "depth_against_min": (min(since) if since else None),
              "components": {"intensity": s_int, "decay": s_dec, "rebuild": s_reb},
              "direction": direction, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"decay {decay if decay is None else round(decay, 2)}, rebuild {rebuild_share}")


# ============================================================================ #14
@register
class LiquidityVacuum(Mechanism):
    """#14 Liquidity vacuum.

    Rule: per side, visible depth now against the median of the side's visible
    depth over [now − 300 s, now − 30 s] (≥ 3 points; a side with no displayed
    level now counts as fully collapsed when it had a baseline): collapse =
    1 − now / median, clipped to [0, 1].  Replenishment = Σ ``depth_added``
    counted once per change of the displayed book (the field is the last
    update's diff carried onto every state until the next update; else
    positive visible-depth steps) over the last 60 s on the more collapsed
    side, as a share of the missing depth (median − now).
    score = ramp(max collapse, 0.4 → 0.9) × (0.6 + 0.4 × ramp(min collapse,
    0.4 → 0.9)) × (1 − 0.8 × replenish share).  The queue engine's
    ``liquidity_vacuum`` flag and the resilience state are reported as
    corroborating evidence, not used in the score.  direction: bid-only
    collapse (bid ≥ 0.5, ask < 0.25) → −1, ask-only → +1, else 0.
    """

    name = "liquidity_vacuum"
    family = "sweep"
    requires = ("bids", "asks", "visible_bid_liq", "visible_ask_liq", "depth_added_bid", "depth_added_ask")
    window_s = 300.0
    burst_s = 30.0
    repl_s = 60.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        states = fr.states(self.window_s)
        hi = ms.t - timedelta(seconds=self.burst_s)
        col: Dict[str, Optional[float]] = {}
        sides_ev: Dict[str, Any] = {}
        for side in ("bid", "ask"):
            hist_vals = [visible_depth(s, side) for s in states if s.t <= hi and s is not ms]
            hist_vals = [v for v in hist_vals if v is not None]
            med = _median(hist_vals) if len(hist_vals) >= 3 else None
            now_v = visible_depth(ms, side)
            if now_v is None and (levels_of(ms, "bid") or levels_of(ms, "ask")):
                now_v = 0.0                                   # a displayed book with this side empty
            c = None
            if med is not None and med > 0 and now_v is not None:
                c = clamp01(1.0 - now_v / med)
            col[side] = c
            # replenishment in the last 60 s
            recent = [s for s in states if (ms.t - s.t).total_seconds() <= self.repl_s]
            added = [getattr(s, f"depth_added_{side}") for s in recent]
            if any(a is not None for a in added):
                # depth_added_* is the book engine's last-update diff and is carried onto every state
                # until the next book update: count it once per displayed book, not once per state
                added_sum = 0.0
                prev_book = None
                for s, a in zip(recent, added):
                    book = (tuple(map(tuple, s.bids or [])), tuple(map(tuple, s.asks or [])))
                    same = prev_book is not None and book == prev_book
                    prev_book = book
                    if a is None or same:
                        continue
                    added_sum += float(a)
            else:
                vs = [visible_depth(s, side) for s in recent]
                vs = [v for v in vs if v is not None]
                added_sum = float(sum(max(0.0, b - a) for a, b in zip(vs, vs[1:])))
            missing_depth = (med - now_v) if (med is not None and now_v is not None) else None
            repl = (min(1.0, added_sum / missing_depth) if (missing_depth and missing_depth > 0) else None)
            sides_ev[side] = {"visible_now": now_v, "median_baseline": med, "baseline_points": len(hist_vals),
                              "collapse": c, "added_60s": added_sum, "replenish_share": repl}
        if col["bid"] is None and col["ask"] is None:
            miss = ["bids/asks"] if ms.empty_book and not levels_of(ms, "bid") and not levels_of(ms, "ask") \
                else ["visible depth baseline (< 3 points in [now−300 s, now−30 s])"]
            return missing_reading(self, miss, base, {"sides": sides_ev})
        vals = {k: v for k, v in col.items() if v is not None}
        worst = max(vals, key=lambda k: vals[k])
        c_max = vals[worst]
        c_min = min(vals.values()) if len(vals) == 2 else 0.0
        repl = sides_ev[worst]["replenish_share"] or 0.0
        score = ramp(c_max, 0.4, 0.9) * (0.6 + 0.4 * ramp(c_min, 0.4, 0.9)) * (1.0 - 0.8 * repl)
        cb, ca = col.get("bid"), col.get("ask")
        direction = 0
        if cb is not None and cb >= 0.5 and (ca is None or ca < 0.25):
            direction = -1
        elif ca is not None and ca >= 0.5 and (cb is None or cb < 0.25):
            direction = 1
        if score <= 0:
            direction = 0
        ev = {"side": worst, "collapse_max": c_max, "collapse_min": c_min, "replenish_share": repl,
              "engine_vacuum_flag": ms.liquidity_vacuum, "resilience_state": ms.resilience_state,
              "sides": sides_ev, "direction": direction, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"{worst} collapsed {c_max:.2f}")


# ============================================================================ #15
@register
class VacuumSnapback(Mechanism):
    """#15 Vacuum + snapback.

    Rule (window 300 s): total visible depth (bid + ask) per state; pre = the
    median of the first half of the window (≥ 2 points, else the first value);
    trough = the minimum depth strictly before now, collapse = 1 − trough / pre.
    mid_pre = the mid at the window start, mid_low = the mid at the trough,
    mid_now: revert_share = (mid_low − mid_now) / (mid_low − mid_pre) (None
    when the trough did not move the mid — then there is nothing to snap back
    from); depth_return = (depth_now − trough) / (pre − trough).  time factor
    = 1 up to 60 s after the trough, then falling linearly to 0 at 180 s.
    score = geometric mean(ramp(collapse, 0.4 → 0.9), ramp(revert_share, 0.3 →
    0.9), ramp(depth_return, 0.3 → 0.9)) × time factor.  The resilience
    record's ``snapback`` flag is reported as corroboration.  direction =
    sign(mid_now − mid_low) (the reversal's direction).
    """

    name = "vacuum_snapback"
    family = "sweep"
    requires = ("bids", "asks", "mid", "tick_size", "resilience_state")
    window_s = 300.0
    fast_s = 60.0
    late_s = 180.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        rows = []
        for s in fr.states(self.window_s):
            vb, va = visible_depth(s, "bid"), visible_depth(s, "ask")
            if vb is None and va is None:
                continue
            rows.append((s.t, (vb or 0.0) + (va or 0.0), mid_of(s)))
        if len(rows) < 4:
            return missing_reading(self, ["visible depth history (< 4 points)"], base, {"points": len(rows)})
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        half = rows[: max(2, len(rows) // 2)]
        pre = _median([d for _, d, _ in half]) if len(half) >= 2 else rows[0][1]
        body = rows[:-1]
        t_low, d_low, i_low = _extreme([(t, d) for t, d, _ in body], want_max=False)
        collapse = clamp01(1.0 - d_low / pre) if pre and pre > 0 else None
        mid_pre = next((m for _, _, m in rows if m is not None), None)
        mid_low = rows[i_low][2]
        if mid_low is None:                                   # nearest mid around the trough
            near = [m for t, _, m in rows if m is not None and abs((t - t_low).total_seconds()) <= 10.0]
            mid_low = near[0] if near else None
        mid_now = rows[-1][2]
        revert = None
        exc_ticks = None
        if mid_pre is not None and mid_low is not None and mid_now is not None:
            exc_ticks = (mid_low - mid_pre) / tick
            if abs(mid_low - mid_pre) > _EPS:
                revert = (mid_low - mid_now) / (mid_low - mid_pre)
        d_now = rows[-1][1]
        depth_return = ((d_now - d_low) / (pre - d_low)) if (pre is not None and pre - d_low > _EPS) else None
        dt = (ms.t - t_low).total_seconds()
        tf = 1.0 if dt <= self.fast_s else clamp01(1.0 - (dt - self.fast_s) / (self.late_s - self.fast_s))
        score = geo_mean([ramp(collapse, 0.4, 0.9), ramp(revert, 0.3, 0.9), ramp(depth_return, 0.3, 0.9)]) * tf
        res = ms.session_state.get("resilience") if isinstance(ms.session_state, dict) else None
        direction = sign(mid_now - mid_low) if (score > 0 and mid_now is not None and mid_low is not None) else 0
        ev = {"depth_pre": pre, "depth_trough": d_low, "depth_now": d_now, "collapse": collapse,
              "depth_return": depth_return, "mid_pre": mid_pre, "mid_trough": mid_low, "mid_now": mid_now,
              "excursion_ticks": exc_ticks, "revert_share": revert, "seconds_since_trough": dt, "time_factor": tf,
              "t_trough": t_low.isoformat(), "engine_snapback": (res.get("snapback") if isinstance(res, dict) else None),
              "direction": direction, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"collapse {collapse}, revert {revert}, depth back {depth_return}")


# ============================================================================ #20
@register
class LiquidityRun(Mechanism):
    """#20 Stop / liquidity-run-like.

    Rule (window 180 s): mid_pre = the oldest mid in the window; the extreme
    (max or min, whichever is farther from mid_pre, reached strictly before
    now) ends the run: run_ticks = |extreme − pre| / tick, run_speed = run_ticks
    per minute between the window start and the extreme.  Consumed side = ask
    for an up run, bid for a down run.  flow consistency = |Σ direction ×
    volume| / Σ volume of the classified tape rows between the window start and
    the extreme (0.5 neutral when no tape is observable, reported under
    ``missing``).  thin = 1 − top-K depth of the consumed side at the window
    start / max(its window median, its depth now) (clipped) — the reference is
    the book's normal capacity, not the thin run itself.  stall: seconds since the extreme →
    ramp(10 → 40 s) × (1 − |mid_now − extreme| / 1.5 ticks) clipped.
    score = ramp(run_ticks, 2 → 6) × (0.6 + 0.4 × ramp(run_speed, 1 → 5
    ticks/min)) × (0.4 + 0.6 × ramp(consistency, 0.3 → 0.9)) × (0.5 + 0.5 ×
    ramp(thin, 0.1 → 0.6)) × stall.  direction = −sign(run) (the stall after a
    run through resting liquidity implies a reversal).
    """

    name = "liquidity_run"
    family = "sweep"
    requires = ("mid", "bids", "asks", "tick_size", "trade_flow_direction", "interval_volume")
    window_s = 180.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        if not tick:
            return missing_reading(self, ["tick_size"], base)
        pts = fr.series(mid_of, self.window_s)
        if len(pts) < 3:
            return missing_reading(self, ["mid history (< 3 points)"], base, {"points": len(pts)})
        pre_t, pre = pts[0]
        body = pts[:-1]
        t_lo, lo, _ = _extreme(body, want_max=False)
        t_hi, hi, _ = _extreme(body, want_max=True)
        if hi - pre >= pre - lo:
            rdir, t_ext, ext = 1, t_hi, hi
        else:
            rdir, t_ext, ext = -1, t_lo, lo
        run_ticks = abs(ext - pre) / tick
        run_min = max((t_ext - pre_t).total_seconds(), 1.0) / 60.0
        run_speed = run_ticks / run_min
        consumed = "ask" if rdir > 0 else "bid"
        # directional flow between the window start and the extreme
        rows = [r for r in fr.tape_rows(self.window_s) if pre_t <= r["t"] <= t_ext and r["volume"]]
        cls = [r for r in rows if r["direction"] is not None]
        consistency = None
        missing: List[str] = []
        if cls:
            tot = sum(r["volume"] for r in cls)
            consistency = abs(sum(r["direction"] * r["volume"] for r in cls)) / tot if tot > 0 else None
            signed_flow = sum(r["direction"] * r["volume"] for r in cls)
        else:
            signed_flow = None
            missing.append("trade_flow_direction/interval_volume")
        flow_along = None
        if consistency is not None and signed_flow is not None:
            flow_along = consistency if sign(signed_flow) == rdir else 0.0
        s_flow = ramp(flow_along, 0.3, 0.9) if flow_along is not None else 0.5
        # thinness of the consumed side before the run
        deps = [(s.t, topk_depth(s, consumed)) for s in fr.states(self.window_s)]
        deps = [(t, d) for t, d in deps if d is not None]
        med = _median([d for _, d in deps])
        d_pre = deps[0][1] if deps else None
        d_now = deps[-1][1] if deps else None
        ref = max(x for x in (med, d_now) if x is not None) if (med is not None or d_now is not None) else None
        thin = clamp01(1.0 - d_pre / ref) if (ref and d_pre is not None) else None
        # stall at the extreme
        dt = (ms.t - t_ext).total_seconds()
        dist_ext = abs(pts[-1][1] - ext) / tick
        stall = ramp(dt, 10.0, 40.0) * clamp01(1.0 - dist_ext / 1.5)
        score = ramp(run_ticks, 2.0, 6.0) * (0.6 + 0.4 * ramp(run_speed, 1.0, 5.0)) * (0.4 + 0.6 * s_flow) * \
            (0.5 + 0.5 * ramp(thin, 0.1, 0.6)) * stall
        direction = -rdir if score > 0 else 0
        ev = {"run_direction": rdir, "run_ticks": run_ticks, "run_speed_ticks_per_min": run_speed,
              "consumed_side": consumed, "flow_consistency": consistency, "flow_along_run": flow_along,
              "signed_flow": signed_flow, "thin_share": thin, "depth_consumed_side_pre": d_pre,
              "depth_consumed_side_median": med, "depth_consumed_side_ref": ref,
              "seconds_since_extreme": dt, "dist_from_extreme_ticks": dist_ext,
              "stall": stall, "mid_pre": pre, "mid_extreme": ext, "mid_now": pts[-1][1],
              "direction": direction, "window_s": self.window_s}
        if missing:
            ev["missing"] = missing
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"run {run_ticks:.1f} ticks {'up' if rdir > 0 else 'down'}, stall {stall:.2f}")


# ============================================================================ #21
@register
class Ignition(Mechanism):
    """#21 Momentum expansion / ignition.

    Rule: v = ``price_velocity`` (else mid change over 60 s in ticks/min);
    a = ``price_acceleration`` (else v − v(now − 60 s)); ta =
    ``trade_acceleration`` (else trade_intensity − intensity(now − 120 s)),
    normalised by the mean trade intensity over the 300-s window (floor 1
    trade/min); spread expansion = spread_ticks − median spread_ticks over the
    window.  With sgn = sign(v): score = geometric mean(ramp(|v|, 1 → 5),
    ramp(a·sgn, 0.5 → 3), ramp(ta_rel, 0.25 → 1.5)) × (0.7 + 0.3 ×
    ramp(spread expansion, 0 → 2 ticks)).  direction = sgn.
    """

    name = "ignition"
    family = "sweep"
    requires = ("price_velocity", "price_acceleration", "trade_acceleration", "trade_intensity", "spread_ticks",
                "mid", "tick_size")
    window_s = 300.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick
        missing: List[str] = []
        vels = _velocity_series(fr, 120.0, tick) if tick else []
        v = ms.price_velocity if ms.price_velocity is not None else (vels[-1][1] if vels else None)
        if v is None:
            missing.append("price_velocity/mid history")
        a = ms.price_acceleration
        if a is None and vels:
            prev = next((vv for t, vv in reversed(vels) if t <= ms.t - timedelta(seconds=60.0)), None)
            a = (v - prev) if (prev is not None and v is not None) else None
        if a is None:
            missing.append("price_acceleration")
        ints = fr.series(lambda s: s.trade_intensity, self.window_s)
        ta = ms.trade_acceleration
        if ta is None and ints:
            prev_i = next((x for t, x in reversed(ints) if t <= ms.t - timedelta(seconds=120.0)), None)
            ta = (ints[-1][1] - prev_i) if prev_i is not None else None
        if ta is None:
            missing.append("trade_acceleration")
        if missing:
            return missing_reading(self, missing, base)
        mean_int = (sum(x for _, x in ints) / len(ints)) if ints else None
        ta_rel = ta / max(1.0, mean_int if mean_int is not None else 1.0)
        sp_now = spread_ticks_of(ms, tick)
        sps = [spread_ticks_of(s, tick) for s in fr.states(self.window_s)]
        sps = [x for x in sps if x is not None]
        sp_med = _median(sps)
        sp_exp = (sp_now - sp_med) if (sp_now is not None and sp_med is not None) else None
        sgn = sign(v)
        s_v, s_a, s_ta = ramp(abs(v), 1.0, 5.0), ramp(a * sgn, 0.5, 3.0), ramp(ta_rel, 0.25, 1.5)
        score = geo_mean([s_v, s_a, s_ta]) * (0.7 + 0.3 * ramp(sp_exp, 0.0, 2.0))
        direction = sgn if score > 0 else 0
        ev = {"velocity": v, "acceleration": a, "trade_acceleration": ta, "trade_acceleration_rel": ta_rel,
              "mean_intensity": mean_int, "spread_ticks": sp_now, "spread_median": sp_med, "spread_expansion": sp_exp,
              "components": {"velocity": s_v, "acceleration": s_a, "trade_acceleration": s_ta},
              "direction": direction, "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"v={v:.2f} a={a:.2f} ta={ta:.2f}")


# ============================================================================ #34
@register
class LiquidityDepletion(Mechanism):
    """#34 Liquidity depletion.

    Rule (window 120 s): touch depth = bid_qty1 + ask_qty1 and top-K depth =
    Σ first 5 levels per side, each as a series (both sides together and per
    side); then = the first value in the window (≥ 10 s before now), now = the
    latest: depletion = 1 − now / then for every series; the largest of them
    (and the queue engine's ``liquidity_depletion`` estimate, when present) is
    the depletion share — one side emptying is a depletion even when the other
    side is untouched.
    consistency = share of the non-zero touch-depth steps that fall.  price
    factor = 1 − |mid(now) − mid(then)| / 2 ticks, clipped ("without price
    move").  score = ramp(depletion, 0.2 → 0.7) × (0.5 + 0.5 × consistency) ×
    price factor.  direction: the side depleting more by ≥ 0.2 share sets it
    (bid −1, ask +1), else 0.
    """

    name = "liquidity_depletion"
    family = "sweep"
    requires = ("bid_qty1", "ask_qty1", "bids", "asks", "mid", "tick_size", "liquidity_depletion")
    window_s = 120.0
    min_span_s = 10.0

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:
        fr = Frame(ms, hist)
        base = baselines(fr)
        tick = fr.tick

        def touch(s: MarketState) -> Optional[float]:
            _, b = best_of(s, "bid")
            _, a = best_of(s, "ask")
            if b is None and a is None:
                return None
            return (b or 0.0) + (a or 0.0)

        def topk(s: MarketState) -> Optional[float]:
            b, a = topk_depth(s, "bid"), topk_depth(s, "ask")
            if b is None and a is None:
                return None
            return (b or 0.0) + (a or 0.0)

        ts = fr.series(touch, self.window_s)
        ks = fr.series(topk, self.window_s)
        if len(ts) < 2 or (ts[-1][0] - ts[0][0]).total_seconds() < self.min_span_s:
            miss = [k for k in ("bid_qty1", "ask_qty1") if getattr(ms, k) is None and not levels_of(ms, k[:3])] \
                or ["touch depth history (span < %.0f s)" % self.min_span_s]
            return missing_reading(self, miss, base, {"points": len(ts)})
        then_t, then_v = ts[0]
        now_v = ts[-1][1]
        d_touch = (1.0 - now_v / then_v) if then_v > 0 else None
        d_topk = (1.0 - ks[-1][1] / ks[0][1]) if (len(ks) >= 2 and ks[0][1] > 0) else None
        per_side: Dict[str, Optional[float]] = {}
        per_side_touch: Dict[str, Optional[float]] = {}
        for side in ("bid", "ask"):
            ser = fr.series(lambda s, sd=side: topk_depth(s, sd), self.window_s)
            per_side[side] = (1.0 - ser[-1][1] / ser[0][1]) if (len(ser) >= 2 and ser[0][1] > 0) else None
            ser1 = fr.series(lambda s, sd=side: best_of(s, sd)[1], self.window_s)
            per_side_touch[side] = (1.0 - ser1[-1][1] / ser1[0][1]) if (len(ser1) >= 2 and ser1[0][1] > 0) else None
        cands = [x for x in (d_touch, d_topk, ms.liquidity_depletion, *per_side.values(), *per_side_touch.values())
                 if x is not None]
        depl = max(cands) if cands else None
        if depl is None:
            return missing_reading(self, ["touch depth (zero at window start)"], base)
        cons = _step_consistency([v for _, v in ts], down=True)
        cons = cons if cons is not None else 0.0
        m_then = mid_of(fr.at_or_before(then_t)) if fr.at_or_before(then_t) is not None else None
        m_now = mid_of(ms)
        move = (abs(m_now - m_then) / tick) if (m_now is not None and m_then is not None and tick) else None
        pf = clamp01(1.0 - move / 2.0) if move is not None else 0.5
        direction = 0
        pb, pa = per_side["bid"], per_side["ask"]
        if pb is not None and pa is not None:
            if pb - pa >= 0.2:
                direction = -1
            elif pa - pb >= 0.2:
                direction = 1
        elif pb is not None and pb >= 0.2:
            direction = -1
        elif pa is not None and pa >= 0.2:
            direction = 1
        score = ramp(depl, 0.2, 0.7) * (0.5 + 0.5 * cons) * pf
        if score <= 0:
            direction = 0
        ev = {"depletion": depl, "depletion_touch": d_touch, "depletion_topk": d_topk,
              "engine_depletion": ms.liquidity_depletion, "consistency": cons, "mid_move_ticks": move,
              "price_factor": pf, "touch_then": then_v, "touch_now": now_v, "span_s": (ts[-1][0] - then_t).total_seconds(),
              "per_side_topk": per_side, "per_side_touch": per_side_touch, "direction": direction,
              "window_s": self.window_s}
        return MechanismReading(self.name, self.family, clamp01(score), "inactive", ev, base,
                                note=f"depleted {depl:.2f} with {move} ticks move")
