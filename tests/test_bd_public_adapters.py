"""Adapters for the public sources beyond DSE and LankaBD.

Every fixture here is a slice of a payload this repository actually fetched on
2026-09-08 — not a hand-written mock — so a schema drift at the source shows up
as a failing test rather than as silently empty frames.

The brief for each adapter also demands the malformed / empty / drifted cases,
because a public site returning an error page with HTTP 200 is the normal
failure mode on these hosts, not the exotic one.
"""
import json

import pytest

from seeing.capture.adapters.bd_public import (BullBDDetailAdapter, CSECurrentPriceAdapter,
                                               StockNowInstrumentsAdapter, extract_initial_state)

from .conftest import fixture


# ---------------------------------------------------------------- StockNow
def sn():
    return StockNowInstrumentsAdapter(client=None)


def test_stocknow_parses_the_real_payload():
    p = sn().parse(fixture("stocknow_instruments_sample.json"))
    assert not p.problems
    assert len(p.frames) == 4
    b = next(f for f in p.frames if f["symbol"] == "BRACBANK")
    assert b["company_name"] == "Brac Bank Ltd."
    assert b["market_category"] == "A"
    assert (b["open"], b["high"], b["low"], b["close_published"], b["yclose"]) == \
           (62.5, 62.8, 62.0, 62.3, 62.4)
    assert b["day_trades"] == 1307 and b["day_volume"] == 1016520
    assert b["t_source_str"] == "2026-09-07 14:09:30"


def test_stocknow_carries_the_horizon_prices_that_justify_the_source():
    """The reason this source is registered: no other free sensor publishes these."""
    b = next(f for f in sn().parse(fixture("stocknow_instruments_sample.json")).frames
             if f["symbol"] == "BRACBANK")
    assert b["ref_7d"] == 62.2 and b["ref_30d"] == 63.5 and b["ref_365d"] == 65.3
    assert b["yearly_high"] == 89.6 and b["yearly_low"] == 60.0
    assert b["floor_flag"] == 0


def test_stocknow_flags_a_zero_price_instead_of_reporting_it_as_traded():
    body = json.dumps({"XYZ": {"code": "XYZ", "open": 0, "high": 0, "low": 0,
                               "close": 0, "ycp": 0, "trades": 0, "volume": 0}}).encode()
    fr = sn().parse(body).frames[0]
    assert set(fr["zero_fields"]) == {"open", "high", "low", "close_published", "yclose"}


@pytest.mark.parametrize("body,expect", [
    (b"not json at all", "json:"),
    (b"[1,2,3]", "expected an object keyed by symbol"),
    (b"{}", "no instruments"),
])
def test_stocknow_reports_malformed_payloads_rather_than_returning_empty(body, expect):
    p = sn().parse(body)
    assert p.frames == [] and any(expect in x for x in p.problems)


def test_stocknow_keeps_good_rows_when_one_row_is_broken():
    body = json.dumps({"GOOD": {"code": "GOOD", "close": 1.5}, "BAD": "not-an-object"}).encode()
    p = sn().parse(body)
    assert [f["symbol"] for f in p.frames] == ["GOOD"]
    assert any("BAD" in x for x in p.problems)


# ---------------------------------------------------------------- CSE
def cse():
    return CSECurrentPriceAdapter(client=None)


def test_cse_parses_the_real_page_and_reassembles_the_spaced_stock_code():
    """CSE renders the code one letter per node — '1 J A N A T A M F'."""
    p = cse().parse(fixture("cse_current_price_sample.html"))
    assert not p.problems and len(p.frames) == 5
    codes = [f["symbol"] for f in p.frames]
    assert "1JANATAMF" in codes and not any(" " in c for c in codes)
    j = next(f for f in p.frames if f["symbol"] == "1JANATAMF")
    assert j["exchange"] == "CSE"
    assert (j["ltp"], j["open"], j["high"], j["low"], j["yclose"]) == (3.50, 3.50, 3.50, 3.40, 3.50)
    assert j["day_trades"] == 5 and j["day_volume"] == 9720 and j["day_value_mn"] == 0.03


def test_cse_reports_a_missing_table_instead_of_an_empty_market():
    p = cse().parse(b"<html><body><h1>502 Bad Gateway</h1></body></html>")
    assert p.frames == [] and any("no table" in x for x in p.problems)


def test_cse_reports_a_table_with_no_usable_rows():
    p = cse().parse(b"<html><table><tr><th>SL.</th><th>CODE</th></tr></table></html>")
    assert p.frames == [] and any("no data rows" in x for x in p.problems)


def test_cse_zero_prices_are_flagged_not_treated_as_trades():
    row = "<tr>" + "".join(f"<td>{v}</td>" for v in
                           ["2", "XYZ", "0", "0", "0", "0", "0", "0", "0", "0"]) + "</tr>"
    fr = cse().parse(f"<html><table>{row}</table></html>".encode()).frames[0]
    assert set(fr["zero_fields"]) == {"ltp", "open", "high", "low", "yclose"}


# ---------------------------------------------------------------- BullBD
def bb():
    return BullBDDetailAdapter(client=None)


def test_bullbd_parses_the_real_page_fundamentals():
    p = bb().parse(fixture("bullbd_detail_sample.html"), "BRACBANK")
    assert not p.problems and len(p.frames) == 1
    f = p.frames[0]
    assert f["symbol"] == "BRACBANK" and f["mic"] == "XDHA" and f["sector"] == "Bank"
    assert f["eps"] == 10.64 and f["nav"] == 44.11 and f["pe"] == 5.86
    assert f["total_shares"] == 1990926167          # the field no other free source gives
    assert f["paid_up_capital_mn"] == 19909.26
    assert f["ltp"] == 62.3 and f["yclose"] == 62.4
    assert f["t_source_str"] == "2026-09-07T08:09:30.000Z"


def test_bullbd_converts_crore_to_millions_without_touching_the_source_value():
    f = bb().parse(fixture("bullbd_detail_sample.html"), "BRACBANK").frames[0]
    assert f["day_value_crore"] == 6.3214
    assert f["day_value_mn"] == pytest.approx(63.214)


def test_bullbd_names_the_socket_only_fields_rather_than_emitting_an_empty_book():
    """An empty depth slot must never read as 'nobody is resting'."""
    f = bb().parse(fixture("bullbd_detail_sample.html"), "BRACBANK").frames[0]
    assert set(f["socket_only_fields"]) == {"selectedShareMarketDepth", "minuteDataVolumeSeries",
                                            "selectedShareCircuitBreaker"}
    assert "bid_levels" not in f and "ask_levels" not in f


def test_bullbd_reports_a_page_without_the_state_object():
    p = bb().parse(b"<html><body>login wall</body></html>", "BRACBANK")
    assert p.frames == [] and any("__INITIAL_STATE__" in x for x in p.problems)


def test_bullbd_reports_not_found_for_an_unknown_ticker():
    html = '<script>window.__INITIAL_STATE__={"shareDetail":{"notFound":true}}</script>'
    p = bb().parse(html.encode(), "NOPE")
    assert p.frames == [] and any("notFound" in x for x in p.problems)


def test_bullbd_reports_a_hollow_state_object():
    html = '<script>window.__INITIAL_STATE__={"shareDetail":{"ticker":"X","company":{}}}</script>'
    p = bb().parse(html.encode(), "X")
    assert p.frames == [] and any("all empty" in x for x in p.problems)


# ---------------------------------------------------------------- state extraction
def test_state_extraction_survives_braces_inside_strings():
    """Brace counting must ignore braces that live inside string literals."""
    html = '<script>window.__INITIAL_STATE__={"a":"}{ tricky \\" }","b":{"c":1}}</script>'
    st = extract_initial_state(html)
    assert st == {"a": '}{ tricky " }', "b": {"c": 1}}


def test_state_extraction_returns_none_rather_than_raising():
    assert extract_initial_state("no state here") is None
    assert extract_initial_state("window.__INITIAL_STATE__={not json}") is None
    assert extract_initial_state("window.__INITIAL_STATE__={\"unterminated\":1") is None
    assert extract_initial_state("") is None


def test_hollow_placeholders_are_not_mistaken_for_data():
    """BullBD pre-creates socket slots with zeros; `{"ul":0,"ll":0,"date":""}` is
    not an upper limit of zero, it is an unfilled slot."""
    from seeing.capture.adapters.bd_public import is_hollow
    assert is_hollow({"ul": 0, "ll": 0, "fl": 0, "date": ""}) is True
    assert is_hollow([{"c": 0, "d": ""}, {"c": 0, "d": ""}]) is True
    assert is_hollow(None) is True and is_hollow([]) is True and is_hollow("  ") is True
    # anything carrying a real value is not hollow
    assert is_hollow({"ul": 68.5, "ll": 56.1, "date": "2026-09-07"}) is False
    assert is_hollow([{"c": 0, "d": ""}, {"c": 3.2, "d": "2026-01-02"}]) is False
    assert is_hollow(0.0001) is False and is_hollow("x") is False
