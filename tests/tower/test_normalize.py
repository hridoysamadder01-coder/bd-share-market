"""tower.normalize — synthetic raw stores (machinery) + the real closed-market fixture."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from seeing.capture.raw_store import RawStore
from tests.conftest import FIXTURES, fixture
from tower.events import SOURCE_PRIORITY, EventType
from tower.normalize import Normalizer, QAStats, normalize_store

CLOSED = os.path.join(FIXTURES, "capture_closed")
T0 = datetime(2026, 9, 6, 4, 15, 0, tzinfo=timezone.utc)      # 10:15 Dhaka, continuous session


def ts(sec: float) -> str:
    return (T0 + timedelta(seconds=sec)).isoformat()


def _table(levels):
    rows = "".join(f"<tr><td><div>{p}</div></td><td><div>{q}</div></td></tr>" for p, q in levels)
    return f"<table><tr><td><div>Price</div></td><td><div>Volume</div></td></tr>{rows}</table>"


def depth_body(symbol, bids, asks, ltp=244.1, trades=100, volume=5000):
    return json.dumps({"symbol": symbol, "buyPriceTable": _table(bids), "sellPriceTable": _table(asks),
                       "lastTradePrice": ltp, "openPrice": 244.0, "daysHigh": 245.0, "daysLow": 243.5,
                       "closePrice": 0.0, "yesterdayClosePrice": 243.9, "noOfTrade": trades,
                       "totalVolume": volume, "totalValueMN": round(volume * ltp / 1e6, 3)}).encode()


def tape_body(rows):
    """rows: [(epoch_ms, price, cum_trades, cum_volume, cum_value_mn)]"""
    return json.dumps({"length": len(rows), "data": [[r[0], r[1], r[2], r[3], r[4], r[1]] for r in rows]}).encode()


def watch_body(stamp, items):
    """items: [(symbol, lm_date_time, ltp, yclose)]"""
    return json.dumps({"timestamp": stamp, "items": [
        {"mkistaT_INSTRUMENT_CODE": s, "mkistaT_LM_DATE_TIME": lm, "mkistaT_PUB_LAST_TRADED_PRICE": ltp,
         "mkistaT_YDAY_CLOSE_PRICE": yc, "mkistaT_TOTAL_TRADES": 10, "mkistaT_TOTAL_VOLUME": 100,
         "mkistaT_TOTAL_VALUE": 0.02, "mkistaT_QUOTE_BASES": "A-EQ", "mkistaT_INSTRUMENT_NUMBER": 7,
         "sectorID": 3} for s, lm, ltp, yc in items]}).encode()


def market_body(stamp, trades=1000):
    return json.dumps({"timestamp": stamp, "trades": trades, "volume": 50000, "value": 12.5, "symbols": 300,
                       "priceupsymbols": 120, "pricedownsymbols": 150, "priceflatsymbols": 30}).encode()


def wd(store, source, key, body, t, http_extra=None, src_seq=None):
    return store.write_data(source, key=key, body=body, http={"status": 200, **(http_extra or {})},
                            t_recv_utc=ts(t), src_seq=src_seq)


def by_type(events, et):
    return [e for e in events if e.event_type is et]


# ---------------------------------------------------------------------------- machinery
def test_machinery_ordering_seq_local_and_determinism(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    # book and tape received at the very same instant: book must come first (source priority)
    wd(st, "lankabd_tape", "GP", tape_body([(1788408900000, 244.1, 1, 100, 0.024)]), 0)
    wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 100)], [(244.2, 50)]), 0)
    wd(st, "lankabd_market", None, market_body("2026-09-06 10:15:00"), 0)
    wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 120)], [(244.2, 50)]), 5)
    wd(st, "lankabd_depth", "BRACBANK", depth_body("BRACBANK", [(62.0, 10)], []), 6)
    st.close()
    ev1, s1 = normalize_store(root)
    ev2, _ = normalize_store(root)
    assert [e.to_dict() for e in ev1] == [e.to_dict() for e in ev2], "two replays must be identical"
    keys = [e.sort_key() for e in ev1]
    assert keys == sorted(keys)
    at0 = [e for e in ev1 if e.t_recv == T0]
    assert [e.event_type for e in at0] == [EventType.BOOK_SNAPSHOT, EventType.CUM_TOTALS, EventType.MARKET_STATS]
    assert SOURCE_PRIORITY["lankabd_depth"] < SOURCE_PRIORITY["lankabd_tape"] < SOURCE_PRIORITY["lankabd_market"]
    depth = [e for e in ev1 if e.source == "lankabd_depth"]
    assert [e.seq_local for e in depth] == [0, 1, 2]            # per-source monotonic in raw seq order
    assert all(e.session_phase == "CONTINUOUS" for e in ev1)
    b = depth[0]
    assert b.is_snapshot and b.payload["bids"] == [(244.0, 100.0)] and b.payload["asks"] == [(244.2, 50.0)]
    assert b.payload["orders_per_level"] is None and "bid_orders_per_level" not in b.observed_fields
    assert b.raw_ref[0] == "lankabd_depth" and len(b.raw_ref[2]) == 64
    assert b.payload["close_published"] == 0.0 and "close_published" in b.payload["zero_fields"]
    assert isinstance(s1, QAStats) and s1.src("lankabd_depth").events == 3 and s1.totals()["parse_failures"] == 0


def test_machinery_tape_dedupe_across_pulls_first_receipt_wins(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    r1 = (1788408900000, 244.1, 1, 100, 0.0244)
    r2 = (1788408960000, 244.2, 3, 300, 0.0733)
    r3 = (1788409020000, 244.0, 4, 350, 0.0855)
    r4 = (1788409080000, 244.3, 9, 900, 0.2199)
    wd(st, "lankabd_tape", "GP", tape_body([r1, r2, r3]), 0)
    wd(st, "lankabd_tape", "GP", tape_body([r1, r2, r3, r4]), 30)
    wd(st, "lankabd_tape", "GP", tape_body([r1, r2, r3, r4]), 60)          # identical pull
    st.close()
    ev, s = normalize_store(root)
    ct = by_type(ev, EventType.CUM_TOTALS)
    assert len(ct) == 4
    assert [e.payload["cum_trades"] for e in ct] == [1, 3, 4, 9]
    assert [e.payload["pulls_seen"] for e in ct] == [3, 3, 3, 2]
    assert [e.t_recv for e in ct[:3]] == [T0] * 3 and ct[3].t_recv == T0 + timedelta(seconds=30)   # first receipt wins
    assert ct[0].t_exch == datetime(2026, 9, 3, 4, 15, tzinfo=timezone.utc)      # epoch ms → UTC
    assert ct[0].freshness_s == (T0 - ct[0].t_exch).total_seconds()
    assert ct[0].price == 244.1 and ct[0].observed_fields == ("t_recv", "t_source", "day_trades", "day_volume", "day_value", "ltp")
    status = [e for e in ev if e.source == "lankabd_tape" and e.event_type is EventType.STATUS]
    assert len(status) == 1 and status[0].status == "no_new_rows" and status[0].flags == {"duplicate": True}
    assert status[0].payload == {"rows_in_pull": 4, "new_rows": 0}
    c = s.src("lankabd_tape")
    assert c.tape_rows_deduped == 3 + 4 and c.duplicates == 1 and c.records == 3 and c.events == 5


def test_machinery_tape_correction_and_out_of_order(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    wd(st, "lankabd_tape", "GP", tape_body([(1788408900000, 244.1, 1, 100, 0.0244), (1788408960000, 244.2, 3, 300, 0.0733)]), 0)
    # same stamp, different cumulative values → correction; plus an older stamp back-filled → out_of_order
    wd(st, "lankabd_tape", "GP", tape_body([(1788408900000, 244.1, 1, 100, 0.0244), (1788408960000, 244.2, 4, 300, 0.0733)]), 30)
    wd(st, "lankabd_tape", "GP", tape_body([(1788408840000, 244.0, 1, 50, 0.0122)]), 60)
    st.close()
    ev, s = normalize_store(root)
    ct = by_type(ev, EventType.CUM_TOTALS)
    assert len(ct) == 4
    corr = [e for e in ct if e.flags.get("correction")]
    assert len(corr) == 1 and corr[0].payload["cum_trades"] == 4
    ooo = [e for e in ct if e.flags.get("out_of_order")]
    assert len(ooo) == 1 and ooo[0].payload["cum_volume"] == 50
    assert s.src("lankabd_tape").corrections == 1 and s.src("lankabd_tape").out_of_order == 1


def test_machinery_duplicate_flag_identical_payload(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    a = depth_body("GP", [(244.0, 100)], [(244.2, 50)])
    b = depth_body("GP", [(244.0, 90)], [(244.2, 50)])
    for i, body in enumerate([a, a, b, a, a, a]):
        wd(st, "lankabd_depth", "GP", body, 5 * i)
    st.close()
    ev, s = normalize_store(root)
    bs = by_type(ev, EventType.BOOK_SNAPSHOT)
    assert [bool(e.flags.get("duplicate")) for e in bs] == [False, True, False, False, True, True]
    assert s.src("lankabd_depth").duplicates == 3
    assert all(e.payload["bids"] for e in bs)          # duplicates are kept, not dropped


def test_machinery_stale_flag_uses_causal_median_cadence(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    times = [0, 10, 20, 30, 40, 50, 110, 120]           # 10 s cadence, one 60 s hole
    for i, t in enumerate(times):
        wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 100 + i)], []), t)
    st.close()
    ev, s = normalize_store(root)
    bs = by_type(ev, EventType.BOOK_SNAPSHOT)
    assert [bool(e.flags.get("stale")) for e in bs] == [False] * 6 + [True, False]
    assert s.src("lankabd_depth").stale == 1
    # too few intervals → cadence unknown → never flagged, even with a large gap
    root2 = str(tmp_path / "cap2")
    st2 = RawStore(root2, capturer_id="t")
    for i, t in enumerate([0, 10, 200]):
        wd(st2, "lankabd_depth", "GP", depth_body("GP", [(244.0, 100 + i)], []), t)
    st2.close()
    ev2, s2 = normalize_store(root2)
    assert not any(e.flags.get("stale") for e in ev2) and s2.src("lankabd_depth").stale == 0


def test_machinery_watch_quotes_breadth_and_out_of_order(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    wd(st, "lankabd_watch", None, watch_body("2026-09-06 10:15:00", [
        ("GP", "2026-09-06 10:14:58", 244.1, 244.0), ("BRACBANK", "2026-09-06 10:14:50", 62.0, 62.5),
        ("FLAT", "2026-09-06 10:14:00", 10.0, 10.0), ("NOPX", "2026-09-06 10:00:00", 0.0, 0.0)]), 0)
    wd(st, "lankabd_watch", None, watch_body("2026-09-06 10:15:20", [
        ("GP", "2026-09-06 10:14:30", 244.1, 244.0), ("BRACBANK", "2026-09-06 10:15:10", 62.6, 62.5),
        ("FLAT", "2026-09-06 10:14:00", 10.0, 10.0), ("NOPX", "2026-09-06 10:00:00", 0.0, 0.0)]), 20)
    st.close()
    ev, s = normalize_store(root)
    q = by_type(ev, EventType.QUOTE)
    assert len(q) == 8 and {e.symbol for e in q} == {"GP", "BRACBANK", "FLAT", "NOPX"}
    gp = [e for e in q if e.symbol == "GP"]
    assert gp[0].t_exch == datetime(2026, 9, 6, 4, 14, 58, tzinfo=timezone.utc) and gp[0].freshness_s == 2.0
    assert gp[0].price == 244.1 and gp[0].instrument_id == "7" and gp[0].payload["sector_id"] == 3
    assert "t_source" in gp[0].observed_fields and "ltp" in gp[0].observed_fields
    assert gp[1].flags.get("out_of_order") is True                        # LM time went backwards
    flat = [e for e in q if e.symbol == "FLAT"]
    assert flat[1].flags.get("unchanged") is True and not flat[1].flags.get("duplicate")
    ms = by_type(ev, EventType.MARKET_STATS)
    assert len(ms) == 2 and all(e.symbol is None for e in ms)
    assert ms[0].payload["up"] == 1 and ms[0].payload["down"] == 1 and ms[0].payload["flat"] == 1 and ms[0].payload["unpriced"] == 1
    assert ms[1].payload["up"] == 2 and ms[1].payload["down"] == 0
    assert ms[0].payload["inferred_fields"] == ["up", "down", "flat", "unpriced"] and ms[0].observed_fields == ("t_recv",)
    assert s.src("lankabd_watch").out_of_order == 1 and s.src("lankabd_watch").unchanged >= 1


def test_machinery_gap_records_seq_holes_heartbeat_silence_and_recovery(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 100)], []), 0, src_seq=10)
    st.write("lankabd_depth", {"kind": "GAP", "reason": "http", "detail": "503", "key": "GP",
                               "http": {"status": 503}, "t_recv_utc": ts(5)})
    wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 110)], []), 10, src_seq=11)
    wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 120)], []), 15, src_seq=14)   # hole: 12, 13 missing
    st.write("heartbeat", {"kind": "HEARTBEAT", "status": {"phase": "CONTINUOUS", "ages_s": {"lankabd_depth": 1.0}}, "t_recv_utc": ts(0)})
    st.write("heartbeat", {"kind": "HEARTBEAT", "status": {"phase": "CONTINUOUS", "ages_s": {"lankabd_depth": 6.0}}, "t_recv_utc": ts(5)})
    st.write("heartbeat", {"kind": "HEARTBEAT", "status": {"phase": "CONTINUOUS", "ages_s": {"lankabd_depth": 2.0}}, "t_recv_utc": ts(50)})
    st.close()
    ev, s = normalize_store(root)
    gaps = by_type(ev, EventType.GAP)
    assert [(g.source, g.status) for g in gaps] == [("lankabd_depth", "http"), ("lankabd_depth", "seq_hole"), ("heartbeat", "heartbeat_silence")]
    assert gaps[0].symbol == "GP" and gaps[0].payload["http_status"] == 503 and gaps[0].flags == {"gap": True}
    assert gaps[1].payload == {"reason": "seq_hole", "expected": 12, "got": 14, "missing": 2}
    assert gaps[2].payload["silence_s"] == 45.0 and gaps[2].t_recv == T0 + timedelta(seconds=50)
    bs = by_type(ev, EventType.BOOK_SNAPSHOT)
    assert [e.is_recovery for e in bs] == [False, True, False]
    assert [e.seq_feed for e in bs] == [10, 11, 14] and bs[2].flags.get("gap") is True
    hb = [e for e in ev if e.source == "heartbeat" and e.event_type is EventType.STATUS]
    assert len(hb) == 3 and hb[1].payload["ages_s"] == {"lankabd_depth": 6.0} and hb[1].payload["phase"] == "CONTINUOUS"
    # the silence GAP sorts before the heartbeat that revealed it (same t_recv, lower seq_local)
    at50 = [e for e in ev if e.t_recv == T0 + timedelta(seconds=50)]
    assert [e.event_type for e in at50] == [EventType.GAP, EventType.STATUS]
    assert s.src("lankabd_depth").gaps == 2 and s.src("heartbeat").gaps == 1


def test_machinery_restart_new_epoch_marks_recovery(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 100)], []), 0)
    wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 101)], []), 5)
    st.close()
    st2 = RawStore(root, capturer_id="t")                     # new process → new epoch, seq restarts at 0
    wd(st2, "lankabd_depth", "GP", depth_body("GP", [(244.0, 102)], []), 60)
    wd(st2, "lankabd_depth", "GP", depth_body("GP", [(244.0, 103)], []), 65)
    st2.close()
    ev, _ = normalize_store(root)
    bs = by_type(ev, EventType.BOOK_SNAPSHOT)
    assert [e.is_recovery for e in bs] == [False, False, True, False]
    assert [e.seq_local for e in bs] == [0, 1, 2, 3] and [e.raw_ref[1] for e in bs] == [1, 2, 1, 2]


def test_machinery_reference_sessions_block_market_from_real_bodies(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    wd(st, "lankabd_circuit", None, fixture("lankabd_circuit_head.html"), 0)
    wd(st, "dsebd_hts", None, fixture("dsebd_hts_2026-09-06.html"), 1)
    wd(st, "lankabd_block", None, fixture("lankabd_block_2026-09-03.json"), 2)
    wd(st, "lankabd_market", None, fixture("lankabd_market_2026-09-03.json"), 3)
    wd(st, "dsebd_depth", "GP", fixture("dsebd_depth_GP_closed.html"), 4)
    st.close()
    ev, s = normalize_store(root)
    ref = by_type(ev, EventType.REFERENCE)
    assert len(ref) >= 30
    r = {e.symbol: e for e in ref}["1JANATAMF"]
    assert r.payload["upper_limit"] == 3.9 and r.payload["lower_limit"] == 3.3 and r.payload["tick_size"] == 0.1
    assert r.payload["breaker_pct"] == 10.0 and r.payload["reference_date"] == "2026-09-03" and r.payload["sector"]
    assert set(r.observed_fields) >= {"upper_limit", "lower_limit", "tick_size", "breaker_pct"}
    hts = [e for e in ev if e.source == "dsebd_hts"]
    assert len(hts) == 1 and hts[0].event_type is EventType.STATUS and hts[0].status == "sessions"
    assert hts[0].payload["n_holidays"] > 0 and hts[0].payload["n_sessions"] > 0
    bl = by_type(ev, EventType.BLOCK_PRINT)
    assert len(bl) == 29 and bl[0].symbol == "SHARPIND" and bl[0].qty == 20000 and bl[0].payload["block_trades"] == 1
    ms = by_type(ev, EventType.MARKET_STATS)
    assert ms[0].payload["market_trades"] == 197767 and ms[0].t_exch == datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc)
    dd = [e for e in ev if e.source == "dsebd_depth"]
    assert len(dd) == 1 and dd[0].symbol == "GP" and dd[0].event_type is EventType.BOOK_SNAPSHOT
    assert s.totals()["parse_failures"] == 0


def test_machinery_filters_symbols_time_sources(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    for i in range(4):
        wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 100 + i)], []), 10 * i)
        wd(st, "lankabd_depth", "BRACBANK", depth_body("BRACBANK", [(62.0, 10 + i)], []), 10 * i + 1)
    wd(st, "lankabd_watch", None, watch_body("2026-09-06 10:15:00", [("GP", "2026-09-06 10:14:58", 244.1, 244.0),
                                                                    ("BRACBANK", "2026-09-06 10:14:50", 62.0, 62.5)]), 2)
    wd(st, "lankabd_market", None, market_body("2026-09-06 10:15:00"), 3)
    st.close()
    ev, _ = normalize_store(root, symbols=["gp"])
    assert {e.symbol for e in ev} == {"GP", None}            # market-wide events are kept
    assert len(by_type(ev, EventType.BOOK_SNAPSHOT)) == 4 and len(by_type(ev, EventType.QUOTE)) == 1
    ev, _ = normalize_store(root, t_from=ts(10), t_to=ts(21))
    assert all(T0 + timedelta(seconds=10) <= e.t_recv <= T0 + timedelta(seconds=21) for e in ev) and len(ev) == 4
    ev, s = normalize_store(root, sources=["lankabd_market"])
    assert {e.source for e in ev} == {"lankabd_market"} and set(s.per_source) == {"lankabd_market"}


def test_machinery_parse_failures_and_tampering_are_counted(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    wd(st, "lankabd_depth", "GP", b"<html>not json</html>", 0)
    rec = wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 100)], []), 5)
    wd(st, "lankabd_depth", "GP", depth_body("GP", [(244.0, 101)], []), 10)
    st.close()
    seg = [os.path.join(root, s["path"]) for s in json.load(open(os.path.join(root, "MANIFEST.json")))["segments"]][0]
    lines = open(seg, "rb").read().split(b"\n")
    lines[3] = lines[3].replace(b'"noOfTrade\\": 100', b'"noOfTrade\\": 999')      # tamper the third record's body
    open(seg, "wb").write(b"\n".join(lines))
    ev, s = normalize_store(root)
    c = s.src("lankabd_depth")
    assert c.records == 3 and c.events == 1 and c.parse_failures == 2
    assert any("sha256 mismatch" in p for p in s.problems) and any("json" in p for p in s.problems)
    assert ev[0].raw_ref[2] == rec["body_sha256"]


def test_machinery_streaming_normalizer_is_causal():
    """Feeding records one at a time gives the same flags as the batch path — no lookahead."""
    n = Normalizer()
    a = depth_body("GP", [(244.0, 100)], [])
    import hashlib
    recs = []
    for i, t in enumerate([0, 10, 20, 30, 40, 100]):
        recs.append({"kind": "DATA", "source": "lankabd_depth", "key": "GP", "seq": i + 1, "epoch": "e1",
                     "t_recv_utc": ts(t), "http": {"status": 200}, "body": a.decode(), "body_encoding": "utf8",
                     "body_sha256": hashlib.sha256(a).hexdigest()})
    flags_seen = []
    for r in recs:
        n.on_record(r, True)
        flags_seen.append(dict(n.events[-1].flags))
    assert flags_seen[-1] == {"duplicate": True, "stale": True} and flags_seen[1] == {"duplicate": True}
    assert flags_seen[0] == {}
    assert [e.seq_local for e in n.finish()] == list(range(6))


def test_machinery_heartbeat_silence_is_cadence_aware(tmp_path):
    root = str(tmp_path / "cap")
    st = RawStore(root, capturer_id="t")
    # waiting mode: a legitimate 30 s cadence with millisecond jitter — never a gap once the cadence is known
    beats = [0, 30.001, 60.002, 90.001, 120.003, 150.002]
    # session mode: 5 s cadence, then a real 34 s silence
    beats += [151, 156, 161, 166, 171, 176, 181, 215]
    for b in beats:
        st.write("heartbeat", {"kind": "HEARTBEAT", "status": {"phase": "CONTINUOUS", "ages_s": {}}, "t_recv_utc": ts(b)})
    st.close()
    ev, s = normalize_store(root)
    gaps = by_type(ev, EventType.GAP)
    assert len(gaps) == 1 and gaps[0].t_recv == T0 + timedelta(seconds=215)
    assert gaps[0].payload["silence_s"] == 34.0 and 30.0 <= gaps[0].payload["threshold_s"] < 31.0
    assert gaps[0].payload["jitter_s"] == 1.0
    assert s.src("heartbeat").gaps == 1 and s.src("heartbeat").events == len(beats) + 1


def test_machinery_records_fed_in_receipt_order_across_epochs(tmp_path):
    """An unclosed earlier-epoch segment is listed after the manifest's segments;
    normalize must still process it first (receipt order), so cadence and
    duplicate state never see the future before the past."""
    root = str(tmp_path / "cap")
    late = RawStore(root, capturer_id="t")                      # written first, but stamped LATER
    wd(late, "lankabd_depth", "GP", depth_body("GP", [(244.0, 200)], []), 100)
    wd(late, "lankabd_depth", "GP", depth_body("GP", [(244.0, 200)], []), 105)
    late.close()
    early = RawStore(root, capturer_id="t")                     # stamped earlier; left unclosed (no manifest entry)
    for i, t in enumerate([0, 10, 20, 30, 40]):
        wd(early, "lankabd_depth", "GP", depth_body("GP", [(244.0, 100 + i)], []), t)
    early.sync_all()
    ev, s = normalize_store(root)
    bs = by_type(ev, EventType.BOOK_SNAPSHOT)
    assert [e.payload["bids"][0][1] for e in bs] == [100, 101, 102, 103, 104, 200, 200]
    assert [e.seq_local for e in bs] == list(range(7))
    assert [bool(e.flags.get("stale")) for e in bs] == [False] * 5 + [True, False]     # 60 s hole vs 10 s cadence
    assert [bool(e.flags.get("duplicate")) for e in bs] == [False] * 6 + [True]
    assert bs[5].is_recovery is True                                                # epoch changed → recovery


# ---------------------------------------------------------------------------- real data
LIVE = "/home/user/bd-share-market/evidence/capture/2026-09-06"


def test_realdata_live_capture_if_present():
    if not os.path.isdir(os.path.join(LIVE, "segments")):
        pytest.skip("live capture not present")
    ev, s = normalize_store(LIVE)
    assert len(ev) > 0 and s.totals()["parse_failures"] == 0
    keys = [e.sort_key() for e in ev]
    assert keys == sorted(keys)
    ev2, _ = normalize_store(LIVE)
    assert [e.to_dict() for e in ev] == [e.to_dict() for e in ev2]
    hb = [e for e in ev if e.source == "heartbeat" and e.event_type is EventType.STATUS]
    assert hb and all("ages_s" in e.payload for e in hb)
    # heartbeats were fed in receipt order; every silence GAP exceeds the 30 s floor (+1 s jitter)
    # and, once the cadence was known, 3× the median cadence before it — recomputed here independently
    beats = [e.t_recv for e in hb]
    assert beats == sorted(beats)
    import statistics
    for g in [e for e in ev if e.source == "heartbeat" and e.event_type is EventType.GAP]:
        earlier = [(b - a).total_seconds() for a, b in zip(beats, beats[1:]) if b < g.t_recv]
        assert g.payload["silence_s"] > 31.0
        if len(earlier) >= 3:
            assert g.payload["silence_s"] > 3 * statistics.median(earlier) + 1.0
    for e in ev:
        assert e.t_recv.tzinfo is not None and e.session_phase in ("CLOSED", "PRE_OPEN", "CONTINUOUS", "POST_CLOSE")
        if e.event_type is EventType.BOOK_SNAPSHOT:
            assert e.payload["orders_per_level"] is None and isinstance(e.payload["bids"], list)
        if e.event_type is EventType.CUM_TOTALS:
            assert e.t_exch is not None and e.freshness_s is not None


def test_realdata_normalize_fixture():
    ev, s = normalize_store(CLOSED)
    assert len(ev) > 0
    present = {"lankabd_depth", "dsebd_depth", "lankabd_tape", "lankabd_market", "lankabd_block", "heartbeat"}
    for src in present:
        assert s.src(src).events > 0, src
    assert s.totals()["parse_failures"] == 0 and s.unmapped_sources == {"lankabd_cidmap": 1}
    keys = [e.sort_key() for e in ev]
    assert keys == sorted(keys)
    ev2, _ = normalize_store(CLOSED)
    assert [e.to_dict() for e in ev] == [e.to_dict() for e in ev2]
    # closed market: no two-sided book anywhere (resting one-sided orders survive the close), day totals frozen
    books = by_type(ev, EventType.BOOK_SNAPSHOT)
    assert len(books) == 14 and {e.source for e in books} == {"lankabd_depth", "dsebd_depth"}
    for b in books:
        assert b.session_phase == "CLOSED" and b.is_snapshot and b.t_exch is None and b.freshness_s is None
        assert not (b.payload["bids"] and b.payload["asks"]), b.symbol
        assert b.payload["orders_per_level"] is None
    for sym in {b.symbol for b in books}:
        totals = {(b.payload["day_trades"], b.payload["day_volume"], b.payload["ltp"]) for b in books if b.symbol == sym}
        assert len(totals) == 1, (sym, totals)
    # the two sensors agree on the book image for every symbol
    for sym in {b.symbol for b in books}:
        imgs = {src: {(tuple(b.payload["bids"]), tuple(b.payload["asks"])) for b in books
                      if b.symbol == sym and b.source == src} for src in ("lankabd_depth", "dsebd_depth")}
        assert len(imgs["lankabd_depth"]) == 1 and imgs["lankabd_depth"] == imgs["dsebd_depth"], sym
    # tape: exchange-stamped, monotone per symbol, de-duplicated across the two pulls
    ct = by_type(ev, EventType.CUM_TOTALS)
    assert len(ct) == 535 and all(e.payload["pulls_seen"] == 2 for e in ct)
    assert s.src("lankabd_tape").tape_rows_deduped == 535
    for sym in {e.symbol for e in ct}:
        rows = sorted((e for e in ct if e.symbol == sym), key=lambda e: e.t_exch)
        for a, b in zip(rows, rows[1:]):
            assert b.payload["cum_trades"] >= a.payload["cum_trades"] and b.payload["cum_volume"] >= a.payload["cum_volume"]
        assert all(e.freshness_s is not None and e.freshness_s > 0 for e in rows)
        assert not any(e.flags.get("out_of_order") for e in rows)
    # re-polled identical payloads are flagged, never dropped
    assert s.src("lankabd_depth").duplicates == 4 and s.src("dsebd_depth").duplicates == 4
    assert s.src("lankabd_market").duplicates == 1 and s.src("lankabd_block").duplicates == 29
    assert s.src("lankabd_tape").duplicates == 3 and len([e for e in ev if e.status == "no_new_rows"]) == 3
    hb = [e for e in ev if e.source == "heartbeat"]
    assert len(hb) == 5 and all(e.event_type is EventType.STATUS and "ages_s" in e.payload for e in hb)
    assert not by_type(ev, EventType.GAP)
    assert s.t_first < s.t_last and s.t_first.date().isoformat() == "2026-09-06"
