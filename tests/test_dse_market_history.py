"""The rolling market history dsebd.org gives away for free.

Found by checking a broker terminal's header ribbon against the public site. The
ribbon read `DSEX: 5539.32 | Tr: 168,534 | Vol: 191,268,628 | Val: 590.44 cr`, and
this page's row for the same session carries exactly those figures — plus total
market capitalisation and the DSES and DS30 indices, which no other wired source
publishes at all.

The window rolls (~30 sessions) and DSE keeps no archive behind it, so the panel
only exists if it is polled. That makes the parser's job narrow and important: get
every dated row, never invent one, and never confuse the 397-row scrolling ticker
at the top of the page for the data.
"""
import os

import pytest

from seeing.capture.adapters.bd_public import DSEMarketHistoryAdapter

FIX = os.path.join(os.path.dirname(__file__), "fixtures",
                   "dse_recent_market_information_2026-09-08.html")


@pytest.fixture(scope="module")
def parsed():
    with open(FIX, "rb") as fh:
        return DSEMarketHistoryAdapter(None).parse(fh.read())


def test_every_session_on_the_page_is_read(parsed):
    assert parsed.problems == []
    assert len(parsed.frames) == 30
    assert parsed.frames[0]["sessions_on_page"] == 30


def test_the_header_is_fully_matched_and_recorded(parsed):
    f = parsed.frames[0]
    assert f["columns_missing"] == [], "a dropped column is silent data loss"
    assert f["header_seen"][0] == "Date"
    assert "Total Market Cap" in f["header_seen"][4]


def test_the_top_row_matches_the_terminal_ribbon_for_the_same_session(parsed):
    """The whole reason this source was looked for. Same numbers, no account."""
    f = parsed.frames[0]
    assert f["date"] == "2026-09-08"
    assert f["total_trade"] == 168534          # ribbon: Tr: 168,534
    assert f["total_volume"] == 191268628      # ribbon: Vol: 191,268,628
    assert f["total_value_mn"] == pytest.approx(5904.379)   # ribbon: Val: 590.44 cr
    assert f["dsex"] == pytest.approx(5539.32, abs=0.01)    # ribbon: DSEX: 5539.32


def test_the_three_figures_no_other_source_publishes(parsed):
    f = parsed.frames[0]
    assert f["market_cap_mn"] == pytest.approx(6911303.853)
    assert f["dses"] == pytest.approx(1109.79706)
    assert f["ds30"] == pytest.approx(2108.26982)


def test_a_retired_index_is_empty_not_zero(parsed):
    """DGEN ships as a literal "-". A discontinued index did not fall to nothing."""
    assert all(fr.get("dgen") is None for fr in parsed.frames)


def test_dates_are_iso_and_strictly_descending(parsed):
    dates = [fr["date"] for fr in parsed.frames]
    assert all(len(d) == 10 and d[4] == "-" for d in dates)
    assert dates == sorted(dates, reverse=True), "newest first, and no duplicates"
    assert len(set(dates)) == len(dates)


def test_the_scrolling_ticker_is_not_mistaken_for_the_data(parsed):
    """The page opens with a 397-row ticker of every instrument, far the biggest
    table. Picking a table by size — or by being first — reads that instead."""
    assert len(parsed.frames) == 30
    assert all("symbol" not in fr for fr in parsed.frames)


def test_a_page_without_the_history_table_is_a_named_problem_not_an_empty_success():
    p = DSEMarketHistoryAdapter(None).parse(b"<html><body><table><tr><td>x</td></tr></table></body></html>")
    assert p.frames == []
    assert p.problems and "Date" in p.problems[0]


def test_a_reworded_header_still_matches(parsed):
    """Columns are matched by pattern so a cosmetic rewrite does not drop them."""
    html = (b"<table><tr><th>Date</th><th>Total  Trades</th><th>Total Volume</th>"
            b"<th>Total Value in Taka (million)</th><th>Total Market Capitalisation (mn)</th>"
            b"<th>DSEX</th><th>DSES</th><th>DS30</th><th>DGEN</th></tr>"
            b"<tr><td>08-09-2026</td><td>168,534</td><td>191,268,628</td><td>5,904.379</td>"
            b"<td>6,911,303.853</td><td>5539.32016</td><td>1109.79706</td><td>2108.26982</td>"
            b"<td>-</td></tr></table>")
    p = DSEMarketHistoryAdapter(None).parse(html)
    assert p.problems == [] and len(p.frames) == 1
    assert p.frames[0]["market_cap_mn"] == pytest.approx(6911303.853)
    assert p.frames[0]["columns_missing"] == []


def test_undated_rows_are_skipped_rather_than_parsed_as_a_session():
    html = (b"<table><tr><th>Date</th><th>Total Trade</th><th>DSEX Index</th></tr>"
            b"<tr><td>Total</td><td>999</td><td>999</td></tr>"
            b"<tr><td>08-09-2026</td><td>168534</td><td>5539.32</td></tr></table>")
    p = DSEMarketHistoryAdapter(None).parse(html)
    assert [f["date"] for f in p.frames] == ["2026-09-08"]


def test_the_source_is_registered_and_routed():
    from seeing.capture.adapters import bd_public
    from seeing.replay import SOURCE_TABLE
    names = {s.name for s in bd_public.build_specs(None, [])}
    assert "dse_market_history" in names
    assert SOURCE_TABLE["dse_market_history"] == "market_history"
