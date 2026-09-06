"""Raw capture store → one totally ordered stream of :class:`tower.events.Event`.

This module is the only bridge between the ``seeing`` raw store (append-only
JSONL segments of META / DATA / HEARTBEAT / GAP / TRAILER records) and the
tower engines. It reuses the ``seeing`` adapters for parsing — nothing is
re-parsed here — and adds what the engines need on top of parsed frames:

* one ``Event`` per observation with ``seq_local`` (per source, in raw seq
  order), ``session_phase`` (``seeing.clock.session_phase(t_recv)``),
  ``t_exch`` when the source carries an exchange stamp, ``freshness_s`` =
  ``t_recv − t_exch`` when both exist, and ``raw_ref`` provenance;
* QA flags computed **causally** (only from records already seen, in the
  source's own order):

  ``duplicate``     same (source, symbol, body_sha256) as the previous record
                    of that (source, symbol) — the source re-sent an identical
                    payload (kept: it is still an observation that nothing
                    changed at that receipt time);
  ``unchanged``     multi-symbol payloads only: this symbol's frame content is
                    identical to its previous frame although the body differs;
  ``stale``         the receipt gap since the previous record of the same
                    (source, key) exceeds ``stale_factor`` (3) × the median
                    inter-receipt cadence observed **so far** for that
                    (source, key); needs at least ``min_cadence_samples``
                    earlier intervals, otherwise the cadence is unknown and
                    nothing is flagged;
  ``out_of_order``  ``t_exch`` earlier than the previous emitted event's
                    ``t_exch`` for the same (source, symbol);
  ``gap``           a feed-sequence hole (``src_seq`` present and not the
                    previous + 1) — a synthetic GAP event is emitted too;
  ``correction``    tape only: the same exchange stamp re-delivered with
                    different cumulative values (source-side correction; kept,
                    never repaired).

* tape de-duplication: cumulative rows repeated across pulls are emitted once
  (first receipt wins) and the emitted event's ``payload["pulls_seen"]`` counts
  how many pulls carried it (in a batch run: over the whole store; in a
  streaming run: so far). A pull that adds no new rows emits a STATUS event
  (``status="no_new_rows"``) so the source's liveness stays observable;
* GAP records → GAP events; heartbeat silence longer than
  ``heartbeat_silence_s`` (30 s) → a synthetic GAP event stamped at the
  heartbeat that made the silence observable; the first DATA event of a
  (source, key) after a GAP or after a capture restart (new epoch) carries
  ``is_recovery=True``.

Never a silent zero: a field the adapter did not deliver stays ``None`` and is
absent from ``observed_fields``. Derived numbers (breadth from the watch poll)
are labelled ``payload["inferred_fields"]`` with their rule.

Ordering: ``Event.sort_key()`` = (t_recv, source priority, seq_local); the
final sort adds (source, event_type, symbol) as a deterministic tie-break so
two replays of the same store yield byte-identical streams.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from seeing.capture.raw_store import decode_body, iter_segment, sha256_bytes
from seeing.clock import epoch_ms_to_utc, session_phase
from seeing.replay import _adapters, _segment_paths

from .events import Event, EventType, utc

# ---------------------------------------------------------------------------- source tables
BOOK_SOURCES = ("lankabd_depth", "dsebd_depth")
QUOTE_SOURCES = ("lankabd_watch", "dsebd_latest", "lankabd_grid")
PER_SYMBOL_KEY_SOURCES = ("lankabd_depth", "dsebd_depth", "lankabd_tape")   # record key == symbol

# canonical fields (seeing.truth.CANONICAL_FIELDS vocabulary) each frame field maps to
_FRAME_TO_CANONICAL = {
    "bid_levels": "bid_levels", "ask_levels": "ask_levels", "ltp": "ltp", "open": "open", "high": "high",
    "low": "low", "close_published": "close_published", "yclose": "yclose", "day_trades": "day_trades",
    "day_volume": "day_volume", "day_value_mn": "day_value", "market_category": "market_category",
    "market_trades": "market_trades", "market_volume": "market_volume", "market_value_mn": "market_value",
    "upper_limit": "upper_limit", "lower_limit": "lower_limit", "tick_size": "tick_size",
    "breaker_pct": "breaker_pct", "cum_trades": "day_trades", "cum_volume": "day_volume",
    "cum_value_mn": "day_value", "price": "ltp", "block_trades": "block_prints",
}


def _observed(frame: Dict[str, Any], always: Sequence[str] = ()) -> Tuple[str, ...]:
    """Canonical fields this frame actually carries (value not None); lists count
    as observed even when empty — an empty book is an observation."""
    out: List[str] = list(always)
    for k, canon in _FRAME_TO_CANONICAL.items():
        if k in frame and frame[k] is not None and canon not in out:
            out.append(canon)
    return tuple(out)


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _levels(levels: Any) -> List[Tuple[float, float]]:
    return [(float(p), float(q)) for p, q in (levels or [])]


_POLL_WIDE_KEYS = ("feed_timestamp_str", "feed_timestamp_utc")


def _frame_hash(frame: Dict[str, Any]) -> str:
    """Content hash of one symbol's frame, excluding poll-wide stamps that change
    every poll regardless of the symbol's own row."""
    body = {k: v for k, v in frame.items() if k not in _POLL_WIDE_KEYS}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------- QA stats
@dataclass
class SourceCounts:
    records: int = 0            # raw DATA records seen for this source
    events: int = 0             # events emitted
    duplicates: int = 0         # events flagged duplicate (+ tape pulls identical to the previous pull)
    unchanged: int = 0          # per-symbol unchanged frames inside multi-symbol payloads
    stale: int = 0
    gaps: int = 0               # GAP records + sequence holes + heartbeat silences
    out_of_order: int = 0
    parse_failures: int = 0     # undecodable / hash-mismatched / adapter produced no frame
    corrections: int = 0        # tape stamp re-delivered with different values
    tape_rows_deduped: int = 0  # cumulative rows dropped as already emitted


@dataclass
class QAStats:
    per_source: Dict[str, SourceCounts] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)
    unmapped_sources: Dict[str, int] = field(default_factory=dict)
    t_first: Optional[datetime] = None
    t_last: Optional[datetime] = None
    max_problems: int = 500

    def src(self, source: str) -> SourceCounts:
        return self.per_source.setdefault(source, SourceCounts())

    def problem(self, msg: str) -> None:
        if len(self.problems) < self.max_problems:
            self.problems.append(msg)

    def totals(self) -> Dict[str, int]:
        keys = ("records", "events", "duplicates", "unchanged", "stale", "gaps", "out_of_order",
                "parse_failures", "corrections", "tape_rows_deduped")
        return {k: sum(getattr(c, k) for c in self.per_source.values()) for k in keys}

    def to_dict(self) -> Dict[str, Any]:
        return {"per_source": {s: vars(c) for s, c in self.per_source.items()}, "totals": self.totals(),
                "n_problems": len(self.problems), "problems": list(self.problems),
                "unmapped_sources": dict(self.unmapped_sources),
                "t_first": self.t_first.isoformat() if self.t_first else None,
                "t_last": self.t_last.isoformat() if self.t_last else None}


# ---------------------------------------------------------------------------- frames → events
def _breadth(frames: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Breadth from a watch poll: per item, ltp vs yclose when both are present
    and yclose > 0 (a 0 yclose is 'not populated', not a price). Items without
    a usable pair are counted as ``unpriced`` — never folded into ``flat``."""
    up = down = flat = unpriced = 0
    for fr in frames:
        ltp, yc = _f(fr.get("ltp")), _f(fr.get("yclose"))
        if ltp is None or yc is None or yc <= 0 or ltp <= 0:
            unpriced += 1
        elif ltp > yc:
            up += 1
        elif ltp < yc:
            down += 1
        else:
            flat += 1
    return {"up": up, "down": down, "flat": flat, "unpriced": unpriced, "n_items": len(frames)}


def events_from_frames(source: str, frames: Sequence[Dict[str, Any]], *, t_recv: datetime, seq: int,
                       body_sha256: Optional[str] = None, key: Optional[str] = None,
                       http: Optional[Dict[str, Any]] = None) -> List[Event]:
    """Pure mapping of one record's parsed frames to Events (no QA state).

    ``seq_local`` is left at 0 here and assigned by :class:`Normalizer`. Rules
    per source are documented inline; every value comes from the frame, and a
    missing value stays ``None``."""
    t_recv = utc(t_recv)
    phase = session_phase(t_recv)
    raw_ref = (source, int(seq), body_sha256 or "")
    out: List[Event] = []
    params = (http or {}).get("params") or {}

    def mk(et: EventType, **kw: Any) -> Event:
        ev = Event(source=source, event_type=et, t_recv=t_recv, seq_local=0, session_phase=phase, raw_ref=raw_ref, **kw)
        if ev.t_exch is not None:
            ev.freshness_s = (ev.t_recv - ev.t_exch).total_seconds()
        return ev

    if source in BOOK_SOURCES:
        # one full book image per record: bids best-first, asks best-first, plus the day fields the page shows
        for fr in frames:
            payload = {
                "bids": _levels(fr.get("bid_levels")), "asks": _levels(fr.get("ask_levels")),
                "n_bid_levels": fr.get("n_bid_levels"), "n_ask_levels": fr.get("n_ask_levels"),
                "src_order_preserved": fr.get("src_order_preserved",
                                              fr.get("bid_levels_src_order_preserved")),
                "ltp": _f(fr.get("ltp")), "open": _f(fr.get("open")), "high": _f(fr.get("high")),
                "low": _f(fr.get("low")), "close_published": _f(fr.get("close_published")),
                "yclose": _f(fr.get("yclose")), "day_trades": _f(fr.get("day_trades")),
                "day_volume": _f(fr.get("day_volume")), "day_value_mn": _f(fr.get("day_value_mn")),
                "zero_fields": list(fr.get("zero_fields") or []),
                "orders_per_level": None,      # NOT_OBSERVABLE on both depth pages
            }
            if fr.get("buy_pct") is not None or fr.get("total_buy_volume") is not None:
                payload.update({"buy_pct": _f(fr.get("buy_pct")), "sell_pct": _f(fr.get("sell_pct")),
                                "total_buy_volume": _f(fr.get("total_buy_volume")),
                                "total_sell_volume": _f(fr.get("total_sell_volume"))})
            sym = (fr.get("symbol") or key or "").upper() or None
            out.append(mk(EventType.BOOK_SNAPSHOT, symbol=sym, is_snapshot=True, price=payload["ltp"],
                          payload=payload, observed_fields=_observed(fr, ("t_recv", "bid_levels", "ask_levels"))))
        return out

    if source == "lankabd_tape":
        # exchange-stamped cumulative day totals, one row per change; t_exch from the epoch-ms stamp
        cid = params.get("cid") if isinstance(params, dict) else None
        for fr in frames:
            t_ms = fr.get("t_source_ms")
            t_exch = epoch_ms_to_utc(float(t_ms)) if t_ms is not None else None
            payload = {"cum_trades": _f(fr.get("cum_trades")), "cum_volume": _f(fr.get("cum_volume")),
                       "cum_value_mn": _f(fr.get("cum_value_mn")), "price": _f(fr.get("price")),
                       "price2": _f(fr.get("price2")), "t_source_ms": t_ms, "row_index": fr.get("row_index"),
                       "pulls_seen": 1}
            sym = (fr.get("symbol") or key or "").upper() or None
            out.append(mk(EventType.CUM_TOTALS, symbol=sym, t_exch=t_exch, price=payload["price"],
                          instrument_id=str(cid) if cid is not None else None, payload=payload,
                          observed_fields=_observed(fr, ("t_recv", "t_source"))))
        return out

    if source in QUOTE_SOURCES:
        for fr in frames:
            sym = (fr.get("symbol") or key or "").upper() or None
            t_exch = utc(fr.get("t_source_utc")) if fr.get("t_source_utc") else None
            payload = {k: v for k, v in fr.items() if k != "symbol"}
            inst = fr.get("instrument_number")
            always = ("t_recv", "t_source") if t_exch is not None else ("t_recv",)
            out.append(mk(EventType.QUOTE, symbol=sym, t_exch=t_exch, price=_f(fr.get("ltp")),
                          instrument_id=str(inst) if inst is not None else None, payload=payload,
                          observed_fields=_observed(fr, always)))
        if source == "lankabd_watch" and frames:
            # market-wide breadth derived from this poll: INFERRED, rule stated in the payload
            b = _breadth(frames)
            feed_t = frames[0].get("feed_timestamp_utc")
            b.update({"rule": "per item: ltp > yclose → up, < → down, == → flat; missing/0 → unpriced",
                      "inferred_fields": ["up", "down", "flat", "unpriced"],
                      "feed_timestamp_utc": feed_t, "kind": "breadth_from_watch"})
            out.append(mk(EventType.MARKET_STATS, symbol=None, t_exch=utc(feed_t) if feed_t else None,
                          payload=b, observed_fields=("t_recv",)))
        return out

    if source == "lankabd_market":
        for fr in frames:
            t_exch = utc(fr.get("t_source_utc")) if fr.get("t_source_utc") else None
            payload = {"market_trades": _f(fr.get("market_trades")), "market_volume": _f(fr.get("market_volume")),
                       "market_value_mn": _f(fr.get("market_value_mn")), "symbols_traded": _f(fr.get("symbols_traded")),
                       "up": _f(fr.get("up")), "down": _f(fr.get("down")), "flat": _f(fr.get("flat")),
                       "t_source_str": fr.get("t_source_str"), "kind": "market_totals"}
            always = ("t_recv", "t_source") if t_exch is not None else ("t_recv",)
            out.append(mk(EventType.MARKET_STATS, symbol=None, t_exch=t_exch, payload=payload,
                          observed_fields=_observed(fr, always)))
        return out

    if source == "lankabd_block":
        for fr in frames:
            sym = (fr.get("symbol") or "").upper() or None
            payload = {k: (_f(v) if k not in ("block_date",) else v) for k, v in fr.items() if k != "symbol"}
            out.append(mk(EventType.BLOCK_PRINT, symbol=sym, price=payload.get("block_max_price"),
                          qty=payload.get("block_quantity"), payload=payload,
                          observed_fields=_observed(fr, ("t_recv", "block_prints"))))
        return out

    if source == "lankabd_circuit":
        for fr in frames:
            sym = (fr.get("symbol") or "").upper() or None
            payload = {"upper_limit": _f(fr.get("upper_limit")), "lower_limit": _f(fr.get("lower_limit")),
                       "tick_size": _f(fr.get("tick_size")), "breaker_pct": _f(fr.get("breaker_pct")),
                       "open_adj_price": _f(fr.get("open_adj_price")), "sector": fr.get("sector"),
                       "reference_date": fr.get("reference_date")}
            out.append(mk(EventType.REFERENCE, symbol=sym, payload=payload,
                          observed_fields=_observed(fr, ("t_recv",))))
        return out

    if source == "dsebd_hts":
        for fr in frames:
            out.append(mk(EventType.STATUS, symbol=None, status="sessions",
                          payload={"holidays": fr.get("holidays"), "sessions": fr.get("sessions"),
                                   "n_holidays": len(fr.get("holidays") or []),
                                   "n_sessions": len(fr.get("sessions") or [])},
                          observed_fields=("t_recv",)))
        return out

    # unknown source: keep the frames verbatim as STATUS so nothing is silently lost
    for fr in frames:
        out.append(mk(EventType.STATUS, symbol=(fr.get("symbol") or key or None), status="unmapped_frame",
                      payload=dict(fr), observed_fields=("t_recv",)))
    return out


# ---------------------------------------------------------------------------- the normalizer
class Normalizer:
    """Stateful, causal record → event converter. Feed raw-store records in the
    order they were written (per source: seq order); call :meth:`finish` for the
    sorted stream. All QA state is keyed per (source, key) / (source, symbol)."""

    def __init__(self, symbols: Optional[Iterable[str]] = None, t_from: Any = None, t_to: Any = None,
                 sources: Optional[Iterable[str]] = None, *, stale_factor: float = 3.0,
                 min_cadence_samples: int = 3, heartbeat_silence_s: float = 30.0,
                 heartbeat_jitter_s: float = 1.0) -> None:
        self.symbols = {s.upper() for s in symbols} if symbols else None
        self.t_from = utc(t_from) if t_from is not None else None
        self.t_to = utc(t_to) if t_to is not None else None
        self.sources = set(sources) if sources else None
        self.stale_factor = float(stale_factor)
        self.min_cadence_samples = int(min_cadence_samples)
        self.heartbeat_silence_s = float(heartbeat_silence_s)
        self.heartbeat_jitter_s = float(heartbeat_jitter_s)
        self.stats = QAStats()
        self.events: List[Event] = []
        self._adapters = _adapters()
        self._seq_local: Dict[str, int] = {}
        self._last_body: Dict[Tuple[str, Optional[str]], str] = {}          # (source, symbol) → body sha
        self._last_frame_hash: Dict[Tuple[str, Optional[str]], str] = {}    # multi-symbol payloads
        self._last_recv: Dict[Tuple[str, Optional[str]], datetime] = {}     # (source, key) → t_recv
        self._intervals: Dict[Tuple[str, Optional[str]], List[float]] = {}
        self._last_exch: Dict[Tuple[str, Optional[str]], datetime] = {}     # (source, symbol) → t_exch
        self._last_feed_seq: Dict[str, int] = {}
        self._pending_recovery: Dict[Tuple[str, Optional[str]], bool] = {}
        self._data_epoch: Dict[Tuple[str, Optional[str]], str] = {}       # (source, key) → epoch of last DATA
        self._tape_rows: Dict[Tuple[str, Any, Any, Any, Any], Event] = {}    # dedupe key → emitted event
        self._tape_stamps: Dict[Tuple[str, Any], Tuple[Any, Any, Any]] = {}  # (symbol, stamp) → values
        self._last_heartbeat: Optional[datetime] = None

    # ------------------------------------------------------------------ helpers
    def _next_seq(self, source: str) -> int:
        n = self._seq_local.get(source, 0)
        self._seq_local[source] = n + 1
        return n

    def _emit(self, ev: Event) -> None:
        ev.seq_local = self._next_seq(ev.source)
        self.events.append(ev)
        c = self.stats.src(ev.source)
        c.events += 1
        if self.stats.t_first is None or ev.t_recv < self.stats.t_first:
            self.stats.t_first = ev.t_recv
        if self.stats.t_last is None or ev.t_recv > self.stats.t_last:
            self.stats.t_last = ev.t_recv

    def _in_window(self, t: datetime) -> bool:
        if self.t_from is not None and t < self.t_from:
            return False
        if self.t_to is not None and t > self.t_to:
            return False
        return True

    def _want_symbol(self, sym: Optional[str]) -> bool:
        return self.symbols is None or sym is None or sym.upper() in self.symbols

    def _stale(self, source: str, key: Optional[str], t: datetime) -> bool:
        """Causal cadence rule: compare this receipt gap with 3× the median of the
        gaps seen before it for the same (source, key)."""
        k = (source, key)
        prev = self._last_recv.get(k)
        self._last_recv[k] = t
        if prev is None:
            return False
        gap = (t - prev).total_seconds()
        hist = self._intervals.setdefault(k, [])
        stale = False
        if len(hist) >= self.min_cadence_samples:
            med = statistics.median(hist)
            stale = med > 0 and gap > self.stale_factor * med
        hist.append(gap)
        return stale

    def _seq_hole(self, source: str, rec: Dict[str, Any], t: datetime, key: Optional[str]) -> Tuple[Optional[int], bool]:
        raw = rec.get("src_seq")
        if raw is None:
            return None, False
        try:
            s = int(raw)
        except (TypeError, ValueError):
            return None, False
        prev = self._last_feed_seq.get(source)
        self._last_feed_seq[source] = s
        if prev is not None and s > prev + 1:
            self.stats.src(source).gaps += 1
            self._emit(Event(source=source, event_type=EventType.GAP, t_recv=t, seq_local=0, symbol=key,
                             session_phase=session_phase(t), status="seq_hole", seq_feed=s,
                             payload={"reason": "seq_hole", "expected": prev + 1, "got": s, "missing": s - prev - 1},
                             flags={"gap": True}))
            return s, True
        return s, False

    # ------------------------------------------------------------------ record kinds
    def on_record(self, rec: Any, ok: bool = True) -> None:
        if not ok or not isinstance(rec, dict):
            self.stats.problem("unparseable segment line")
            return
        kind, source = rec.get("kind"), rec.get("source")
        if source is None or (self.sources is not None and source not in self.sources):
            return
        if kind == "HEARTBEAT":
            self._on_heartbeat(rec)
        elif kind == "GAP":
            self._on_gap(rec)
        elif kind == "DATA":
            self._on_data(rec)
        # META / TRAILER / CLOCK carry no observation; restarts are detected from the
        # epoch each DATA record carries (see _on_data), not from META order.

    def _on_heartbeat(self, rec: Dict[str, Any]) -> None:
        t = utc(rec["t_recv_utc"])
        if not self._in_window(t):
            return
        st = rec.get("status") or {}
        prev = self._last_heartbeat
        self._last_heartbeat = t
        if prev is not None:
            # Rule: a silence is a gap when it exceeds the 30 s floor AND, once the
            # heartbeat cadence is known (>= min_cadence_samples earlier intervals),
            # also stale_factor × the median cadence seen so far. A runner that
            # legitimately beats every 30 s (waiting mode) is therefore not a gap
            # on 1 ms of jitter, while a 5 s session heartbeat going quiet for 31 s is.
            # ``heartbeat_jitter_s`` (1 s) is the tolerance for scheduler jitter on the
            # runner's whole-second sleep loop: 30.001 s on a 30 s cadence is not silence.
            silence = (t - prev).total_seconds()
            hist = self._intervals.setdefault(("heartbeat", None), [])
            threshold = self.heartbeat_silence_s
            if len(hist) >= self.min_cadence_samples:
                threshold = max(threshold, self.stale_factor * statistics.median(hist))
            hist.append(silence)
            if silence > threshold + self.heartbeat_jitter_s:
                self.stats.src("heartbeat").gaps += 1
                self._emit(Event(source="heartbeat", event_type=EventType.GAP, t_recv=t, seq_local=0,
                                 session_phase=session_phase(t), status="heartbeat_silence",
                                 payload={"reason": "heartbeat_silence", "silence_s": silence,
                                          "t_prev": prev.isoformat(), "threshold_s": threshold,
                                          "jitter_s": self.heartbeat_jitter_s,
                                          "rule": "silence > max(30 s, 3 x median heartbeat cadence so far) + jitter"},
                                 flags={"gap": True}, raw_ref=("heartbeat", int(rec.get("seq", 0)), "")))
        self.stats.src("heartbeat").records += 1
        self._emit(Event(source="heartbeat", event_type=EventType.STATUS, t_recv=t, seq_local=0,
                         session_phase=session_phase(t), status="heartbeat",
                         payload={"ages_s": dict(st.get("ages_s") or {}), "phase": st.get("phase"),
                                  "counts": st.get("counts"), "symbols": st.get("symbols"),
                                  "backoff_s": st.get("backoff_s"), "waiting_for": st.get("waiting_for"),
                                  "t_status_utc": st.get("t_utc")},
                         raw_ref=("heartbeat", int(rec.get("seq", 0)), "")))

    def _on_gap(self, rec: Dict[str, Any]) -> None:
        source = rec["source"]
        t = utc(rec["t_recv_utc"])
        key = rec.get("key")
        sym = key.upper() if (key and source in PER_SYMBOL_KEY_SOURCES) else None
        if not self._in_window(t) or not self._want_symbol(sym):
            return
        self.stats.src(source).gaps += 1
        self._pending_recovery[(source, key)] = True
        http = rec.get("http") or {}
        self._emit(Event(source=source, event_type=EventType.GAP, t_recv=t, seq_local=0, symbol=sym,
                         session_phase=session_phase(t), status=rec.get("reason"),
                         payload={"reason": rec.get("reason"), "detail": rec.get("detail"), "key": key,
                                  "http_status": http.get("status"), "url": http.get("url")},
                         raw_ref=(source, int(rec.get("seq", 0)), rec.get("body_sha256") or ""),
                         flags={"gap": True}))

    def _on_data(self, rec: Dict[str, Any]) -> None:
        source = rec["source"]
        key = rec.get("key")
        http = rec.get("http") or {}
        t = utc(http.get("t_last_byte_utc") or rec["t_recv_utc"])
        if not self._in_window(t):
            return
        if source in PER_SYMBOL_KEY_SOURCES and key and not self._want_symbol(key.upper()):
            return
        c = self.stats.src(source)
        c.records += 1
        adapter = self._adapters.get(source)
        if adapter is None:
            self.stats.unmapped_sources[source] = self.stats.unmapped_sources.get(source, 0) + 1
            return
        try:
            body = decode_body(rec)
        except Exception as e:  # noqa: BLE001
            c.parse_failures += 1
            self.stats.problem(f"{source} seq {rec.get('seq')}: body undecodable: {e}")
            return
        sha = rec.get("body_sha256")
        if sha and sha256_bytes(body) != sha:
            c.parse_failures += 1
            self.stats.problem(f"{source} seq {rec.get('seq')}: body sha256 mismatch — skipped")
            return
        try:
            parsed = adapter.parse(body, key)
        except Exception as e:  # noqa: BLE001
            c.parse_failures += 1
            self.stats.problem(f"{source} seq {rec.get('seq')} {key or ''}: parser raised {e!r}")
            return
        for pr in parsed.problems:
            self.stats.problem(f"{source} seq {rec.get('seq')} {key or ''}: {pr}")
        if not parsed.frames:
            c.parse_failures += 1
            return
        frames = parsed.frames
        if self.symbols is not None and source not in PER_SYMBOL_KEY_SOURCES:
            frames = [fr for fr in frames if self._want_symbol((fr.get("symbol") or "").upper() or None)]
            if not frames:
                return
        events = events_from_frames(source, frames, t_recv=t, seq=int(rec.get("seq", 0)), body_sha256=sha,
                                    key=key, http=http)
        # record-level QA (shared by every event of the record)
        stale = self._stale(source, key, t)
        seq_feed, hole = self._seq_hole(source, rec, t, key)
        # recovery: first DATA of this (source, key) after a GAP record, or the first one
        # written by a new capture epoch (process restart) — judged against the epoch of
        # this key's previous DATA record, never against META order
        recovery = self._pending_recovery.pop((source, key), False)
        epoch = rec.get("epoch")
        prev_epoch = self._data_epoch.get((source, key))
        if epoch and prev_epoch is not None and prev_epoch != epoch:
            recovery = True
        if epoch:
            self._data_epoch[(source, key)] = epoch
        if source == "lankabd_tape":
            self._emit_tape(source, key, events, sha, t, stale, seq_feed, hole, recovery, len(frames),
                            int(rec.get("seq", 0)))
            return
        for ev in events:
            ev.seq_feed = seq_feed
            ev.is_recovery = recovery
            self._qa_flags(ev, sha, stale, hole, multi=(source not in PER_SYMBOL_KEY_SOURCES))
            self._emit(ev)

    def _qa_flags(self, ev: Event, sha: Optional[str], stale: bool, hole: bool, multi: bool) -> None:
        c = self.stats.src(ev.source)
        k = (ev.source, ev.symbol)
        if sha is not None:
            dup = self._last_body.get(k) == sha
            self._last_body[k] = sha
            if dup:
                ev.flags["duplicate"] = True
                c.duplicates += 1
        if multi and ev.symbol is not None:
            fh = _frame_hash(ev.payload)
            if self._last_frame_hash.get(k) == fh and not ev.flags.get("duplicate"):
                ev.flags["unchanged"] = True
                c.unchanged += 1
            self._last_frame_hash[k] = fh
        if stale:
            ev.flags["stale"] = True
            c.stale += 1
        if hole:
            ev.flags["gap"] = True
        if ev.t_exch is not None:
            prev = self._last_exch.get(k)
            if prev is not None and ev.t_exch < prev:
                ev.flags["out_of_order"] = True
                c.out_of_order += 1
            self._last_exch[k] = ev.t_exch

    def _emit_tape(self, source: str, key: Optional[str], events: List[Event], sha: Optional[str], t: datetime,
                   stale: bool, seq_feed: Optional[int], hole: bool, recovery: bool, n_rows: int,
                   seq: int = 0) -> None:
        """Cumulative rows: first receipt wins; later pulls only bump ``pulls_seen``.
        A stamp seen again with different values is a correction (kept, flagged)."""
        c = self.stats.src(source)
        sym = (key or "").upper() or None
        body_dup = sha is not None and self._last_body.get((source, sym)) == sha
        if sha is not None:
            self._last_body[(source, sym)] = sha
        new = 0
        first_new = True
        for ev in events:
            p = ev.payload
            dk = (ev.symbol, p.get("t_source_ms"), p.get("cum_trades"), p.get("cum_volume"), p.get("cum_value_mn"))
            seen = self._tape_rows.get(dk)
            if seen is not None:
                seen.payload["pulls_seen"] = seen.payload.get("pulls_seen", 1) + 1
                c.tape_rows_deduped += 1
                continue
            sk = (ev.symbol, p.get("t_source_ms"))
            vals = (p.get("cum_trades"), p.get("cum_volume"), p.get("cum_value_mn"))
            if sk in self._tape_stamps and self._tape_stamps[sk] != vals:
                ev.flags["correction"] = True
                c.corrections += 1
            self._tape_stamps[sk] = vals
            self._tape_rows[dk] = ev
            ev.seq_feed = seq_feed
            ev.is_recovery = recovery and first_new
            first_new = False
            self._qa_flags(ev, None, stale, hole, multi=False)
            self._emit(ev)
            new += 1
        if new == 0:
            if body_dup:
                c.duplicates += 1
            self._emit(Event(source=source, event_type=EventType.STATUS, t_recv=t, seq_local=0, symbol=sym,
                             session_phase=session_phase(t), status="no_new_rows", seq_feed=seq_feed,
                             is_recovery=recovery, raw_ref=(source, seq, sha or ""),
                             payload={"rows_in_pull": n_rows, "new_rows": 0},
                             flags={k: True for k, v in (("duplicate", body_dup), ("stale", stale), ("gap", hole)) if v},
                             observed_fields=("t_recv",)))
            if stale:
                c.stale += 1

    # ------------------------------------------------------------------ output
    def finish(self) -> List[Event]:
        self.events.sort(key=lambda e: (e.sort_key(), e.source, e.event_type.value, e.symbol or ""))
        return self.events


def normalize_store(root: str, symbols: Optional[Iterable[str]] = None, t_from: Any = None, t_to: Any = None,
                    sources: Optional[Iterable[str]] = None, **kw: Any) -> Tuple[List[Event], QAStats]:
    """Read every segment of a raw store and return ``(events sorted by
    sort_key, QAStats)``.

    Records are fed to the :class:`Normalizer` in **receipt order across the
    whole store** — sorted by (t_recv_utc, source, seq) — not in segment-file
    order: a store with several capture epochs (restarts) lists segments per
    epoch, and an unclosed segment is listed last regardless of its time, so
    file order would break causality (cadence, duplicate, gap state would see
    the future before the past). ``t_from``/``t_to`` bound ``t_recv``
    inclusively; ``symbols`` keeps per-symbol events of those symbols
    (market-wide events are always kept); ``sources`` restricts raw sources."""
    n = Normalizer(symbols=symbols, t_from=t_from, t_to=t_to, sources=sources, **kw)
    recs: List[Tuple[datetime, str, int, Dict[str, Any]]] = []
    for path in _segment_paths(root):
        for rec, ok in iter_segment(path):
            if not ok or not isinstance(rec, dict):
                n.stats.problem(f"unparseable line in {os.path.basename(path)}")
                continue
            try:
                t = utc(rec["t_recv_utc"])
            except (KeyError, ValueError, TypeError) as e:
                n.stats.problem(f"{os.path.basename(path)}: record without a usable t_recv_utc ({e!r})")
                continue
            recs.append((t, str(rec.get("source")), int(rec.get("seq", 0)), rec))
    recs.sort(key=lambda r: (r[0], r[1], r[2]))
    for _, _, _, rec in recs:
        n.on_record(rec, True)
    return n.finish(), n.stats


def normalize_records(records: Iterable[Dict[str, Any]], **kw: Any) -> Tuple[List[Event], QAStats]:
    """Same as :func:`normalize_store` for an in-memory iterable of raw-store records."""
    n = Normalizer(**kw)
    for rec in records:
        n.on_record(rec, True)
    return n.finish(), n.stats
