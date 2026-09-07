"""QueueState — touch-queue dynamics of one symbol from successive book observations.

``on_book(t, book, interval_volume=None)`` is called after every book update
(``t`` may also be a ``MarketState`` — then its ``t`` and tape fields are used).
The book is read through a small adapter (``EvolvingBook.levels(side)``, or
``bids``/``asks`` lists with optional ``bid_orders``/``ask_orders``), so any
displayed-book object works.

Per side the engine keeps the touch queue — (price, qty[, orders]) at the best —
over time and classifies every change against the previous observation:

  stack      touch qty rises at the same price (+Δ);
  improve    a better price appears in front (fresh liquidity at the touch);
  drop       touch qty falls at the same price, or the best retreats (the old
             best level is gone — its whole qty is the drop).

A drop is split into **traded** and **pulled** with the tape's interval volume,
bounded so that traded ≤ volume: interval volumes arriving from the tape form a
budget (each entry lives ``LAG_S`` = 90 s, because the tape and the book are
polled independently); a drop consumes the budget first, the remainder stays
*pending*; new volume is allocated to pending drops oldest-first; a drop still
pending after ``LAG_S`` is finalised as a **pull** (cancel-like: no trades ever
accounted for it).  Counters therefore settle with at most 90 s of delay and
the unsettled quantity is reported as ``pending_qty``.

Other rules
  refresh churn        best-quote (price or qty) changes per minute over 60 s,
                       counted only when the best price shows zero net drift
                       over the same 60 s (otherwise the changes are a move,
                       not churn — churn is reported 0 and the raw change rate
                       and drift are reported alongside);
  depletion episode    the touch qty at one price falls to ≤ 50 % of the qty
                       before the drop (a retreat counts as a fall to 0);
                       pre-depletion qty = the qty before the drop;
  replenishment        within 120 s of the episode start the touch qty (same
                       price or a better one) is back to ≥ 80 % of the
                       pre-depletion qty; time-to-replenish is recorded;
  queue position       an order joining side s at price p has
                       Σ qty at better-or-equal prices ahead of it (and Σ
                       orders when the source carries counts);
  liquidity_depletion  share of touch depth (bid_qty1 + ask_qty1) consumed over
                       120 s: max(0, (D(t−120 s) − D(t)) / D(t−120 s)); a book
                       that emptied after showing levels has D = 0 (both
                       touches retreated), a book that never showed a level
                       has no D at all (None);
  liquidity_replenishment  share of the most recent depletion episode (≤ 120 s
                       old) rebuilt: (touch qty − low) / (pre − low), clamped;
  liquidity_retreat    over 120 s both sides finalised pulls > 0, no traded
                       consumption on either side, and both touch depths fell;
  liquidity_vacuum     visible depth on both sides < 20 % of that side's 300-s
                       median (≥ 5 observations spanning ≥ 60 s) and no stack /
                       improve on either side for 60 s;
  depth_added/removed  Σ positive / |negative| level Δqty per side of the last
                       update (written only when the book engine left them None).

Every value that cannot be formed from the observations is None; all times are
event times supplied by the caller.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

from .state import MarketState
from .windows import RollingSeries

LAG_S = 90.0
CHURN_W_S = 60.0
DEPLETION_W_S = 120.0
REPLENISH_W_S = 120.0
REPLENISH_SHARE = 0.80
DEPLETION_SHARE = 0.50
VACUUM_MEDIAN_W_S = 300.0
VACUUM_MIN_POINTS = 5
VACUUM_MIN_SPAN_S = 60.0
VACUUM_SHARE = 0.20
VACUUM_QUIET_S = 60.0
KEEP_S = 1800.0


def _levels_of(book: Any, side: str) -> List[Tuple[float, float, Optional[int]]]:
    """(price, qty, orders) best first from an EvolvingBook, a MarketState-like object or a dict."""
    if book is None:
        return []
    lv = getattr(book, "levels", None)
    if callable(lv):
        return [(float(l.price), float(l.qty), l.orders) for l in lv(side)]
    key = "bids" if side == "bid" else "asks"
    rows = book.get(key) if isinstance(book, dict) else getattr(book, key, None)
    if callable(rows):
        rows = rows()
    okey = f"{side}_orders"
    orders = book.get(okey) if isinstance(book, dict) else getattr(book, okey, None)
    out: List[Tuple[float, float, Optional[int]]] = []
    for i, r in enumerate(rows or []):
        o = r[2] if len(r) > 2 else None
        if orders is not None and i < len(orders) and orders[i] is not None:
            o = orders[i]
        out.append((float(r[0]), float(r[1]), (int(o) if o is not None else None)))
    out.sort(key=lambda x: -x[0] if side == "bid" else x[0])
    return out


@dataclass
class _Episode:
    t: datetime
    side: str
    price: float
    pre: float
    low: float
    replenished: bool = False
    t_repl: Optional[datetime] = None
    closed: bool = False


@dataclass
class _Side:
    name: str
    touch: Deque[Tuple[datetime, Optional[float], Optional[float], Optional[int]]] = \
        field(default_factory=lambda: deque(maxlen=20000))
    visible: RollingSeries = field(default_factory=lambda: RollingSeries(window_s=KEEP_S, min_keep=0))
    changes: Deque[datetime] = field(default_factory=lambda: deque(maxlen=20000))
    events: Deque[Tuple[datetime, str, float]] = field(default_factory=lambda: deque(maxlen=20000))
    pending: Deque[Dict[str, Any]] = field(default_factory=deque)
    levels: List[Tuple[float, float, Optional[int]]] = field(default_factory=list)
    last_add_t: Optional[datetime] = None
    episode: Optional[_Episode] = None
    episodes: int = 0
    replenished: int = 0
    repl_times: List[float] = field(default_factory=list)
    last_episode: Optional[_Episode] = None
    pulls: int = 0
    pulled_qty: float = 0.0
    stacks: int = 0
    stacked_qty: float = 0.0
    improves: int = 0
    retreats: int = 0
    traded_qty: float = 0.0
    drops: int = 0
    added: Optional[float] = None
    removed: Optional[float] = None

    def touch_at_or_before(self, t: datetime):
        for rec in reversed(self.touch):
            if rec[0] <= t:
                return rec
        return None

    def window_sum(self, kind: str, now: datetime, seconds: float) -> float:
        lo = now - timedelta(seconds=seconds)
        return float(sum(q for (t, k, q) in self.events if k == kind and lo < t <= now))

    def window_count(self, kind: str, now: datetime, seconds: float) -> int:
        lo = now - timedelta(seconds=seconds)
        return sum(1 for (t, k, _) in self.events if k == kind and lo < t <= now)


class QueueState:
    """Touch-queue engine for one symbol (rules in the module docstring)."""

    def __init__(self) -> None:
        self.t: Optional[datetime] = None
        self.t_first: Optional[datetime] = None
        self.n_updates = 0
        self.sides: Dict[str, _Side] = {"bid": _Side("bid"), "ask": _Side("ask")}
        self.budget: Deque[Dict[str, Any]] = deque()
        self.volume_arrivals = 0
        self.volume_budgeted = 0.0
        self._last_iv_key: Any = None
        self.touch_depth: RollingSeries = RollingSeries(window_s=KEEP_S, min_keep=0)
        self.vacuum_since: Optional[datetime] = None
        self._low_since: Optional[datetime] = None
        self._seen_levels = False                    # a displayed level has been observed at least once

    # ------------------------------------------------------------- volume budget
    def _new_volume(self, t: datetime, iv: Optional[float], key: Any) -> None:
        """A new tape interval (detected by a change of its identity) adds its volume to the budget."""
        if iv is None or key == self._last_iv_key:
            return
        self._last_iv_key = key
        if iv <= 0:
            return
        self.volume_arrivals += 1
        self.volume_budgeted += float(iv)
        remaining = float(iv)
        # oldest pending drops first (ties: bid before ask, then arrival order), bounded by the volume
        pend = sorted([(d["t"], rank, i, sd, d) for rank, sd in enumerate(("bid", "ask"))
                       for i, d in enumerate(self.sides[sd].pending)], key=lambda x: (x[0], x[1], x[2]))
        for _, _, _, sd, d in pend:
            if remaining <= 0:
                break
            take = min(remaining, d["remaining"])
            d["remaining"] -= take
            remaining -= take
            self.sides[sd].traded_qty += take
            self.sides[sd].events.append((t, "traded", take))
        for sd in ("bid", "ask"):
            side = self.sides[sd]
            side.pending = deque(d for d in side.pending if d["remaining"] > 1e-9)
        if remaining > 0:
            self.budget.append({"t": t, "remaining": remaining})

    def _expire(self, t: datetime) -> None:
        cutoff = t - timedelta(seconds=LAG_S)
        while self.budget and self.budget[0]["t"] < cutoff:
            self.budget.popleft()
        for sd in ("bid", "ask"):
            side = self.sides[sd]
            keep: Deque[Dict[str, Any]] = deque()
            for d in side.pending:
                if d["t"] < cutoff:
                    side.pulls += 1
                    side.pulled_qty += d["remaining"]
                    side.events.append((t, "pull", d["remaining"]))
                else:
                    keep.append(d)
            side.pending = keep

    def _drop(self, t: datetime, sd: str, price: float, qty: float) -> None:
        side = self.sides[sd]
        side.drops += 1
        remaining = float(qty)
        for b in self.budget:
            if remaining <= 0:
                break
            take = min(remaining, b["remaining"])
            b["remaining"] -= take
            remaining -= take
            if take > 0:
                side.traded_qty += take
                side.events.append((t, "traded", take))
        self.budget = deque(b for b in self.budget if b["remaining"] > 1e-9)
        if remaining > 1e-9:
            side.pending.append({"t": t, "price": price, "remaining": remaining})

    # ---------------------------------------------------------------- episodes
    def _episode_update(self, t: datetime, sd: str, p0: Optional[float], q0: Optional[float],
                        p1: Optional[float], q1: Optional[float], dropped: bool) -> None:
        side = self.sides[sd]
        ep = side.episode
        if ep is not None:
            if (t - ep.t).total_seconds() > REPLENISH_W_S:
                ep.closed = True
                side.episode = None
                ep = None
        if ep is None and dropped and q0 is not None and q0 > 0:
            q_now = q1 if (p1 is not None and p0 is not None and abs(p1 - p0) < 1e-9) else 0.0
            if q_now <= (1.0 - DEPLETION_SHARE) * q0:
                ep = _Episode(t=t, side=sd, price=p0, pre=q0, low=q_now)
                side.episode = ep
                side.last_episode = ep
                side.episodes += 1
                return
        if ep is None:
            return
        same_or_better = p1 is not None and (
            abs(p1 - ep.price) < 1e-9 or (sd == "bid" and p1 > ep.price) or (sd == "ask" and p1 < ep.price))
        q_now = (q1 or 0.0) if same_or_better else 0.0
        ep.low = min(ep.low, q_now)
        if q_now >= REPLENISH_SHARE * ep.pre:
            ep.replenished = True
            ep.t_repl = t
            ep.closed = True
            side.replenished += 1
            side.repl_times.append((t - ep.t).total_seconds())
            side.episode = None

    # ------------------------------------------------------------------- update
    def on_book(self, t: Any, book: Any, interval_volume: Optional[float] = None,
                interval_key: Any = None) -> Dict[str, Any]:
        """Apply one book observation (see module docstring). Returns the per-side change summary.

        The tape interval is identified by ``interval_key`` (e.g. the tape row number): the same
        interval is presented on every frame between two tape rows and must be budgeted once.
        With a ``MarketState`` the key is taken from its ``session_state["tape"]`` (feed + row
        number, written by ``TapeState.fill_state``), and a first-of-day cumulative row is not
        budgeted (it is a day total, not an interval).  Without either, consecutive intervals
        of identical volume collapse into one — the caller should pass a key.
        """
        iv_key: Any = interval_key
        if isinstance(t, MarketState):
            ms = t
            t = ms.t
            if book is None:
                book = ms                                      # the state's displayed levels are the book
            interval_volume = ms.interval_volume if interval_volume is None else interval_volume
            tp = ms.session_state.get("tape") if isinstance(ms.session_state, dict) else None
            if iv_key is None and isinstance(tp, dict) and tp.get("rows") is not None:
                iv_key = ("row", tp.get("feed"), tp.get("kind"), tp.get("rows"))
                if tp.get("last_first_row"):
                    interval_volume = None                     # cumulative day total, not an interval
            elif iv_key is None:
                iv_key = ("iv", ms.interval_volume, ms.interval_trades, ms.interval_vwap)
        elif iv_key is None:
            iv_key = ("iv", interval_volume)
        if self.t_first is None:
            self.t_first = t
        self.t = t if self.t is None else max(self.t, t)
        self.n_updates += 1
        self._expire(t)
        self._new_volume(t, interval_volume, iv_key)
        summary: Dict[str, Any] = {}
        depth_total = 0.0
        any_side = False
        for sd in ("bid", "ask"):
            side = self.sides[sd]
            new = _levels_of(book, sd)
            old = side.levels
            p0, q0, o0 = (old[0][0], old[0][1], old[0][2]) if old else (None, None, None)
            p1, q1, o1 = (new[0][0], new[0][1], new[0][2]) if new else (None, None, None)
            kind = "none"
            dropped = False
            if p0 is None and p1 is not None:
                kind = "appear"
                side.last_add_t = t
                side.events.append((t, "add", q1))
            elif p0 is not None and p1 is None:
                kind = "vanish"
                side.retreats += 1
                self._drop(t, sd, p0, q0)
                dropped = True
            elif p0 is not None and p1 is not None:
                if abs(p1 - p0) < 1e-9:
                    dq = q1 - q0
                    if dq > 0:
                        kind = "stack"
                        side.stacks += 1
                        side.stacked_qty += dq
                        side.last_add_t = t
                        side.events.append((t, "add", dq))
                    elif dq < 0:
                        kind = "drop"
                        self._drop(t, sd, p0, -dq)
                        dropped = True
                else:
                    better = (p1 > p0) if sd == "bid" else (p1 < p0)
                    if better:
                        kind = "improve"
                        side.improves += 1
                        side.last_add_t = t
                        side.events.append((t, "add", q1))
                    else:
                        kind = "retreat"
                        side.retreats += 1
                        self._drop(t, sd, p0, q0)
                        dropped = True
            if side.touch and (p0, q0) != (p1, q1):            # any change of the best after a prior observation
                side.changes.append(t)
            self._episode_update(t, sd, p0, q0, p1, q1, dropped)
            # per-level diff for added / removed
            if old or self.n_updates > 1:
                pm = {p: q for p, q, _ in old}
                cm = {p: q for p, q, _ in new}
                side.added = float(sum(max(0.0, cm[p] - pm.get(p, 0.0)) for p in cm))
                side.removed = float(sum(max(0.0, pm[p] - cm.get(p, 0.0)) for p in pm))
            side.levels = new
            side.touch.append((t, p1, q1, o1))
            vis = float(sum(q for _, q, _ in new)) if new else None
            if vis is not None:
                side.visible.push(t, vis)
                any_side = True
            depth_total += (q1 or 0.0)
            summary[sd] = {"kind": kind, "price": p1, "qty": q1, "orders": o1}
        if any_side:
            self._seen_levels = True
        if any_side or self._seen_levels:
            # a book that emptied after showing levels has a touch depth of 0 (both touches retreated);
            # a book that never showed a level (closed market) has no observable touch depth at all
            self.touch_depth.push(t, depth_total)
        self._update_vacuum(t)
        return summary

    # ------------------------------------------------------------------ vacuum
    def _median_visible(self, sd: str, now: datetime) -> Optional[float]:
        pts = [p for p in self.sides[sd].visible.buf if now - timedelta(seconds=VACUUM_MEDIAN_W_S) < p.t <= now]
        if len(pts) < VACUUM_MIN_POINTS or (pts[-1].t - pts[0].t).total_seconds() < VACUUM_MIN_SPAN_S:
            return None
        v = sorted(p.v for p in pts)
        n = len(v)
        return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])

    def _low_both(self, now: datetime) -> Optional[bool]:
        """Both sides' visible depth < 20 % of their 300-s median (None while a median is unavailable)."""
        for sd in ("bid", "ask"):
            side = self.sides[sd]
            med = self._median_visible(sd, now)
            if med is None or med <= 0:
                return None
            cur = float(sum(q for _, q, _ in side.levels)) if side.levels else 0.0
            if cur >= VACUUM_SHARE * med:
                return False
        return True

    def vacuum(self) -> Optional[bool]:
        """Collapsed on both sides for ≥ 60 s with no stack / improve on either side in the last 60 s."""
        now = self.t
        if now is None:
            return None
        low = self._low_both(now)
        if low is None:
            return None
        if not low or self._low_since is None or (now - self._low_since).total_seconds() < VACUUM_QUIET_S:
            return False
        for sd in ("bid", "ask"):
            la = self.sides[sd].last_add_t
            if la is not None and (now - la).total_seconds() < VACUUM_QUIET_S:
                return False
        return True

    def _update_vacuum(self, t: datetime) -> None:
        low = self._low_both(t)
        if low:
            if self._low_since is None:
                self._low_since = t
        else:
            self._low_since = None
        if self.vacuum():
            if self.vacuum_since is None:
                self.vacuum_since = t
        else:
            self.vacuum_since = None

    # --------------------------------------------------------------- queries
    def queue_position(self, side: str, price: float) -> Dict[str, Optional[float]]:
        """Qty (and orders when carried) ahead of a hypothetical order joining ``side`` at ``price``."""
        sd = "bid" if str(side).lower().startswith("b") else "ask"
        lv = self.sides[sd].levels
        if sd == "bid":
            ahead = [l for l in lv if l[0] >= price - 1e-9]
        else:
            ahead = [l for l in lv if l[0] <= price + 1e-9]
        qty = float(sum(q for _, q, _ in ahead))
        orders = None
        if ahead and all(o is not None for _, _, o in ahead):
            orders = int(sum(o for _, _, o in ahead))
        return {"qty_ahead": qty, "orders_ahead": orders, "levels_ahead": len(ahead)}

    def refresh_churn(self, sd: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """(churn per min, raw best-change rate per min, net drift ticks) over 60 s."""
        side = self.sides[sd]
        now = self.t
        if now is None or self.t_first is None:
            return None, None, None
        span = min(CHURN_W_S, (now - self.t_first).total_seconds())
        if span <= 0:
            return None, None, None
        lo = now - timedelta(seconds=CHURN_W_S)
        n = sum(1 for ct in side.changes if lo < ct <= now)
        rate = n / (span / 60.0)
        then = side.touch_at_or_before(lo)
        cur = side.touch[-1] if side.touch else None
        drift = None
        if then is not None and cur is not None and then[1] is not None and cur[1] is not None:
            drift = cur[1] - then[1]
        if drift is None:
            return None, rate, None
        churn = rate if abs(drift) < 1e-9 else 0.0
        return churn, rate, drift

    # ------------------------------------------------------------------ state
    def fill_state(self, ms: MarketState) -> Dict[str, Any]:
        now = self.t
        tick = ms.tick_size
        q: Dict[str, Any] = {"updates": self.n_updates, "t": now.isoformat() if now else None,
                             "volume_arrivals": self.volume_arrivals, "volume_budgeted": self.volume_budgeted,
                             "budget_remaining": float(sum(b["remaining"] for b in self.budget)),
                             "vacuum_since": self.vacuum_since.isoformat() if self.vacuum_since else None}
        if now is None:
            ms.liquidity_depletion = None
            ms.liquidity_replenishment = None
            ms.liquidity_retreat = None
            ms.liquidity_vacuum = None
            ms.session_state["queue"] = q
            return q
        # ---- depletion: share of touch depth consumed over 120 s
        d_now = self.touch_depth.last()
        then = self.touch_depth.value_at_or_before(now - timedelta(seconds=DEPLETION_W_S))
        depl = None
        if d_now is not None and then is not None and then > 0:
            depl = max(0.0, (then - d_now) / then)
        ms.liquidity_depletion = depl
        # ---- replenishment share of the most recent episode (≤ 120 s old)
        repl = None
        best_ep: Optional[_Episode] = None
        for sd in ("bid", "ask"):
            ep = self.sides[sd].last_episode
            if ep is not None and (now - ep.t).total_seconds() <= REPLENISH_W_S:
                if best_ep is None or ep.t > best_ep.t:
                    best_ep = ep
        if best_ep is not None and best_ep.pre > best_ep.low:
            side = self.sides[best_ep.side]
            cur = side.touch[-1] if side.touch else None
            q_now = 0.0
            if cur is not None and cur[1] is not None:
                p1 = cur[1]
                same_or_better = abs(p1 - best_ep.price) < 1e-9 or (best_ep.side == "bid" and p1 > best_ep.price) or \
                    (best_ep.side == "ask" and p1 < best_ep.price)
                q_now = (cur[2] or 0.0) if same_or_better else 0.0
            repl = max(0.0, min(1.0, (q_now - best_ep.low) / (best_ep.pre - best_ep.low)))
        ms.liquidity_replenishment = repl
        # ---- retreat: both sides pulled, nothing traded, both touch depths fell over 120 s
        retreat = None
        lo_t = now - timedelta(seconds=DEPLETION_W_S)
        if then is not None:
            ok = True
            for sd in ("bid", "ask"):
                side = self.sides[sd]
                pulled = side.window_sum("pull", now, DEPLETION_W_S)
                traded = side.window_sum("traded", now, DEPLETION_W_S)
                t_then = side.touch_at_or_before(lo_t)
                cur = side.touch[-1] if side.touch else None
                fell = (t_then is not None and cur is not None and (t_then[2] or 0.0) > (cur[2] or 0.0))
                if not (pulled > 0 and traded <= 0 and fell):
                    ok = False
                    break
            retreat = ok
        ms.liquidity_retreat = retreat
        ms.liquidity_vacuum = self.vacuum()
        # ---- depth added / removed when the book engine did not provide them
        for sd in ("bid", "ask"):
            side = self.sides[sd]
            if getattr(ms, f"depth_added_{sd}") is None and side.added is not None:
                setattr(ms, f"depth_added_{sd}", side.added)
            if getattr(ms, f"depth_removed_{sd}") is None and side.removed is not None:
                setattr(ms, f"depth_removed_{sd}", side.removed)
        # ---- counters
        for sd in ("bid", "ask"):
            side = self.sides[sd]
            cur = side.touch[-1] if side.touch else None
            churn, rate, drift = self.refresh_churn(sd)
            pos = self.queue_position(sd, cur[1]) if (cur is not None and cur[1] is not None) else None
            orders = cur[3] if cur is not None else None
            q[sd] = {
                "touch_price": cur[1] if cur else None, "touch_qty": cur[2] if cur else None,
                "touch_orders": orders,
                "avg_order_size_touch": ((cur[2] / orders) if (cur and orders) else None),
                "queue_ahead_at_touch": pos["qty_ahead"] if pos else None,
                "orders_ahead_at_touch": pos["orders_ahead"] if pos else None,
                "pulls": side.pulls, "pulled_qty": side.pulled_qty,
                "stacks": side.stacks, "stacked_qty": side.stacked_qty,
                "improves": side.improves, "retreats": side.retreats, "drops": side.drops,
                "traded_qty": side.traded_qty,
                "pending_qty": float(sum(d["remaining"] for d in side.pending)),
                "pulled_qty_120s": side.window_sum("pull", now, DEPLETION_W_S),
                "traded_qty_120s": side.window_sum("traded", now, DEPLETION_W_S),
                "added_qty_120s": side.window_sum("add", now, DEPLETION_W_S),
                "pulls_120s": side.window_count("pull", now, DEPLETION_W_S),
                "stacks_120s": side.window_count("add", now, DEPLETION_W_S),
                "refresh_churn_per_min": churn, "best_changes_per_min": rate,
                "net_drift_ticks": (drift / tick) if (drift is not None and tick) else None,
                "depletion_episodes": side.episodes, "replenished": side.replenished,
                "episode_open": side.episode is not None,
                "last_time_to_replenish_s": side.repl_times[-1] if side.repl_times else None,
                "mean_time_to_replenish_s": (sum(side.repl_times) / len(side.repl_times)) if side.repl_times else None,
                "visible_median_300s": self._median_visible(sd, now),
                "visible": float(sum(qq for _, qq, _ in side.levels)) if side.levels else None,
                "depth_added": side.added, "depth_removed": side.removed,
                "last_add_age_s": ((now - side.last_add_t).total_seconds() if side.last_add_t else None),
            }
        q["touch_depth"] = d_now
        q["touch_depth_120s_ago"] = then
        ms.session_state["queue"] = q
        return q
