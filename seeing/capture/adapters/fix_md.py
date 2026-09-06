"""FIX market-data adapter (DSE MDS / BHOMS / FIX ecosystem; ICE-style L1/L2 feeds).

What is implemented and tested here, with no credentials:

* ``parse_fix(raw)`` — tag=value parser (SOH or '|' delimited), BodyLength and
  CheckSum verification, repeating-group aware for MarketData messages.
* ``md_snapshot_frames(msg)`` — 35=W MarketDataSnapshotFullRefresh → book levels
  (269=0 bid, 269=1 offer) with price (270), size (271), position (290), and
  NumberOfOrders (346) when present — the only path in this repository that
  makes ``bid_orders_per_level`` OBSERVED — plus trade prints (269=2) with
  time (273), price, size, aggressor side (2446 / 54) when present.
* ``md_incremental_frames(msg)`` — 35=X MarketDataIncrementalRefresh with
  MDUpdateAction (279: 0 new, 1 change, 2 delete) applied by ``FIXBook``.
* ``build_logon`` / ``build_market_data_request`` (35=A / 35=V) message builders.
* ``FIXSessionConfig.missing()`` — names exactly which external inputs are
  absent. ``FIXMarketDataSession.connect()`` refuses to run without them and
  raises ``BlockedError`` listing them; nothing is simulated.

The wire format (FIX 4.4 tags) is standard. DSE's own MDS/BHOMS dictionary,
host, port, CompIDs and the entitlement to receive market data are the external
inputs; when they exist the adapter writes raw frames into the same RawStore
as every other source (``source="fix_md"``), one record per FIX message.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...truth import Truth
from .base import Parsed, capability_map

SOH = "\x01"
MD_GROUP_TAGS = {"269", "270", "271", "272", "273", "279", "290", "346", "278", "280", "55", "48", "22",
                 "1023", "2446", "54", "37", "1003", "58", "336", "625", "276", "277", "282", "283"}
GROUP_TAGS = {"268": MD_GROUP_TAGS, "146": {"55", "48", "22", "65", "207", "167", "461"}}


class BlockedError(RuntimeError):
    """Raised when an external dependency (credential, entitlement, host) is absent."""


def checksum(body: str) -> str:
    return f"{sum(body.encode('latin-1')) % 256:03d}"


def parse_fix(raw: str) -> Dict[str, Any]:
    """Parse one FIX message. Returns {'fields': {tag: value} (non-group), 'groups': {group_tag: [ {tag: v} ]},
    'valid_checksum': bool, 'valid_length': bool, 'msg_type': str}."""
    s = raw.replace("|", SOH) if SOH not in raw else raw
    if not s.endswith(SOH):
        s += SOH
    parts = [p for p in s.split(SOH) if p]
    pairs: List[Tuple[str, str]] = []
    for p in parts:
        if "=" not in p:
            continue
        t, v = p.split("=", 1)
        pairs.append((t, v))
    fields: Dict[str, str] = {}
    groups: Dict[str, List[Dict[str, str]]] = {}
    i = 0
    while i < len(pairs):
        t, v = pairs[i]
        if t in GROUP_TAGS and v.isdigit():             # NoMDEntries / NoRelatedSym
            n = int(v)
            allowed = GROUP_TAGS[t]
            entries: List[Dict[str, str]] = []
            i += 1
            cur: Dict[str, str] = {}
            while i < len(pairs) and len(entries) < n:
                tt, vv = pairs[i]
                if tt not in allowed:                    # group ended: a non-group tag follows
                    break
                if tt in cur:                            # repeated tag → next entry
                    entries.append(cur)
                    cur = {}
                    if len(entries) == n:
                        break
                cur[tt] = vv
                i += 1
            if cur and len(entries) < n:
                entries.append(cur)
            groups[t] = entries
            continue
        fields[t] = v
        i += 1
    # integrity
    valid_len = valid_sum = False
    try:
        head_end = s.index(SOH, s.index("9=")) + 1
        body = s[head_end:]
        tail = body.rfind("10=")
        valid_len = int(fields.get("9", "-1")) == len(body[:tail].encode("latin-1"))
        valid_sum = checksum(s[:head_end + tail]) == fields.get("10")
    except ValueError:
        pass
    return {"fields": fields, "groups": groups, "valid_checksum": valid_sum, "valid_length": valid_len,
            "msg_type": fields.get("35")}


def build_message(msg_type: str, sender: str, target: str, seq: int, body: Iterable[Tuple[str, str]],
                  begin: str = "FIX.4.4") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]
    b = f"35={msg_type}{SOH}49={sender}{SOH}56={target}{SOH}34={seq}{SOH}52={ts}{SOH}" + \
        "".join(f"{t}={v}{SOH}" for t, v in body)
    head = f"8={begin}{SOH}9={len(b.encode('latin-1'))}{SOH}"
    msg = head + b
    return msg + f"10={checksum(msg)}{SOH}"


def build_logon(sender: str, target: str, seq: int, username: Optional[str] = None,
                password: Optional[str] = None, heartbeat_s: int = 30) -> str:
    body = [("98", "0"), ("108", str(heartbeat_s))]
    if username:
        body.append(("553", username))
    if password:
        body.append(("554", password))
    return build_message("A", sender, target, seq, body)


def build_market_data_request(sender: str, target: str, seq: int, symbols: List[str], req_id: str,
                              depth: int = 0, entry_types: Iterable[str] = ("0", "1", "2"),
                              incremental: bool = True) -> str:
    """35=V: SubscriptionRequestType 1 (snapshot+updates), MarketDepth 0 = full book,
    MDUpdateType 1 = incremental, entry types bid/offer/trade, NoRelatedSym symbols."""
    et = list(entry_types)
    body: List[Tuple[str, str]] = [("262", req_id), ("263", "1"), ("264", str(depth)),
                                   ("265", "1" if incremental else "0"), ("267", str(len(et)))]
    body += [("269", e) for e in et]
    body.append(("146", str(len(symbols))))
    body += [("55", s) for s in symbols]
    return build_message("V", sender, target, seq, body)


def _f(v: Optional[str]) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def md_snapshot_frames(msg: Dict[str, Any]) -> Dict[str, Any]:
    """35=W → one frame: bid/ask levels (+ orders per level when tag 346 present), trades."""
    fields = msg["fields"]
    entries = msg["groups"].get("268", [])
    bids: List[Tuple[float, float]] = []
    asks: List[Tuple[float, float]] = []
    bid_orders: List[Optional[int]] = []
    ask_orders: List[Optional[int]] = []
    trades: List[Dict[str, Any]] = []
    for e in entries:
        typ = e.get("269")
        px, sz = _f(e.get("270")), _f(e.get("271"))
        n_ord = int(e["346"]) if e.get("346", "").isdigit() else None
        if typ == "0" and px is not None:
            bids.append((px, sz or 0.0)); bid_orders.append(n_ord)
        elif typ == "1" and px is not None:
            asks.append((px, sz or 0.0)); ask_orders.append(n_ord)
        elif typ == "2" and px is not None:
            trades.append({"price": px, "qty": sz, "time": e.get("273"), "date": e.get("272"),
                           "aggressor": e.get("2446") or e.get("54"), "trade_id": e.get("1003")})
    order_b = sorted(zip(bids, bid_orders), key=lambda x: -x[0][0])
    order_a = sorted(zip(asks, ask_orders), key=lambda x: x[0][0])
    has_orders = any(o is not None for _, o in order_b + order_a)
    return {"symbol": fields.get("55"), "t_source": fields.get("52"), "md_req_id": fields.get("262"),
            "bid_levels": [l for l, _ in order_b], "ask_levels": [l for l, _ in order_a],
            "bid_orders_per_level": [o for _, o in order_b] if has_orders else None,
            "ask_orders_per_level": [o for _, o in order_a] if has_orders else None,
            "trade_prints": trades, "n_entries": len(entries), "valid": msg["valid_checksum"] and msg["valid_length"]}


class FIXBook:
    """Applies 35=X incremental updates to a per-symbol book (price-level keyed)."""

    def __init__(self) -> None:
        self.books: Dict[str, Dict[str, Dict[float, Tuple[float, Optional[int]]]]] = {}
        self.trades: List[Dict[str, Any]] = []

    def apply_snapshot(self, frame: Dict[str, Any]) -> None:
        sym = frame["symbol"]
        b = {p: (q, (frame.get("bid_orders_per_level") or [None] * len(frame["bid_levels"]))[i])
             for i, (p, q) in enumerate(frame["bid_levels"])}
        a = {p: (q, (frame.get("ask_orders_per_level") or [None] * len(frame["ask_levels"]))[i])
             for i, (p, q) in enumerate(frame["ask_levels"])}
        self.books[sym] = {"bid": b, "ask": a}

    def apply_incremental(self, msg: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = []
        for e in msg["groups"].get("268", []):
            sym = e.get("55") or msg["fields"].get("55")
            side = {"0": "bid", "1": "ask"}.get(e.get("269"))
            action = {"0": "NEW", "1": "CHANGE", "2": "DELETE"}.get(e.get("279"), "UNKNOWN")
            px, sz = _f(e.get("270")), _f(e.get("271"))
            n_ord = int(e["346"]) if e.get("346", "").isdigit() else None
            if e.get("269") == "2" and px is not None:
                self.trades.append({"symbol": sym, "price": px, "qty": sz, "time": e.get("273"),
                                    "aggressor": e.get("2446") or e.get("54")})
                events.append({"symbol": sym, "kind": "TRADE", "price": px, "qty": sz})
                continue
            if side is None or px is None or sym is None:
                continue
            book = self.books.setdefault(sym, {"bid": {}, "ask": {}})[side]
            if action == "DELETE":
                book.pop(px, None)
            else:
                book[px] = (sz or 0.0, n_ord)
            events.append({"symbol": sym, "kind": action, "side": side, "price": px, "qty": sz, "orders": n_ord})
        return events

    def levels(self, sym: str) -> Dict[str, Any]:
        b = self.books.get(sym, {"bid": {}, "ask": {}})
        bids = sorted(b["bid"].items(), key=lambda x: -x[0])
        asks = sorted(b["ask"].items(), key=lambda x: x[0])
        return {"symbol": sym, "bid_levels": [(p, q) for p, (q, _) in bids], "ask_levels": [(p, q) for p, (q, _) in asks],
                "bid_orders_per_level": [o for _, (_, o) in bids], "ask_orders_per_level": [o for _, (_, o) in asks]}


@dataclass
class FIXSessionConfig:
    host: Optional[str] = None
    port: Optional[int] = None
    sender_comp_id: Optional[str] = None
    target_comp_id: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    entitlement_confirmed: bool = False      # written confirmation that market data may be received/recorded
    dictionary_confirmed: bool = False       # DSE MDS/BHOMS FIX dictionary in hand (tags may differ from 4.4 defaults)

    def missing(self) -> List[str]:
        m = []
        if not self.host: m.append("MDS/BHOMS host")
        if not self.port: m.append("MDS/BHOMS port")
        if not self.sender_comp_id: m.append("SenderCompID (assigned by DSE/TREC holder)")
        if not self.target_comp_id: m.append("TargetCompID (DSE MDS)")
        if not (self.username and self.password): m.append("session credentials")
        if not self.entitlement_confirmed: m.append("market-data entitlement / written permission to record")
        if not self.dictionary_confirmed: m.append("DSE FIX market-data dictionary (tag map)")
        return m


class FIXMarketDataSession:
    name = "fix_md"
    kind = "book+tape"
    observes = ("bid_levels", "ask_levels", "bid_orders_per_level", "ask_orders_per_level", "trade_prints",
                "trade_side", "t_source", "t_recv")

    def __init__(self, cfg: FIXSessionConfig) -> None:
        self.cfg = cfg
        self.sock: Optional[socket.socket] = None
        self.seq = 1

    def connect(self) -> None:
        miss = self.cfg.missing()
        if miss:
            raise BlockedError("FIX market-data session cannot start; external inputs required: " + "; ".join(miss))
        self.sock = socket.create_connection((self.cfg.host, int(self.cfg.port)), timeout=30)
        self.sock.sendall(build_logon(self.cfg.sender_comp_id, self.cfg.target_comp_id, self.seq,
                                      self.cfg.username, self.cfg.password).encode("latin-1"))
        self.seq += 1

    def subscribe(self, symbols: List[str]) -> None:
        if not self.sock:
            raise BlockedError("not connected")
        self.sock.sendall(build_market_data_request(self.cfg.sender_comp_id, self.cfg.target_comp_id, self.seq,
                                                    symbols, req_id=f"md-{self.seq}").encode("latin-1"))
        self.seq += 1

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        msg = parse_fix(body.decode("latin-1"))
        if not (msg["valid_checksum"] and msg["valid_length"]):
            out.problems.append("checksum/length invalid")
        if msg["msg_type"] == "W":
            fr = md_snapshot_frames(msg)
            if fr["bid_orders_per_level"] is None:
                out.truth["bid_orders_per_level"] = Truth.NOT_OBSERVABLE
                out.truth["ask_orders_per_level"] = Truth.NOT_OBSERVABLE
            out.frames.append(fr)
        elif msg["msg_type"] == "X":
            out.frames.append({"incremental": msg["groups"].get("268", []), "t_source": msg["fields"].get("52")})
        else:
            out.problems.append(f"not a market-data message: 35={msg['msg_type']}")
        return out
