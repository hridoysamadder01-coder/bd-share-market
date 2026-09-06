import json
import os

import pytest

from seeing.capture.adapters import fix_md, broker_export, har_import, minute_dataset
from seeing.capture.raw_store import RawStore, iter_segment
from seeing.truth import Truth


def _fix(msg_type, body_pairs, sender="SEEING", target="DSEMDS", seq=7):
    return fix_md.build_message(msg_type, sender, target, seq, body_pairs)


def test_fix_roundtrip_checksum_and_groups():
    body = [("262", "r1"), ("55", "GP"), ("268", "5"),
            ("269", "0"), ("270", "244.0"), ("271", "1500"), ("290", "1"), ("346", "12"),
            ("269", "0"), ("270", "243.9"), ("271", "800"), ("290", "2"), ("346", "5"),
            ("269", "1"), ("270", "244.2"), ("271", "300"), ("290", "1"), ("346", "3"),
            ("269", "1"), ("270", "244.3"), ("271", "900"), ("290", "2"), ("346", "7"),
            ("269", "2"), ("270", "244.2"), ("271", "100"), ("273", "10:01:05"), ("2446", "1")]
    raw = _fix("W", body)
    msg = fix_md.parse_fix(raw)
    assert msg["valid_checksum"] and msg["valid_length"] and msg["msg_type"] == "W"
    assert len(msg["groups"]["268"]) == 5
    fr = fix_md.md_snapshot_frames(msg)
    assert fr["symbol"] == "GP" and fr["bid_levels"] == [(244.0, 1500.0), (243.9, 800.0)]
    assert fr["ask_levels"] == [(244.2, 300.0), (244.3, 900.0)]
    assert fr["bid_orders_per_level"] == [12, 5] and fr["ask_orders_per_level"] == [3, 7]
    assert fr["trade_prints"][0]["price"] == 244.2 and fr["trade_prints"][0]["aggressor"] == "1"
    # a tampered message fails the checksum
    bad = raw.replace("271=1500", "271=1501")
    assert not fix_md.parse_fix(bad)["valid_checksum"]
    # pipe-delimited input is accepted
    assert fix_md.parse_fix(raw.replace("\x01", "|"))["valid_checksum"]


def test_fix_missing_size_is_none_never_zero():
    snap = fix_md.parse_fix(_fix("W", [("55", "GP"), ("268", "2"), ("269", "0"), ("270", "244.0"),
                                       ("269", "1"), ("270", "244.2"), ("271", "50")]))
    fr = fix_md.md_snapshot_frames(snap)
    assert fr["bid_levels"] == [(244.0, None)] and fr["ask_levels"] == [(244.2, 50.0)] and fr["size_missing"] == 1
    book = fix_md.FIXBook()
    book.apply_snapshot(fr)
    inc = fix_md.parse_fix(_fix("X", [("268", "1"), ("279", "0"), ("269", "1"), ("55", "GP"), ("270", "244.3")]))
    book.apply_incremental(inc)
    lv = book.levels("GP")
    assert (244.3, None) in lv["ask_levels"] and (244.0, None) in lv["bid_levels"]


def test_fix_incremental_book_and_truth():
    snap = fix_md.parse_fix(_fix("W", [("55", "GP"), ("268", "2"), ("269", "0"), ("270", "244.0"), ("271", "100"),
                                       ("269", "1"), ("270", "244.2"), ("271", "50")]))
    book = fix_md.FIXBook()
    book.apply_snapshot(fix_md.md_snapshot_frames(snap))
    inc = fix_md.parse_fix(_fix("X", [("268", "3"),
                                      ("279", "1"), ("269", "0"), ("55", "GP"), ("270", "244.0"), ("271", "60"),
                                      ("279", "0"), ("269", "1"), ("55", "GP"), ("270", "244.3"), ("271", "10"),
                                      ("279", "2"), ("269", "1"), ("55", "GP"), ("270", "244.2"), ("271", "0")]))
    ev = book.apply_incremental(inc)
    assert [e["kind"] for e in ev] == ["CHANGE", "NEW", "DELETE"]
    lv = book.levels("GP")
    assert lv["bid_levels"] == [(244.0, 60.0)] and lv["ask_levels"] == [(244.3, 10.0)]
    sess = fix_md.FIXMarketDataSession(fix_md.FIXSessionConfig())
    p = sess.parse(_fix("W", [("55", "GP"), ("268", "1"), ("269", "0"), ("270", "1.0"), ("271", "1")]).encode())
    assert p.truth["bid_orders_per_level"] is Truth.NOT_OBSERVABLE     # no tag 346 in this message
    assert p.truth["trade_prints"] is Truth.OBSERVED


def test_fix_session_blocked_names_exact_dependencies():
    sess = fix_md.FIXMarketDataSession(fix_md.FIXSessionConfig())
    with pytest.raises(fix_md.BlockedError) as ei:
        sess.connect()
    msg = str(ei.value)
    for dep in ("host", "SenderCompID", "credentials", "entitlement", "dictionary"):
        assert dep in msg
    req = fix_md.parse_fix(fix_md.build_market_data_request("A", "B", 1, ["GP", "BRACBANK"], "r9"))
    assert req["msg_type"] == "V" and [g["55"] for g in req["groups"]["146"]] == ["GP", "BRACBANK"]
    assert req["valid_checksum"]


def test_broker_l2_wide_and_long_layouts():
    wide = ("Symbol,Time,Bid Price 1,Bid Qty 1,Bid Orders 1,Bid Price 2,Bid Qty 2,Bid Orders 2,Ask Price 1,Ask Qty 1,Ask Orders 1\n"
            "GP,2026-09-06 10:15:01,244.0,1500,12,243.9,800,5,244.2,300,3\n").encode()
    p = broker_export.BrokerLevel2Adapter().parse(wide, "GP")
    f = p.frames[0]
    assert f["bid_levels"] == [(244.0, 1500.0), (243.9, 800.0)] and f["ask_levels"] == [(244.2, 300.0)]
    assert f["bid_orders_per_level"] == [12, 5] and f["t_source_utc"] == "2026-09-06T04:15:01+00:00"
    assert p.truth["bid_orders_per_level"] is Truth.OBSERVED and p.truth["trade_prints"] is Truth.NOT_OBSERVABLE
    long = ("symbol,timestamp,side,price,quantity\nGP,2026-09-06 10:15:01,Buy,244.0,1500\n"
            "GP,2026-09-06 10:15:01,Buy,243.9,800\nGP,2026-09-06 10:15:01,Sell,244.2,300\n").encode()
    p2 = broker_export.BrokerLevel2Adapter().parse(long)
    assert p2.frames[0]["bid_levels"] == [(244.0, 1500.0), (243.9, 800.0)] and p2.frames[0]["ask_levels"] == [(244.2, 300.0)]
    assert p2.truth["bid_orders_per_level"] is Truth.NOT_OBSERVABLE      # no order column → not invented


def test_broker_time_and_sales():
    tns = ("Time,Price,Volume,Side\n10:15:01,244.2,100,B\n10:15:04,244.1,50,S\n").encode()
    p = broker_export.BrokerTimeAndSalesAdapter().parse(tns, "GP")
    assert [f["side"] for f in p.frames] == ["B", "S"] and p.frames[0]["qty"] == 100
    assert p.truth["trade_prints"] is Truth.OBSERVED and p.truth["trade_side"] is Truth.OBSERVED
    p2 = broker_export.BrokerTimeAndSalesAdapter().parse(b"Time,Price,Volume\n10:15:01,244.2,100\n", "GP")
    assert p2.truth["trade_side"] is Truth.NOT_OBSERVABLE


def test_har_import_preserves_every_entry(tmp_path):
    har = {"log": {"entries": [
        {"startedDateTime": "2026-09-06T10:15:01.000+06:00", "time": 120,
         "request": {"method": "GET", "url": "https://terminal.example/api/depth?sym=GP", "headers": [{"name": "Cookie", "value": "secret"}]},
         "response": {"status": 200, "headers": [], "content": {"mimeType": "application/json", "text": json.dumps({"bids": [[244.0, 100]], "asks": []})}}},
        {"startedDateTime": "2026-09-06T10:15:02.000+06:00", "time": 5,
         "request": {"method": "GET", "url": "wss://terminal.example/md", "headers": []},
         "response": {"status": 101, "headers": [], "content": {}},
         "_webSocketMessages": [{"type": "receive", "time": 1.0, "opcode": 1, "data": "{\"q\":1}"}]},
    ]}}
    store = RawStore(str(tmp_path), capturer_id="t")
    rep = har_import.import_har(json.dumps(har).encode(), store)
    store.close()
    assert rep == {"entries": 2, "ws_frames": 1}
    recs = [r for p in [os.path.join(str(tmp_path), s["path"]) for s in json.load(open(tmp_path / "MANIFEST.json"))["segments"]]
            for r, ok in iter_segment(p) if ok and r["kind"] == "DATA"]
    assert len(recs) == 3 and all("Cookie" not in json.dumps(r["http"]) for r in recs)
    p = har_import.HARPayloadAdapter().parse(json.dumps({"bids": [], "asks": []}).encode())
    assert p.frames[0]["schema_unknown"] and p.frames[0]["top_level_keys"] == ["asks", "bids"]


REAL_MINUTE = "/home/user/data_ext/dse_minute/minute_price_unadjusted/BRACBANK.csv"


def test_minute_dataset_adapter_on_real_file_if_present():
    if not os.path.exists(REAL_MINUTE):
        pytest.skip("real minute dataset not checked out")
    with open(REAL_MINUTE, "rb") as fh:
        head = b"".join([next(fh) for _ in range(200)])
    p = minute_dataset.MinuteDatasetAdapter().parse(head, "BRACBANK")
    assert len(p.frames) == 199 and not p.problems
    assert p.frames[0]["t_source_utc"] == "2015-10-15T04:36:00+00:00" and p.frames[0]["minute_volume"] == 500
    assert p.truth["bid_levels"] is Truth.NOT_OBSERVABLE and p.truth["interval_volume"] is Truth.OBSERVED
