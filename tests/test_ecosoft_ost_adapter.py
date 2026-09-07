"""EcoSoft OST broker-terminal adapter + HAR probe, on the real (sanitized) payloads
recorded by the account holder on 2026-09-07 (market closed)."""
import json
import os

from seeing.capture import har_probe
from seeing.capture.adapters import ecosoft_ost as eo
from seeing.truth import Truth

from .conftest import ROOT, fixture

DEPTH = "ecosoft_ost_marketdepth_CITYGENINS_closed.json"
INDEX = "ecosoft_ost_indexdata_DSEX_closed.json"
EVIDENCE = os.path.join(ROOT, "evidence", "ecosoft_ost", "rich_sensor_probe_2026-09-07.ndjson")


def test_depth_fixture_parses_ten_paired_rows():
    out = eo.EcoSoftOSTDepthAdapter().parse(fixture(DEPTH))
    assert out.problems == []
    (f,) = out.frames
    assert f["symbol"] == "CITYGENINS" and f["stock_exchange_code"] == 1 and f["is_block"] is False
    assert f["book_depth_count"] == 10 and f["n_bid_levels"] == 10 and f["n_ask_levels"] == 7
    assert f["bid_levels"][0] == (128.1, 914.0) and f["ask_levels"][0] == (128.3, 949.0)
    assert f["best_bid"] == 128.1 and f["best_ask"] == 128.3 and f["src_order_preserved"]
    assert f["bid_levels"] == sorted(f["bid_levels"], key=lambda x: -x[0])
    assert f["ask_levels"] == sorted(f["ask_levels"], key=lambda x: x[0])
    assert f["ltp"] == 128.3 and f["open"] == 126.0 and f["high"] == 128.4 and f["low"] == 126.0
    assert f["close_published"] == 128.2 and f["yclose"] == 127.6
    assert f["day_trades"] == 212 and f["day_volume"] == 112750 and f["day_value_mn"] == 14.376
    assert 0.9 < f["day_value_unit_check_ratio"] < 1.1          # million-BDT inference holds on this recording
    assert f["feed_source_tag"] == "DWS" and f["zero_fields"] == []


def test_depth_never_invents_missing_fields():
    out = eo.EcoSoftOSTDepthAdapter().parse(fixture(DEPTH))
    (f,) = out.frames
    assert f["bid_orders_per_level"] is None and f["ask_orders_per_level"] is None and f["sequence_id"] is None
    t = out.truth
    for k in ("bid_levels", "ask_levels", "best_bid", "best_ask", "ltp", "day_value", "t_source", "t_recv"):
        assert t[k] is Truth.OBSERVED
    for k in ("bid_orders_per_level", "ask_orders_per_level", "trade_prints", "trade_side", "order_events",
              "queue_position", "level_quantity_delta", "cancel_vs_trade_split"):
        assert t[k] is Truth.NOT_OBSERVABLE


def test_depth_timestamp_convention_is_left_unresolved():
    (f,) = eo.EcoSoftOSTDepthAdapter().parse(fixture(DEPTH)).frames
    assert f["t_source_str"] == "2026-09-07T14:13:45.1505564Z" and f["t_source_suffix"] == "Z"
    assert f["t_source_utc"] is None and f["t_source_tz_convention"] == "UNRESOLVED"
    c = f["t_source_utc_candidates"]
    assert c["as_stamped_utc"] == "2026-09-07T14:13:45.150556+00:00"
    assert c["as_dhaka_wall_clock"] == "2026-09-07T08:13:45.150556+00:00"


def test_dotnet_seven_digit_fraction_and_bad_stamps():
    naive, suf = eo.parse_dotnet_iso("2026-09-07T14:13:45.1505564Z")
    assert naive.microsecond == 150556 and suf == "Z"
    assert eo.parse_dotnet_iso("2026-09-07T00:00:00") == (eo.parse_dotnet_iso("2026-09-07T00:00:00")[0], None)
    assert eo.parse_dotnet_iso("not a time") == (None, None)
    assert eo.parse_dotnet_iso(None) == (None, None)
    assert eo.source_time_candidates("garbage")["t_source_utc_candidates"] == {}


def test_buy_sell_rows_one_sided_and_malformed():
    rows = [{"BuyPrice": 10.0, "BuyVolume": 5, "SellPrice": 10.2, "SellVolume": 7},
            {"SellPrice": 10.3, "SellVolume": 1},                      # ask-only row
            {"BuyPrice": 9.9},                                         # price without volume → None, never 0
            {"BuyPrice": 9.8, "BuyVolume": 2, "Orders": 3},            # unexpected key → reported, kept
            "junk", {}]
    bids, asks, problems = eo.parse_buy_sell_details(rows)
    assert bids == [(10.0, 5.0), (9.9, None), (9.8, 2.0)] and asks == [(10.2, 7.0), (10.3, 1.0)]
    assert any("unexpected keys ['Orders']" in p for p in problems)
    assert any("row 4 is str" in p for p in problems) and any("neither BuyPrice nor SellPrice" in p for p in problems)


def test_depth_flags_crossed_book_and_unit_break():
    d = json.loads(fixture(DEPTH))
    d["Depth"]["BuySellDetails"] = [{"BuyPrice": 130.0, "BuyVolume": 1, "SellPrice": 129.0, "SellVolume": 1}]
    d["Depth"]["Value"] = 1437.6                                       # ×100 the observed value
    out = eo.EcoSoftOSTDepthAdapter().parse(json.dumps(d).encode())
    assert any("crossed/locked" in p for p in out.problems)
    assert any("unit inference does not hold" in p for p in out.problems)


def test_depth_bad_bodies_are_problems_not_crashes():
    ad = eo.EcoSoftOSTDepthAdapter()
    assert ad.parse(b"not json").problems and ad.parse(b"[1,2]").problems
    assert ad.parse(b'{"Success": false}').problems == ["Success=false", "no Depth object"]
    assert ad.fetch("CITYGENINS").ok is False and "never fetches" in ad.fetch().error


def test_depth_strips_a_sensitive_key_that_slipped_through():
    d = json.loads(fixture(DEPTH))
    d["Depth"]["ClientId"] = "should-not-survive"
    d["AccessToken"] = "nor-this"
    out = eo.EcoSoftOSTDepthAdapter().parse(json.dumps(d).encode())
    assert any("sensitive keys stripped" in p for p in out.problems)
    (f,) = out.frames
    assert "ClientId" not in f["raw_keys"] and "should-not-survive" not in json.dumps(f)


def test_index_fixture_parses():
    out = eo.EcoSoftOSTIndexAdapter().parse(fixture(INDEX))
    assert out.problems == []
    (f,) = out.frames
    assert f["index_code"] == "DSEX" and f["index_ltp"] == 5567.42 and f["index_yclose"] == 5558.45
    assert f["gainers"] == 168 and f["losers"] == 146 and f["unchanged"] == 63
    assert f["market_trades"] == 185987 and f["market_volume"] == 200350191
    assert f["market_value_crore"] == 571.32463 and abs(f["market_value_mn"] - 5713.2463) < 1e-6
    assert f["date_str"] == "2026-09-07T00:00:00" and f["is_sector"] is False
    assert out.truth["market_trades"] is Truth.OBSERVED and out.truth["bid_levels"] is Truth.NOT_OBSERVABLE


def test_route_by_path():
    assert eo.route("https://ost.ecosoftbd.com/core/api/v1/Order/MarketDepth").name == "ecosoft_ost_depth"
    assert eo.route("https://ost.ecosoftbd.com/core/api/v1/Analysis/IndexData?x=1").name == "ecosoft_ost_index"
    assert eo.route("https://ost.ecosoftbd.com/core/api/v1/Account/Login") is None


def test_probe_file_roundtrip(tmp_path):
    p = tmp_path / "rich_sensor_probe.ndjson"
    recs = [{"t_recv_utc": "2026-09-07T14:22:14.127Z", "url": "https://ost.ecosoftbd.com" + eo.DEPTH_PATH,
             "status": 200, "transport": "xhr_or_fetch", "body_sha256": "a" * 64, "payload": json.loads(fixture(DEPTH))},
            {"t_recv_utc": "2026-09-07T14:22:50.084Z", "url": "https://ost.ecosoftbd.com" + eo.INDEX_PATH,
             "status": 200, "transport": "xhr_or_fetch", "body_sha256": "b" * 64, "payload": json.loads(fixture(INDEX))},
            {"t_recv_utc": "2026-09-07T14:22:51.000Z", "url": "https://ost.ecosoftbd.com/core/api/v1/Other/Thing",
             "status": 200, "transport": "xhr_or_fetch", "body_sha256": "c" * 64, "payload": {"Price": 1}}]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n{bad json\n")
    r = eo.frames_from_probe(str(p))
    assert r["records"] == 4 and len(r["frames"]) == 2
    assert r["unrouted"] == {"https://ost.ecosoftbd.com/core/api/v1/Other/Thing": 1}
    assert any("line 4" in x for x in r["problems"])
    d, i = r["frames"]
    assert d["source"] == "ecosoft_ost_depth" and d["t_recv_utc"] == "2026-09-07T14:22:14.127Z" and d["body_sha256"] == "a" * 64
    assert i["source"] == "ecosoft_ost_index" and i["http_status"] == 200


def test_real_recording_replays_completely():
    """The committed sanitized recording: every record routes, nothing leaks, and the
    closed-market book is one static snapshot (48/48 duplicates by body hash)."""
    if not os.path.exists(EVIDENCE):
        import pytest
        pytest.skip("evidence file not present")
    r = eo.frames_from_probe(EVIDENCE)
    assert r["records"] == 58 and len(r["frames"]) == 58 and r["unrouted"] == {} and r["problems"] == []
    depth = [f for f in r["frames"] if f["source"] == "ecosoft_ost_depth"]
    index = [f for f in r["frames"] if f["source"] == "ecosoft_ost_index"]
    assert len(depth) == 49 and len(index) == 9
    assert {f["symbol"] for f in depth} == {"CITYGENINS"} and len({f["body_sha256"] for f in depth}) == 1
    assert all(f["t_source_utc"] is None for f in depth)          # convention unresolved on a closed-market recording
    recs = list(eo.iter_probe_records(EVIDENCE))
    assert har_probe.leaked_keys(recs) == []
    assert sum(x["sensitive_keys_removed"] for x in recs) == 0


# ---------------------------------------------------------------------- probe (HAR → NDJSON)
def _har(entries):
    return {"log": {"entries": entries}}


def _entry(url, body, method="GET", started="2026-09-07T14:22:14.127Z", post=None, ws=None):
    e = {"startedDateTime": started, "request": {"method": method, "url": url,
                                                 "headers": [{"name": "Cookie", "value": "SECRET=1"}]},
         "response": {"status": 200, "headers": [{"name": "Set-Cookie", "value": "x=y"}],
                      "content": {"mimeType": "application/json", "text": body}}}
    if post is not None:
        e["request"]["postData"] = {"text": post}
    if ws is not None:
        e["_webSocketMessages"] = ws
    return e


def test_probe_skips_login_strips_keys_and_never_reads_headers():
    depth = json.loads(fixture(DEPTH))
    depth["Depth"]["Token"] = "tok"                      # planted inside a market payload
    depth["Account"] = {"BoId": "123", "Balance": 9}
    har = _har([
        _entry("https://ost.ecosoftbd.com/core/api/v1/Account/Login?u=1", '{"token":"abc","price":1,"volume":2}',
               method="POST", post='{"password":"p"}'),
        _entry("https://ost.ecosoftbd.com/core/api/v1/Order/MarketDepth?code=CITYGENINS&t=9", json.dumps(depth)),
        _entry("https://ost.ecosoftbd.com/static/app.js", "var x = 1;"),
        _entry("https://ost.ecosoftbd.com/core/api/v1/Analysis/IndexData", fixture(INDEX).decode(),
               ws=[{"type": "receive", "data": "{}"}]),
    ])
    recs, summary = har_probe.probe_har(har)
    assert summary["entries"] == 4 and summary["kept"] == 2 and summary["websocket_frames_in_har"] == 1
    assert summary["skipped"] == {"sensitive_url": 1, "not_market": 1}
    assert [r["url"] for r in recs] == ["https://ost.ecosoftbd.com/core/api/v1/Order/MarketDepth",
                                        "https://ost.ecosoftbd.com/core/api/v1/Analysis/IndexData"]
    d = recs[0]
    assert d["sensitive_keys_removed"] == 2 and "Token" not in d["payload"]["Depth"] and "Account" not in d["payload"]
    assert d["has_post_body"] is False and "?" not in d["url"]
    blob = json.dumps(recs)
    assert "SECRET" not in blob and "Set-Cookie" not in blob and "tok" not in json.dumps(d["payload"])
    assert har_probe.leaked_keys(recs) == []
    assert summary["endpoints"][0]["n"] == 1 and summary["sensitive_keys_removed_total"] == 2


def test_probe_text_bodies_need_three_market_hints():
    recs, s = har_probe.probe_har(_har([
        _entry("https://x/market/feed", "bid 1 ask 2 depth 3 price 4"),
        _entry("https://x/market/feed2", "hello world"),
        _entry("https://x/market/feed3", "{}"),
    ]))
    assert s["kept"] == 1 and recs[0]["format"] == "text" and recs[0]["payload"] == {"raw_text": "bid 1 ask 2 depth 3 price 4"}
    assert s["skipped"] == {"not_market": 2}


def test_probe_never_keeps_pages_or_static_assets():
    """A logged-in HTML page and JS/CSS bundles carry plenty of market words; they are
    not market-data endpoints and can carry the account holder's details."""
    page = "<html>bid ask depth price volume ltp trade <span>BoAccountId 1</span></html>"
    js = "var bid=1,ask=2,depth=3,price=4,volume=5,ltp=6,trade=7;"
    e_page = _entry("https://ost.example/Order", page)
    e_page["response"]["content"]["mimeType"] = "text/html"
    e_js = _entry("https://ost.example/Scripts/Js/vendor", js)
    e_js["response"]["content"]["mimeType"] = "application/javascript"
    e_feed_html = _entry("https://ost.example/market/feed", js)             # market URL but a script body
    e_feed_html["response"]["content"]["mimeType"] = "text/javascript"
    e_feed_txt = _entry("https://ost.example/market/feed", js)              # market URL, plain text → kept
    e_feed_txt["response"]["content"]["mimeType"] = "text/plain"
    recs, s = har_probe.probe_har(_har([e_page, e_js, e_feed_html, e_feed_txt]))
    assert s["kept"] == 1 and recs[0]["url"] == "https://ost.example/market/feed" and recs[0]["format"] == "text"
    assert s["skipped"] == {"text_not_market_endpoint": 3}
    assert "BoAccountId" not in json.dumps(recs)


def test_probe_cli_refuses_to_write_on_leak(tmp_path, monkeypatch, capsys):
    har = tmp_path / "s.har"
    har.write_text(json.dumps(_har([_entry("https://x/market", '{"price":1,"volume":2}')])))
    out = tmp_path / "o.ndjson"
    monkeypatch.setattr(har_probe, "leaked_keys", lambda recs: ["rec0.payload.Token"])
    assert har_probe.main(["prog", str(har), str(out)]) == 1 and not out.exists()
    assert "REFUSING" in capsys.readouterr().out
    monkeypatch.setattr(har_probe, "leaked_keys", lambda recs: [])
    assert har_probe.main(["prog", str(har), str(out)]) == 0 and out.exists()
    assert har_probe.main(["prog"]) == 2
