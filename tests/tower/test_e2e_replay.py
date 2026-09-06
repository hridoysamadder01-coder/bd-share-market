"""End-to-end: raw capture → normalize → engine → state store, twice, deterministically.

test_realdata_* use real captured DSE data: the committed closed-market fixture
(always) and the live session capture when it exists on this machine (skipped
otherwise, with the reason printed). Machinery tests use a synthetic store.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from seeing.capture.raw_store import RawStore
from tower.replay import Replayer, replay_hashes
from tower.store import read_states, read_timeline

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
LIVE = "/home/user/bd-share-market/evidence/capture/2026-09-06"


def _synthetic_store(root: str, n: int = 40) -> None:
    """A real-format raw store with a moving book, tape rows and a circuit reference."""
    st = RawStore(root, capturer_id="t", software_version="test")
    t0 = datetime(2026, 9, 6, 4, 0, tzinfo=timezone.utc)

    def depth_json(sym, bids, asks, trades, vol, val, ltp):
        def tbl(rows, side):
            head = f'<table><tr><td><div align="center">{side} Price </div></td><td><div align="center">{side} Volume </div></td></tr>'
            body = "".join(f'<tr><td><div align="center">{p:.2f}</div></td><td><div align="center">{int(q)}</div></td></tr>' for p, q in rows)
            return head + body + "</table>"
        return json.dumps({"symbol": sym, "buyPriceTable": tbl(bids, "Buy"), "sellPriceTable": tbl(asks, "Sell"),
                           "openPrice": 10.0, "lastTradePrice": ltp, "yesterdayClosePrice": 10.0, "closePrice": 0.0,
                           "daysHigh": 10.5, "daysLow": 9.9, "noOfTrade": trades, "totalVolume": vol,
                           "totalValueMN": val, "buyPercentage": 50.0, "sellPercentage": 50.0,
                           "totalBuyVolume": 0, "totalSellVolume": 0}).encode()

    circuit_html = ('<html><body><div>2026-09-06</div><table id="TableDataMatrixDSE"><tr><th>SL</th></tr>'
                    '<tr><td>1</td><td><a href="/x">SYN</a></td><td>Bank</td><td>10.00</td><td>0.10</td><td>10.00</td><td>9.00</td><td>11.00</td></tr>'
                    '</table></body></html>').encode()
    st.write_data("lankabd_circuit", key=None, body=circuit_html,
                  http={"status": 200, "t_last_byte_utc": t0.isoformat()})
    trades, vol, val = 10, 1000.0, 0.0102
    rows = []
    for i in range(n):
        t = t0 + timedelta(seconds=15 * i)
        press = min(i, 20)
        bids = [(10.0, 100 + 40 * press), (9.9, 300 + 20 * press), (9.8, 300)]
        asks = [(10.1, max(50, 500 - 25 * press)), (10.2, 200), (10.3, 800)]
        ltp = 10.1 if i < 25 else 10.2
        if i % 3 == 0:
            trades += 3; vol += 300; val += 0.00303
            rows.append([int(t.timestamp() * 1000), ltp, trades, vol, round(val, 5), ltp])
        body = depth_json("SYN", bids, asks, trades, vol, val, ltp)
        st.write_data("lankabd_depth", key="SYN", body=body, http={"status": 200, "t_last_byte_utc": t.isoformat()},
                      t_recv_utc=t.isoformat())
        st.write_data("dsebd_depth", key="SYN", body=_dse_html(bids, asks, ltp, trades, vol, val),
                      http={"status": 200, "t_last_byte_utc": (t + timedelta(seconds=1)).isoformat()},
                      t_recv_utc=(t + timedelta(seconds=1)).isoformat())
        if i % 10 == 9:
            st.write_data("lankabd_tape", key="SYN", body=json.dumps({"length": len(rows), "data": rows}).encode(),
                          http={"status": 200, "t_last_byte_utc": (t + timedelta(seconds=2)).isoformat()},
                          t_recv_utc=(t + timedelta(seconds=2)).isoformat())
    st.close()


def _dse_html(bids, asks, ltp, trades, vol, val):
    def tbl(rows, side):
        head = (f'<table><tr><td colspan="2"><div align="center"><strong><font>{side}</font></strong></div></td></tr>'
                f'<tr><td><div align="center">{side} Price </div></td><td><div align="center">{side} Volume </div></td></tr>')
        body = "".join(f'<tr><td><div align="center">{p:.2f}</div></td><td><div align="center">{int(q)}</div></td></tr>' for p, q in rows)
        return head + body + "</table>"
    stats = (f'<table><tr><td colspan="4"><strong>Price Statistics </strong></td></tr>'
             f'<tr><td>Open Price </td><td>: 10.0</td><td>Day\'s High : </td><td>10.5</td></tr>'
             f'<tr><td>Last Trade Price</td><td>: {ltp}</td><td>Day\'s Low : </td><td>9.9</td></tr>'
             f'<tr><td>Yesterday Close Price </td><td>: 10.0</td><td>No. of Trade : </td><td>{trades}</td></tr>'
             f'<tr><td>Close Price </td><td>: 0</td><td>Total Volume : </td><td>{int(vol)}</td></tr>'
             f'<tr><td>&nbsp;</td><td>&nbsp;</td><td>Total Value (mn): </td><td>{val:.3f}</td></tr></table>')
    return (f'<div>Instrument : <strong><a href="displayCompany.php?name=SYN">SYN</a></strong></div>'
            + tbl(bids, "Buy") + tbl(asks, "Sell") + stats).encode()


def test_machinery_replay_is_deterministic_and_states_are_populated(tmp_path):
    cap = str(tmp_path / "cap")
    _synthetic_store(cap)
    h1 = replay_hashes(cap, str(tmp_path / "out1"))
    h2 = replay_hashes(cap, str(tmp_path / "out2"))
    assert h1 == h2 and "SYN" in h1
    rows = read_states(str(tmp_path / "out1"), "SYN")
    assert len(rows) >= 40
    last = rows[-1]
    assert last["best_bid"] == 10.0 and last["best_ask"] == 10.1 and last["spread_ticks"] == 1.0
    # planted bid pressure: bids 900+700+300 vs asks 50+200+800 → (1900−1050)/2950 = 0.288
    assert last["imb_topk"] is not None and abs(last["imb_topk"] - 850 / 2950) < 1e-6
    assert last["circuit"]["upper_limit"] == 11.0 and last["circuit"]["dist_up_ticks"] is not None
    assert last["book_source"] in ("lankabd_depth", "dsebd_depth")
    assert "lankabd_depth" in last["sources"] and "dsebd_depth" in last["sources"]
    assert last["sources"]["lankabd_depth"]["freshness_s"] is not None
    assert last["source_agreement"].get("book") is True                          # two sensors identical
    # mechanisms are computed, not constant: scores move with the scenario (rise during the build, fall at the flat tail)
    assert last["mechanisms"]
    max_scores = {}
    for r_ in rows:
        for n, m in r_["mechanisms"].items():
            max_scores[n] = max(max_scores.get(n, 0.0), m["score"])
    assert any(v > 0.3 for v in max_scores.values()), max_scores
    assert any(m["score"] == 0.0 for m in last["mechanisms"].values())
    assert last["layer_states"]["pressure"] in ("pressure_building", "expansion", "rejection", "reversal", "balanced")
    assert last["trade_count"] is not None and last["interval_volume"] is not None
    tl = read_timeline(str(tmp_path / "out1"))
    assert tl and any(t["layer"] == "pressure" for t in tl)
    run = json.load(open(tmp_path / "out1" / "RUN.json"))
    assert run["final_state_hash"]["SYN"] == h1["SYN"] and run["segments"]
    # symbol and time filters
    r = Replayer(cap, str(tmp_path / "out3"), symbols=["NOPE"])
    assert r.load() == 0 or all(e.symbol != "SYN" for e in r.events if e.symbol)
    r2 = Replayer(cap, str(tmp_path / "out4"), t_from="2026-09-06T04:05:00+00:00", t_to="2026-09-06T04:06:00+00:00")
    r2.load()
    assert all("2026-09-06T04:05" <= e.t_recv.isoformat()[:16] <= "2026-09-06T04:06" for e in r2.events)


def test_machinery_step_pause_resume(tmp_path):
    cap = str(tmp_path / "cap")
    _synthetic_store(cap, n=6)
    r = Replayer(cap, str(tmp_path / "out"))
    r.load()
    first = r.step()
    assert r.pos == 1
    r.pause()
    assert not r._pause.is_set()
    r.resume()
    r.run()
    assert r.pos == len(r.events)


def test_realdata_fixture_replay_closed_market(tmp_path):
    h = replay_hashes(FIXTURE, str(tmp_path / "out"))
    assert h, "no symbols replayed from the real fixture"
    for sym in h:
        rows = read_states(str(tmp_path / "out"), sym)
        assert rows
        # market was closed: no two-sided book anywhere (some symbols carry resting one-sided orders)
        assert all(r["spread"] is None and r["mid"] is None for r in rows)
        for r in rows:
            if r["empty_book"]:
                assert r["imb_l1"] is None                                       # NOT_OBSERVABLE, never 0
            else:
                assert r["one_sided"] and r["imb_l1"] in (1.0, -1.0)
        assert rows[-1]["trade_count"] is not None                           # day totals were observed
        assert rows[-1]["sources"]
    assert replay_hashes(FIXTURE, str(tmp_path / "out2")) == h


def _live_has_market_data() -> bool:
    import glob
    return bool(glob.glob(os.path.join(LIVE, "segments", "lankabd_depth*")) or
                glob.glob(os.path.join(LIVE, "segments", "dsebd_depth*")))


@pytest.mark.skipif(not _live_has_market_data(), reason="live session capture has no depth segments yet (market not open)")
def test_realdata_live_session_capture(tmp_path):
    r = Replayer(LIVE, str(tmp_path / "out"))
    n = r.load()
    assert n > 0
    r.run()
    syms = list(r.store.hashes)
    assert syms, "no symbol states from the live capture"
    nonempty = 0
    for sym in syms:
        rows = read_states(str(tmp_path / "out"), sym)
        nonempty += sum(1 for x in rows if not x["empty_book"])
    print(f"live capture: {n} events, {len(syms)} symbols, {nonempty} non-empty book states")
    assert r.engine.metrics_snapshot()["reconstruction_failures"] == 0
