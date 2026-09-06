from seeing.capture.adapters import dsebd

SAMPLE = """
<html><body><table class="table">
<tr><th>#</th><th>Trading Code</th><th>LTP</th><th>High</th><th>Low</th><th>Close</th><th>YCP</th><th>Trade</th><th>Volume</th><th>Value (mn)</th></tr>
<tr><td>1</td><td>GP</td><td>320.5</td><td>325.0</td><td>318.0</td><td>0</td><td>319.0</td><td>1,234</td><td>56,000</td><td>17.9</td></tr>
<tr><td>2</td><td>BATBC</td><td>510.0</td><td>515.0</td><td>505.0</td><td>0</td><td>508.0</td><td>700</td><td>12,000</td><td>6.1</td></tr>
</table></body></html>
"""

ARCHIVE = """
<table><tr><th>#</th><th>DATE</th><th>TRADING CODE</th><th>LTP*</th><th>HIGH</th><th>LOW</th><th>OPENP*</th><th>CLOSEP*</th><th>YCP</th><th>TRADE</th><th>VALUE (mn)</th><th>VOLUME</th></tr>
<tr><td>1</td><td>2026-09-03</td><td>GP</td><td>244.1</td><td>245.4</td><td>244.0</td><td>245.4</td><td>244.1</td><td>244.5</td><td>641</td><td>10.603</td><td>43,402</td></tr>
</table>"""

DEPTH = """
<table><tr><th colspan=2>BUY</th></tr><tr><th>Price</th><th>Volume</th></tr>
<tr><td>62.30</td><td>400</td></tr><tr><td>62.40</td><td>50</td></tr></table>
<table><tr><th colspan=2>SELL</th></tr><tr><th>Price</th><th>Volume</th></tr>
<tr><td>62.60</td><td>1,200</td></tr><tr><td>62.50</td><td>10</td></tr></table>"""


def test_vendored_latest_parser_uses_ycp_not_closep():
    rows = dsebd.parse_latest_share_price(SAMPLE)
    by = {r["symbol"]: r for r in rows}
    assert by["GP"]["ltp"] == 320.5 and by["GP"]["yclose"] == 319.0 and by["GP"]["day_trades"] == 1234
    assert by["BATBC"]["day_volume"] == 12000 and by["BATBC"]["day_value_mn"] == 6.1


def test_vendored_archive_parser():
    rows = dsebd.parse_archive(ARCHIVE)
    assert rows[0]["trade_date"] == "2026-09-03" and rows[0]["close"] == 244.1 and rows[0]["day_volume"] == 43402


def test_index_series_parser():
    assert dsebd.parse_index_series("x 2026-09-02,5400.5\n2026-09-03,5390.1 0") == [("2026-09-02", 5400.5), ("2026-09-03", 5390.1)]


def test_load_instrument_parser_on_real_closed_market_payload():
    from tests.conftest import fixture
    from seeing.capture.http_client import PoliteClient
    a = dsebd.DSEBDDepthAdapter(PoliteClient())
    p = a.parse(fixture("dsebd_depth_GP_closed.html"), "GP")
    f = p.frames[0]
    assert f["symbol"] == "GP" and f["bid_levels"] == [] and f["ask_levels"] == []
    assert f["ltp"] == 244.1 and f["open"] == 245.4 and f["high"] == 245.4 and f["low"] == 244.0
    assert f["yclose"] == 244.5 and f["close_published"] == 244.1
    assert f["day_trades"] == 641 and f["day_volume"] == 43402 and f["day_value_mn"] == 10.603
    assert not [x for x in p.problems if "mismatch" in x]


def test_load_instrument_parser_levels():
    html = ('<table><tr><td colspan="2"><div align="center"><strong><font>Buy</font></strong></div></td></tr>'
            '<tr><td><div align="center">Buy Price </div></td><td><div align="center">Buy Volume </div></td></tr>'
            '<tr><td><div align="center">62.30</div></td><td><div align="center">1,000</div></td></tr>'
            '<tr><td><div align="center">62.40</div></td><td><div align="center">50</div></td></tr></table>'
            '<table><tr><td colspan="2"><div align="center"><strong><font>Sell</font></strong></div></td></tr>'
            '<tr><td><div align="center">62.60</div></td><td><div align="center">7</div></td></tr>'
            '<tr><td><div align="center">62.50</div></td><td><div align="center">9</div></td></tr></table>')
    r = dsebd.parse_load_instrument(html)
    assert r["bid_levels"] == [(62.4, 50.0), (62.3, 1000.0)] and r["ask_levels"] == [(62.5, 9.0), (62.6, 7.0)]
    assert r["bid_levels_src_order_preserved"] is False and r["ask_levels_src_order_preserved"] is False


def test_hts_parser_holidays_and_sessions():
    from tests.conftest import fixture
    r = dsebd.parse_hts(fixture("dsebd_hts_2026-09-06.html").decode("utf-8", "replace"))
    names = {h["name"]: h for h in r["holidays"]}
    assert "Janmashtami" in names and names["Janmashtami"]["date"] == "4 September"
    assert not any("6 September" in h["date"] for h in r["holidays"])
    pub = [s for s in r["sessions"] if s.get("Market", "").startswith("Public")][0]
    vals = list(pub.values())
    assert any("10:00 AM to 2:00 PM" in v for v in vals) and any("2:00 PM to 2:10 PM" in v for v in vals)


def test_generic_depth_page_parser_sorts_sides():
    bids, asks, problems = dsebd.parse_depth_page(DEPTH)
    assert bids == [(62.4, 50.0), (62.3, 400.0)] and asks == [(62.5, 10.0), (62.6, 1200.0)]
    assert not problems
    assert dsebd.parse_depth_page("<html></html>")[2]
