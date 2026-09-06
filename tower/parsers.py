"""Richer-feed parsers → :class:`tower.events.Event`.

Three inputs no public DSE source delivers today but which the tower must be
able to consume unchanged the day they are attached:

1. **ITCH-style binary framing** (``itch_frames`` / ``itch_encode`` /
   ``itch_to_events``). Wire format (all big-endian):

       [uint16 length][payload]           length counts the payload only
       payload = type:char, t_ns:uint64, then per type

       'A' add order      order_ref u64, side 'B'|'S', shares u32, stock 8 ascii (space padded), price u32 (1/10000)
       'E' execute        order_ref u64, shares u32, match u64
       'X' cancel         order_ref u64, shares u32                     (partial cancel)
       'D' delete         order_ref u64
       'U' replace        old_ref u64, new_ref u64, shares u32, price u32  (same stock & side)
       'P' trade          order_ref u64, side, shares u32, stock 8, price u32, match u64  (non-displayed)
       'S' system event   code char

   ``t_ns`` is nanoseconds since the Unix epoch (UTC) unless ``itch_to_events``
   is given ``t0`` (then it is nanoseconds since ``t0``, the ITCH "since
   midnight" convention). ``itch_frames`` returns a :class:`FrameList`: a list
   of frame dicts with ``.tail`` (bytes of an incomplete trailing message, to
   be prepended to the next read) and ``.problems``.

   ``itch_to_events`` keeps every live order (``ItchBook``) and reduces the
   order-level (L3) stream to price-level BOOK_UPDATE events carrying the
   aggregate ``qty`` **and** ``order_count`` at that price after the message,
   plus TRADE events for executions ('E': price = the resting order's price,
   aggressor = the opposite of the resting side; 'P': side is the resting side
   per ITCH convention, aggressor the opposite).

2. **FIX market data** (``fix_to_events``) — 35=W full refresh → BOOK_SNAPSHOT
   (with orders per level when tag 346 is carried) + TRADE per 269=2 entry;
   35=X incremental → BOOK_UPDATE (NEW/CHANGE/DELETE by MDUpdateAction 279) /
   TRADE. Parsing and book state are ``seeing.capture.adapters.fix_md``.
   MsgSeqNum (34) becomes ``seq_feed`` and holes set ``flags["gap"]``; an
   invalid checksum/length sets ``flags["checksum_invalid"]`` (the message is
   still emitted — the raw bytes are the record, the flag is the warning).

3. **Broker exports** (``broker_export_to_events``) — Level-II (wide/long CSV
   or JSON) → BOOK_SNAPSHOT; Time & Sales → TRADE, via
   ``seeing.capture.adapters.broker_export``.

Receipt time: these inputs often arrive as files with no receipt clock. Every
converter takes ``t_recv`` as None (use the source stamp as the frame clock;
``freshness_s`` stays None because it is not observable), one datetime (all
events received at once), or a per-message sequence. A row with neither a
source stamp nor a receipt time cannot be placed in time and raises
``ValueError`` — it is never silently stamped.

When the source stamp is the frame clock it is held **monotone**: buffer /
file order is receipt order, so a message cannot have been received before
the message that precedes it. A stamp that runs backwards keeps its own
``t_exch`` and is flagged ``out_of_order``, but its ``t_recv`` is clamped to
the previous frame's and flagged ``t_recv_clamped`` — otherwise the final
sort (``Event.sort_key`` is ``t_recv`` first) would place a cancel before the
add it cancels and a replay of the stream would rebuild a book the feed never
had.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from seeing.capture.adapters import broker_export, fix_md
from seeing.clock import DHAKA, session_phase

from .events import Event, EventType, utc

PRICE_SCALE = 10000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_HDR = struct.Struct(">H")
_FMT: Dict[str, struct.Struct] = {
    "A": struct.Struct(">cQQcI8sI"),
    "E": struct.Struct(">cQQIQ"),
    "X": struct.Struct(">cQQI"),
    "D": struct.Struct(">cQQ"),
    "U": struct.Struct(">cQQQII"),
    "P": struct.Struct(">cQQcI8sIQ"),
    "S": struct.Struct(">cQc"),
}
SYSTEM_CODES = {"O": "start_of_messages", "S": "start_of_system_hours", "Q": "start_of_market_hours",
                "M": "end_of_market_hours", "E": "end_of_system_hours", "C": "end_of_messages"}
_SIDE = {"B": "bid", "S": "ask"}
_OPP = {"B": "S", "S": "B"}
TRecv = Union[None, datetime, Sequence[Optional[datetime]]]


class FrameList(list):
    """``list`` of frame dicts plus ``tail`` (incomplete trailing bytes) and ``problems``."""

    def __init__(self, items: Iterable[Dict[str, Any]] = (), tail: bytes = b"", problems: Optional[List[str]] = None) -> None:
        super().__init__(items)
        self.tail = tail
        self.problems: List[str] = problems or []


# ---------------------------------------------------------------------------- ITCH framing
def _stock(b: bytes) -> str:
    return b.decode("ascii", "replace").strip()


def itch_frames(buf: bytes) -> FrameList:
    """Split a byte buffer into ITCH-style frames.

    Rule: read a uint16 length; if fewer bytes than that remain the rest is an
    incomplete tail (returned, not parsed). A payload shorter than its type's
    fixed size, or with an unknown type, is kept as a ``{"type": "?"}`` frame
    with the raw bytes and a problem entry — nothing is silently skipped."""
    out = FrameList()
    i, n = 0, len(buf)
    while i < n:
        if n - i < _HDR.size:
            out.tail = buf[i:]
            break
        (ln,) = _HDR.unpack_from(buf, i)
        if n - i - _HDR.size < ln:
            out.tail = buf[i:]
            break
        payload = buf[i + _HDR.size: i + _HDR.size + ln]
        off = i
        i += _HDR.size + ln
        typ = chr(payload[0]) if payload else "?"
        st = _FMT.get(typ)
        if st is None or len(payload) != st.size:
            out.problems.append(f"offset {off}: type {typ!r} len {ln} (expected {st.size if st else 'unknown'})")
            out.append({"type": "?", "raw_type": typ, "offset": off, "raw": payload, "t_ns": None})
            continue
        v = st.unpack(payload)
        fr: Dict[str, Any] = {"type": typ, "t_ns": v[1], "offset": off}
        if typ == "A":
            fr.update(order_ref=v[2], side=v[3].decode("ascii"), shares=v[4], stock=_stock(v[5]),
                      price_int=v[6], price=v[6] / PRICE_SCALE)
        elif typ == "E":
            fr.update(order_ref=v[2], shares=v[3], match=v[4])
        elif typ == "X":
            fr.update(order_ref=v[2], shares=v[3])
        elif typ == "D":
            fr.update(order_ref=v[2])
        elif typ == "U":
            fr.update(old_ref=v[2], new_ref=v[3], shares=v[4], price_int=v[5], price=v[5] / PRICE_SCALE)
        elif typ == "P":
            fr.update(order_ref=v[2], side=v[3].decode("ascii"), shares=v[4], stock=_stock(v[5]),
                      price_int=v[6], price=v[6] / PRICE_SCALE, match=v[7])
        elif typ == "S":
            fr.update(code=v[2].decode("ascii"))
        out.append(fr)
    return out


def _price_int(m: Dict[str, Any]) -> int:
    if "price_int" in m and m["price_int"] is not None:
        return int(m["price_int"])
    return int(round(float(m["price"]) * PRICE_SCALE))


def itch_encode(msgs: Iterable[Dict[str, Any]]) -> bytes:
    """Inverse of :func:`itch_frames` (test helper and reference encoder).
    Each dict needs ``type`` and ``t_ns`` plus the type's fields; ``price`` may
    be given as a float (scaled by 1/10000) or as ``price_int``."""
    out = bytearray()
    for m in msgs:
        typ = m["type"]
        st = _FMT[typ]
        t = bytes(typ, "ascii")
        ts = int(m["t_ns"])
        if typ == "A":
            body = st.pack(t, ts, int(m["order_ref"]), m["side"].encode("ascii"), int(m["shares"]),
                           m["stock"].encode("ascii").ljust(8)[:8], _price_int(m))
        elif typ == "E":
            body = st.pack(t, ts, int(m["order_ref"]), int(m["shares"]), int(m["match"]))
        elif typ == "X":
            body = st.pack(t, ts, int(m["order_ref"]), int(m["shares"]))
        elif typ == "D":
            body = st.pack(t, ts, int(m["order_ref"]))
        elif typ == "U":
            body = st.pack(t, ts, int(m["old_ref"]), int(m["new_ref"]), int(m["shares"]), _price_int(m))
        elif typ == "P":
            body = st.pack(t, ts, int(m["order_ref"]), m["side"].encode("ascii"), int(m["shares"]),
                           m["stock"].encode("ascii").ljust(8)[:8], _price_int(m), int(m["match"]))
        elif typ == "S":
            body = st.pack(t, ts, m["code"].encode("ascii"))
        else:
            raise ValueError(f"unknown ITCH type {typ!r}")
        out += _HDR.pack(len(body)) + body
    return bytes(out)


# ---------------------------------------------------------------------------- ITCH L3 → L2 book
@dataclass
class _Order:
    stock: str
    side: str          # 'B' | 'S'
    shares: int
    price_int: int


class ItchBook:
    """Order-level book: every live order by reference, aggregated per
    (stock, side, price) into ``[qty, order_count]``. All mutations return the
    aggregate after the change so the caller can emit one BOOK_UPDATE per
    touched price level."""

    def __init__(self) -> None:
        self.orders: Dict[int, _Order] = {}
        self.levels: Dict[Tuple[str, str, int], List[int]] = {}

    def _adj(self, o: _Order, dq: int, dn: int) -> Tuple[int, int]:
        k = (o.stock, o.side, o.price_int)
        lv = self.levels.setdefault(k, [0, 0])
        lv[0] += dq
        lv[1] += dn
        if lv[1] <= 0 or lv[0] <= 0:
            self.levels.pop(k, None)
            return 0, 0
        return lv[0], lv[1]

    def add(self, ref: int, stock: str, side: str, shares: int, price_int: int) -> Tuple[int, int, bool]:
        """Returns (qty, order_count, level_was_new)."""
        was_new = (stock, side, price_int) not in self.levels
        o = _Order(stock, side, int(shares), int(price_int))
        self.orders[ref] = o
        q, n = self._adj(o, o.shares, 1)
        if o.shares <= 0:
            self.orders.pop(ref, None)       # nothing rests: no live order to execute or cancel later
        return q, n, was_new

    def reduce(self, ref: int, shares: int) -> Tuple[_Order, int, int, int]:
        """Reduce (execute/cancel) ``shares`` of an order; removes it at zero.
        Returns (order, shares_actually_removed, qty_after, orders_after)."""
        o = self.orders[ref]
        take = min(int(shares), o.shares)
        o.shares -= take
        if o.shares == 0:
            del self.orders[ref]
            q, n = self._adj(o, -take, -1)
        else:
            q, n = self._adj(o, -take, 0)
        return o, take, q, n

    def delete(self, ref: int) -> Tuple[_Order, int, int]:
        o = self.orders.pop(ref)
        q, n = self._adj(o, -o.shares, -1)
        return o, q, n

    def rank(self, stock: str, side: str, price_int: int) -> Optional[int]:
        """1-based distance from the touch among non-empty levels of that side; None if absent."""
        ps = sorted((p for (s, sd, p) in self.levels if s == stock and sd == side), reverse=(side == "B"))
        return ps.index(price_int) + 1 if price_int in ps else None

    def snapshot(self, stock: str) -> Dict[str, Any]:
        bids = sorted(((p, v[0], v[1]) for (s, sd, p), v in self.levels.items() if s == stock and sd == "B"),
                      key=lambda x: -x[0])
        asks = sorted(((p, v[0], v[1]) for (s, sd, p), v in self.levels.items() if s == stock and sd == "S"),
                      key=lambda x: x[0])
        return {"symbol": stock,
                "bids": [(p / PRICE_SCALE, float(q)) for p, q, _ in bids],
                "asks": [(p / PRICE_SCALE, float(q)) for p, q, _ in asks],
                "bid_orders": [n for _, _, n in bids], "ask_orders": [n for _, _, n in asks],
                "live_orders": sum(1 for o in self.orders.values() if o.stock == stock)}


def _t_recv_for(i: int, t_recv: TRecv, t_exch: Optional[datetime], what: str) -> Tuple[datetime, bool]:
    """Resolve the receipt clock for item ``i``; returns (t_recv, receipt_observed)."""
    if isinstance(t_recv, datetime):
        return utc(t_recv), True
    if t_recv is not None and not isinstance(t_recv, (str, bytes)):
        tr = t_recv[i] if i < len(t_recv) else None
        if tr is not None:
            return utc(tr), True
    if t_exch is None:
        raise ValueError(f"{what}: neither a source timestamp nor a receipt time — cannot place it in time")
    return t_exch, False


class _FrameClock:
    """Receipt clock of a message sequence (see the module doc). ``resolve``
    returns ``(t_recv, receipt_observed, clamped)``: when the receipt time is
    not observed the source stamp stands in for it, clamped to be no earlier
    than any earlier message's receipt time — messages are received in order."""

    def __init__(self) -> None:
        self.last: Optional[datetime] = None

    def resolve(self, i: int, t_recv: TRecv, t_exch: Optional[datetime], what: str) -> Tuple[datetime, bool, bool]:
        tr, obs = _t_recv_for(i, t_recv, t_exch, what)
        clamped = False
        if not obs and self.last is not None and tr < self.last:
            tr, clamped = self.last, True
        if self.last is None or tr > self.last:
            self.last = tr
        return tr, obs, clamped


def itch_to_events(frames: Sequence[Dict[str, Any]], source: str = "itch", t_recv: TRecv = None,
                   t0: Optional[datetime] = None, venue: str = "DSE", book: Optional[ItchBook] = None
                   ) -> List[Event]:
    """Reduce ITCH frames to Events with an order-tracking book (see module doc).

    Unknown order references are never guessed: 'E' on an unknown ref still
    emits the TRADE (a trade did happen) with ``price=None`` and
    ``flags["unknown_order"]``; 'X'/'D'/'U' on an unknown ref emit a STATUS
    ``unknown_order_ref`` and change nothing."""
    book = book if book is not None else ItchBook()
    base = utc(t0) if t0 is not None else None
    out: List[Event] = []
    seq = 0
    clock = _FrameClock()
    cur = {"clamped": False}                 # receipt-clock state of the message being reduced

    last_te: Optional[datetime] = None

    def stamp(ns: Optional[int]) -> Optional[datetime]:
        # integer arithmetic: ns/1e9 as a float loses the microsecond at epoch scale
        if ns is None:
            return None
        if base is not None:
            return base + timedelta(microseconds=int(ns) // 1000)
        return _EPOCH + timedelta(microseconds=int(ns) // 1000)

    def mk(et: EventType, tr: datetime, te: Optional[datetime], observed_recv: bool, **kw: Any) -> Event:
        nonlocal seq, last_te
        ev = Event(source=source, event_type=et, t_recv=tr, seq_local=seq, venue=venue, t_exch=te,
                   session_phase=session_phase(tr), **kw)
        if observed_recv and te is not None:
            ev.freshness_s = (tr - te).total_seconds()
        if te is not None:
            if last_te is not None and te < last_te:
                ev.flags["out_of_order"] = True        # the feed's own clock went backwards
            last_te = te
        if cur["clamped"]:
            ev.flags["t_recv_clamped"] = True
        seq += 1
        return ev

    def book_update(tr: datetime, te: Optional[datetime], obs: bool, stock: str, side: str, price_int: int,
                    qty: int, n: int, action: str, msg: Dict[str, Any], dq: int, dn: int) -> Event:
        return mk(EventType.BOOK_UPDATE, tr, te, obs, symbol=stock, side=_SIDE[side], price=price_int / PRICE_SCALE,
                  qty=float(qty), order_count=int(n), level=book.rank(stock, side, price_int),
                  payload={"action": action, "msg": msg["type"], "order_ref": msg.get("order_ref", msg.get("new_ref")),
                           "delta_qty": dq, "delta_orders": dn, "t_ns": msg.get("t_ns")},
                  observed_fields=("t_source", "level_quantity_delta", "order_events",
                                   "bid_orders_per_level" if side == "B" else "ask_orders_per_level"))

    for i, m in enumerate(frames):
        typ = m.get("type")
        te = stamp(m.get("t_ns"))
        tr, obs, cur["clamped"] = clock.resolve(i, t_recv, te, f"itch frame {i} type {typ}")
        if typ == "A":
            q, n, new = book.add(m["order_ref"], m["stock"], m["side"], m["shares"], _price_int(m))
            # an add of 0 shares leaves no live order: the level is absent after it (DELETE),
            # not a NEW level of quantity 0 that a book engine would have to carry
            action = "DELETE" if n == 0 else ("NEW" if new else "CHANGE")
            ev = book_update(tr, te, obs, m["stock"], m["side"], _price_int(m), q, n, action, m,
                             +int(m["shares"]), +1 if n else 0)
            if int(m["shares"]) <= 0:
                ev.flags["zero_shares"] = True
            out.append(ev)
        elif typ == "E":
            if m["order_ref"] not in book.orders:
                out.append(mk(EventType.TRADE, tr, te, obs, symbol=None, price=None, qty=float(m["shares"]),
                              trade_id=str(m["match"]), payload={"msg": "E", "order_ref": m["order_ref"], "t_ns": m["t_ns"]},
                              flags={"unknown_order": True}, observed_fields=("t_source", "trade_prints")))
                continue
            o, take, q, n = book.reduce(m["order_ref"], m["shares"])
            fl = {"over_execution": True} if take < int(m["shares"]) else {}
            out.append(mk(EventType.TRADE, tr, te, obs, symbol=o.stock, side=_SIDE[o.side], price=o.price_int / PRICE_SCALE,
                          qty=float(m["shares"]), trade_id=str(m["match"]), aggressor=_OPP[o.side],
                          payload={"msg": "E", "order_ref": m["order_ref"], "resting_side": o.side,
                                   "rule": "aggressor = opposite of the resting order's side", "t_ns": m["t_ns"]},
                          flags=fl, observed_fields=("t_source", "trade_prints", "trade_side")))
            out.append(book_update(tr, te, obs, o.stock, o.side, o.price_int, q, n,
                                   "DELETE" if n == 0 else "CHANGE", m, -take, -1 if o.shares == 0 else 0))
        elif typ in ("X", "D"):
            if m["order_ref"] not in book.orders:
                out.append(mk(EventType.STATUS, tr, te, obs, status="unknown_order_ref",
                              payload={"msg": typ, "order_ref": m["order_ref"], "t_ns": m["t_ns"]},
                              flags={"unknown_order": True}))
                continue
            if typ == "X":
                o, take, q, n = book.reduce(m["order_ref"], m["shares"])
                dn = -1 if o.shares == 0 else 0
            else:
                o, q, n = book.delete(m["order_ref"])
                take, dn = o.shares, -1
            out.append(book_update(tr, te, obs, o.stock, o.side, o.price_int, q, n,
                                   "DELETE" if n == 0 else "CHANGE", m, -take, dn))
        elif typ == "U":
            if m["old_ref"] not in book.orders:
                out.append(mk(EventType.STATUS, tr, te, obs, status="unknown_order_ref",
                              payload={"msg": "U", "order_ref": m["old_ref"], "t_ns": m["t_ns"]},
                              flags={"unknown_order": True}))
                continue
            old, q_old, n_old = book.delete(m["old_ref"])
            new_px = _price_int(m)
            q_new, n_new, new_lvl = book.add(m["new_ref"], old.stock, old.side, m["shares"], new_px)
            if new_px == old.price_int:
                out.append(book_update(tr, te, obs, old.stock, old.side, new_px, q_new, n_new, "CHANGE", m,
                                       int(m["shares"]) - old.shares, 0))
            else:
                out.append(book_update(tr, te, obs, old.stock, old.side, old.price_int, q_old, n_old,
                                       "DELETE" if n_old == 0 else "CHANGE", m, -old.shares, -1))
                out.append(book_update(tr, te, obs, old.stock, old.side, new_px, q_new, n_new,
                                       "NEW" if new_lvl else "CHANGE", m, +int(m["shares"]), +1))
        elif typ == "P":
            out.append(mk(EventType.TRADE, tr, te, obs, symbol=m["stock"], side=_SIDE.get(m["side"]),
                          price=_price_int(m) / PRICE_SCALE, qty=float(m["shares"]), trade_id=str(m["match"]),
                          aggressor=_OPP.get(m["side"]),
                          payload={"msg": "P", "order_ref": m["order_ref"], "resting_side": m["side"],
                                   "displayed": False, "rule": "side is the resting (non-displayed) side; aggressor opposite",
                                   "t_ns": m["t_ns"]},
                          observed_fields=("t_source", "trade_prints", "trade_side")))
        elif typ == "S":
            out.append(mk(EventType.STATUS, tr, te, obs, status=SYSTEM_CODES.get(m["code"], f"system_{m['code']}"),
                          payload={"msg": "S", "code": m["code"], "t_ns": m["t_ns"]}))
        else:
            out.append(mk(EventType.STATUS, tr, te, obs, status="unknown_message_type",
                          payload={"raw_type": m.get("raw_type"), "offset": m.get("offset"),
                                   "raw_len": len(m.get("raw") or b"")},
                          flags={"parse_problem": True}))
    out.sort(key=lambda e: (e.sort_key(), e.source, e.event_type.value, e.symbol or ""))
    return out


# ---------------------------------------------------------------------------- FIX
def _fix_time(v: Optional[str]) -> Optional[datetime]:
    """Tag 52/60 UTCTimestamp ``YYYYMMDD-HH:MM:SS[.sss[sss]]`` → aware UTC."""
    if not v:
        return None
    for fmt in ("%Y%m%d-%H:%M:%S.%f", "%Y%m%d-%H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _aggressor(v: Optional[str]) -> Optional[str]:
    return {"1": "B", "2": "S", "B": "B", "S": "S"}.get(v) if v else None


def fix_to_events(messages: Sequence[Union[str, bytes]], source: str = "fix_md", t_recv: TRecv = None,
                  venue: str = "DSE", book: Optional[fix_md.FIXBook] = None) -> List[Event]:
    """FIX 35=W / 35=X market-data messages → Events (see module doc)."""
    book = book if book is not None else fix_md.FIXBook()
    out: List[Event] = []
    seq = 0
    prev_feed: Optional[int] = None
    prev_te: Optional[datetime] = None
    clock = _FrameClock()
    for i, raw in enumerate(messages):
        s = raw.decode("latin-1") if isinstance(raw, bytes) else raw
        msg = fix_md.parse_fix(s)
        f = msg["fields"]
        te = _fix_time(f.get("52"))
        tr, obs, clamped = clock.resolve(i, t_recv, te, f"fix message {i}")
        seq_feed = int(f["34"]) if f.get("34", "").isdigit() else None
        flags: Dict[str, bool] = {}
        if clamped:
            flags["t_recv_clamped"] = True
        if not (msg["valid_checksum"] and msg["valid_length"]):
            flags["checksum_invalid"] = True
        if seq_feed is not None and prev_feed is not None and seq_feed > prev_feed + 1:
            flags["gap"] = True
        if seq_feed is not None:
            prev_feed = seq_feed
        if te is not None:
            if prev_te is not None and te < prev_te:
                flags["out_of_order"] = True           # SendingTime went backwards
            prev_te = te
        phase = session_phase(tr)

        def mk(et: EventType, **kw: Any) -> Event:
            nonlocal seq
            ev = Event(source=source, event_type=et, t_recv=tr, seq_local=seq, venue=venue, t_exch=te,
                       seq_feed=seq_feed, session_phase=phase, flags=dict(flags), **kw)
            if obs and te is not None:
                ev.freshness_s = (tr - te).total_seconds()
            seq += 1
            return ev

        mt = msg["msg_type"]
        if mt == "W":
            fr = fix_md.md_snapshot_frames(msg)
            book.apply_snapshot(fr)
            has_orders = fr.get("bid_orders_per_level") is not None
            obs_fields: Tuple[str, ...] = ("t_source", "bid_levels", "ask_levels")
            if has_orders:
                obs_fields += ("bid_orders_per_level", "ask_orders_per_level")
            out.append(mk(EventType.BOOK_SNAPSHOT, symbol=fr["symbol"], is_snapshot=True,
                          payload={"bids": [(float(p), float(q)) for p, q in fr["bid_levels"]],
                                   "asks": [(float(p), float(q)) for p, q in fr["ask_levels"]],
                                   "bid_orders": fr.get("bid_orders_per_level"), "ask_orders": fr.get("ask_orders_per_level"),
                                   "orders_per_level": has_orders, "md_req_id": fr.get("md_req_id"),
                                   "n_entries": fr.get("n_entries")},
                          observed_fields=obs_fields))
            for tp in fr["trade_prints"]:
                agg = _aggressor(tp.get("aggressor"))
                out.append(mk(EventType.TRADE, symbol=fr["symbol"], price=tp["price"], qty=tp.get("qty"),
                              trade_id=tp.get("trade_id"), aggressor=agg,
                              payload={"time": tp.get("time"), "date": tp.get("date"), "in_snapshot": True},
                              observed_fields=("t_source", "trade_prints") + (("trade_side",) if agg else ())))
        elif mt == "X":
            # entries are applied one at a time so each BOOK_UPDATE's level rank is the
            # rank at that entry's moment, not after the whole message
            for src_e in msg["groups"].get("268", []):
                sub = {"fields": msg["fields"], "groups": {"268": [src_e]}}
                for e in book.apply_incremental(sub):
                    if e["kind"] == "TRADE":
                        agg = _aggressor(src_e.get("2446") or src_e.get("54"))
                        out.append(mk(EventType.TRADE, symbol=e["symbol"], price=e["price"], qty=e["qty"],
                                      trade_id=src_e.get("1003"), aggressor=agg,
                                      payload={"time": src_e.get("273"), "date": src_e.get("272")},
                                      observed_fields=("t_source", "trade_prints") + (("trade_side",) if agg else ())))
                        continue
                    lv = book.levels(e["symbol"])
                    side_levels = lv["bid_levels"] if e["side"] == "bid" else lv["ask_levels"]
                    prices = [p for p, _ in side_levels]
                    rank = prices.index(e["price"]) + 1 if e["price"] in prices else None
                    # DELETE means the level is gone (qty 0 by definition); a NEW/CHANGE without
                    # MDEntrySize did not deliver a quantity — None, never a silent 0
                    qty = 0.0 if e["kind"] == "DELETE" else (float(e["qty"]) if e.get("qty") is not None else None)
                    out.append(mk(EventType.BOOK_UPDATE, symbol=e["symbol"], side=e["side"], price=e["price"], qty=qty,
                                  order_count=e.get("orders"), level=rank,
                                  payload={"action": e["kind"], "orders": e.get("orders"),
                                           "size_missing": e["kind"] != "DELETE" and e.get("qty") is None},
                                  observed_fields=("t_source", "level_quantity_delta") +
                                  ((("bid_orders_per_level",) if e["side"] == "bid" else ("ask_orders_per_level",))
                                   if e.get("orders") is not None else ())))
        else:
            out.append(mk(EventType.STATUS, status=f"fix_{mt}", payload={"msg_type": mt, "fields": dict(f)}))
    out.sort(key=lambda e: (e.sort_key(), e.source, e.event_type.value, e.symbol or ""))
    return out


# ---------------------------------------------------------------------------- broker exports
def _anchor(te: Optional[datetime], i: int, t_recv: TRecv, trade_date: Optional[date]) -> Tuple[Optional[datetime], Optional[str]]:
    """A time-only export stamp (the seeing adapter parses ``HH:MM:SS`` onto
    1900-01-01) carries no date. Anchor it to ``trade_date`` (Dhaka trading
    day) or, failing that, to the receipt time's Dhaka date; otherwise the
    stamp is unusable and ``None`` is returned so the caller raises."""
    if te is None or te.year > 1900:
        return te, None
    local = te.astimezone(DHAKA)
    if trade_date is not None:
        day, how = trade_date, "trade_date"
    else:
        tr = t_recv if isinstance(t_recv, datetime) else (t_recv[i] if (t_recv is not None and i < len(t_recv)) else None)
        if tr is None:
            return None, None
        day, how = utc(tr).astimezone(DHAKA).date(), "t_recv_date"
    return datetime.combine(day, local.time(), tzinfo=DHAKA).astimezone(timezone.utc), how


def broker_export_to_events(body: bytes, kind: str = "l2", symbol: Optional[str] = None,
                            source: Optional[str] = None, t_recv: TRecv = None, venue: str = "DSE",
                            trade_date: Optional[date] = None) -> List[Event]:
    """Broker Level-II (``kind='l2'``) or Time & Sales (``kind='tns'``) export → Events.
    ``trade_date`` anchors time-only stamps (see :func:`_anchor`)."""
    out: List[Event] = []
    clock = _FrameClock()
    prev_te: Optional[datetime] = None

    def flags_for(te: Optional[datetime], clamped: bool) -> Dict[str, bool]:
        nonlocal prev_te
        fl: Dict[str, bool] = {}
        if te is not None:
            if prev_te is not None and te < prev_te:
                fl["out_of_order"] = True          # the export's own stamps run backwards
            prev_te = te
        if clamped:
            fl["t_recv_clamped"] = True
        return fl

    if kind == "l2":
        parsed = broker_export.BrokerLevel2Adapter().parse(body, symbol)
        src = source or parsed.source
        for i, fr in enumerate(parsed.frames):
            te, how = _anchor(utc(fr["t_source_utc"]) if fr.get("t_source_utc") else None, i, t_recv, trade_date)
            tr, obs, clamped = clock.resolve(i, t_recv, te, f"broker L2 frame {i}")
            has_orders = fr.get("bid_orders_per_level") is not None
            fields: Tuple[str, ...] = ("bid_levels", "ask_levels") + (("t_source",) if te else ()) + \
                (("bid_orders_per_level", "ask_orders_per_level") if has_orders else ())
            ev = Event(source=src, event_type=EventType.BOOK_SNAPSHOT, t_recv=tr, seq_local=i, venue=venue,
                       symbol=(fr.get("symbol") or symbol or "").upper() or None, t_exch=te,
                       session_phase=session_phase(tr), is_snapshot=True,
                       payload={"bids": [(float(p), float(q)) for p, q in fr["bid_levels"]],
                                "asks": [(float(p), float(q)) for p, q in fr["ask_levels"]],
                                "bid_orders": fr.get("bid_orders_per_level"), "ask_orders": fr.get("ask_orders_per_level"),
                                "orders_per_level": has_orders, "layout": fr.get("layout"), "format": fr.get("format"),
                                "date_anchor": how},
                       observed_fields=fields, flags=flags_for(te, clamped))
            if obs and te is not None:
                ev.freshness_s = (tr - te).total_seconds()
            out.append(ev)
    elif kind == "tns":
        parsed = broker_export.BrokerTimeAndSalesAdapter().parse(body, symbol)
        src = source or parsed.source
        for i, fr in enumerate(parsed.frames):
            te, how = _anchor(utc(fr["t_source_utc"]) if fr.get("t_source_utc") else None, i, t_recv, trade_date)
            tr, obs, clamped = clock.resolve(i, t_recv, te, f"broker T&S print {i} ({fr.get('t_source_str')})")
            ev = Event(source=src, event_type=EventType.TRADE, t_recv=tr, seq_local=i, venue=venue,
                       symbol=(fr.get("symbol") or symbol or "").upper() or None, t_exch=te,
                       session_phase=session_phase(tr), price=fr.get("price"), qty=fr.get("qty"),
                       aggressor=fr.get("side"), trade_id=(str(fr["trade_id"]) if fr.get("trade_id") else None),
                       payload={"board": fr.get("board"), "print_index": fr.get("print_index"),
                                "t_source_str": fr.get("t_source_str"), "extra": fr.get("extra") or {},
                                "date_anchor": how},
                       observed_fields=("trade_prints",) + (("t_source",) if te else ()) +
                       (("trade_side",) if fr.get("side") else ()), flags=flags_for(te, clamped))
            if obs and te is not None:
                ev.freshness_s = (tr - te).total_seconds()
            out.append(ev)
    else:
        raise ValueError(f"kind must be 'l2' or 'tns', got {kind!r}")
    if parsed.problems and not parsed.frames:
        raise ValueError("broker export unreadable: " + "; ".join(parsed.problems))
    out.sort(key=lambda e: (e.sort_key(), e.source, e.event_type.value, e.symbol or ""))
    return out
