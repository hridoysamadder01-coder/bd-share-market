"""Stage 1 parsers: Bangladesh Bank bills, CDBL statistics, BSEC publications, DSE ownership.

Every fixture is cut from a payload this repository actually fetched on
2026-09-08, so a layout change at the source fails a test instead of silently
producing empty frames.
"""
import pytest

from seeing.capture.adapters.bd_public import (BSECPublicationsAdapter, BangladeshBankBillAdapter,
                                               CDBLStatisticsAdapter, DSEOwnershipAdapter)
from seeing.capture.failures import BOT_CHALLENGE, classify_fetch
from seeing.capture.http_client import Fetched

from .conftest import fixture


# ---------------------------------------------------------------- Bangladesh Bank bills
def bb():
    return BangladeshBankBillAdapter(client=None)


def test_bb_bill_parses_the_real_auction_table():
    p = bb().parse(fixture("bb_bill_rate_sample.html"))
    assert not p.problems and len(p.frames) >= 4
    first = p.frames[0]
    assert first["issue_date"] == "18/11/2021"
    assert first["isin"] == "BD0100717216"
    assert first["tenor"] == "07-day BB Bill"
    assert first["bids_received_count"] == 8.0
    assert first["bids_received_face_value_cr"] == 916.8
    assert first["bids_received_yield_range"] == "1.91-4.75"


def test_bb_bill_distinguishes_accepted_from_no_bid_accepted():
    """A rejected auction row is SHORTER than an accepted one — positional
    parsing must not assume a fixed column count."""
    fr = bb().parse(fixture("bb_bill_rate_sample.html")).frames
    assert fr[0]["any_bid_accepted"] is False
    accepted = [f for f in fr if f["any_bid_accepted"]]
    assert accepted, "the fixture must contain at least one accepted auction"
    a = accepted[0]
    assert a["isin"] == "BD0101416214" and a["bids_accepted_count"] is not None
    assert a["accepted_yield_range"]


def test_bb_bill_reports_a_missing_table_rather_than_an_empty_result():
    p = bb().parse(b"<html><body><p>nothing here</p></body></html>")
    assert p.frames == [] and any("no auction table" in x for x in p.problems)


def test_bb_bill_ignores_header_rows():
    fr = bb().parse(fixture("bb_bill_rate_sample.html")).frames
    assert all(f["isin"] and f["issue_date"] for f in fr)
    assert not any("ISIN" in (f["tenor"] or "") for f in fr)


# ---------------------------------------------------------------- CDBL
def test_cdbl_parses_every_headline_statistic():
    p = CDBLStatisticsAdapter(client=None).parse(fixture("cdbl_stats_sample.html"))
    assert not p.problems and len(p.frames) == 1
    f = p.frames[0]
    assert f["depository_participants"] == 560.0
    assert f["bo_accounts"] == 1661613.0          # the participation metric that matters
    assert f["isin_enlisted"] == 829.0
    assert f["cds_market_value"] == 3115786.0
    assert f["shares_in_cds"] == 105084.0
    assert f["n_pairs"] >= 5


def test_cdbl_keeps_every_pair_it_saw_including_ones_it_could_not_match():
    """A wording change must be visible, not silently unmatched."""
    f = CDBLStatisticsAdapter(client=None).parse(fixture("cdbl_stats_sample.html")).frames[0]
    assert isinstance(f["all_pairs"], list) and f["all_pairs"]
    assert "unmatched_labels" in f


def test_cdbl_reports_a_page_with_no_statistics():
    p = CDBLStatisticsAdapter(client=None).parse(b"<html><body><p>hi</p></body></html>")
    assert p.frames == [] and any("no label/value" in x for x in p.problems)


# ---------------------------------------------------------------- BSEC
def test_bsec_parses_dated_publications_with_type_and_category():
    p = BSECPublicationsAdapter(client=None).parse(fixture("bsec_publications_sample.html"))
    assert not p.problems and len(p.frames) >= 25
    dated = [f for f in p.frames if f["announcement_date"]]
    assert len(dated) >= 25
    assert {f["category"] for f in p.frames} >= {"law_order_directive", "press_release"}
    assert all(f["url"].startswith("http") for f in p.frames)


def test_bsec_extracts_enforcement_orders():
    """Orders are the enforcement/context labels this source exists for."""
    fr = BSECPublicationsAdapter(client=None).parse(
        fixture("bsec_publications_sample.html")).frames
    orders = [f for f in fr if f["announcement_type"] == "Order"]
    assert orders, "the fixture must contain at least one Order"
    assert any("floor price" in (f["announcement_text"] or "").lower() for f in orders)
    assert all(f["announcement_date"] for f in orders)


def test_bsec_folds_the_undated_hero_duplicate_of_a_dated_publication():
    html = ('<html><body>'
            '<a href="https://sec.gov.bd/storage/laws/X.pdf">Discover More</a>'
            '<a href="https://sec.gov.bd/storage/laws/X.pdf">Sep 01, 2026 Directive Regarding Y</a>'
            '<a href="https://sec.gov.bd/storage/laws/Z.pdf">Discover More</a>'
            '</body></html>').encode()
    fr = BSECPublicationsAdapter(client=None).parse(html).frames
    urls = [f["url"] for f in fr]
    assert urls.count("https://sec.gov.bd/storage/laws/X.pdf") == 1
    kept = next(f for f in fr if f["url"].endswith("X.pdf"))
    assert kept["announcement_date"] == "Sep 01, 2026"     # the dated row wins
    # an undated link nobody else lists is still the only record of it — kept
    assert any(f["url"].endswith("Z.pdf") for f in fr)


def test_bsec_ignores_navigation_links():
    p = BSECPublicationsAdapter(client=None).parse(
        b'<html><body><a href="https://sec.gov.bd/about">About</a></body></html>')
    assert p.frames == [] and any("no publication links" in x for x in p.problems)


# ---------------------------------------------------------------- ownership
def test_ownership_parses_all_three_as_on_dates():
    """DSE publishes three as-on dates and no archive. A dict() over the matches
    would keep only the last and throw away two thirds of a scarce resource."""
    p = DSEOwnershipAdapter(client=None).parse(
        fixture("dse_company_ownership_sample.html"), "BRACBANK")
    assert not p.problems
    assert len(p.frames) == 3
    assert [f["as_on"] for f in p.frames] == ["Dec 31, 2025", "Jul 31, 2026", "Aug 31, 2026"]
    assert p.frames[0]["as_on_dates_on_page"] == 3


def test_ownership_percentages_are_read_per_row():
    fr = DSEOwnershipAdapter(client=None).parse(
        fixture("dse_company_ownership_sample.html"), "BRACBANK").frames
    assert [f["institution_pct"] for f in fr] == [11.48, 12.73, 13.04]
    assert [f["foreign_pct"] for f in fr] == [36.06, 32.96, 32.39]
    assert [f["public_pct"] for f in fr] == [6.29, 8.14, 8.40]
    assert all(f["sponsor_director_pct"] == 46.17 for f in fr)
    assert all(f["government_pct"] == 0.0 for f in fr)
    assert all(f["symbol"] == "BRACBANK" for f in fr)


def test_ownership_reports_a_page_without_the_block():
    p = DSEOwnershipAdapter(client=None).parse(b"<html><body>error</body></html>", "X")
    assert p.frames == [] and any("no shareholding block" in x for x in p.problems)


# ---------------------------------------------------------------- bot challenge
def test_a_captcha_page_is_a_named_failure_not_a_healthy_source():
    """Bangladesh Bank's FX page answers 200 with a CAPTCHA. Counting that as
    WORKING is how a blocked source hides in a green status file."""
    body = (b"<html><body>" + b"x" * 6000 +
            b"<p>This question is for testing whether you are a human visitor</p>"
            b"<p>Your support ID is: 1234</p></body></html>")
    f = classify_fetch(Fetched(True, 200, body, {"url": "bb"}, None))
    assert f is not None and f.code == BOT_CHALLENGE
    assert f.retryable is False          # retrying cannot fix a challenge


def test_a_noscript_message_is_not_treated_as_a_challenge():
    """Every SPA here ships 'please enable JavaScript' — matching it would mark
    healthy sources as challenged."""
    body = b"<html><noscript>Please enable JavaScript to view this app.</noscript></html>"
    assert classify_fetch(Fetched(True, 200, body, {}, None)) is None


def test_the_registry_blocks_bb_fx_with_the_captcha_reason():
    from seeing.capture.engine import build_registry
    from seeing.capture.http_client import PoliteClient
    by = {s.name: s for s in build_registry(PoliteClient(), ["GP"])}
    fx = by["bb_exchange_rate"]
    assert fx.blocked and fx.adapter is None and "CAPTCHA" in fx.blocked_reason
    # and the sources proven live on 2026-09-08 are registered and enabled
    for n in ("bb_bill_rate", "bsec_publications", "cdbl_stats", "dse_ownership"):
        assert by[n].enabled and not by[n].blocked
    assert by["dse_ownership"].per_symbol and by["dse_ownership"].cadence_s == 2592000.0
