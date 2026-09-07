"""TapeState — the trade tape of one symbol from prints **or** cumulative totals.

Two kinds of tape source exist for DSE symbols:

* a **print feed** (FIX / ITCH / Time & Sales export): ``on_trade`` receives one
  print at a time — price, qty, optional aggressor side and trade id;
* a **cumulative feed** (LankaBD ``MkSecondData`` rows, exchange-stamped) or the
  day totals carried by every depth snapshot: ``on_cum_totals`` /
  ``on_day_totals`` receive running day totals and the interval is the
  difference between consecutive rows (mirrors ``seeing.reconstruct.tape``):

      d_trades = Δcum_trades, d_volume = Δcum_volume, d_value = Δcum_value,
      interval_vwap = d_value / d_volume.

  The first row of a symbol's day has no predecessor: its "interval" is the
  cumulative value itself (``first_row`` flag; it carries no rate information,
  no direction, and is excluded from the rolling windows).  The same holds for
  a row none of whose carried totals has a comparable predecessor (the earlier
  rows carried other quantities).  A negative Δ is a source-side reset /
  correction: the row is **kept** with ``monotone_break=True`` and excluded
  from the windows (never repaired).  A row whose (carried) totals did not
  change advances the tape clock (the source affirms "no trade through this
  stamp") but produces no interval — the next interval starts at that stamp;
  a row carrying no total at all only advances the clock (``empty_rows``).  A
  quantity the source does not carry (trade count, volume, value) is None on
  the row and makes every window built on it None (``unsized_rows`` counts
  them); it is never a silent zero, and a quantity the source stopped carrying
  has no predecessor for the next row (never a two-row aggregate).  When the
  value is not carried the row's last price stands in for the interval VWAP in
  the direction rule, at low confidence.  A print re-delivered with an already
  seen ``trade_id`` is counted (``duplicate_prints``) and never applied twice.

Feeds are kept separately and the state is filled from the best available one
(prints > exchange-stamped cumulative > snapshot day totals, ties broken by
``SOURCE_PRIORITY``): nothing is double counted when several sources carry the
same totals.

Trade direction (``trade_flow_direction`` ∈ [−1, 1]):
  * the carried ``aggressor`` when a print feed delivers it (OBSERVED);
  * otherwise the quote rule on the print price / interval VWAP against the
    book at the **last update before** the print / before the interval started
    (the quotes seen by ``fill_state`` / the ``book`` argument are tracked
    against time; a book that emptied ends its quote, so a later print has no
    pre-trade quote; with no quote before the interval started the one seen
    inside it is used at low confidence):  +1 at or above the ask, −1 at or
    below the bid, otherwise the position inside the spread scaled to (−1, 1);
    a crossed book (bid > ask) decides nothing → None;
  * a **locked** book (bid == ask, a price-limit queue) makes the side exact by
    construction: prints execute against the resting queue, so the direction
    is −1 when the bid queue is the larger displayed side (sellers hit it) and
    +1 when the ask queue is; equal / unknown queues → None;
  * no quote at all → None (NOT_OBSERVABLE), never 0.

Rolling quantities (all keyed on the **tape clock** — exchange stamps when the
feed carries them — so a closed-market replay of yesterday's tape and a live
session behave identically; ``tape_age_s`` tells how far the frame is behind):
  trade_intensity      trades per minute over the trailing 120 s:
                       Σ trades in (max(now−120 s, t_first), now] / span(min),
                       None with fewer than two tape instants;
  trade_acceleration   intensity(now) − intensity(now − 120 s) (trades/min per 120 s);
  signed_flow_window   Σ direction × volume over the trailing 300 s (classified rows);
  volume_only_response Σ volume over the trailing 120 s;
  price_velocity       mid change over 60 s in ticks per minute (mid series from ``on_mid``);
  price_acceleration   velocity(now) − velocity(now − 60 s);
  price_only_response  mid change over 120 s in ticks;
  price_impact         Δmid (ticks, 300 s) / signed flow (300 s) — None while |signed flow|
                       is below 20 % of the 300-s volume (no attributable flow);
  failed_response      |signed_flow_window| ≥ 2 × its trailing mean (30 min, ≥ 5 tape rows)
                       and ≥ 50 % of the 300-s volume net one-sided, while
                       |price_only_response| ≤ 1 tick; None when the baseline or the
                       price response is not available.

Nothing here reads a clock; every time is an event time supplied by the caller.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

from .events import SOURCE_PRIORITY
from .state import MarketState
from .windows import RollingSeries

INTENSITY_W_S = 120.0
FLOW_W_S = 300.0
RESPONSE_W_S = 120.0
VELOCITY_W_S = 60.0
IMPACT_W_S = 300.0
IMPACT_FLOW_FLOOR_SHARE = 0.20
BASELINE_W_S = 1800.0
BASELINE_MIN_POINTS = 5
FAILED_FLOW_RATIO = 2.0
FAILED_ONE_SIDED_SHARE = 0.50
FAILED_PRICE_TICKS = 1.0
KEEP_S = 3600.0
_EPS = 1e-9

_KIND_RANK = {"prints": 0, "cum": 1, "snap": 2}


# --------------------------------------------------------------------------- helpers
def _touch_of(book: Any) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """(best_bid, best_ask, bid_qty1, ask_qty1) from an ``EvolvingBook`` (``best(side)``),
    a ``MarketState``-like object (``best_bid`` … attributes) or a dict."""
    if book is None:
        return None, None, None, None
    best = getattr(book, "best", None)
    if callable(best):
        b, a = best("bid"), best("ask")
        return ((b.price if b else None), (a.price if a else None), (b.qty if b else None), (a.qty if a else None))
    if isinstance(book, dict):
        return book.get("best_bid"), book.get("best_ask"), book.get("bid_qty1"), book.get("ask_qty1")
    return (getattr(book, "best_bid", None), getattr(book, "best_ask", None),
            getattr(book, "bid_qty1", None), getattr(book, "ask_qty1", None))


def classify_direction(price: Optional[float], bid: Optional[float], ask: Optional[float],
                       bid_qty: Optional[float] = None, ask_qty: Optional[float] = None,
                       aggressor: Optional[str] = None) -> Tuple[Optional[float], str, str]:
    """Direction of a print / interval → (direction ∈ [−1, 1] or None, rule, confidence).

    aggressor carried → ±1 (OBSERVED, exact).  Locked book (bid == ask) → the
    resting (larger) queue absorbs: bid queue larger → −1, ask queue larger →
    +1, undetermined → None.  Else quote rule: price ≥ ask → +1, price ≤ bid →
    −1, inside the spread → 2·(p − bid)/(ask − bid) − 1.  One-sided book: the
    available side alone decides when the price is at or through it, otherwise
    None.  No quote / no price → None.
    """
    if aggressor is not None:
        a = str(aggressor).upper()
        if a in ("B", "BUY", "1"):
            return 1.0, "aggressor carried", "exact"
        if a in ("S", "SELL", "2"):
            return -1.0, "aggressor carried", "exact"
    if price is None or (isinstance(price, float) and math.isnan(price)):
        return None, "no traded price", "none"
    if bid is None and ask is None:
        return None, "no pre-trade quote", "none"
    if bid is not None and ask is not None and bid > ask + _EPS:
        return None, "crossed book: side undetermined", "none"
    if bid is not None and ask is not None and abs(bid - ask) <= _EPS:
        bq, aq = bid_qty or 0.0, ask_qty or 0.0
        if bq > aq:
            return -1.0, "locked book: prints hit the resting bid queue", "exact"
        if aq > bq:
            return 1.0, "locked book: prints lift the resting ask queue", "exact"
        return None, "locked book: resting side undetermined", "none"
    if ask is not None and price >= ask - _EPS:
        return 1.0, "quote rule: at/above ask", "medium"
    if bid is not None and price <= bid + _EPS:
        return -1.0, "quote rule: at/below bid", "medium"
    if bid is not None and ask is not None and ask > bid:
        d = 2.0 * (price - bid) / (ask - bid) - 1.0
        if abs(d) < _EPS:
            d = 0.0                                   # exactly mid: no float residue
        return d, "quote rule: inside spread", "medium"
    return None, "quote rule: one-sided book, price away from the displayed side", "none"


class _QuoteTrack:
    """Time-stamped best quotes seen by the tape engine, for the pre-trade lookup."""

    def __init__(self, keep_s: float = KEEP_S) -> None:
        self.keep_s = keep_s
        self.buf: Deque[Tuple[datetime, Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]]] = \
            deque(maxlen=20000)

    def push(self, t: datetime, q: Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]) -> None:
        if q[0] is None and q[1] is None:
            # an empty book is recorded only as the END of a quote: from here on a print has no
            # pre-trade quote (the last displayed one is gone, it must not classify later prints)
            if not self.buf or (self.buf[-1][1][0] is None and self.buf[-1][1][1] is None):
                return
            q = (None, None, None, None)
        if self.buf and self.buf[-1][0] == t and self.buf[-1][1] == q:
            return
        if self.buf and t < self.buf[-1][0]:
            return                                    # out-of-order quote: keep the track monotone
        self.buf.append((t, q))
        cutoff = t - timedelta(seconds=self.keep_s)
        while len(self.buf) > 4 and self.buf[0][0] < cutoff:
            self.buf.popleft()

    def at_or_before(self, t: datetime):
        for pt, q in reversed(self.buf):
            if pt <= t:
                return q
        return None


@dataclass
class _Row:
    t: datetime                      # tape clock (exchange stamp when carried)
    t_recv: datetime
    n: Optional[float]               # trades in the interval / 1 for a print; None when the source did not carry it
    vol: Optional[float]             # traded quantity; None when not carried (never a silent zero)
    val: Optional[float]             # traded value (price units, not millions)
    vwap: Optional[float]
    price: Optional[float]           # last traded price of the row
    direction: Optional[float]
    dir_rule: str
    dir_conf: str
    first_row: bool = False
    monotone_break: bool = False
    dt_s: Optional[float] = None
    trade_id: Optional[str] = None

    @property
    def in_windows(self) -> bool:
        return not self.first_row and not self.monotone_break


@dataclass
class _Feed:
    kind: str                        # prints | cum | snap
    source: str
    rows: Deque[_Row] = field(default_factory=lambda: deque(maxlen=50000))
    now: Optional[datetime] = None                  # tape clock
    t_first: Optional[datetime] = None
    last_recv: Optional[datetime] = None
    cum_trades: Optional[float] = None
    cum_volume: Optional[float] = None
    cum_value: Optional[float] = None
    n_rows: int = 0
    repeat_rows: int = 0
    monotone_breaks: int = 0
    unsized_rows: int = 0                           # rows whose trades or quantity the source did not carry
    empty_rows: int = 0                             # rows carrying no total at all (a stamp only)
    duplicate_prints: int = 0                       # prints re-delivered with an already seen trade id
    seen_ids: Deque[str] = field(default_factory=lambda: deque(maxlen=20000))
    seen_set: set = field(default_factory=set)
    intensity: Optional[float] = None
    intensity_series: RollingSeries = field(default_factory=lambda: RollingSeries(window_s=KEEP_S, min_keep=0))
    abs_flow_series: RollingSeries = field(default_factory=lambda: RollingSeries(window_s=BASELINE_W_S, min_keep=0))
    last_print: Optional[Dict[str, Any]] = None

    @property
    def rank(self) -> Tuple[int, int, str]:
        return (_KIND_RANK.get(self.kind, 9), SOURCE_PRIORITY.get(self.source, 60), self.source)

    def advance(self, t: datetime) -> None:
        self.now = t if self.now is None else max(self.now, t)
        if self.t_first is None:
            self.t_first = t
        cutoff = self.now - timedelta(seconds=KEEP_S)
        while len(self.rows) > 2 and self.rows[0].t < cutoff:
            self.rows.popleft()

    def add(self, row: _Row) -> None:
        self.rows.append(row)
        self.n_rows += 1
        if row.monotone_break:
            self.monotone_breaks += 1
        if row.n is None or row.vol is None:
            self.unsized_rows += 1
        self.advance(row.t)
        self._update_intensity()

    def _update_intensity(self) -> None:
        """Σ trades in (max(now−W, t_first), now] over min(W, now − t_first) minutes.

        None while the feed has a single instant, or while a row inside the window
        does not carry its trade count (an unknown term makes the sum unknown)."""
        if self.now is None or self.t_first is None:
            self.intensity = None
            return
        elapsed = (self.now - self.t_first).total_seconds()
        span = min(INTENSITY_W_S, elapsed)
        if span <= 0:
            self.intensity = None
            return
        lo = self.now - timedelta(seconds=span)
        rows = [r for r in self.rows if r.in_windows and lo < r.t <= self.now]
        if any(r.n is None for r in rows):
            self.intensity = None
            return
        n = sum(r.n for r in rows)
        self.intensity = n / (span / 60.0)
        self.intensity_series.push(self.now, self.intensity)

    def acceleration(self) -> Optional[float]:
        if self.intensity is None or self.now is None:
            return None
        prev = self.intensity_series.value_at_or_before(self.now - timedelta(seconds=INTENSITY_W_S))
        return None if prev is None else self.intensity - prev

    def window_rows(self, seconds: float) -> List[_Row]:
        if self.now is None:
            return []
        lo = self.now - timedelta(seconds=seconds)
        return [r for r in self.rows if r.in_windows and lo < r.t <= self.now]

    def signed_flow(self, seconds: float) -> Optional[float]:
        """Σ direction × volume over the classified, sized rows of the window (None when there are none)."""
        rows = [r for r in self.window_rows(seconds) if r.direction is not None and r.vol is not None and r.vol > 0]
        return float(sum(r.direction * r.vol for r in rows)) if rows else None

    def volume(self, seconds: float) -> Optional[float]:
        """Σ volume over the window; None when empty or when a row's quantity was not carried."""
        rows = self.window_rows(seconds)
        if not rows or any(r.vol is None for r in rows):
            return None
        return float(sum(r.vol for r in rows))

    def classified_share(self, seconds: float) -> Optional[float]:
        rows = [r for r in self.window_rows(seconds) if r.vol is not None]
        tot = sum(r.vol for r in rows)
        if tot <= 0:
            return None
        return sum(r.vol for r in rows if r.direction is not None) / tot

    def abs_flow_baseline(self) -> Optional[float]:
        """Trailing mean of |signed flow| over 30 min, excluding the latest point; None below 5 points."""
        pts = self.abs_flow_series.points(BASELINE_W_S)
        base = [p.v for p in pts[:-1]]
        if len(base) < BASELINE_MIN_POINTS:
            return None
        return sum(base) / len(base)

    def record_flow(self, t: datetime) -> None:
        """Baseline sample of |signed flow|: only when a signed flow exists (never a silent zero)."""
        sf = self.signed_flow(FLOW_W_S)
        if sf is not None:
            self.abs_flow_series.push(t, abs(sf))

    def last_row(self) -> Optional[_Row]:
        return self.rows[-1] if self.rows else None

    def remember_id(self, t_row: datetime, trade_id: Optional[str]) -> bool:
        """Record a print id; True when it was already seen (a re-delivered print).

        Ids restart daily on most feeds, so the key is (tape-clock date, id): a day-2 print
        reusing a day-1 id is a new print, never swallowed."""
        if trade_id is None:
            return False
        key = f"{t_row.date().isoformat()}|{trade_id}"
        if key in self.seen_set:
            return True
        if len(self.seen_ids) == self.seen_ids.maxlen:
            self.seen_set.discard(self.seen_ids[0])
        self.seen_ids.append(key)
        self.seen_set.add(key)
        return False


# --------------------------------------------------------------------------- TapeState
class TapeState:
    """Trade-tape engine for one symbol (see module docstring for every rule)."""

    def __init__(self, tick: float = 0.10, value_scale: float = 1e6) -> None:
        self.tick = tick
        self.value_scale = float(value_scale)         # cumulative values arrive in millions (DSE convention)
        self._feeds: Dict[Tuple[str, str], _Feed] = {}
        self._quotes = _QuoteTrack()
        self._mid = RollingSeries(window_s=KEEP_S, min_keep=0)
        self._vel = RollingSeries(window_s=KEEP_S, min_keep=0)

    @property
    def last_print(self) -> Optional[Dict[str, Any]]:
        """Last print of the preferred feed (a real print, or one inferred from a one-trade Δ)."""
        feed = self.preferred_feed()
        return feed.last_print if feed is not None else None

    # ------------------------------------------------------------ feeds / quotes
    def _feed(self, kind: str, source: str) -> _Feed:
        k = (kind, source)
        f = self._feeds.get(k)
        if f is None:
            f = _Feed(kind=kind, source=source)
            self._feeds[k] = f
        return f

    def preferred_feed(self) -> Optional[_Feed]:
        feeds = [f for f in self._feeds.values() if f.now is not None]
        return min(feeds, key=lambda f: f.rank) if feeds else None

    def observe_quote(self, t: datetime, book: Any) -> None:
        """Record the best quotes of ``book`` (or MarketState) at ``t`` for later pre-trade lookups."""
        q = _touch_of(book)
        bt = getattr(book, "t", None)
        self._quotes.push(bt if isinstance(bt, datetime) else t, q)

    def _quote_for(self, t_start: Optional[datetime], t_end: datetime, book: Any):
        """Quote at the last update before the interval start (else before its end, else the given book).
        Returns (quote tuple or None, touch_moved inside the interval, quote taken from inside the
        interval / the given book rather than before its start)."""
        q_start = self._quotes.at_or_before(t_start) if t_start is not None else None
        q_end = self._quotes.at_or_before(t_end)
        moved = bool(q_start is not None and q_end is not None and q_start[:2] != q_end[:2])
        q = q_start or q_end
        if q is None and book is not None:
            qb = _touch_of(book)
            q = qb if (qb[0] is not None or qb[1] is not None) else None
        inside = bool(t_start is not None and q_start is None and q is not None)
        return q, moved, inside

    # ------------------------------------------------------------------ prints
    def on_trade(self, t: datetime, price: Optional[float], qty: Optional[float], aggressor: Optional[str] = None,
                 trade_id: Optional[str] = None, book: Any = None, t_exch: Optional[datetime] = None,
                 source: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """One print. Direction = carried aggressor, else quote rule vs the last quote before the print."""
        if book is not None:
            self.observe_quote(t, book)
        feed = self._feed("prints", source or "prints")
        t_row = t_exch or t
        if feed.remember_id(t_row, trade_id):
            # the same print delivered again (recovery / re-poll): counted, never applied twice
            feed.duplicate_prints += 1
            feed.last_recv = t
            return feed.last_print
        q, _, _ = self._quote_for(None, t_row, book)
        bid, ask, bq, aq = q if q is not None else (None, None, None, None)
        d, rule, conf = classify_direction(price, bid, ask, bq, aq, aggressor)
        vol = float(qty) if qty is not None else None          # size not carried → None, never 0
        val = (float(price) * vol) if (price is not None and vol is not None) else None
        t_prev = feed.now                                       # tape clock before this print
        row = _Row(t=t_row, t_recv=t, n=1.0, vol=vol, val=val, vwap=(float(price) if price is not None else None),
                   price=(float(price) if price is not None else None), direction=d, dir_rule=rule, dir_conf=conf,
                   dt_s=((t_row - t_prev).total_seconds() if t_prev is not None else None), trade_id=trade_id)
        feed.add(row)
        feed.last_recv = t
        feed.cum_trades = (feed.cum_trades or 0.0) + 1.0
        if vol is not None:
            feed.cum_volume = (feed.cum_volume or 0.0) + vol
        if val is not None:
            feed.cum_value = (feed.cum_value or 0.0) + val
        feed.record_flow(row.t)
        feed.last_print = {"t": t_row.isoformat(), "price": row.price, "qty": vol, "trade_id": trade_id,
                           "aggressor": aggressor, "direction": d, "direction_rule": rule, "inferred_from_delta": False}
        return feed.last_print

    # ------------------------------------------------------------ cumulative
    def on_cum_totals(self, t_exch: datetime, t_recv: datetime, cum_trades: Optional[float],
                      cum_volume: Optional[float], cum_value: Optional[float], price: Optional[float],
                      book: Any = None, source: Optional[str] = None, kind: str = "cum") -> Optional[Dict[str, Any]]:
        """One cumulative-totals row (``cum_value`` in millions → × value_scale).

        Interval = Δ against the previous row of the same feed; first row of the
        day = the cumulative values themselves (flagged); negative Δ kept and
        flagged ``monotone_break``; unchanged totals only advance the clock.
        """
        if book is not None:
            self.observe_quote(t_recv, book)
        feed = self._feed(kind, source or ("cum_totals" if kind == "cum" else "day_totals"))
        t_exch = t_exch or t_recv
        ct = None if cum_trades is None else float(cum_trades)
        cv = None if cum_volume is None else float(cum_volume)
        cval = None if cum_value is None else float(cum_value) * self.value_scale
        feed.last_recv = t_recv
        if ct is None and cv is None and cval is None:
            # a stamp without any total: nothing to difference, the clock alone advances
            feed.empty_rows += 1
            feed.advance(t_exch)
            feed._update_intensity()
            return None
        d_n = None if (ct is None or feed.cum_trades is None) else ct - feed.cum_trades
        d_v = None if (cv is None or feed.cum_volume is None) else cv - feed.cum_volume
        d_val = None if (cval is None or feed.cum_value is None) else cval - feed.cum_value
        # first row of the day — or a row none of whose carried totals has a comparable predecessor
        # (the previous rows carried other quantities): the cumulative values themselves, flagged
        first = feed.n_rows == 0 or (d_n is None and d_v is None and d_val is None)
        if first:
            d_n, d_v, d_val = ct, cv, cval
        # the interval starts at the tape clock — the last stamp of this feed, a repeat row included
        # (a repeat affirms "no trade through this stamp", so it bounds the interval)
        t_prev = feed.now
        # remember the totals as carried (a quantity the source stopped carrying has no predecessor
        # for the next row: it becomes None, never a two-row aggregate against an older total)
        feed.cum_trades, feed.cum_volume, feed.cum_value = ct, cv, cval
        known = [x for x in (d_n, d_v, d_val) if x is not None]
        if not first and known and all(x == 0.0 for x in known):
            feed.repeat_rows += 1
            feed.advance(t_exch)
            feed._update_intensity()
            return None
        mono = any(x is not None and x < 0 for x in (d_n, d_v, d_val))
        vwap = (d_val / d_v) if (d_val is not None and d_v is not None and d_v > 0) else None
        traded = d_v is not None and d_v > 0
        q, moved, inside = self._quote_for(t_prev if not first else None, t_exch, book)
        bid, ask, bq, aq = q if q is not None else (None, None, None, None)
        d, rule, conf = (None, "no traded volume in interval", "none")
        if first:
            # the cumulative row has no interval: nothing to set against a pre-interval quote
            d, rule, conf = None, "first row of the day: no interval to classify", "none"
        elif mono:
            d, rule, conf = None, "monotone break: interval not classified", "none"
        elif vwap is not None:
            d, rule, conf = classify_direction(vwap, bid, ask, bq, aq, None)
            if inside and d is not None:
                # no quote before the interval started: the one seen inside it (after some of its
                # trades) is weaker evidence
                conf, rule = "low", rule + " (quote from inside interval)"
            elif moved and conf == "medium":
                conf, rule = "low", rule + " (touch moved inside interval)"
        elif traded and price is not None:
            # value not carried: the row's last price stands in for the VWAP (weaker evidence)
            d, rule, conf = classify_direction(float(price), bid, ask, bq, aq, None)
            if d is not None:
                conf, rule = "low", rule + " (last price, value not carried)"
                if inside:
                    rule += " (quote from inside interval)"
        elif traded:
            d, rule, conf = None, "no traded price / value in interval", "none"
        elif d_v is None:
            d, rule, conf = None, "volume not carried", "none"
        row = _Row(t=t_exch, t_recv=t_recv, n=d_n, vol=d_v, val=d_val, vwap=vwap,
                   price=(float(price) if price is not None else None), direction=d, dir_rule=rule, dir_conf=conf,
                   first_row=first, monotone_break=mono,
                   dt_s=((t_exch - t_prev).total_seconds() if t_prev is not None else None))
        feed.add(row)
        feed.record_flow(row.t)
        px = vwap if vwap is not None else (float(price) if (price is not None and traded) else None)
        if not first and not mono and row.n == 1.0 and traded and px is not None:
            # a one-trade interval is one print: its size and price are known exactly (INFERRED from Δ)
            feed.last_print = {"t": t_exch.isoformat(), "price": px, "qty": row.vol, "trade_id": None,
                               "aggressor": None, "direction": d, "direction_rule": rule, "inferred_from_delta": True}
        return {"interval_trades": row.n, "interval_volume": row.vol, "interval_vwap": vwap, "direction": d,
                "first_row": first, "monotone_break": mono}

    def on_day_totals(self, t: datetime, day_trades: Optional[float], day_volume: Optional[float],
                      day_value_mn: Optional[float], source: Optional[str] = None,
                      book: Any = None) -> Optional[Dict[str, Any]]:
        """Day totals carried by a depth snapshot (receipt-stamped): a coarser cumulative feed,
        used only when no exchange-stamped cumulative feed and no print feed exists."""
        if day_trades is None and day_volume is None:
            return None
        return self.on_cum_totals(t, t, day_trades, day_volume, day_value_mn, None, book=book,
                                  source=source or "day_totals", kind="snap")

    # ------------------------------------------------------------------- price
    def on_mid(self, t: datetime, mid: Optional[float]) -> None:
        """Push the frame mid (book mid, else ltp — the caller's choice) and update the velocity series."""
        if mid is None or (isinstance(mid, float) and math.isnan(mid)):
            return
        if len(self._mid) and t < self._mid.buf[-1].t:
            return
        self._mid.push(t, float(mid))
        v = self.price_velocity()
        if v is not None:
            self._vel.push(t, v)

    def _ticks(self, dp: Optional[float]) -> Optional[float]:
        if dp is None or not self.tick:
            return None
        return dp / float(self.tick)

    def price_velocity(self) -> Optional[float]:
        """Mid change over the trailing 60 s in ticks per minute (window length exactly 60 s)."""
        return self._ticks(self._mid.change(VELOCITY_W_S))

    def price_acceleration(self) -> Optional[float]:
        if not len(self._vel):
            return None
        return self._vel.change(VELOCITY_W_S)

    def price_only_response(self) -> Optional[float]:
        return self._ticks(self._mid.change(RESPONSE_W_S))

    def price_impact(self, feed: Optional[_Feed]) -> Optional[float]:
        """Δmid (ticks over 300 s) per unit signed flow (300 s); None below the flow floor."""
        if feed is None:
            return None
        flow = feed.signed_flow(IMPACT_W_S)
        vol = feed.volume(IMPACT_W_S)
        dm = self._ticks(self._mid.change(IMPACT_W_S))
        if flow is None or vol is None or dm is None or vol <= 0:
            return None
        if abs(flow) < IMPACT_FLOW_FLOOR_SHARE * vol or abs(flow) <= 0:
            return None
        return dm / flow

    def failed_response(self, feed: Optional[_Feed], sfw: Optional[float], por: Optional[float]) -> Optional[bool]:
        if feed is None or sfw is None or por is None:
            return None
        mean = feed.abs_flow_baseline()
        vol = feed.volume(FLOW_W_S)
        if mean is None or mean <= _EPS or vol is None or vol <= 0:
            return None                      # degenerate baseline / unsized window: unknown, never "normal"
        large = abs(sfw) >= FAILED_FLOW_RATIO * mean and abs(sfw) >= FAILED_ONE_SIDED_SHARE * vol
        return bool(large and abs(por) <= FAILED_PRICE_TICKS)

    # ------------------------------------------------------------------- state
    def fill_state(self, ms: MarketState, book: Any = None) -> None:
        """Write every tape / price-response MarketState field (None where nothing is observable)."""
        if ms.best_bid is not None or ms.best_ask is not None:
            self._quotes.push(ms.t, (ms.best_bid, ms.best_ask, ms.bid_qty1, ms.ask_qty1))
        elif book is not None:
            self.observe_quote(ms.t, book)
        feed = self.preferred_feed()
        ms.price_velocity = self.price_velocity()
        ms.price_acceleration = self.price_acceleration()
        ms.price_only_response = self.price_only_response()
        if feed is None:
            for k in ("trade_count", "trade_volume", "trade_value", "interval_trades", "interval_volume",
                      "interval_vwap", "trade_flow_direction", "trade_intensity", "trade_acceleration",
                      "signed_flow_window", "last_print", "tape_source", "tape_age_s", "price_impact",
                      "volume_only_response", "failed_response"):
                setattr(ms, k, None)
            ms.session_state["tape"] = {"feed": None, "feeds": sorted(f"{k}:{s}" for k, s in self._feeds)}
            return
        row = feed.last_row()
        ms.trade_count = feed.cum_trades
        ms.trade_volume = feed.cum_volume
        ms.trade_value = feed.cum_value
        ms.interval_trades = row.n if row else None
        ms.interval_volume = row.vol if row else None
        ms.interval_vwap = row.vwap if row else None
        ms.trade_flow_direction = row.direction if row else None
        ms.trade_intensity = feed.intensity
        ms.trade_acceleration = feed.acceleration()
        ms.signed_flow_window = feed.signed_flow(FLOW_W_S)
        ms.volume_only_response = feed.volume(RESPONSE_W_S)
        ms.last_print = feed.last_print
        ms.tape_source = feed.source
        ms.tape_age_s = ((ms.t - feed.last_recv).total_seconds() if feed.last_recv is not None else None)
        ms.price_impact = self.price_impact(feed)
        ms.failed_response = self.failed_response(feed, ms.signed_flow_window, ms.price_only_response)
        if ms.ltp is None and row is not None and row.price is not None:
            ms.ltp = row.price
        ms.session_state["tape"] = {
            "feed": feed.source, "kind": feed.kind, "feeds": sorted(f"{k}:{s}" for k, s in self._feeds),
            "tape_clock": feed.now.isoformat() if feed.now else None,
            # receipt clock − tape clock: how far behind the exchange stamps the frame runs (days in a
            # closed-market replay of an old tape, seconds live); 0 for receipt-stamped prints
            "exchange_lag_s": ((feed.last_recv - feed.now).total_seconds()
                               if (feed.last_recv is not None and feed.now is not None) else None),
            "totals_are_day_totals": feed.kind != "prints",     # prints count from the first print seen
            "rows": feed.n_rows, "repeat_rows": feed.repeat_rows, "monotone_breaks": feed.monotone_breaks,
            "unsized_rows": feed.unsized_rows, "empty_rows": feed.empty_rows,
            "duplicate_prints": feed.duplicate_prints,
            "last_first_row": bool(row.first_row) if row else None,
            "last_monotone_break": bool(row.monotone_break) if row else None,
            "last_dt_s": row.dt_s if row else None,
            "last_interval_value": row.val if row else None,
            "direction_rule": row.dir_rule if row else None,
            "direction_confidence": row.dir_conf if row else None,
            "classified_flow_share_300s": feed.classified_share(FLOW_W_S),
            "volume_300s": feed.volume(FLOW_W_S),
            "abs_flow_baseline": feed.abs_flow_baseline(),
        }
