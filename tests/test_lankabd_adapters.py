import json

from seeing.capture.adapters import lankabd as lb
from seeing.capture.http_client import PoliteClient
from seeing.truth import Truth
from tests.conftest import fixture


def _adapters():
    return lb.build_adapters(PoliteClient())


def test_depth_parse_after_close_bracbank():
    a = _adapters()["depth"]
    p = a.parse(fixture("lankabd_depth_BRACBANK_closed.json"), "BRACBANK")
    assert not p.problems
    f = p.frames[0]
    assert f["symbol"] == "BRACBANK"
    assert f["bid_levels"] == [(62.4, 50.0), (62.3, 400.0)]
    assert f["ask_levels"] == []
    assert f["src_order_preserved"] is True
    assert f["ltp"] == 62.5 and f["close_published"] == 62.5 and f["yclose"] == 62.3
    assert f["day_trades"] == 1275 and f["day_volume"] == 1323138 and f["day_value_mn"] == 82.341
    assert p.truth["bid_levels"] is Truth.OBSERVED
    assert p.truth["bid_orders_per_level"] is Truth.NOT_OBSERVABLE
    assert p.truth["trade_prints"] is Truth.NOT_OBSERVABLE


def test_depth_parse_empty_book_gp():
    p = _adapters()["depth"].parse(fixture("lankabd_depth_GP_closed.json"), "GP")
    f = p.frames[0]
    assert f["bid_levels"] == [] and f["ask_levels"] == []
    assert f["ltp"] == 244.1 and f["open"] == 245.4 and f["high"] == 245.4 and f["low"] == 244.0


def test_depth_table_sorting_and_flag():
    html = ('<table><tr><td><div>Sell Price</div></td><td><div>Sell Volume</div></td></tr>'
            '<tr><td><div>10.20</div></td><td><div>1,000</div></td></tr>'
            '<tr><td><div>10.10</div></td><td><div>500</div></td></tr></table>')
    body = json.dumps({"symbol": "X", "buyPriceTable": "", "sellPriceTable": html}).encode()
    f = _adapters()["depth"].parse(body, "X").frames[0]
    assert f["ask_levels"] == [(10.1, 500.0), (10.2, 1000.0)]
    assert f["src_order_preserved"] is False


def test_watch_parse():
    p = _adapters()["watch"].parse(fixture("lankabd_watch_12_2026-09-03.json"))
    assert not p.problems and len(p.frames) == 12
    b = {f["symbol"]: f for f in p.frames}["BRACBANK"]
    assert b["t_source_str"] == "2026-09-03 14:09:55"
    assert b["t_source_utc"] == "2026-09-03T08:09:55+00:00"
    assert b["ltp"] == 62.8 and b["day_trades"] == 1139 and b["market_category"] == "A-EQ"
    assert b["feed_timestamp_utc"] == "2026-09-03T08:29:04+00:00"
    assert p.truth["t_source"] is Truth.OBSERVED and p.truth["bid_levels"] is Truth.NOT_OBSERVABLE


def test_tape_parse_cumulative_rows():
    a = _adapters()["tape"]
    p = a.parse(fixture("lankabd_tape_BRACBANK_2026-09-03.json"), "BRACBANK")
    assert not p.problems and len(p.frames) == 221
    first, last = p.frames[0], p.frames[-1]
    assert first["t_source_utc"].startswith("2026-09-03T04:03:43")
    assert last["cum_trades"] == 1139 and last["cum_volume"] == 1043807 and last["price"] == 62.8
    cum = [f["cum_trades"] for f in p.frames]
    assert cum == sorted(cum), "cumulative trades must be monotone"
    assert p.truth["interval_trades"] is Truth.INFERRED and p.truth["trade_prints"] is Truth.NOT_OBSERVABLE


def test_market_and_block_parse():
    m = _adapters()["market"].parse(fixture("lankabd_market_2026-09-03.json")).frames[0]
    assert m["market_trades"] == 197767 and m["t_source_utc"] == "2026-09-03T08:10:00+00:00"
    b = _adapters()["block"].parse(fixture("lankabd_block_2026-09-03.json"))
    assert len(b.frames) == 29 and b.frames[0]["symbol"] == "SHARPIND" and b.frames[0]["block_quantity"] == 20000


def test_circuit_parse():
    p = _adapters()["circuit"].parse(fixture("lankabd_circuit_head.html"))
    assert not p.problems and len(p.frames) >= 30
    r = p.frames[0]
    assert r["symbol"] == "1JANATAMF" and r["breaker_pct"] == 10.0 and r["tick_size"] == 0.1
    assert r["lower_limit"] == 3.3 and r["upper_limit"] == 3.9 and r["reference_date"] == "2026-09-03"


def test_cid_map():
    m = lb.parse_cid_map(fixture("lankabd_minutechart_select.html").decode())
    assert m["GP"] == 160 and m["BRACBANK"] == 15 and len(m) > 600


def test_universe_selection_is_deterministic():
    from seeing.capture.universe import select_universe
    frames = _adapters()["watch"].parse(fixture("lankabd_watch_12_2026-09-03.json")).frames
    a = select_universe(frames, n_top=3, n_mid=2, seed=7)
    b = select_universe(frames, n_top=3, n_mid=2, seed=7)
    assert a == b and len(a["symbols"]) == 5 and a["top"][0] == "MALEKSPIN"
    assert all(s not in a["top"] for s in a["mid"])
