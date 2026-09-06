"""collector.dse_public_collector — parsers on real archived public payloads.

Every expected value below was read off the live page on 2026-09-06 and is asserted
exactly: a parser that starts inventing or dropping fields fails here.
"""
import os

from collector.dse_public_collector import num, parse_company_page
from seeing.capture.adapters import dsebd

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
COMPANY = open(os.path.join(FIXTURES, "dsebd_company_BRACBANK_2026-09-06.html"), encoding="utf-8").read()


def test_num_keeps_zero_and_rejects_placeholders():
    assert num("1,234.5") == 1234.5 and num("0") == 0.0 and num(-3) == -3.0
    for blank in ("", "-", "--", "N/A", None, "*"):
        assert num(blank) is None


def test_company_page_fundamentals_are_read_exactly():
    c = parse_company_page(COMPANY, "BRACBANK")
    i = c["info"]
    assert not c["problems"]
    assert i["symbol"] == "BRACBANK" and i["sector"] == "Bank" and i["market_category"] == "A"
    assert i["instrument_type"] == "Equity" and i["electronic_share"] == "Y" and i["listing_year"] == 2007.0
    assert i["market_cap_mn"] == 143784.688 and i["free_float_market_cap_mn"] == 66964.820
    assert i["outstanding_securities"] == 2289565092.0 and i["face_value"] == 10.0 and i["market_lot"] == 1.0
    assert i["authorized_capital_mn"] == 50000.0 and i["paid_up_capital_mn"] == 22895.65
    assert i["last_trading_price"] == 62.4 and i["yclose"] == 62.8 and i["open"] == 62.2
    assert i["day_trades"] == 1168.0 and i["day_volume"] == 913660.0 and i["day_value_mn"] == 57.04
    assert i["days_range"] == "62.20 - 62.80" and i["week52_range"] == "60.00 - 89.60"
    # free float share of market cap is derived here, and says so by being absent when a term is missing
    assert i["free_float_pct_of_mcap"] == 46.573


def test_company_page_pe_blocks_stay_separate():
    """The page publishes an audited and an un-audited P/E for the same dates; they differ and
    are kept apart (collapsing them would publish a number the exchange never printed)."""
    c = parse_company_page(COMPANY, "BRACBANK")
    i = c["info"]
    assert i["pe_as_of"] == "Sep 06, 2026"
    assert i["pe_basic_audited"] == 6.84 and i["pe_basic_unaudited"] == 6.15
    assert i["pe_diluted_audited"] == 7.87 and i["pe_diluted_unaudited"] is None      # printed as '-'
    assert i["pe_trailing_unaudited"] == 5.87
    bases = {r["basis"] for r in c["pe"]}
    assert bases == {"audited", "unaudited"}
    assert len({r["as_of"] for r in c["pe"]}) == 6                                     # six dated columns


def test_company_page_financials_and_shareholding():
    c = parse_company_page(COMPANY, "BRACBANK")
    years = {r["year"]: r for r in c["financials"]}
    assert set(years) == {2021, 2022, 2023, 2024, 2025}
    assert years[2025]["eps_basic"] == 6.92 and years[2025]["dividend_yield_pct"] == 2.38
    assert years[2025]["dividend_raw"] == "15.00, 15%B"
    assert years[2021]["eps_basic"] == 14.09
    h = {r["as_on"]: r for r in c["holdings"]}
    assert "Aug 31, 2026" in h and len(h) == 3
    last = h["Aug 31, 2026"]
    assert last["sponsor_director_pct"] == 46.17 and last["govt_pct"] == 0.0
    assert last["institute_pct"] == 13.04 and last["foreign_pct"] == 32.39 and last["public_pct"] == 8.40
    assert round(sum(v for k, v in last.items() if k.endswith("_pct")), 2) == 100.00


def test_company_page_of_an_unknown_shape_reports_a_problem_not_a_guess():
    c = parse_company_page("<html><body><table><tr><td>nothing</td></tr></table></body></html>", "XYZ")
    assert c["problems"] and c["info"] == {"symbol": "XYZ"}
    assert c["financials"] == [] and c["pe"] == [] and c["holdings"] == []


def test_archive_parser_reads_the_trading_code_of_the_all_instrument_archive():
    html = ("<table><tr><th>#</th><th>DATE</th><th>TRADING CODE</th><th>LTP*</th><th>HIGH</th><th>LOW</th>"
            "<th>OPENP*</th><th>CLOSEP*</th><th>YCP*</th><th>TRADE</th><th>VALUE (mn)</th><th>VOLUME</th></tr>"
            "<tr><td>1</td><td>2026-09-03</td><td>GP</td><td>320.5</td><td>322</td><td>319</td><td>320</td>"
            "<td>320.4</td><td>319.0</td><td>1,234</td><td>12.5</td><td>39,000</td></tr>"
            "<tr><td>2</td><td>2026-09-03</td><td>BATBC</td><td>240</td><td>241</td><td>238</td><td>239</td>"
            "<td>240.1</td><td>239.5</td><td>456</td><td>6.1</td><td>12,000</td></tr></table>")
    rows = dsebd.parse_archive(html)
    by = {r["symbol"]: r for r in rows}
    assert set(by) == {"GP", "BATBC"}
    assert by["GP"]["close"] == 320.4 and by["GP"]["day_trades"] == 1234 and by["GP"]["day_volume"] == 39000
    assert by["BATBC"]["day_value_mn"] == 6.1 and by["BATBC"]["trade_date"] == "2026-09-03"


def test_market_statistics_report_is_parsed_from_the_official_text():
    """market-statistics.php is a plain-text report, not a table: breadth per category, day
    totals, market capitalisation by instrument class and the block board are all read from it."""
    from collector.dse_public_collector import parse_market_statistics
    html = open(os.path.join(FIXTURES, "dsebd_market_statistics_2026-09-06.html"), encoding="utf-8").read()
    ms = parse_market_statistics(html)
    assert ms["report_date"] == "2026-09-06" and ms["block_date"] == "2026-09-06"
    by = {r["category"]: r for r in ms["breadth"]}
    assert by["All Category"] == {"category": "All Category", "advanced": 20, "declined": 348,
                                  "unchanged": 21, "total_traded": 389}
    assert by["A Category"]["declined"] == 168 and by["Z Category"]["total_traded"] == 118
    assert set(by) >= {"All Category", "A Category", "B Category", "N Category", "Z Category"}
    assert ms["day_trades"] == 178150.0 and ms["day_volume"] == 204341869.0
    assert ms["day_value_tk"] == 5452768307.80
    assert ms["mcap_equity_tk"] == 3456850605183.20 and ms["mcap_debt_tk"] == 3430092389399.60
    assert ms["mcap_total_tk"] == 6917160139876.50
    blk = {r["symbol"]: r for r in ms["block"]}
    assert len(blk) == 35 and blk["SAIHAMTEX"]["quantity"] == 941523.0
    assert blk["SAIHAMTEX"]["max_price"] == 35.2 and blk["SAIHAMTEX"]["min_price"] == 31.0
    assert blk["ACMEPL"]["value_mn"] == 12.0 and blk["ACMEPL"]["trades"] == 1


def test_page_table_picks_the_data_table_not_the_ticker():
    from collector.dse_public_collector import parse_page_table
    html = ("<table><tr><td>TICK 1.00 +1%</td></tr><tr><td>TICK2 2.00</td></tr><tr><td>T3 3</td></tr></table>"
            "<table><tr><th>Code</th><th>Limit</th><th>Tick</th></tr>"
            "<tr><td>GP</td><td>10</td><td>0.1</td></tr><tr><td>BATBC</td><td>20</td><td>0.1</td></tr>"
            "<tr><td>ACI</td><td>30</td><td>0.1</td></tr></table>")
    head, rows = parse_page_table(html)
    assert head == ["Code", "Limit", "Tick"]
    assert [r[0] for r in rows] == ["GP", "BATBC", "ACI"]
