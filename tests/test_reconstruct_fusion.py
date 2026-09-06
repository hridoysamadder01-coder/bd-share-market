import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from seeing.reconstruct.book import book_stats, diff_levels, reconstruct_books
from seeing.reconstruct.tape import classify_side, interval_tape
from seeing.reconstruct.events import queue_events
from seeing.fusion.fuse import fuse

T0 = datetime(2026, 9, 6, 4, 0, 0, tzinfo=timezone.utc)


def _books(rows):
    """rows: (source, symbol, t_offset_s, bids, asks, day_trades, day_volume, day_value_mn)"""
    out = []
    for i, (src, sym, dt, bids, asks, tr, vol, val) in enumerate(rows):
        out.append({"source": src, "symbol": sym, "t_recv": T0 + timedelta(seconds=dt), "seq": i,
                    "epoch": "e", "body_sha256": f"h{src}{sym}{json.dumps(bids)}{json.dumps(asks)}{tr}",
                    "http_status": 200, "elapsed_ms": 300, "bid_levels": bids, "ask_levels": asks,
                    "n_bid_levels": len(bids), "n_ask_levels": len(asks), "ltp": (asks[0][0] if asks else None),
                    "open": 10.0, "high": 10.5, "low": 9.9, "close_published": 0.0, "yclose": 10.0,
                    "day_trades": tr, "day_volume": vol, "day_value_mn": val})
    df = pd.DataFrame(out)
    df["t_recv"] = pd.to_datetime(df["t_recv"], utc=True)
    df["dup_payload"] = df.groupby(["source", "symbol"])["body_sha256"].transform(lambda s: s.eq(s.shift()))
    return df


def test_book_stats_and_imbalance():
    st = book_stats([(10.0, 100), (9.9, 300)], [(10.1, 50), (10.2, 50), (10.3, 1000)], tick=0.1)
    assert st.best_bid == 10.0 and st.best_ask == 10.1 and st.spread_ticks == 1.0 and st.mid == 10.05
    assert st.bid_depth_top3 == 400 and st.ask_depth_top3 == 1100 and st.largest_wall_side == "ask"
    assert st.largest_wall_qty == 1000 and abs(st.largest_wall_share - 1000 / 1500) < 1e-9
    assert not st.crossed and not st.locked and not st.one_sided
    assert book_stats([(10.0, 1)], [(10.0, 1)]).locked
    assert book_stats([(10.1, 1)], [(10.0, 1)]).crossed
    assert book_stats([(10.1, 1)], []).one_sided and book_stats([], []).empty


def test_diff_levels_classifies_add_reduce_remove_and_sweep():
    ev = diff_levels([(10.1, 50), (10.2, 50), (10.3, 1000)], [(10.3, 900), (10.4, 20)], "ask", 10.1, 10.3)
    kinds = {(e["price"], e["kind"]) for e in ev}
    assert (10.1, "REMOVE") in kinds and (10.2, "REMOVE") in kinds and (10.3, "REDUCE") in kinds and (10.4, "ADD") in kinds
    by = {e["price"]: e for e in ev}
    assert by[10.1]["through"] and by[10.2]["through"] and by[10.1]["at_touch"] and not by[10.3]["through"]


def test_interval_tape_deltas_and_monotone_flag():
    t = pd.DataFrame({"symbol": ["X"] * 4, "row_index": range(4), "t_source_ms": [0, 60000, 120000, 180000],
                      "price": [10.0, 10.1, 10.1, 10.0], "cum_trades": [1, 3, 3, 2], "cum_volume": [100, 400, 400, 350],
                      "cum_value_mn": [0.001, 0.00403, 0.00403, 0.0035]})
    t["t_source"] = pd.to_datetime(t["t_source_ms"], unit="ms", utc=True)
    it = interval_tape(t)
    assert bool(it["first_row_of_day"].iloc[0]) and it["d_volume"].iloc[0] == 100   # day starts at zero
    assert it["d_trades"].tolist()[1] == 2 and it["d_volume"].tolist()[1] == 300
    assert abs(it["vwap"].iloc[1] - 10.1) < 1e-6
    assert bool(it["monotone_break"].iloc[3]) and not bool(it["monotone_break"].iloc[1])
    assert np.isnan(it["vwap"].iloc[2])  # no volume → no vwap, never 0


def test_classify_side_rules():
    assert classify_side(10.1, 10.0, 10.1)["side_score"] == 1.0
    assert classify_side(10.0, 10.0, 10.1)["side_score"] == -1.0
    mid = classify_side(10.05, 10.0, 10.1)
    assert abs(mid["side_score"]) < 1e-9 and mid["side_truth"] == "INFERRED"
    locked = classify_side(10.0, 10.0, 10.0)
    assert locked["side_truth"] == "OBSERVED" and locked["side_conf"] == "exact"
    assert classify_side(None, 10.0, 10.1)["side_truth"] == "NOT_OBSERVABLE"
    assert classify_side(10.05, 10.0, 10.1, touch_moved=True)["side_conf"] == "low"


def test_reconstruct_and_queue_events_split_by_interval_volume():
    rows = [
        ("lankabd_depth", "X", 0, [(10.0, 100), (9.9, 300)], [(10.1, 500), (10.2, 200)], 10, 1000, 0.01),
        ("lankabd_depth", "X", 15, [(10.0, 100), (9.9, 300)], [(10.1, 200), (10.2, 200)], 12, 1200, 0.01202),
        ("lankabd_depth", "X", 30, [(10.0, 100), (9.9, 300)], [(10.1, 450), (10.2, 200)], 12, 1200, 0.01202),
    ]
    b = _books(rows)
    r = reconstruct_books(b)
    assert r["imb_l1"].iloc[0] == (100 - 500) / 600
    ev = queue_events(r, interval_volume=pd.Series([np.nan, 200.0, 0.0], index=r.index))
    e1 = ev[ev.frame_idx == 1].iloc[0]
    assert e1["event_class"] == "TOUCH_REDUCED_MIXED" and e1["traded_est"] == 200 and e1["cancelled_est"] == 100
    e2 = ev[ev.frame_idx == 2].iloc[0]
    assert e2["event_class"] == "REFILL" and e2["side"] == "ask" and e2["dq"] == 250
    assert r["unchanged_run"].tolist() == [0, 0, 0]


def test_fuse_aligns_two_book_sensors_and_tape():
    rows = [
        ("lankabd_depth", "X", 0, [(10.0, 100)], [(10.1, 500)], 10, 1000, 0.01),
        ("dsebd_depth", "X", 1, [(10.0, 100)], [(10.1, 500)], 10, 1000, 0.01),
        ("lankabd_depth", "X", 15, [(10.0, 100)], [(10.1, 200)], 13, 1300, 0.01303),
        ("dsebd_depth", "X", 16, [(10.0, 100)], [(10.1, 190)], 13, 1300, 0.01303),
        ("dsebd_depth", "X", 40, [(10.0, 90)], [(10.1, 190)], 13, 1300, 0.01303),   # orphan → fallback frame
    ]
    books = _books(rows)
    tape = pd.DataFrame({"source": "lankabd_tape", "symbol": ["X", "X"], "row_index": [0, 1],
                         "t_source_ms": [int((T0 - timedelta(seconds=5)).timestamp() * 1000),
                                         int((T0 + timedelta(seconds=10)).timestamp() * 1000)],
                         "price": [10.1, 10.1], "cum_trades": [10, 13], "cum_volume": [1000, 1300],
                         "cum_value_mn": [0.01, 0.01303], "t_recv": [T0 + timedelta(seconds=20)] * 2, "seq": [0, 1]})
    tape["t_source"] = pd.to_datetime(tape["t_source_ms"], unit="ms", utc=True)
    tape["t_recv"] = pd.to_datetime(tape["t_recv"], utc=True)
    circuit = pd.DataFrame({"symbol": ["X"], "t_recv": [pd.Timestamp(T0)], "upper_limit": [11.0], "lower_limit": [9.0],
                            "tick_size": [0.1], "breaker_pct": [10.0]})
    f = fuse({"books": books, "tape": tape, "circuit": circuit, "watch": pd.DataFrame(), "market": pd.DataFrame(),
              "block": pd.DataFrame()})
    assert len(f) == 3 and f["book_primary"].tolist() == ["lankabd_depth", "lankabd_depth", "dsebd_depth"]
    assert f["book_agree"].tolist()[0] == 1.0 and f["book_agree"].tolist()[1] == 0.0 and f["book_diff"].tolist()[1] == 2
    assert f["tape_rows"].tolist()[1] == 1 and f["tape_d_volume"].tolist()[1] == 300
    assert abs(f["int_vwap"].iloc[1] - 10.1) < 1e-6 and f["side_score"].iloc[1] == 1.0
    assert f["int_source"].tolist()[1] == "exchange_stamped_tape" and f["int_source"].tolist()[2] == "snapshot_day_totals"
    assert f["shares_to_door"].iloc[0] == 500 and not bool(f["door_visible"].iloc[0])
    assert f["ev_ask_touch_consumed"].iloc[1] == 300 and f["upper_limit"].iloc[0] == 11.0
    assert "truth" in f.attrs and any("NOT_OBSERVABLE" in v for v in f.attrs["truth"].values())
