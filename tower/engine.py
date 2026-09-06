"""Engine: normalized events → per-symbol engines → one MarketState per update →
mechanics → cross context → timeline → store. Identical for replay and live.

Per-symbol machinery: one EvolvingBook per (symbol, book source) so each
sensor's dynamics (OFI, velocity) are internally consistent; the fusion layer
names the primary sensor for each update and exposes agreement/disagreement
with the others. Tape, queue, circuit, auction, resilience, mechanisms and the
timeline are per symbol; the cross engine is global.

Metrics: ingest_rate (events/s wall), processing_rate, backlog (events queued
but not yet processed when fed from a queue), event_lag_s (wall − t_recv, live
only), parse_failures, sequence_gaps, duplicate_rate, stale_sources,
reconstruction_failures (exceptions inside engines, counted and re-raised only
in strict mode).
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .auction import AuctionEngine
from .book import EvolvingBook
from .circuit import CircuitEngine
from .cross import CrossEngine
from .events import Event, EventType
from .fusion import Fuser
from .mechanics import all_mechanisms
from .mechanics.base import Mechanism, StateHistory
from .pressure import fill_pressure
from .queue import QueueState
from .resilience import ResilienceEngine
from .state import MarketState, MechanismState
from .tape import TapeState
from .timeline import Timeline
from .truth_map import STATE_TRUTH
from seeing.clock import trading_date

BOOK_TYPES = (EventType.BOOK_SNAPSHOT, EventType.BOOK_UPDATE)


@dataclass
class EngineConfig:
    default_tick: float = 0.10
    mechanics_min_interval_s: float = 2.0     # recompute mechanisms at most this often per symbol
    strict: bool = False                      # re-raise engine exceptions (tests) instead of counting them
    coalesce_s: float = 6.0
    live: bool = False                        # compute event lag against the wall clock
    emit_quote_only_states: bool = False      # symbols seen only through the all-symbol watch (no book, no
                                              # tape) still feed the cross engine but are not stored as states


@dataclass
class _Sym:
    books: Dict[str, EvolvingBook] = field(default_factory=dict)
    tape: Optional[TapeState] = None
    queue: QueueState = field(default_factory=QueueState)
    circuit: CircuitEngine = field(default_factory=CircuitEngine)
    auction: AuctionEngine = field(default_factory=AuctionEngine)
    resilience: ResilienceEngine = field(default_factory=ResilienceEngine)
    hist: StateHistory = field(default_factory=StateHistory)
    mechs: List[Mechanism] = field(default_factory=list)
    last_mech_t: Optional[datetime] = None
    last_mech: Dict[str, MechanismState] = field(default_factory=dict)
    seq: int = 0
    tick: Optional[float] = None
    block: Optional[Dict[str, Any]] = None
    last_quote: Dict[str, Any] = field(default_factory=dict)
    has_book_or_tape: bool = False


class Engine:
    def __init__(self, cfg: Optional[EngineConfig] = None) -> None:
        self.cfg = cfg or EngineConfig()
        self.fuser = Fuser(coalesce_s=self.cfg.coalesce_s)
        self.cross = CrossEngine()
        self.timeline = Timeline()
        self.syms: Dict[str, _Sym] = {}
        self.metrics: Dict[str, Any] = {
            "events_in": 0, "states_out": 0, "parse_failures": 0, "sequence_gaps": 0, "duplicates": 0,
            "stale_events": 0, "out_of_order": 0, "reconstruction_failures": 0, "gap_events": 0,
            "wall_start": time.time(), "last_event_lag_s": None, "max_event_lag_s": 0.0, "backlog": 0,
            "by_type": {}, "by_source": {}, "errors": [],
        }
        self._t0 = time.time()

    # ------------------------------------------------------------------ helpers
    def _sym(self, symbol: str) -> _Sym:
        s = self.syms.get(symbol)
        if s is None:
            s = _Sym(tick=self.cfg.default_tick)
            s.tape = TapeState(self.cfg.default_tick)
            s.mechs = all_mechanisms()
            self.syms[symbol] = s
        return s

    def _book(self, s: _Sym, source: str) -> EvolvingBook:
        b = s.books.get(source)
        if b is None:
            b = EvolvingBook(s.tick or self.cfg.default_tick)
            s.books[source] = b
        return b

    def metrics_snapshot(self) -> Dict[str, Any]:
        m = dict(self.metrics)
        elapsed = max(1e-9, time.time() - self._t0)
        m["ingest_rate_eps"] = m["events_in"] / elapsed
        m["processing_rate_sps"] = m["states_out"] / elapsed
        m["duplicate_rate"] = (m["duplicates"] / m["events_in"]) if m["events_in"] else 0.0
        m["stale_sources"] = sorted({src for s in self.syms.values() for src, st in
                                     (s.hist.buf[-1].sources.items() if len(s.hist.buf) else []) if st.stale})
        m["symbols"] = len(self.syms)
        m["errors"] = m["errors"][-20:]
        return m

    # ------------------------------------------------------------------ main
    def process(self, ev: Event) -> Optional[MarketState]:
        self.metrics["events_in"] += 1
        bt = self.metrics["by_type"]
        bt[ev.event_type.value] = bt.get(ev.event_type.value, 0) + 1
        bs = self.metrics["by_source"]
        bs[ev.source] = bs.get(ev.source, 0) + 1
        for flag, key in (("duplicate", "duplicates"), ("stale", "stale_events"), ("out_of_order", "out_of_order"),
                          ("gap", "sequence_gaps")):
            if ev.flags.get(flag):
                self.metrics[key] += 1
        if self.cfg.live:
            lag = (datetime.now(ev.t_recv.tzinfo) - ev.t_recv).total_seconds()
            self.metrics["last_event_lag_s"] = lag
            self.metrics["max_event_lag_s"] = max(self.metrics["max_event_lag_s"], lag)
        try:
            return self._process(ev)
        except Exception as e:  # noqa: BLE001
            self.metrics["reconstruction_failures"] += 1
            self.metrics["errors"].append(f"{ev.source} {ev.event_type.value} {ev.symbol} seq {ev.seq_local}: "
                                          f"{type(e).__name__}: {e}")
            if self.cfg.strict:
                raise
            return None

    def _process(self, ev: Event) -> Optional[MarketState]:
        self.fuser.on_event(ev)
        et = ev.event_type
        if et == EventType.GAP:
            self.metrics["gap_events"] += 1
            return None
        if et == EventType.STATUS:
            if ev.symbol:
                s = self._sym(ev.symbol)
                s.auction.on_event(ev)
            return None
        if et == EventType.MARKET_STATS:
            p = ev.payload
            self.cross.on_market_stats(ev.t_recv, p)
            if p.get("up") is not None or p.get("mkt_up") is not None:
                self.cross.on_market_breadth(ev.t_recv, p.get("up", p.get("mkt_up")), p.get("down", p.get("mkt_down")),
                                             p.get("n", p.get("mkt_n")))
            return None
        if ev.symbol is None:
            return None
        s = self._sym(ev.symbol)
        touched_book = False
        if et == EventType.REFERENCE:
            p = ev.payload
            if p.get("tick_size"):
                s.tick = p["tick_size"]
                for b in s.books.values():
                    b.tick = s.tick
                s.tape.tick = s.tick
            s.circuit.on_reference(ev.symbol, p.get("upper_limit"), p.get("lower_limit"), p.get("tick_size"),
                                   p.get("breaker_pct"), p.get("reference_date"), rule_source=ev.source)
            if p.get("sector"):
                self.cross.on_reference(ev.symbol, p["sector"])
            return None
        if et == EventType.BLOCK_PRINT:
            s.block = dict(ev.payload)
            return None
        if et in BOOK_TYPES or et in (EventType.TRADE, EventType.CUM_TOTALS):
            s.has_book_or_tape = True
        if et in BOOK_TYPES:
            b = self._book(s, ev.source)
            # a duplicate BOOK_SNAPSHOT (identical payload) is still applied as a frame: the book does not
            # change, and its staleness stays visible through fusion's per-source status
            if et == EventType.BOOK_SNAPSHOT:
                p = ev.payload
                b.apply_snapshot(ev.t_recv, p.get("bids") or [], p.get("asks") or [],
                                 bid_orders=p.get("bid_orders"), ask_orders=p.get("ask_orders"))
                zero_fields = set(p.get("zero_fields") or ())          # adapter's 'not populated' 0 sentinels
                for k in ("ltp", "open", "high", "low", "close_published", "yclose", "day_trades", "day_volume",
                          "day_value_mn"):
                    if p.get(k) is not None and k not in zero_fields:
                        s.last_quote[k] = p[k]
                if p.get("day_trades") is not None and p.get("day_volume") is not None:
                    s.tape.on_day_totals(ev.t_recv, p.get("day_trades"), p.get("day_volume"), p.get("day_value_mn"),
                                         source=ev.source, book=b)
            else:
                b.apply_update(ev.t_recv, ev.side, ev.price, ev.qty, order_count=ev.order_count,
                               action=(ev.payload or {}).get("action"), level=ev.level)
            touched_book = True
        elif et == EventType.TRADE:
            primary = self.fuser.primary_book_source(ev.symbol, ev.t_recv)
            b = s.books.get(primary) if primary else None
            s.tape.on_trade(ev.t_recv, ev.price, ev.qty, aggressor=ev.aggressor, trade_id=ev.trade_id, book=b,
                            t_exch=ev.t_exch, source=ev.source)
        elif et == EventType.CUM_TOTALS:
            # A tape pull made before the day's first trade returns the PREVIOUS session's rows
            # (observed 2026-09-06 03:5x UTC). Rows whose exchange stamp falls on another trading
            # date than the receipt are not today's tape: counted, never applied.
            if ev.t_exch is not None and trading_date(ev.t_exch) != trading_date(ev.t_recv):
                self.metrics["previous_session_tape_rows"] = self.metrics.get("previous_session_tape_rows", 0) + 1
                return None
            primary = self.fuser.primary_book_source(ev.symbol, ev.t_recv)
            b = s.books.get(primary) if primary else None
            p = ev.payload
            s.tape.on_cum_totals(ev.t_exch or ev.t_recv, ev.t_recv, p.get("cum_trades"), p.get("cum_volume"),
                                 p.get("cum_value_mn"), p.get("price"), book=b, source=ev.source)
        elif et == EventType.QUOTE:
            zero_fields = set(ev.payload.get("zero_fields") or ())
            for k, v in ev.payload.items():
                if v is not None and k not in zero_fields and k in (
                        "ltp", "open", "high", "low", "close_published", "yclose", "day_trades",
                        "day_volume", "day_value_mn", "market_category"):
                    s.last_quote[k] = v
        elif et == EventType.AUCTION:
            s.auction.on_event(ev)
        return self._build_state(s, ev, touched_book)

    # ------------------------------------------------------------------ state assembly
    def _build_state(self, s: _Sym, ev: Event, touched_book: bool) -> MarketState:
        s.seq += 1
        ms = MarketState(symbol=ev.symbol, t=ev.t_recv, seq=s.seq, session_phase=ev.session_phase)
        ms.tick_size = s.tick
        primary = self.fuser.primary_book_source(ev.symbol, ev.t_recv)
        book = s.books.get(primary) if primary else None
        if book is not None:
            book.fill_state(ms)
            ms.book_source = primary
        # quote-level fields (fusion sets provenance; fallback to the latest seen)
        if ms.ltp is None and s.last_quote.get("ltp") is not None:
            ms.ltp = s.last_quote["ltp"]
        ms.session_state["quote"] = dict(s.last_quote)
        if s.block:
            ms.session_state["block"] = s.block
        # velocity series takes the book mid only: mixing in ltp on bookless frames produced spurious jumps
        s.tape.on_mid(ev.t_recv, ms.mid)
        s.tape.fill_state(ms, book)
        if book is not None:
            # MarketState path: the tape interval is identified by (feed, kind, row) from session_state["tape"],
            # so identical consecutive intervals are budgeted once each and a first-of-day day total is not budgeted
            s.queue.on_book(ms, book)
        s.queue.fill_state(ms)
        self.fuser.fill_state(ms, ev.t_recv)
        s.circuit.on_state(ms, s.hist)
        s.circuit.fill_state(ms)
        s.auction.fill_state(ms)
        s.resilience.on_state(ms, s.hist)
        s.resilience.fill_state(ms)
        fill_pressure(ms, s.hist)
        # cross context (global engine sees every symbol's state in event order)
        self.cross.on_state(ms)
        ms.cross, ms.sector = self.cross.context_for(ev.symbol, ev.t_recv)
        # mechanics (throttled per symbol; between recomputes the last readings are carried)
        due = s.last_mech_t is None or (ev.t_recv - s.last_mech_t).total_seconds() >= self.cfg.mechanics_min_interval_s
        if due:
            for mech in s.mechs:
                try:
                    s.last_mech[mech.name] = mech.update(ms, s.hist)
                except Exception as e:  # noqa: BLE001
                    self.metrics["reconstruction_failures"] += 1
                    self.metrics["errors"].append(f"mechanism {mech.name}: {type(e).__name__}: {e}")
                    if self.cfg.strict:
                        raise
            s.last_mech_t = ev.t_recv
        ms.mechanisms = dict(s.last_mech)
        ms.active_mechanisms = sorted(n for n, m in ms.mechanisms.items() if m.state in ("active", "confirmed"))
        self.timeline.on_state(ms)
        ms.truth = STATE_TRUTH
        s.hist.push(ms)
        self.metrics["states_out"] += 1
        if not s.has_book_or_tape:
            ms.session_state["quote_only"] = True
            if not self.cfg.emit_quote_only_states:
                self.metrics["quote_only_states_suppressed"] = self.metrics.get("quote_only_states_suppressed", 0) + 1
                return None
        return ms
