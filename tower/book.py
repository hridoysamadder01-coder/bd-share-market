"""EvolvingBook — one continuously evolving displayed order book per symbol.

The book accepts either a **full snapshot** (``apply_snapshot``: the displayed
levels are replaced wholesale, but per-price ``first_seen`` / ``last_changed``
survive for prices that persist) or an **incremental update** (``apply_update``:
one price on one side is set / deleted, level- or price-keyed).  After each
application the book exposes, per update:

* level events — ``ADD`` / ``REDUCE`` / ``REMOVE`` / ``SWEEP`` per price with the
  ``at_touch`` and ``through`` flags of ``seeing.reconstruct.book.diff_levels``
  (``SWEEP`` = a ``REMOVE`` lying inside the range the touch retreated through);
* qty added / removed per side (Σ positive Δqty, Σ |negative Δqty|);
* order-flow imbalance ``e_n`` per Cont–Kukanov–Stoikov (2014) on the best
  bid / ask changes, and its rolling 60 s sum;
* book-change velocity (Σ|Δqty| over the trailing 60 s per second) and
  acceleration (velocity change over 60 s, per second);
* ``unchanged_run`` — consecutive updates that changed nothing.

``geometry()`` computes the deep-book shape (depth by level, distance from
touch, concentration, distance-weighted depth, cumulative-depth slope and
curvature, hollows, walls with persistence and migration, depth migration,
side asymmetry, imbalances) and ``fill_state(ms)`` writes every corresponding
``MarketState`` field.

Truth discipline: everything here is arithmetic on displayed levels (OBSERVED)
or a diff between two observations (INFERRED).  A quantity that cannot be
formed from what was observed is ``None`` — never a silent zero.  The first
observation has no predecessor, so it produces no events, no OFI and no
velocity.  All times come from the caller (event times); nothing reads a clock.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from .state import MarketState
from .windows import RollingSeries, curvature, slope

TOP_K = 5
WINDOW_S = 60.0                # velocity / acceleration / OFI / migration window
WALL_PERSIST_SHARE = 0.5       # persistence counts while qty ≥ 50 % of its current size
_ACTIONS_DELETE = {"DELETE", "REMOVE", "D", "2"}       # FIX MDUpdateAction 2 = Delete
_ACTIONS_SET = {"NEW", "ADD", "CHANGE", "UPDATE", "N", "C", "0", "1", None}
_SIDES_BID = {"bid", "b", "buy", "0"}                  # FIX MDEntryType 0 = Bid
_SIDES_ASK = {"ask", "a", "sell", "s", "offer", "o", "1"}   # FIX MDEntryType 1 = Offer


def _px(p: Any) -> Optional[float]:
    """Canonical float price key (rounds away float noise so 461.80000001 == 461.8).
    ``None`` for a price that is missing or not finite — such a row is not a level."""
    if p is None:
        return None
    try:
        f = float(p)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, 6)


def _side(side: Any) -> str:
    """Canonical side name. Unknown or missing sides raise: silently routing an
    update to the wrong side would corrupt the book without any visible error."""
    s = str(side).strip().lower() if side is not None else ""
    if s in _SIDES_BID:
        return "bid"
    if s in _SIDES_ASK:
        return "ask"
    raise ValueError(f"unknown book side {side!r} (expected bid/ask)")


@dataclass
class Level:
    """One displayed price level with its observation history.

    ``history`` holds (t, qty) at every observed change (first observation
    included) so wall persistence — "since when has this level continuously
    held ≥ 50 % of its current size" — is exact, not sampled.
    """
    price: float
    qty: float
    orders: Optional[int] = None
    first_seen: Optional[datetime] = None
    last_changed: Optional[datetime] = None
    history: Deque[Tuple[datetime, float]] = field(default_factory=lambda: deque(maxlen=512))

    def set_qty(self, t: datetime, qty: float, orders: Optional[int]) -> float:
        """Apply a new quantity; returns Δqty. Records history only on change.

        An order count is kept only while it describes the displayed quantity:
        when the quantity changes and the observation carries no count, the old
        count is no longer observed (it belonged to the previous size) → None.
        """
        dq = qty - self.qty
        if dq != 0.0:
            self.qty = qty
            self.last_changed = t
            self.history.append((t, qty))
            if orders is None:
                self.orders = None
        if orders is not None:
            if orders != self.orders:
                self.last_changed = t
            self.orders = orders
        return dq

    def persistence_s(self, now: datetime, share: float = WALL_PERSIST_SHARE) -> Optional[float]:
        """Seconds since the level has continuously held ≥ ``share`` × current qty.

        Walks the change history backwards; the first entry below the threshold
        ends the run. The run start is the time of the earliest surviving entry.
        """
        if not self.history:
            return None
        thr = share * self.qty
        start: Optional[datetime] = None
        for t, q in reversed(self.history):
            if q >= thr:
                start = t
            else:
                break
        if start is None:
            return 0.0
        return max(0.0, (now - start).total_seconds())


class _Track:
    """Time-stamped ring that keeps ``None`` values (RollingSeries drops them).
    Used for series where "no value at that time" must remain visible (wall
    price of an empty side)."""

    def __init__(self, window_s: float = 600.0, min_keep: int = 8) -> None:
        self.window_s = window_s
        self.min_keep = min_keep
        self.buf: Deque[Tuple[datetime, Any]] = deque(maxlen=5000)

    def push(self, t: datetime, v: Any) -> None:
        self.buf.append((t, v))
        cutoff = t - timedelta(seconds=self.window_s)
        while len(self.buf) > self.min_keep and self.buf[0][0] < cutoff:
            self.buf.popleft()

    def at_or_before(self, t: datetime) -> Tuple[bool, Any]:
        """(found, value) of the last point at or before ``t``."""
        for pt, v in reversed(self.buf):
            if pt <= t:
                return True, v
        return False, None


class EvolvingBook:
    """Displayed book for one symbol that evolves by snapshots and/or updates."""

    def __init__(self, tick: float = 0.10, window_s: float = WINDOW_S, top_k: int = TOP_K) -> None:
        if tick is None or tick <= 0:
            raise ValueError("tick must be a positive price increment")
        self.tick = float(tick)
        self.window_s = float(window_s)
        self.top_k = int(top_k)
        self.t: Optional[datetime] = None                 # time of the last applied observation
        self.t_first: Optional[datetime] = None           # time of the first observation
        self.n_updates = 0
        self._bids: Dict[float, Level] = {}
        self._asks: Dict[float, Level] = {}
        self._has_orders = False
        # ---- per-update outputs (None until a diff against a predecessor exists)
        self.last_events: List[Dict[str, Any]] = []
        self.added: Dict[str, Optional[float]] = {"bid": None, "ask": None}
        self.removed: Dict[str, Optional[float]] = {"bid": None, "ask": None}
        self.ofi: Optional[float] = None
        self.unchanged_run = 0
        self.velocity: Optional[float] = None
        self.acceleration: Optional[float] = None
        # ---- rolling trackers (causal; keyed by event time)
        keep_s = max(600.0, 2 * self.window_s)          # every tracker must outlive one look-back window
        self._abs_dq = RollingSeries(window_s=keep_s, min_keep=0)
        self._vel = RollingSeries(window_s=keep_s, min_keep=0)
        self._ofi = RollingSeries(window_s=keep_s, min_keep=0)
        # wall track holds (price, dist_ticks) so migration in distance is measured against the touch *then*
        self._wall_px: Dict[str, _Track] = {"bid": _Track(keep_s), "ask": _Track(keep_s)}
        self._mean_dist: Dict[str, _Track] = {"bid": _Track(keep_s), "ask": _Track(keep_s)}

    # ------------------------------------------------------------------ views
    def levels(self, side: str) -> List[Level]:
        """Displayed levels of one side, best first (bids descending, asks ascending)."""
        d = self._bids if side == "bid" else self._asks
        return sorted(d.values(), key=lambda l: -l.price if side == "bid" else l.price)

    def bids(self) -> List[Tuple[float, float]]:
        return [(l.price, l.qty) for l in self.levels("bid")]

    def asks(self) -> List[Tuple[float, float]]:
        return [(l.price, l.qty) for l in self.levels("ask")]

    def best(self, side: str) -> Optional[Level]:
        ls = self.levels(side)
        return ls[0] if ls else None

    def _touch(self, side: str) -> Tuple[Optional[float], Optional[float]]:
        b = self.best(side)
        return (b.price, b.qty) if b else (None, None)

    # --------------------------------------------------------------- ingestion
    @staticmethod
    def _normalise(levels: Optional[Iterable[Any]], orders: Optional[Sequence[Optional[int]]]
                   ) -> List[Tuple[float, float, Optional[int]]]:
        """(price, qty, orders) triples; duplicate prices are summed (documented choice);
        qty ≤ 0 rows are dropped (a displayed level with no size is not a level)."""
        out: Dict[float, List[Any]] = {}
        order: List[float] = []
        for i, lv in enumerate(levels or []):
            if lv is None:
                continue
            p = _px(lv[0])
            if p is None:
                continue                                   # no finite price → not a displayed level
            q = float(lv[1]) if lv[1] is not None else 0.0
            o: Optional[int] = None
            if len(lv) > 2 and lv[2] is not None:
                o = int(lv[2])
            if orders is not None and i < len(orders) and orders[i] is not None:
                o = int(orders[i])
            if not math.isfinite(q) or q <= 0:
                continue
            if p in out:
                out[p][0] += q
                if o is not None:
                    out[p][1] = (out[p][1] or 0) + o
            else:
                out[p] = [q, o]
                order.append(p)
        return [(p, out[p][0], out[p][1]) for p in order]

    def apply_snapshot(self, t: datetime, bids: Optional[Iterable[Any]], asks: Optional[Iterable[Any]],
                       orders: Optional[Any] = None,
                       bid_orders: Optional[Sequence[Optional[int]]] = None,
                       ask_orders: Optional[Sequence[Optional[int]]] = None) -> List[Dict[str, Any]]:
        """Replace the displayed book with a full image observed at ``t``.

        Levels whose price persists keep their ``first_seen`` and history;
        prices that vanish are dropped (re-appearance starts a new level).
        ``orders`` (the fourth positional argument, per CONTRACTS) may be given
        as ``(bid_orders, ask_orders)`` or ``{"bid": [...], "ask": [...]}``;
        the explicit keyword lists take precedence when given.
        Returns the level events of this update.
        """
        if orders is not None:
            if isinstance(orders, dict):
                ob, oa = orders.get("bid"), orders.get("ask")
            else:
                ob, oa = orders[0], orders[1]
            bid_orders = ob if bid_orders is None else bid_orders
            ask_orders = oa if ask_orders is None else ask_orders
        prev_state = self._pre_state()
        nb = self._normalise(bids, bid_orders)
        na = self._normalise(asks, ask_orders)
        self._replace_side(t, "bid", nb)
        self._replace_side(t, "ask", na)
        return self._post_update(t, prev_state)

    def _replace_side(self, t: datetime, side: str, new: List[Tuple[float, float, Optional[int]]]) -> None:
        cur = self._bids if side == "bid" else self._asks
        keep = set()
        for p, q, o in new:
            keep.add(p)
            lv = cur.get(p)
            if lv is None:
                lv = Level(price=p, qty=q, orders=o, first_seen=t, last_changed=t)
                lv.history.append((t, q))
                cur[p] = lv
            else:
                lv.set_qty(t, q, o)
            if o is not None:
                self._has_orders = True
        for p in [p for p in cur if p not in keep]:
            del cur[p]

    def apply_update(self, t: datetime, side: str, price: Optional[float], qty: Optional[float],
                     order_count: Optional[int] = None, action: Optional[str] = None,
                     level: Optional[int] = None) -> List[Dict[str, Any]]:
        """Apply one incremental change observed at ``t``.

        Rule: ``action`` in {DELETE, REMOVE} or ``qty == 0`` removes the price;
        NEW / CHANGE / None set the displayed qty (and order count when given).
        ``level`` (1-based from the touch) resolves the price when the feed is
        level-keyed and ``price`` is None; a level-keyed NEW inserts *at* that
        position, which for a price-level book equals setting the resolved
        price. Returns the level events of this update.
        """
        side = _side(side)
        # an unrecognised action (e.g. the FIX adapter's "UNKNOWN") is governed by the qty rule below
        act = None if action is None else str(action).strip().upper()
        prev_state = self._pre_state()
        cur = self._bids if side == "bid" else self._asks
        if price is None:
            if level is None:
                raise ValueError("apply_update needs a price or a level")
            ls = self.levels(side)
            if 1 <= level <= len(ls):
                price = ls[level - 1].price
            else:
                # level beyond the displayed range and no price: nothing observable to change
                return self._post_update(t, prev_state)
        p = _px(price)
        if p is None:
            raise ValueError(f"apply_update needs a finite price, got {price!r}")
        remove = act in _ACTIONS_DELETE or (qty is not None and float(qty) <= 0)
        if remove:
            cur.pop(p, None)
        else:
            if qty is None:
                # a CHANGE without a quantity is unobservable — only the order count can be applied
                lv = cur.get(p)
                if lv is not None and order_count is not None:
                    lv.set_qty(t, lv.qty, int(order_count))
                    self._has_orders = True
            else:
                q = float(qty)
                if not math.isfinite(q):
                    raise ValueError(f"apply_update needs a finite quantity, got {qty!r}")
                lv = cur.get(p)
                if lv is None:
                    lv = Level(price=p, qty=q, orders=order_count, first_seen=t, last_changed=t)
                    lv.history.append((t, q))
                    cur[p] = lv
                else:
                    lv.set_qty(t, q, order_count)
                if order_count is not None:
                    self._has_orders = True
        return self._post_update(t, prev_state)

    # ------------------------------------------------------------ update core
    def _orders_image(self) -> List[Tuple[float, str, Optional[int]]]:
        return [(l.price, s, l.orders) for s in ("bid", "ask") for l in self.levels(s)]

    def _pre_state(self) -> Dict[str, Any]:
        return {"bids": self.bids(), "asks": self.asks(), "orders": self._orders_image(),
                "tb": self._touch("bid"), "ta": self._touch("ask"), "first": self.t is None}

    def _post_update(self, t: datetime, prev: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.n_updates += 1
        if self.t_first is None:
            self.t_first = t
        self.t = t
        cur_b, cur_a = self.bids(), self.asks()
        tb0, ta0 = prev["tb"], prev["ta"]
        tb1, ta1 = self._touch("bid"), self._touch("ask")
        if prev["first"]:
            # no predecessor: nothing can be diffed
            self.last_events = []
            self.added = {"bid": None, "ask": None}
            self.removed = {"bid": None, "ask": None}
            self.ofi = None
            self.unchanged_run = 0
        else:
            ev = (self._diff(prev["bids"], cur_b, "bid", tb0[0], tb1[0]) +
                  self._diff(prev["asks"], cur_a, "ask", ta0[0], ta1[0]))
            self.last_events = ev
            for s in ("bid", "ask"):
                self.added[s] = float(sum(e["dq"] for e in ev if e["side"] == s and e["dq"] > 0))
                self.removed[s] = float(-sum(e["dq"] for e in ev if e["side"] == s and e["dq"] < 0))
            self.ofi = self._ofi_step(tb0, tb1, ta0, ta1)
            if self.ofi is not None:
                self._ofi.push(t, self.ofi)
            # an order-count change with no size change is still a change of the displayed book
            changed = bool(ev) or prev["orders"] != self._orders_image()
            self.unchanged_run = 0 if changed else self.unchanged_run + 1
            self._abs_dq.push(t, sum(abs(e["dq"]) for e in ev))
        self._update_velocity(t)
        # geometry trackers for migration (wall: price and its distance from the touch at that time)
        for s in ("bid", "ask"):
            w = self._wall(s, t, track=False)
            self._wall_px[s].push(t, (w["price"], w["dist_ticks"]) if w else None)
            self._mean_dist[s].push(t, self._mean_distance(s))
        return self.last_events

    @staticmethod
    def _diff(prev: Sequence[Tuple[float, float]], cur: Sequence[Tuple[float, float]], side: str,
              touch_prev: Optional[float], touch_cur: Optional[float]) -> List[Dict[str, Any]]:
        """Per-price Δqty between two observations of one side.

        Mirrors ``seeing.reconstruct.book.diff_levels``: kind ADD (new level or
        qty up) / REDUCE (qty down) / REMOVE (level gone); ``at_touch`` = the
        price was the previous best; ``through`` = the price lies strictly
        inside the range the best price moved through.  Adds ``SWEEP``: a
        REMOVE that is ``through`` while the touch *retreated* (bid best fell,
        ask best rose) — i.e. the level was consumed by the move (INFERRED).
        """
        p_map = {p: q for p, q in prev}
        c_map = {p: q for p, q in cur}
        retreated = (touch_prev is not None and touch_cur is not None and
                     ((side == "bid" and touch_cur < touch_prev) or (side == "ask" and touch_cur > touch_prev)))
        out: List[Dict[str, Any]] = []
        for p in sorted(set(p_map) | set(c_map), reverse=(side == "bid")):
            q0, q1 = p_map.get(p), c_map.get(p)
            if q0 is None and q1 is not None:
                kind, dq = "ADD", q1
            elif q1 is None and q0 is not None:
                kind, dq = "REMOVE", -q0
            elif q0 is not None and q1 is not None and q1 != q0:
                kind, dq = ("ADD" if q1 > q0 else "REDUCE"), q1 - q0
            else:
                continue
            through = False
            if touch_prev is not None and touch_cur is not None and touch_prev != touch_cur:
                lo, hi = sorted((touch_prev, touch_cur))
                through = lo <= p < hi if side == "ask" else lo < p <= hi
            if kind == "REMOVE" and through and retreated:
                kind = "SWEEP"
            out.append({"side": side, "price": p, "kind": kind, "dq": float(dq), "q_prev": q0, "q_cur": q1,
                        "at_touch": (p == touch_prev), "through": through})
        return out

    @staticmethod
    def _ofi_step(tb0: Tuple[Optional[float], Optional[float]], tb1: Tuple[Optional[float], Optional[float]],
                  ta0: Tuple[Optional[float], Optional[float]], ta1: Tuple[Optional[float], Optional[float]]
                  ) -> Optional[float]:
        """Cont–Kukanov–Stoikov order-flow imbalance for one best-quote change:

            e_n = 1{Pb_n ≥ Pb_{n-1}}·qb_n − 1{Pb_n ≤ Pb_{n-1}}·qb_{n-1}
                − 1{Pa_n ≤ Pa_{n-1}}·qa_n + 1{Pa_n ≥ Pa_{n-1}}·qa_{n-1}

        Both best quotes must exist before and after; a side missing at either
        instant makes the contribution unobservable → None.
        """
        pb0, qb0 = tb0
        pb1, qb1 = tb1
        pa0, qa0 = ta0
        pa1, qa1 = ta1
        if None in (pb0, pb1, pa0, pa1):
            return None
        e = 0.0
        if pb1 >= pb0:
            e += qb1
        if pb1 <= pb0:
            e -= qb0
        if pa1 <= pa0:
            e -= qa1
        if pa1 >= pa0:
            e += qa0
        return float(e)

    def _update_velocity(self, t: datetime) -> None:
        """velocity = Σ|Δqty| over updates in (t − W, t] divided by min(W, t − t_first)
        (units per second); None until a second observation exists.  acceleration =
        (velocity(t) − velocity(t')) / (t − t') where t' is the time of the last velocity
        observation at or before t − W (the actual elapsed time, never a nominal W — with
        sparse updates t' can be far older than W); None until such a point exists."""
        if self.t_first is None or t == self.t_first or self.n_updates < 2:
            self.velocity = None
            self.acceleration = None
            return
        elapsed = (t - self.t_first).total_seconds()
        span = min(self.window_s, elapsed)
        if span <= 0:
            self.velocity = None
            self.acceleration = None
            return
        # RollingSeries.values(seconds) anchors on its own last point; anchor on t instead
        cutoff = t - timedelta(seconds=self.window_s)
        total = sum(p.v for p in self._abs_dq.buf if p.t > cutoff)
        self.velocity = total / span
        self._vel.push(t, self.velocity)
        t_then = t - timedelta(seconds=self.window_s)
        prev_pt = next((p for p in reversed(self._vel.buf) if p.t <= t_then), None)
        if prev_pt is None:
            self.acceleration = None
        else:
            gap = (t - prev_pt.t).total_seconds()
            self.acceleration = (self.velocity - prev_pt.v) / gap if gap > 0 else None

    def ofi_window(self) -> Optional[float]:
        """Rolling Σ e_n over the trailing window (None when no e_n was observable)."""
        if self.t is None or not len(self._ofi):
            return None
        cutoff = self.t - timedelta(seconds=self.window_s)
        vals = [p.v for p in self._ofi.buf if p.t > cutoff]
        return float(sum(vals)) if vals else None

    # --------------------------------------------------------------- geometry
    def _dist_ticks(self, side: str, price: float, touch: float) -> float:
        d = (touch - price) if side == "bid" else (price - touch)
        return round(d / self.tick, 6)

    def _mean_distance(self, side: str) -> Optional[float]:
        ls = self.levels(side)
        if not ls:
            return None
        touch = ls[0].price
        tot = sum(l.qty for l in ls)
        if tot <= 0:
            return None
        return sum(l.qty * self._dist_ticks(side, l.price, touch) for l in ls) / tot

    def _wall(self, side: str, now: datetime, track: bool = True) -> Optional[Dict[str, Any]]:
        """Largest displayed level of a side (ties → nearest the touch).

        persistence_s: time the level has continuously held ≥ 50 % of its
        current size.  migrated_ticks: (price_now − price_W_ago) / tick, signed
        in price; migrated_dist_ticks: dist_now − dist_W_ago, the change of the
        wall's distance from the touch *as it was at each time* (so a wall that
        stays at one price while the touch walks away migrates in distance but
        not in price).  Both None when no observation ≥ W old exists or the side
        was empty then.
        """
        ls = self.levels(side)
        if not ls:
            return None
        tot = sum(l.qty for l in ls)
        touch = ls[0].price
        wall = max(ls, key=lambda l: (l.qty, -self._dist_ticks(side, l.price, touch)))
        out: Dict[str, Any] = {"price": wall.price, "qty": wall.qty, "orders": wall.orders,
                               "share": (wall.qty / tot) if tot > 0 else None,
                               "dist_ticks": self._dist_ticks(side, wall.price, touch),
                               "persistence_s": wall.persistence_s(now),
                               "first_seen": wall.first_seen,
                               "migrated_ticks": None, "migrated_dist_ticks": None}
        if track:
            t_then = now - timedelta(seconds=self.window_s)
            found, then = self._wall_px[side].at_or_before(t_then)
            if found and then is not None:
                px_then, d_then = then
                out["migrated_ticks"] = round((wall.price - px_then) / self.tick, 6)
                out["migrated_dist_ticks"] = round(out["dist_ticks"] - d_then, 6)
        return out

    def _side_geometry(self, side: str, now: datetime) -> Dict[str, Any]:
        ls = self.levels(side)
        g: Dict[str, Any] = {"n_levels": len(ls), "levels": [], "touch": None, "qty1": None,
                             "visible": None, "topk": None, "hhi": None, "weighted": None,
                             "slope": None, "curvature": None, "hollow": None, "wall": None,
                             "mean_dist": None, "migration": None}
        if not ls:
            return g
        touch = ls[0].price
        cum = 0.0
        xs: List[float] = []
        ys: List[float] = []
        for l in ls:
            d = self._dist_ticks(side, l.price, touch)
            cum += l.qty
            xs.append(d)
            ys.append(cum)
            g["levels"].append({"price": l.price, "qty": l.qty, "orders": l.orders, "dist_ticks": d,
                                "cum_qty": cum, "first_seen": l.first_seen, "last_changed": l.last_changed})
        tot = cum
        g["touch"] = touch
        g["qty1"] = ls[0].qty
        g["visible"] = tot
        g["topk"] = float(sum(l.qty for l in ls[: self.top_k]))
        g["hhi"] = (sum((l.qty / tot) ** 2 for l in ls) if tot > 0 else None)
        g["weighted"] = float(sum(l.qty / (1.0 + d) for l, d in zip(ls, xs)))
        g["slope"] = slope(xs, ys)
        g["curvature"] = curvature(xs, ys)
        span_ticks = int(round(xs[-1]))
        g["hollow"] = max(0, span_ticks + 1 - len(ls))
        g["wall"] = self._wall(side, now)
        g["mean_dist"] = (sum(l.qty * d for l, d in zip(ls, xs)) / tot) if tot > 0 else None
        found, md_then = self._mean_dist[side].at_or_before(now - timedelta(seconds=self.window_s))
        if found and md_then is not None and g["mean_dist"] is not None:
            g["migration"] = g["mean_dist"] - md_then
        return g

    @staticmethod
    def _imb(b: Optional[float], a: Optional[float]) -> Optional[float]:
        """(bid − ask)/(bid + ask); a missing side counts as no displayed size; None when
        nothing is displayed on either side (never 0 for an empty book)."""
        bb = 0.0 if b is None else b
        aa = 0.0 if a is None else a
        s = bb + aa
        return (bb - aa) / s if s > 0 else None

    def geometry(self) -> Dict[str, Any]:
        """Full deep-book geometry of the current displayed book (see module doc)."""
        now = self.t
        # before any observation there are no levels, so ``now`` is never dereferenced
        gb = self._side_geometry("bid", now)   # type: ignore[arg-type]
        ga = self._side_geometry("ask", now)   # type: ignore[arg-type]
        bb, ba = gb["touch"], ga["touch"]
        bq1, aq1 = gb["qty1"], ga["qty1"]
        spread = (ba - bb) if (bb is not None and ba is not None) else None
        mid = (ba + bb) / 2.0 if spread is not None else None
        micro = None
        if spread is not None and (bq1 + aq1) > 0:
            micro = (ba * bq1 + bb * aq1) / (bq1 + aq1)
        empty = not gb["n_levels"] and not ga["n_levels"]
        one_sided = bool(gb["n_levels"]) != bool(ga["n_levels"])
        out: Dict[str, Any] = {
            "t": now, "tick": self.tick, "n_updates": self.n_updates,
            "best_bid": bb, "best_ask": ba, "bid_qty1": bq1, "ask_qty1": aq1,
            "spread": spread, "spread_ticks": (round(spread / self.tick, 6) if spread is not None else None),
            "mid": mid, "microprice": micro,
            "crossed": bool(spread is not None and spread < 0), "locked": bool(spread is not None and spread == 0),
            "one_sided": one_sided, "empty_book": empty,
            "bids": self.bids(), "asks": self.asks(),
            "bid_orders": ([l.orders for l in self.levels("bid")] if self._has_orders else None),
            "ask_orders": ([l.orders for l in self.levels("ask")] if self._has_orders else None),
            "bid": gb, "ask": ga,
            "imb_l1": self._imb(bq1, aq1) if not empty else None,
            "imb_topk": self._imb(gb["topk"], ga["topk"]) if not empty else None,
            "imb_weighted": self._imb(gb["weighted"], ga["weighted"]) if not empty else None,
            "visible_bid_liq": gb["visible"], "visible_ask_liq": ga["visible"],
            "depth_ratio": None,
            "side_asymmetry": None,
            # dynamics
            "book_change_velocity": self.velocity, "book_change_acceleration": self.acceleration,
            "depth_added_bid": self.added["bid"], "depth_removed_bid": self.removed["bid"],
            "depth_added_ask": self.added["ask"], "depth_removed_ask": self.removed["ask"],
            "ofi": self.ofi, "ofi_window": self.ofi_window(), "unchanged_run": self.unchanged_run,
            "level_events": list(self.last_events),
        }
        if not empty:
            vb = gb["visible"] or 0.0
            va = ga["visible"] or 0.0
            out["depth_ratio"] = vb / (vb + va) if (vb + va) > 0 else None
            out["side_asymmetry"] = self._side_asymmetry(gb, ga)
        return out

    @staticmethod
    def _side_asymmetry(gb: Dict[str, Any], ga: Dict[str, Any]) -> Optional[float]:
        """bid geometry − ask geometry on a normalised scale (−1..1).

        Mean of the components observable on both sides, each in [−1, 1]:
          liquidity share      (Vb − Va)/(Vb + Va)
          weighted-depth share (Wb − Wa)/(Wb + Wa)
          concentration        HHIb − HHIa
          proximity            (Da − Db)/(Da + Db)  (mean distance from touch; closer = stronger)
        Positive = the bid side is the heavier / nearer / more concentrated one.
        """
        comps: List[float] = []
        vb, va = gb["visible"] or 0.0, ga["visible"] or 0.0
        if vb + va > 0:
            comps.append((vb - va) / (vb + va))
        wb, wa = gb["weighted"] or 0.0, ga["weighted"] or 0.0
        if wb + wa > 0:
            comps.append((wb - wa) / (wb + wa))
        if gb["hhi"] is not None and ga["hhi"] is not None:
            comps.append(gb["hhi"] - ga["hhi"])
        db, da = gb["mean_dist"], ga["mean_dist"]
        if db is not None and da is not None and (db + da) > 0:
            comps.append((da - db) / (da + db))
        return (sum(comps) / len(comps)) if comps else None

    # ----------------------------------------------------------- state write
    def fill_state(self, ms: MarketState) -> Dict[str, Any]:
        """Write every book-derived MarketState field from ``geometry()``.
        ``book_source`` is the caller's; ``book_age_s`` = ms.t − last book time."""
        g = self.geometry()
        ms.tick_size = self.tick
        for k in ("best_bid", "best_ask", "bid_qty1", "ask_qty1", "spread", "spread_ticks", "mid", "microprice",
                  "bids", "asks", "bid_orders", "ask_orders", "crossed", "locked", "one_sided", "empty_book",
                  "imb_l1", "imb_topk", "imb_weighted", "visible_bid_liq", "visible_ask_liq", "depth_ratio",
                  "side_asymmetry", "book_change_velocity", "book_change_acceleration",
                  "depth_added_bid", "depth_removed_bid", "depth_added_ask", "depth_removed_ask",
                  "ofi", "ofi_window"):
            setattr(ms, k, g[k])
        for side in ("bid", "ask"):
            sg = g[side]
            setattr(ms, f"depth_concentration_{side}", sg["hhi"])
            setattr(ms, f"depth_slope_{side}", sg["slope"])
            setattr(ms, f"depth_curvature_{side}", sg["curvature"])
            setattr(ms, f"hollow_{side}", sg["hollow"])
            w = sg["wall"]
            setattr(ms, f"wall_{side}", None if w is None else
                    {"price": w["price"], "qty": w["qty"], "share": w["share"], "persistence_s": w["persistence_s"],
                     "migrated_ticks": w["migrated_ticks"], "dist_ticks": w["dist_ticks"],
                     "migrated_dist_ticks": w["migrated_dist_ticks"], "orders": w["orders"]})
            setattr(ms, f"depth_migration_{side}", sg["migration"])
        ms.book_age_s = ((ms.t - self.t).total_seconds() if (self.t is not None and ms.t is not None) else None)
        return g
