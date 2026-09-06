"""tower.parsers — ITCH-style framing + L3→L2 reduction, FIX 35=W/X, broker exports."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from seeing.capture.adapters import fix_md
from tower.events import EventType
from tower.parsers import ItchBook, broker_export_to_events, fix_to_events, itch_encode, itch_frames, itch_to_events

T_NS = 1_788_408_900_000_000_000            # 2026-09-03T04:15:00Z in ns since the epoch
T0 = datetime(2026, 9, 3, 4, 15, tzinfo=timezone.utc)

SCENARIO = [
    {"type": "S", "t_ns": T_NS, "code": "Q"},
    {"type": "A", "t_ns": T_NS + 1_000, "order_ref": 1, "side": "B", "shares": 100, "stock": "GP", "price": 244.0},
    {"type": "A", "t_ns": T_NS + 2_000, "order_ref": 2, "side": "B", "shares": 50, "stock": "GP", "price": 244.0},
    {"type": "A", "t_ns": T_NS + 3_000, "order_ref": 3, "side": "S", "shares": 80, "stock": "GP", "price": 244.2},
    {"type": "A", "t_ns": T_NS + 3_500, "order_ref": 7, "side": "B", "shares": 5, "stock": "BRACBANK", "price": 62.1},
    {"type": "E", "t_ns": T_NS + 4_000, "order_ref": 3, "shares": 30, "match": 900},
    {"type": "X", "t_ns": T_NS + 5_000, "order_ref": 1, "shares": 40},
    {"type": "U", "t_ns": T_NS + 6_000, "old_ref": 2, "new_ref": 4, "shares": 70, "price": 243.9},
    {"type": "D", "t_ns": T_NS + 7_000, "order_ref": 3},
    {"type": "P", "t_ns": T_NS + 8_000, "order_ref": 0, "side": "S", "shares": 10, "stock": "GP", "price": 244.1, "match": 901},
    {"type": "E", "t_ns": T_NS + 9_000, "order_ref": 99, "shares": 5, "match": 902},
]


def by_type(events, et):
    return [e for e in events if e.event_type is et]


# ---------------------------------------------------------------------------- ITCH framing
def test_machinery_itch_round_trip_and_incomplete_tail():
    buf = itch_encode(SCENARIO)
    frames = itch_frames(buf)
    assert len(frames) == len(SCENARIO) and frames.tail == b"" and frames.problems == []
    for m, fr in zip(SCENARIO, frames):
        for k, v in m.items():
            assert fr[k] == v, (k, m, fr)
    assert frames[1]["price_int"] == 2_440_000 and frames[1]["stock"] == "GP"
    # a chunk boundary inside the last message: everything before it parses, the rest is returned as tail
    cut = buf[:-5]
    part = itch_frames(cut)
    assert len(part) == len(SCENARIO) - 1 and len(part.tail) == 29 - 5 + 2 and part.problems == []
    rest = itch_frames(part.tail + buf[-5:])
    assert len(rest) == 1 and rest[0]["type"] == "E" and rest[0]["match"] == 902 and rest.tail == b""
    # a boundary inside the length header itself
    one = itch_frames(buf[:1])
    assert len(one) == 0 and one.tail == buf[:1]
    # an unknown type or a short payload is kept as a '?' frame with a problem, never silently skipped
    bad = b"\x00\x03" + b"Zab" + buf[:2 + 10]          # a 3-byte 'Z' then the real 'S' frame
    fb = itch_frames(bad)
    assert [f["type"] for f in fb] == ["?", "S"] and fb[0]["raw"] == b"Zab" and len(fb.problems) == 1


def test_machinery_itch_l3_to_l2_order_counts():
    buf = itch_encode(SCENARIO)
    book = ItchBook()
    ev = itch_to_events(itch_frames(buf), book=book)
    assert [e.seq_local for e in ev] == list(range(len(ev)))
    assert all(e.source == "itch" and e.t_exch is not None and e.freshness_s is None for e in ev)
    assert ev[0].event_type is EventType.STATUS and ev[0].status == "start_of_market_hours" and ev[0].t_exch == T0
    bu = by_type(ev, EventType.BOOK_UPDATE)
    got = [(e.symbol, e.side, e.price, e.qty, e.order_count, e.level, e.payload["action"]) for e in bu]
    assert got == [
        ("GP", "bid", 244.0, 100.0, 1, 1, "NEW"),          # A ref1
        ("GP", "bid", 244.0, 150.0, 2, 1, "CHANGE"),       # A ref2 — same price: 2 orders
        ("GP", "ask", 244.2, 80.0, 1, 1, "NEW"),           # A ref3
        ("BRACBANK", "bid", 62.1, 5.0, 1, 1, "NEW"),       # another stock, independent book
        ("GP", "ask", 244.2, 50.0, 1, 1, "CHANGE"),        # E 30 of ref3
        ("GP", "bid", 244.0, 110.0, 2, 1, "CHANGE"),       # X 40 of ref1 (partial: order count unchanged)
        ("GP", "bid", 244.0, 60.0, 1, 1, "CHANGE"),        # U: ref2 leaves 244.0
        ("GP", "bid", 243.9, 70.0, 1, 2, "NEW"),           # U: ref4 arrives at 243.9, level 2 from touch
        ("GP", "ask", 244.2, 0.0, 0, None, "DELETE"),      # D ref3 → level gone
    ]
    assert bu[6].payload["delta_qty"] == -50 and bu[6].payload["delta_orders"] == -1
    assert bu[7].payload["delta_qty"] == 70 and bu[7].payload["delta_orders"] == 1
    tr = by_type(ev, EventType.TRADE)
    assert len(tr) == 3
    assert (tr[0].symbol, tr[0].price, tr[0].qty, tr[0].aggressor, tr[0].side, tr[0].trade_id) == ("GP", 244.2, 30.0, "B", "ask", "900")
    assert (tr[1].price, tr[1].qty, tr[1].aggressor, tr[1].trade_id, tr[1].payload["displayed"]) == (244.1, 10.0, "B", "901", False)
    assert tr[2].price is None and tr[2].symbol is None and tr[2].flags == {"unknown_order": True} and tr[2].qty == 5.0
    snap = book.snapshot("GP")
    assert snap["bids"] == [(244.0, 60.0), (243.9, 70.0)] and snap["bid_orders"] == [1, 1]
    assert snap["asks"] == [] and snap["ask_orders"] == [] and snap["live_orders"] == 2
    assert book.snapshot("BRACBANK")["bids"] == [(62.1, 5.0)]
    assert "bid_orders_per_level" in bu[0].observed_fields and "trade_side" in tr[0].observed_fields


def test_machinery_itch_unknown_refs_over_execution_and_same_price_replace():
    msgs = [
        {"type": "X", "t_ns": T_NS, "order_ref": 5, "shares": 1},
        {"type": "D", "t_ns": T_NS + 1, "order_ref": 5},
        {"type": "U", "t_ns": T_NS + 2, "old_ref": 5, "new_ref": 6, "shares": 1, "price": 1.0},
        {"type": "A", "t_ns": T_NS + 3, "order_ref": 8, "side": "S", "shares": 10, "stock": "GP", "price": 244.5},
        {"type": "E", "t_ns": T_NS + 4, "order_ref": 8, "shares": 25, "match": 1},     # more than resting
        {"type": "A", "t_ns": T_NS + 5, "order_ref": 9, "side": "S", "shares": 10, "stock": "GP", "price": 244.5},
        {"type": "U", "t_ns": T_NS + 6, "old_ref": 9, "new_ref": 10, "shares": 15, "price": 244.5},
    ]
    ev = itch_to_events(itch_frames(itch_encode(msgs)))
    st = [e for e in ev if e.status == "unknown_order_ref"]
    assert len(st) == 3 and all(e.flags == {"unknown_order": True} for e in st)
    tr = by_type(ev, EventType.TRADE)
    assert tr[0].flags == {"over_execution": True} and tr[0].qty == 25.0
    bu = by_type(ev, EventType.BOOK_UPDATE)
    assert (bu[1].qty, bu[1].order_count, bu[1].payload["action"]) == (0.0, 0, "DELETE")     # only 10 could leave
    assert (bu[3].qty, bu[3].order_count, bu[3].payload["action"], bu[3].payload["delta_qty"]) == (15.0, 1, "CHANGE", 5)


def test_machinery_itch_stamp_precision_and_backwards_clock():
    msgs = [{"type": "A", "t_ns": T_NS + 1_000, "order_ref": 1, "side": "B", "shares": 1, "stock": "GP", "price": 1.0},
            {"type": "A", "t_ns": T_NS + 999, "order_ref": 2, "side": "B", "shares": 1, "stock": "GP", "price": 1.0},
            {"type": "A", "t_ns": T_NS + 2_000, "order_ref": 3, "side": "B", "shares": 1, "stock": "GP", "price": 1.0}]
    ev = itch_to_events(itch_frames(itch_encode(msgs)), t_recv=T0)
    # microsecond-exact at epoch scale (a float ns/1e9 would round this); sub-µs truncates
    assert [e.t_exch - T0 for e in ev] == [timedelta(microseconds=1), timedelta(0), timedelta(microseconds=2)]
    assert [bool(e.flags.get("out_of_order")) for e in ev] == [False, True, False]     # 999 ns < 1000 ns
    assert [e.seq_local for e in ev] == [0, 1, 2]


def test_machinery_itch_clock_conventions():
    msgs = [{"type": "A", "t_ns": 15 * 60 * 1_000_000_000, "order_ref": 1, "side": "B", "shares": 1, "stock": "GP", "price": 1.0}]
    fr = itch_frames(itch_encode(msgs))
    midnight = datetime(2026, 9, 6, 0, 0, tzinfo=timezone(timedelta(hours=6)))
    ev = itch_to_events(fr, t0=midnight)                    # ns since midnight Dhaka
    assert ev[0].t_exch == datetime(2026, 9, 5, 18, 15, tzinfo=timezone.utc) and ev[0].t_recv == ev[0].t_exch
    assert ev[0].session_phase == "CLOSED"
    recv = ev[0].t_exch + timedelta(milliseconds=250)
    ev2 = itch_to_events(fr, t0=midnight, t_recv=[recv])
    assert ev2[0].t_recv == recv and ev2[0].freshness_s == 0.25
    ev3 = itch_to_events(fr, t0=midnight, t_recv=recv)
    assert ev3[0].freshness_s == 0.25


# ---------------------------------------------------------------------------- FIX
def _fix(msg_type, body, seq):
    return fix_md.build_message(msg_type, "DSEMDS", "SEEING", seq, body)


def test_machinery_fix_snapshot_with_orders_and_incremental():
    recv = datetime(2026, 9, 6, 4, 20, tzinfo=timezone.utc)
    w = _fix("W", [("262", "r1"), ("55", "GP"), ("268", "5"),
                   ("269", "0"), ("270", "244.0"), ("271", "1500"), ("290", "1"), ("346", "12"),
                   ("269", "0"), ("270", "243.9"), ("271", "800"), ("290", "2"), ("346", "5"),
                   ("269", "1"), ("270", "244.2"), ("271", "300"), ("290", "1"), ("346", "3"),
                   ("269", "2"), ("270", "244.2"), ("271", "100"), ("273", "10:01:05"), ("2446", "1"), ("1003", "T77"),
                   ("269", "2"), ("270", "244.0"), ("271", "40"), ("273", "10:01:06"), ("54", "2")], 1)
    x = _fix("X", [("268", "4"),
                   ("279", "1"), ("269", "0"), ("55", "GP"), ("270", "244.0"), ("271", "1200"), ("346", "9"),
                   ("279", "0"), ("269", "1"), ("55", "GP"), ("270", "244.3"), ("271", "10"),
                   ("279", "2"), ("269", "1"), ("55", "GP"), ("270", "244.2"), ("271", "0"),
                   ("279", "0"), ("269", "2"), ("55", "GP"), ("270", "244.3"), ("271", "10"), ("2446", "2")], 3)
    hb = _fix("0", [], 4)
    bad = w.replace("271=1500", "271=1501")
    ev = fix_to_events([w, x, hb, bad], t_recv=recv)
    assert all(e.t_recv == recv and e.source == "fix_md" and e.freshness_s is not None for e in ev)
    snap = [e for e in ev if e.event_type is EventType.BOOK_SNAPSHOT]
    assert len(snap) == 2
    s0 = snap[0]
    assert s0.symbol == "GP" and s0.seq_feed == 1 and s0.is_snapshot and s0.flags == {}
    assert s0.payload["bids"] == [(244.0, 1500.0), (243.9, 800.0)] and s0.payload["asks"] == [(244.2, 300.0)]
    assert s0.payload["bid_orders"] == [12, 5] and s0.payload["ask_orders"] == [3] and s0.payload["orders_per_level"] is True
    assert "bid_orders_per_level" in s0.observed_fields
    tr = by_type(ev, EventType.TRADE)
    assert [(t.price, t.qty, t.aggressor, t.trade_id, t.seq_feed) for t in tr[:3]] == \
        [(244.2, 100.0, "B", "T77", 1), (244.0, 40.0, "S", None, 1), (244.3, 10.0, "S", None, 3)]
    assert tr[0].payload["time"] == "10:01:05" and "trade_side" in tr[0].observed_fields
    bu = by_type(ev, EventType.BOOK_UPDATE)
    assert [(b.side, b.price, b.qty, b.order_count, b.level, b.payload["action"], b.flags.get("gap")) for b in bu] == [
        ("bid", 244.0, 1200.0, 9, 1, "CHANGE", True), ("ask", 244.3, 10.0, None, 2, "NEW", True),
        ("ask", 244.2, 0.0, None, None, "DELETE", True)]                # seq 1 → 3: a hole
    st = [e for e in ev if e.event_type is EventType.STATUS]
    assert len(st) == 1 and st[0].status == "fix_0" and st[0].seq_feed == 4
    assert snap[1].flags.get("checksum_invalid") is True and snap[1].flags.get("gap") is None   # seq 1 again: no hole
    assert [e.seq_local for e in ev] == list(range(len(ev)))


def test_machinery_fix_missing_size_is_none_and_sending_time_regression():
    w = _fix("W", [("55", "GP"), ("268", "1"), ("269", "0"), ("270", "244.0"), ("271", "100")], 1)
    x = _fix("X", [("268", "2"),
                   ("279", "1"), ("269", "0"), ("55", "GP"), ("270", "244.0"),                     # CHANGE, no 271
                   ("279", "2"), ("269", "0"), ("55", "GP"), ("270", "244.0")], 2)                # DELETE, no 271
    older = x.replace("34=2", "34=3")
    import re
    older = re.sub(r"52=\d{8}-\d\d:\d\d:\d\d(\.\d+)?", "52=20200101-00:00:00.000", older)   # SendingTime went back
    ev = fix_to_events([w, x, older], t_recv=datetime(2026, 9, 6, 4, 20, tzinfo=timezone.utc))
    bu = by_type(ev, EventType.BOOK_UPDATE)
    assert (bu[0].payload["action"], bu[0].qty, bu[0].payload["size_missing"]) == ("CHANGE", None, True)
    assert (bu[1].payload["action"], bu[1].qty, bu[1].payload["size_missing"]) == ("DELETE", 0.0, False)
    assert all(e.flags.get("out_of_order") for e in bu[2:]) and not bu[0].flags.get("out_of_order")
    assert all(e.flags.get("checksum_invalid") for e in bu[2:])       # the edited message no longer checksums


def test_machinery_fix_without_orders_and_no_receipt_clock():
    w = _fix("W", [("55", "GP"), ("268", "2"), ("269", "0"), ("270", "244.0"), ("271", "100"),
                   ("269", "1"), ("270", "244.2"), ("271", "50")], 1)
    ev = fix_to_events([w])
    s = ev[0]
    assert s.payload["orders_per_level"] is False and s.payload["bid_orders"] is None
    assert "bid_orders_per_level" not in s.observed_fields
    assert s.t_exch is not None and s.t_recv == s.t_exch and s.freshness_s is None      # receipt not observable


# ---------------------------------------------------------------------------- broker exports
def test_machinery_broker_l2_and_tns_exports():
    wide = ("Symbol,Time,Bid Price 1,Bid Qty 1,Bid Orders 1,Bid Price 2,Bid Qty 2,Bid Orders 2,Ask Price 1,Ask Qty 1,Ask Orders 1\n"
            "GP,2026-09-06 10:15:01,244.0,1500,12,243.9,800,5,244.2,300,3\n"
            "GP,2026-09-06 10:15:03,244.0,1400,11,243.9,800,5,244.2,300,3\n").encode()
    ev = broker_export_to_events(wide, "l2", "GP")
    assert len(ev) == 2 and all(e.event_type is EventType.BOOK_SNAPSHOT and e.source == "broker_l2_export" for e in ev)
    e0 = ev[0]
    assert e0.t_exch == datetime(2026, 9, 6, 4, 15, 1, tzinfo=timezone.utc) and e0.session_phase == "CONTINUOUS"
    assert e0.payload["bids"] == [(244.0, 1500.0), (243.9, 800.0)] and e0.payload["bid_orders"] == [12, 5]
    assert "bid_orders_per_level" in e0.observed_fields and e0.freshness_s is None
    assert [e.seq_local for e in ev] == [0, 1] and ev[1].t_exch > ev[0].t_exch
    long = ("symbol,timestamp,side,price,quantity\nGP,2026-09-06 10:15:01,Buy,244.0,1500\n"
            "GP,2026-09-06 10:15:01,Sell,244.2,300\n").encode()
    e2 = broker_export_to_events(long, "l2")[0]
    assert e2.payload["orders_per_level"] is False and e2.payload["bid_orders"] is None
    # T&S with time-only stamps: the date is not in the row → anchored explicitly, never invented
    tns = b"Time,Price,Volume,Side,Trade ID\n10:15:01,244.2,100,B,T1\n10:15:04,244.1,50,S,T2\n"
    with pytest.raises(ValueError):
        broker_export_to_events(tns, "tns", "GP")
    tr = broker_export_to_events(tns, "tns", "GP", trade_date=date(2026, 9, 6))
    assert [(t.price, t.qty, t.aggressor, t.trade_id) for t in tr] == [(244.2, 100.0, "B", "T1"), (244.1, 50.0, "S", "T2")]
    assert tr[0].t_exch == datetime(2026, 9, 6, 4, 15, 1, tzinfo=timezone.utc) and tr[0].payload["date_anchor"] == "trade_date"
    assert tr[0].source == "broker_tns_export" and "trade_side" in tr[0].observed_fields
    recv = datetime(2026, 9, 6, 4, 15, 10, tzinfo=timezone.utc)
    tr2 = broker_export_to_events(tns, "tns", "GP", t_recv=recv)
    assert tr2[0].payload["date_anchor"] == "t_recv_date" and tr2[0].freshness_s == 9.0 and tr2[1].freshness_s == 6.0
    no_side = broker_export_to_events(b"Time,Price,Volume\n2026-09-06 10:15:01,244.2,100\n", "tns", "GP")
    assert no_side[0].aggressor is None and "trade_side" not in no_side[0].observed_fields
    with pytest.raises(ValueError):
        broker_export_to_events(b"foo,bar\n1,2\n", "tns", "GP")
    with pytest.raises(ValueError):
        broker_export_to_events(tns, "l3", "GP")
