"""Circuit (daily price-limit) engine — everything about a symbol's distance to,
contact with and behaviour at its exchange price limits, plus the cross-session
streak bookkeeping.

Inputs are the fused :class:`tower.state.MarketState` (book, ltp, tape) and the
REFERENCE events carried by the LankaBD circuit-breaker table. Every derived
number is computed from those observations; anything not observable is ``None``.

Rules (all causal — a value at time t reads only states at or before t)
-----------------------------------------------------------------------
limits
    ``on_reference`` stores the published upper/lower limits, tick and breaker
    percentage (``rule_source`` = the source that carried them, e.g.
    ``lankabd_circuit``). When no reference exists for the symbol the limits are
    DERIVED from the previous close with the dated schedule
    ``bdlib.config.CIRCUIT_BANDS_UNVERIFIED``: band = the first (price ≤ X, ±b)
    bucket that fits yclose; upper = floor(yclose·(1+b) / tick)·tick, lower =
    ceil(yclose·(1−b) / tick)·tick (the published DSE limits sit INSIDE the band;
    this rounding rule reproduces 42/43 rows of the real fixture table and the
    43rd differs by one tick because the displayed reference price is rounded).
    These are flagged ``rule_source='bdlib_bands_unverified'``, ``unverified=True``.
distance
    from mid when a two-sided book exists, else ltp: ``dist_up_ticks`` =
    (upper − px)/tick, ``dist_up_pct`` = (upper − px)/px·100 (percent), mirror for
    down. ``nearer_limit`` names the closer side.
approach
    ``approach_velocity`` = −Δ(distance to the nearer limit, ticks) / Δt over the
    last 120 s, in ticks per minute (positive = moving toward the limit);
    ``approach_acceleration`` = Δ(velocity)/Δt over the same window (ticks/min²).
hit / lock
    ``hit_up`` when ltp, best bid or best ask equals upper (within half a tick);
    ``locked_up`` when best bid == upper AND no ask is displayed (the whole ask
    side has been consumed: nothing left to trade against at or below the limit);
    mirror for down. ``first_hit_time`` is the first update with a hit;
    ``time_locked_s`` accumulates the elapsed time between consecutive updates
    while the previous update was locked (a lock is held until observed
    otherwise); ``unlock_count`` counts locked→unlocked transitions,
    ``relock_count`` unlocked→locked transitions after an unlock, and
    ``time_between_unlock_relock_s`` is the last unlock→relock interval.
queue at the limit
    ``queue_at_upper`` = displayed bid qty at the upper limit price,
    ``queue_at_lower`` = ask qty at the lower limit; ``queue_delta_60s`` is the
    change of the nearer-limit queue over 60 s (``queue_growth`` / ``queue_decay``
    are its positive / negative parts) and ``queue_persistence_s`` the time the
    queue has been continuously non-zero.
volume
    interval volume / turnover of each NEW tape row (detected by the tape row
    counter increasing) is added to ``volume_approaching`` / ``turnover_approaching``
    while the nearer distance ≤ 2 % of price and not locked, and to
    ``volume_while_locked`` / ``turnover_while_locked`` while locked. ``None``
    until a tape feed exists.
pre-hit state
    at the first hit, the previous update's imb_topk, pressure, visible liquidity
    and shares_to_door are frozen into ``pre_hit_state``; ``pressure_before_hit``
    and ``liquidity_before_hit`` (visible liquidity on the side that had to be
    consumed to reach the limit) are lifted from it.
shares to the door
    ``shares_to_door`` = Σ displayed ask qty at prices ≤ upper (what must be
    bought to lock up), ``door_visible`` = the displayed ask side reaches the
    upper limit (otherwise the sum is only a lower bound); ``shares_to_floor`` /
    ``floor_visible`` mirror on the bid side.
exception
    ``exception='reference_reset_suspect'`` when the published limits are
    inconsistent with yclose·(1 ± breaker) beyond one tick plus
    ``bdlib.config.LIMIT_BAND_TOLERANCE``·yclose (ex-date / corporate-action
    reference reset), or a price (ltp, best bid/ask) sits beyond the band by the
    same tolerance. ``None`` when nothing needed for the check is observable.
streaks (needs ``set_day_history``)
    prior sessions are day records {date, close, yclose, upper, lower,
    locked_up_close, locked_down_close[, locked_share, unlock_count]}.
    ``consecutive_upper_streak`` = trailing sessions locked up at close, plus one
    when the symbol is locked up now; ``streak_continuation_strength`` = today's
    locked share of the elapsed session (÷ the previous session's locked share
    when the record carries it, capped at 1); ``streak_weakening`` = the queue at
    the limit is decaying over 60 s or today's unlock count exceeds the previous
    session's (≥ 1 when unknown); ``break_day`` = a prior streak exists and the
    symbol is off the limit now (``break_behaviour`` gives the open gap in ticks
    vs the prior limit, queue decay and reversal evidence); ``next_session`` =
    'continuation' when today's open is at/beyond the prior session's locked
    limit, 'reversal' otherwise, ``None`` until an open is observed.
day roll
    when the trading date of an update changes, the finished day's
    ``day_summary`` is appended to the history automatically and the intraday
    accumulators reset, so multi-day replays carry streaks across sessions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bdlib.config import CIRCUIT_BANDS_UNVERIFIED, LIMIT_BAND_TOLERANCE
from seeing.clock import trading_date

from .mechanics.base import StateHistory
from .state import MarketState
from .windows import RollingSeries

APPROACH_WINDOW_S = 120.0
QUEUE_WINDOW_S = 60.0
APPROACH_PCT = 2.0            # "approaching" = nearer distance ≤ 2 % of price
BANDS_RULE_SOURCE = "bdlib_bands_unverified"


def band_for(price: float, bands: Sequence[Tuple[float, float]] = CIRCUIT_BANDS_UNVERIFIED) -> Optional[float]:
    """The ± band fraction of the first dated bucket whose price ceiling fits ``price``."""
    if price is None or price <= 0:
        return None
    for ceiling, b in bands:
        if price <= ceiling:
            return float(b)
    return None


def limits_from_reference(ref_price: float, band: float, tick: Optional[float]) -> Tuple[float, float, bool]:
    """(upper, lower, rounded): limits inside the band, upper floored and lower
    ceiled to the tick grid when a tick is known (the DSE convention seen in the
    published table); unrounded when the tick is unknown."""
    up = ref_price * (1.0 + band)
    lo = ref_price * (1.0 - band)
    if tick and tick > 0:
        n_up = math.floor(up / tick + 1e-9)
        n_lo = math.ceil(lo / tick - 1e-9)
        return round(n_up * tick, 6), round(n_lo * tick, 6), True
    return up, lo, False


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if (x != x or x <= 0) else x


@dataclass
class _Ref:
    upper: Optional[float] = None
    lower: Optional[float] = None
    tick: Optional[float] = None
    breaker_pct: Optional[float] = None
    reference_date: Optional[str] = None
    rule_source: Optional[str] = None
    unverified: bool = True
    band: Optional[float] = None           # fraction used when derived
    derived_from: Optional[float] = None   # yclose the derived limits came from
    rounded: Optional[bool] = None


@dataclass
class _Day:
    """Intraday accumulators for one symbol and one trading date."""
    date: Optional[date] = None
    t_first: Optional[datetime] = None
    t_prev: Optional[datetime] = None
    prev_locked_up: bool = False
    prev_locked_down: bool = False
    first_hit_time: Optional[datetime] = None
    first_hit_side: Optional[str] = None
    first_hit_up_time: Optional[datetime] = None
    first_hit_down_time: Optional[datetime] = None
    first_lock_time: Optional[datetime] = None
    time_locked_s: float = 0.0
    time_locked_up_s: float = 0.0
    time_locked_down_s: float = 0.0
    unlock_count: int = 0
    relock_count: int = 0
    last_unlock_time: Optional[datetime] = None
    last_relock_time: Optional[datetime] = None
    time_between_unlock_relock_s: Optional[float] = None
    ever_locked_up: bool = False
    ever_locked_down: bool = False
    volume_approaching: Optional[float] = None
    turnover_approaching: Optional[float] = None
    volume_while_locked: Optional[float] = None
    turnover_while_locked: Optional[float] = None
    tape_rows_seen: Optional[int] = None
    pre_hit_state: Optional[Dict[str, Any]] = None
    open_price: Optional[float] = None
    max_queue_at_limit: Optional[float] = None
    queue_nonzero_since: Optional[datetime] = None
    dist_up: RollingSeries = field(default_factory=lambda: RollingSeries(window_s=APPROACH_WINDOW_S * 2, min_keep=4))
    dist_down: RollingSeries = field(default_factory=lambda: RollingSeries(window_s=APPROACH_WINDOW_S * 2, min_keep=4))
    velocity: RollingSeries = field(default_factory=lambda: RollingSeries(window_s=APPROACH_WINDOW_S * 2, min_keep=4))
    queue_up: RollingSeries = field(default_factory=lambda: RollingSeries(window_s=QUEUE_WINDOW_S * 2, min_keep=4))
    queue_down: RollingSeries = field(default_factory=lambda: RollingSeries(window_s=QUEUE_WINDOW_S * 2, min_keep=4))
    last: Dict[str, Any] = field(default_factory=dict)
    yclose: Optional[float] = None
    close: Optional[float] = None
    ltp_last: Optional[float] = None
    upper: Optional[float] = None
    lower: Optional[float] = None


def _rate(series: RollingSeries, seconds: float) -> Tuple[Optional[float], Optional[float]]:
    """(Δvalue, Δt seconds) between the latest point and the last point at or
    before ``seconds`` earlier. None when no such earlier point exists."""
    if len(series.buf) < 2:
        return None, None
    now = series.buf[-1]
    cutoff = now.t - timedelta(seconds=seconds)
    prev = None
    for p in reversed(series.buf):
        if p.t <= cutoff:
            prev = p
            break
    if prev is None:
        return None, None
    dt = (now.t - prev.t).total_seconds()
    if dt <= 0:
        return None, None
    return now.v - prev.v, dt


class CircuitEngine:
    """Per-symbol circuit state (keyed by symbol so one instance may serve one
    or many symbols). Call ``on_reference`` for REFERENCE events, ``on_state``
    once per MarketState before ``fill_state``."""

    def __init__(self, approach_window_s: float = APPROACH_WINDOW_S, queue_window_s: float = QUEUE_WINDOW_S,
                 approach_pct: float = APPROACH_PCT) -> None:
        self.approach_window_s = float(approach_window_s)
        self.queue_window_s = float(queue_window_s)
        self.approach_pct = float(approach_pct)
        self._ref: Dict[str, _Ref] = {}
        self._day: Dict[str, _Day] = {}
        self._history: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------ references
    def on_reference(self, symbol: str, upper: Optional[float], lower: Optional[float], tick: Optional[float],
                     breaker_pct: Optional[float], reference_date: Optional[str] = None,
                     rule_source: str = "lankabd_circuit") -> None:
        """Store published limits. A reference without both limits still carries
        the tick and breaker % (used by the band fallback); the limits stay derived."""
        r = self._ref.get(symbol) or _Ref()
        up, lo = _f(upper), _f(lower)
        if up is not None and lo is not None:
            r.upper, r.lower = up, lo
            r.rule_source = rule_source
            r.unverified = rule_source == BANDS_RULE_SOURCE
            r.derived_from = None
            r.band = None
            r.rounded = None
        if _f(tick) is not None:
            r.tick = _f(tick)
        if breaker_pct is not None:
            try:
                r.breaker_pct = float(breaker_pct)
            except (TypeError, ValueError):
                pass
        if reference_date:
            r.reference_date = str(reference_date)
        self._ref[symbol] = r

    def set_day_history(self, symbol: str, records: Sequence[Dict[str, Any]]) -> None:
        """Prior sessions, oldest first; each {date, close, yclose, upper, lower,
        locked_up_close, locked_down_close[, locked_share, unlock_count]}."""
        recs = [dict(r) for r in records]
        recs.sort(key=lambda r: str(r.get("date") or ""))
        self._history[symbol] = recs

    def day_history(self, symbol: str) -> List[Dict[str, Any]]:
        return list(self._history.get(symbol, []))

    # ------------------------------------------------------------------ main update
    def on_state(self, ms: MarketState, hist: Optional[StateHistory] = None) -> Dict[str, Any]:
        sym = ms.symbol
        day = self._day.get(sym)
        tdate = trading_date(ms.t)
        if day is not None and day.date is not None and tdate != day.date:
            # a new trading date: close the finished session into the history
            self._history.setdefault(sym, []).append(self.day_summary(sym))
            day = None
        if day is None:
            day = _Day(date=tdate, t_first=ms.t)
            self._day[sym] = day
        quote = (ms.session_state or {}).get("quote") or {}
        yclose = _f(quote.get("yclose"))
        if yclose is not None:
            day.yclose = yclose
        if _f(quote.get("open")) is not None and day.open_price is None:
            day.open_price = _f(quote.get("open"))
        if _f(quote.get("close_published")) is not None:
            day.close = _f(quote.get("close_published"))
        if ms.ltp is not None:
            day.ltp_last = ms.ltp
            if day.open_price is None:
                day.open_price = ms.ltp
        tick = ms.tick_size if ms.tick_size else None
        ref = self._resolve_reference(sym, day.yclose, tick)
        if tick is None and ref.tick:
            tick = ref.tick
        upper, lower = ref.upper, ref.lower
        day.upper, day.lower = upper, lower

        c: Dict[str, Any] = {
            "upper_limit": upper, "lower_limit": lower, "tick": tick, "rule_source": ref.rule_source,
            "unverified": ref.unverified if ref.rule_source else None, "breaker_pct": ref.breaker_pct,
            "reference_date": ref.reference_date, "band": ref.band, "yclose": day.yclose,
            "price_basis": None, "price": None,
            "dist_up_ticks": None, "dist_down_ticks": None, "dist_up_pct": None, "dist_down_pct": None,
            "nearer_limit": None, "approach_velocity": None, "approach_acceleration": None,
            "hit_up": None, "hit_down": None, "locked_up": None, "locked_down": None,
            "first_hit_time": day.first_hit_time, "first_hit_side": day.first_hit_side,
            "first_lock_time": day.first_lock_time,
            "time_locked_s": day.time_locked_s, "time_locked_up_s": day.time_locked_up_s,
            "time_locked_down_s": day.time_locked_down_s,
            "unlock_count": day.unlock_count, "relock_count": day.relock_count,
            "time_between_unlock_relock_s": day.time_between_unlock_relock_s,
            "queue_at_upper": None, "queue_at_lower": None, "queue_side": None, "queue_delta_60s": None,
            "queue_growth": None, "queue_decay": None, "queue_persistence_s": None,
            "volume_approaching": day.volume_approaching, "turnover_approaching": day.turnover_approaching,
            "volume_while_locked": day.volume_while_locked, "turnover_while_locked": day.turnover_while_locked,
            "pre_hit_state": day.pre_hit_state,
            "pressure_before_hit": (day.pre_hit_state or {}).get("pressure_strength"),
            "liquidity_before_hit": (day.pre_hit_state or {}).get("liquidity_door_side"),
            "shares_to_door": None, "door_visible": None, "shares_to_floor": None, "floor_visible": None,
            "exception": None,
            "consecutive_upper_streak": None, "consecutive_lower_streak": None,
            "streak_continuation_strength": None, "streak_weakening": None, "break_day": None,
            "break_behaviour": None, "next_session": None, "open_price": day.open_price,
            "session_elapsed_s": (ms.t - day.t_first).total_seconds() if day.t_first else None,
        }

        # ---- price basis
        px, basis = None, None
        if ms.mid is not None:
            px, basis = ms.mid, "mid"
        elif ms.ltp is not None:
            px, basis = ms.ltp, "ltp"
        c["price"], c["price_basis"] = px, basis
        half_tick = (tick / 2.0) if tick else None

        # ---- distances
        if px is not None and upper is not None and lower is not None and px > 0:
            c["dist_up_pct"] = (upper - px) / px * 100.0
            c["dist_down_pct"] = (px - lower) / px * 100.0
            if tick:
                c["dist_up_ticks"] = (upper - px) / tick
                c["dist_down_ticks"] = (px - lower) / tick
                day.dist_up.push(ms.t, c["dist_up_ticks"])
                day.dist_down.push(ms.t, c["dist_down_ticks"])
            c["nearer_limit"] = "up" if c["dist_up_pct"] <= c["dist_down_pct"] else "down"

        # ---- approach velocity / acceleration (ticks per minute toward the nearer limit)
        if c["nearer_limit"] is not None and tick:
            series = day.dist_up if c["nearer_limit"] == "up" else day.dist_down
            dv, dt = _rate(series, self.approach_window_s)
            if dv is not None:
                vel = -dv / (dt / 60.0)
                c["approach_velocity"] = vel
                day.velocity.push(ms.t, vel)
                dvv, dtt = _rate(day.velocity, self.approach_window_s)
                if dvv is not None:
                    c["approach_acceleration"] = dvv / (dtt / 60.0)

        # ---- hit / lock
        bb, ba = ms.best_bid, ms.best_ask
        has_asks = bool(ms.asks) or ba is not None
        has_bids = bool(ms.bids) or bb is not None

        def at(p: Optional[float], lim: Optional[float]) -> bool:
            if p is None or lim is None:
                return False
            tol = half_tick if half_tick else 1e-9
            return abs(p - lim) < tol

        if upper is not None and (px is not None or bb is not None or ba is not None):
            c["hit_up"] = at(ms.ltp, upper) or at(bb, upper) or at(ba, upper)
            c["hit_down"] = at(ms.ltp, lower) or at(bb, lower) or at(ba, lower)
            c["locked_up"] = bool(at(bb, upper) and not has_asks)
            c["locked_down"] = bool(at(ba, lower) and not has_bids)
        hit_up, hit_down = bool(c["hit_up"]), bool(c["hit_down"])
        locked_up, locked_down = bool(c["locked_up"]), bool(c["locked_down"])
        locked = locked_up or locked_down
        prev_locked = day.prev_locked_up or day.prev_locked_down

        # ---- shares to the door / floor
        if ms.asks and upper is not None:
            c["shares_to_door"] = float(sum(q for p, q in ms.asks if p <= upper + (half_tick or 0)))
            c["door_visible"] = bool(max(p for p, _ in ms.asks) >= upper - (half_tick or 0))
        elif upper is not None and ms.best_bid is not None and not ms.asks:
            c["shares_to_door"] = 0.0          # nothing displayed to buy: the door is open (or the book is one-sided)
            c["door_visible"] = True
        if ms.bids and lower is not None:
            c["shares_to_floor"] = float(sum(q for p, q in ms.bids if p >= lower - (half_tick or 0)))
            c["floor_visible"] = bool(min(p for p, _ in ms.bids) <= lower + (half_tick or 0))
        elif lower is not None and ms.best_ask is not None and not ms.bids:
            c["shares_to_floor"] = 0.0
            c["floor_visible"] = True

        # ---- pre-hit snapshot (the update BEFORE the first hit; pressure is filled after
        #      this engine runs, so it is read from history, never from the current frame)
        if (hit_up or hit_down) and day.first_hit_time is None:
            day.first_hit_time = ms.t
            day.first_hit_side = "up" if hit_up else "down"
            prev = hist.last(1)[0] if (hist is not None and len(hist)) else None
            if prev is not None:
                pc = prev.circuit or {}
                door_side_liq = prev.visible_ask_liq if hit_up else prev.visible_bid_liq
                day.pre_hit_state = {
                    "t": prev.t.isoformat(), "side": day.first_hit_side, "imb_topk": prev.imb_topk,
                    "imb_l1": prev.imb_l1, "pressure_strength": prev.pressure_strength,
                    "pressure_direction": prev.pressure_direction, "visible_bid_liq": prev.visible_bid_liq,
                    "visible_ask_liq": prev.visible_ask_liq, "liquidity_door_side": door_side_liq,
                    "shares_to_door": pc.get("shares_to_door") if hit_up else pc.get("shares_to_floor"),
                    "approach_velocity": pc.get("approach_velocity"), "dist_ticks":
                        pc.get("dist_up_ticks") if hit_up else pc.get("dist_down_ticks"),
                    "trade_intensity": prev.trade_intensity, "signed_flow_window": prev.signed_flow_window,
                }
            else:
                day.pre_hit_state = {"t": None, "side": day.first_hit_side, "missing": ["no update before the hit"]}
            c["pre_hit_state"] = day.pre_hit_state
            c["pressure_before_hit"] = day.pre_hit_state.get("pressure_strength")
            c["liquidity_before_hit"] = day.pre_hit_state.get("liquidity_door_side")
            c["first_hit_time"], c["first_hit_side"] = day.first_hit_time, day.first_hit_side
        if hit_up and day.first_hit_up_time is None:
            day.first_hit_up_time = ms.t
        if hit_down and day.first_hit_down_time is None:
            day.first_hit_down_time = ms.t
        c["first_hit_up_time"], c["first_hit_down_time"] = day.first_hit_up_time, day.first_hit_down_time

        # ---- lock time accounting and unlock / relock transitions
        if day.t_prev is not None:
            dt = (ms.t - day.t_prev).total_seconds()
            if dt > 0 and prev_locked:
                day.time_locked_s += dt
                if day.prev_locked_up:
                    day.time_locked_up_s += dt
                if day.prev_locked_down:
                    day.time_locked_down_s += dt
        if locked and not prev_locked:
            if day.first_lock_time is None:
                day.first_lock_time = ms.t
            if day.last_unlock_time is not None:
                day.relock_count += 1
                day.last_relock_time = ms.t
                day.time_between_unlock_relock_s = (ms.t - day.last_unlock_time).total_seconds()
        elif prev_locked and not locked:
            day.unlock_count += 1
            day.last_unlock_time = ms.t
        day.ever_locked_up = day.ever_locked_up or locked_up
        day.ever_locked_down = day.ever_locked_down or locked_down
        c.update({"time_locked_s": day.time_locked_s, "time_locked_up_s": day.time_locked_up_s,
                  "time_locked_down_s": day.time_locked_down_s, "unlock_count": day.unlock_count,
                  "relock_count": day.relock_count, "first_lock_time": day.first_lock_time,
                  "time_between_unlock_relock_s": day.time_between_unlock_relock_s,
                  "last_unlock_time": day.last_unlock_time, "last_relock_time": day.last_relock_time,
                  "ever_locked_up": day.ever_locked_up, "ever_locked_down": day.ever_locked_down})

        # ---- queue at the limit
        q_up = q_dn = None
        if ms.bids and upper is not None:
            q_up = float(sum(q for p, q in ms.bids if at(p, upper)))
        if ms.asks and lower is not None:
            q_dn = float(sum(q for p, q in ms.asks if at(p, lower)))
        c["queue_at_upper"], c["queue_at_lower"] = q_up, q_dn
        if q_up is not None:
            day.queue_up.push(ms.t, q_up)
        if q_dn is not None:
            day.queue_down.push(ms.t, q_dn)
        side = c["nearer_limit"]
        if side is None and (q_up is not None or q_dn is not None):
            side = "up" if q_up is not None else "down"
        q_now = q_up if side == "up" else q_dn
        if q_now is not None:
            c["queue_side"] = side
            qs = day.queue_up if side == "up" else day.queue_down
            dq, _ = _rate(qs, self.queue_window_s)
            if dq is not None:
                c["queue_delta_60s"] = dq
                c["queue_growth"] = max(dq, 0.0)
                c["queue_decay"] = max(-dq, 0.0)
            if q_now > 0:
                if day.queue_nonzero_since is None:
                    day.queue_nonzero_since = ms.t
                c["queue_persistence_s"] = (ms.t - day.queue_nonzero_since).total_seconds()
                day.max_queue_at_limit = max(day.max_queue_at_limit or 0.0, q_now)
            else:
                day.queue_nonzero_since = None
                c["queue_persistence_s"] = 0.0
        c["max_queue_at_limit"] = day.max_queue_at_limit

        # ---- volume / turnover while approaching and while locked (new tape rows only)
        tape = (ms.session_state or {}).get("tape") or {}
        rows = tape.get("rows")
        if tape.get("feed") is not None and rows is not None:
            if day.volume_approaching is None:
                day.volume_approaching = day.volume_while_locked = 0.0
                day.turnover_approaching = day.turnover_while_locked = 0.0
            new_rows = day.tape_rows_seen is not None and rows > day.tape_rows_seen
            day.tape_rows_seen = rows if day.tape_rows_seen is None else max(day.tape_rows_seen, rows)
            iv = ms.interval_volume if (ms.interval_volume is not None and ms.interval_volume > 0) else 0.0
            ival = tape.get("last_interval_value")
            ival = float(ival) if (ival is not None and ival > 0) else 0.0
            near_pct = None
            if c["dist_up_pct"] is not None:
                near_pct = min(c["dist_up_pct"], c["dist_down_pct"])
            if new_rows:
                if locked:
                    day.volume_while_locked += iv
                    day.turnover_while_locked += ival
                elif near_pct is not None and near_pct <= self.approach_pct:
                    day.volume_approaching += iv
                    day.turnover_approaching += ival
        c.update({"volume_approaching": day.volume_approaching, "turnover_approaching": day.turnover_approaching,
                  "volume_while_locked": day.volume_while_locked, "turnover_while_locked": day.turnover_while_locked})

        # ---- exception: reference reset / price beyond the band
        c["exception"], c["exception_detail"] = self._exception(ref, day.yclose, tick, ms, upper, lower)

        # ---- streaks across sessions
        self._streaks(sym, day, c, ms, locked_up, locked_down, locked)

        day.t_prev = ms.t
        day.prev_locked_up, day.prev_locked_down = locked_up, locked_down
        day.last = c
        return c

    # ------------------------------------------------------------------ helpers
    def _resolve_reference(self, sym: str, yclose: Optional[float], tick: Optional[float]) -> _Ref:
        r = self._ref.get(sym) or _Ref()
        published = r.rule_source is not None and r.rule_source != BANDS_RULE_SOURCE and r.upper is not None
        if published:
            return r
        if yclose is None:
            # nothing to derive from: keep whatever partial reference exists (tick / breaker)
            r.upper = r.lower = None
            r.rule_source = None
            self._ref[sym] = r
            return r
        tk = tick or r.tick
        if r.rule_source == BANDS_RULE_SOURCE and r.derived_from == yclose and r.tick == tk and r.upper is not None:
            return r
        band = band_for(yclose)
        if band is None:
            return r
        up, lo, rounded = limits_from_reference(yclose, band, tk)
        r.upper, r.lower, r.rounded = up, lo, rounded
        r.band, r.derived_from = band, yclose
        r.tick = tk
        r.rule_source = BANDS_RULE_SOURCE
        r.unverified = True
        self._ref[sym] = r
        return r

    def _exception(self, ref: _Ref, yclose: Optional[float], tick: Optional[float], ms: MarketState,
                   upper: Optional[float], lower: Optional[float]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if upper is None or lower is None:
            return None, None
        tol_price = (tick or 0.0) + LIMIT_BAND_TOLERANCE * (yclose or upper)
        detail: Dict[str, Any] = {}
        # (a) published limits vs the band implied by yclose
        if yclose is not None and ref.rule_source != BANDS_RULE_SOURCE:
            band = (ref.breaker_pct / 100.0) if ref.breaker_pct is not None else band_for(yclose)
            if band is not None:
                exp_up, exp_lo, _ = limits_from_reference(yclose, band, tick)
                if abs(upper - exp_up) > tol_price or abs(lower - exp_lo) > tol_price:
                    detail["limits_vs_yclose"] = {"expected_upper": exp_up, "expected_lower": exp_lo,
                                                  "upper": upper, "lower": lower, "band": band, "tolerance": tol_price}
        # (b) a price beyond the band
        beyond = {}
        for name, p in (("ltp", ms.ltp), ("best_bid", ms.best_bid), ("best_ask", ms.best_ask)):
            if p is None:
                continue
            if p > upper + tol_price or p < lower - tol_price:
                beyond[name] = p
        if beyond:
            detail["price_beyond_band"] = {"prices": beyond, "upper": upper, "lower": lower, "tolerance": tol_price}
        if detail:
            return "reference_reset_suspect", detail
        return None, None

    def _streaks(self, sym: str, day: _Day, c: Dict[str, Any], ms: MarketState,
                 locked_up: bool, locked_down: bool, locked: bool) -> None:
        recs = self._history.get(sym) or []
        prior_up = prior_down = 0
        for r in reversed(recs):
            if r.get("locked_up_close"):
                prior_up += 1
            else:
                break
        for r in reversed(recs):
            if r.get("locked_down_close"):
                prior_down += 1
            else:
                break
        prev = recs[-1] if recs else None
        c["prior_upper_streak"], c["prior_lower_streak"] = prior_up, prior_down
        c["consecutive_upper_streak"] = prior_up + (1 if locked_up else 0)
        c["consecutive_lower_streak"] = prior_down + (1 if locked_down else 0)
        streak_side = "up" if prior_up else ("down" if prior_down else None)
        if streak_side is None:
            return
        elapsed = c.get("session_elapsed_s")
        today_share = (day.time_locked_s / elapsed) if (elapsed and elapsed > 0) else None
        c["locked_share_today"] = today_share
        if today_share is not None:
            prev_share = prev.get("locked_share") if prev else None
            if prev_share is not None and prev_share > 0:
                c["streak_continuation_strength"] = min(1.0, today_share / float(prev_share))
            else:
                c["streak_continuation_strength"] = today_share
        # weakening: the queue at the streak limit decays, or unlocks exceed the previous session's
        q_delta = c.get("queue_delta_60s") if c.get("queue_side") == streak_side else None
        prev_unlocks = prev.get("unlock_count") if prev else None
        unlock_rising = (day.unlock_count > int(prev_unlocks)) if prev_unlocks is not None else (day.unlock_count >= 1)
        weakening = None
        if q_delta is not None or day.t_prev is not None:
            weakening = bool((q_delta is not None and q_delta < 0) or unlock_rising)
        c["streak_weakening"] = weakening
        # break day: prior streak and the symbol is off the limit now
        off_limit = not (locked_up if streak_side == "up" else locked_down)
        opened = day.open_price is not None or ms.ltp is not None or ms.best_bid is not None or ms.best_ask is not None
        c["break_day"] = bool(off_limit and opened) if opened else None
        prior_lim = (prev or {}).get("upper" if streak_side == "up" else "lower")
        tick = c.get("tick")
        beh: Dict[str, Any] = {"gap_open_ticks": None, "queue_decay": None, "reversal": None}
        if day.open_price is not None and prior_lim is not None and tick:
            beh["gap_open_ticks"] = (day.open_price - float(prior_lim)) / tick
        if q_delta is not None:
            beh["queue_decay"] = q_delta < 0
        elif c.get("queue_side") == streak_side and c.get("queue_at_upper" if streak_side == "up" else "queue_at_lower") == 0:
            beh["queue_decay"] = True
        px = c.get("price")
        if px is not None and prior_lim is not None:
            beh["reversal"] = (px < float(prior_lim)) if streak_side == "up" else (px > float(prior_lim))
        if ms.price_velocity is not None:
            beh["price_velocity"] = ms.price_velocity
        c["break_behaviour"] = beh
        # next session: today's open vs the prior locked limit
        if day.open_price is not None and prior_lim is not None:
            if streak_side == "up":
                c["next_session"] = "continuation" if day.open_price >= float(prior_lim) - 1e-9 else "reversal"
            else:
                c["next_session"] = "continuation" if day.open_price <= float(prior_lim) + 1e-9 else "reversal"

    # ------------------------------------------------------------------ outputs
    def fill_state(self, ms: MarketState) -> None:
        day = self._day.get(ms.symbol)
        c = dict(day.last) if (day is not None and day.last) else self.on_state(ms, None)
        ms.circuit = c
        ref = self._ref.get(ms.symbol)
        ms.session_state["circuit_rule"] = {
            "rule_source": c.get("rule_source"), "unverified": c.get("unverified"), "band": c.get("band"),
            "breaker_pct": c.get("breaker_pct"), "reference_date": c.get("reference_date"),
            "tick": c.get("tick"), "rounded": ref.rounded if ref else None,
            "rule": ("published limits" if c.get("rule_source") and c.get("rule_source") != BANDS_RULE_SOURCE else
                     "yclose × (1 ± band) from bdlib.config.CIRCUIT_BANDS_UNVERIFIED, floor/ceil to tick"
                     if c.get("rule_source") == BANDS_RULE_SOURCE else None),
        }

    def current(self, symbol: str) -> Dict[str, Any]:
        day = self._day.get(symbol)
        return dict(day.last) if day else {}

    def day_summary(self, symbol: str) -> Dict[str, Any]:
        """The finished (or running) session as a day-history record for the next session."""
        day = self._day.get(symbol)
        if day is None:
            return {"symbol": symbol, "date": None}
        elapsed = (day.t_prev - day.t_first).total_seconds() if (day.t_first and day.t_prev) else None
        close = day.close if day.close is not None else day.ltp_last
        return {
            "symbol": symbol, "date": day.date.isoformat() if day.date else None,
            "close": close, "yclose": day.yclose, "open": day.open_price,
            "upper": day.upper, "lower": day.lower,
            "locked_up_close": bool(day.prev_locked_up), "locked_down_close": bool(day.prev_locked_down),
            "ever_locked_up": day.ever_locked_up, "ever_locked_down": day.ever_locked_down,
            "locked_share": (day.time_locked_s / elapsed) if (elapsed and elapsed > 0) else None,
            "time_locked_s": day.time_locked_s, "unlock_count": day.unlock_count, "relock_count": day.relock_count,
            "first_hit_time": day.first_hit_time.isoformat() if day.first_hit_time else None,
            "first_hit_side": day.first_hit_side, "max_queue_at_limit": day.max_queue_at_limit,
            "volume_while_locked": day.volume_while_locked, "volume_approaching": day.volume_approaching,
            "t_last": day.t_prev.isoformat() if day.t_prev else None,
        }


# ---------------------------------------------------------------------------- day history from seeing tables
def day_history_from_tables(tables: Dict[str, Any], symbol: str) -> List[Dict[str, Any]]:
    """Build prior-session records for ``set_day_history`` from ``seeing.replay.replay``
    tables: one record per trading date from the depth frames (last close_published
    or ltp of the day, yclose, open) with limits from the circuit table for that
    symbol when present, else the band schedule on yclose. A session counts as
    locked at close when its close equals the limit (within half a tick)."""
    books = tables.get("books")
    if books is None or len(books) == 0 or "symbol" not in books.columns:
        return []
    b = books[books["symbol"] == symbol]
    if len(b) == 0:
        return []
    circ = tables.get("circuit")
    lim_by_date: Dict[str, Tuple[float, float, Optional[float]]] = {}
    if circ is not None and len(circ) and "symbol" in circ.columns:
        for _, row in circ[circ["symbol"] == symbol].iterrows():
            up, lo = _f(row.get("upper_limit")), _f(row.get("lower_limit"))
            if up is None or lo is None:
                continue
            d = str(row.get("reference_date") or trading_date(_utc(row["t_recv"])).isoformat())
            lim_by_date[d] = (up, lo, _f(row.get("tick_size")))
    out: List[Dict[str, Any]] = []
    b = b.assign(_date=[trading_date(_utc(t)).isoformat() for t in b["t_recv"]]).sort_values("t_recv", kind="mergesort")
    for d, g in b.groupby("_date", sort=True):
        last = g.iloc[-1]
        close = _f(last.get("close_published")) or _f(last.get("ltp"))
        yclose = _f(last.get("yclose"))
        opn = _f(last.get("open"))
        up = lo = tick = None
        if d in lim_by_date:
            up, lo, tick = lim_by_date[d]
        elif yclose is not None:
            band = band_for(yclose)
            if band is not None:
                up, lo, _ = limits_from_reference(yclose, band, 0.1)
                tick = 0.1
        half = (tick or 0.1) / 2.0
        out.append({"symbol": symbol, "date": d, "close": close, "yclose": yclose, "open": opn, "upper": up, "lower": lo,
                    "locked_up_close": bool(close is not None and up is not None and abs(close - up) < half),
                    "locked_down_close": bool(close is not None and lo is not None and abs(close - lo) < half),
                    "source": "seeing_tables"})
    return out


def _utc(t: Any) -> datetime:
    from .events import utc
    return utc(t)
