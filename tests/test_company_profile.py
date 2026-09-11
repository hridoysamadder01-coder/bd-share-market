"""The half of `displayCompany.php` that was captured but never read.

`dse_ownership` pulls this page once per symbol for its shareholding block. The
same bytes carry a multi-year dividend record, the right-issue history, the loan
position, reserves and the listing facts — 691 pages of it were already sitting in
the 2026-09-08 store, unread. The parser runs on replay rather than as a source,
so it costs no request and old stores gain the table retroactively.

Two things this file is really pinning: that the dividend string is kept verbatim
beside the parsed pairs (the raw text is the evidence when they later disagree),
and that the personal contact details printed further down the page are never
extracted.
"""
import pytest

from seeing.capture.adapters.bd_public import DSECompanyProfileParser


def parse(html: bytes, key="ACI"):
    return DSECompanyProfileParser().parse(html, key)


ACI_LIKE = (
    b"<html><body>"
    b"<table><tr><td>Trading Code: ACI</td><td>Scrip Code: 18455</td></tr></table>"
    b"<table>"
    b"<tr><td>Cash Dividend</td><td>25% 2025, 20% 2024, 40% 2023, 50% 2022</td></tr>"
    b"<tr><td>Bonus Issue (Stock Dividend)</td><td>15% 2024, 3.50% 2018</td></tr>"
    b"<tr><td>Right Issue</td><td>1R:1(At Par) 1997</td></tr>"
    b"<tr><td>Year End</td><td>30-Jun</td></tr>"
    b"<tr><td>Reserve &amp; Surplus without OCI (mn)</td><td>5,916.5</td></tr>"
    b"<tr><td>Other Comprehensive Income (OCI) (mn)</td><td>0.0</td></tr>"
    b"</table>"
    b"<table>"
    b"<tr><td>Listing Year</td><td>1976</td></tr>"
    b"<tr><td>Market Category</td><td>A</td></tr>"
    b"<tr><td>Electronic Share</td><td>Y</td></tr>"
    b"</table>"
    b"<table>"
    b"<tr><td>Present Operational Status</td><td>Active</td></tr>"
    b"<tr><td></td><td>Short-term loan (mn)</td><td>648.2</td></tr>"
    b"<tr><td>Long-term loan (mn)</td><td>5548.42</td></tr>"
    b"<tr><td>Latest Dividend Status (%)</td><td>25.00 for 2025</td></tr>"
    b"<tr><td>Details of Financial Statement</td><td>https://www.aci-bd.com/financials/</td></tr>"
    b"<tr><td>Price Sensitive Information</td><td>https://www.aci-bd.com/price-sensitive-information/</td></tr>"
    b"</table>"
    b"<table>"
    b"<tr><td>Company Secretary Name</td><td>Mr. Mohammad Mostafizur Rahman</td></tr>"
    b"<tr><td>Cell No.</td><td>+88 01708467600</td></tr>"
    b"<tr><td>E-mail</td><td>mostafizur.rahman@aci-bd.com</td></tr>"
    b"<tr><td>Contact Phone</td><td>02226605101</td></tr>"
    b"<tr><td>Factory</td><td>7 Hajiganj Road, Godnail, Narayanganj</td></tr>"
    b"</table></body></html>"
)


@pytest.fixture(scope="module")
def frame():
    p = parse(ACI_LIKE)
    assert p.problems == []
    return p.frames[0]


def test_the_listing_facts_are_read(frame):
    assert frame["symbol"] == "ACI"
    assert frame["scrip_code"] == "18455"
    assert frame["listing_year"] == 1976
    assert frame["market_category"] == "A"
    assert frame["year_end"] == "30-Jun"


def test_the_dividend_record_is_kept_raw_and_parsed(frame):
    """The string is the evidence; the pairs are the usable form. Keep both."""
    assert frame["cash_dividend"] == "25% 2025, 20% 2024, 40% 2023, 50% 2022"
    assert frame["cash_dividend_history"] == [
        {"year": 2025, "percent": 25.0}, {"year": 2024, "percent": 20.0},
        {"year": 2023, "percent": 40.0}, {"year": 2022, "percent": 50.0}]


def test_a_fractional_percent_survives(frame):
    assert {"year": 2018, "percent": 3.5} in frame["bonus_dividend_history"]


def test_the_latest_dividend_is_split_into_a_number_and_its_year(frame):
    assert frame["latest_dividend_pct"] == 25.0
    assert frame["latest_dividend_year"] == 2025


def test_a_label_that_is_not_beside_its_value_is_still_found(frame):
    """The loan figures arrive as ['', 'Short-term loan (mn)', '648.2']."""
    assert frame["short_term_loan_mn"] == pytest.approx(648.2)
    assert frame["long_term_loan_mn"] == pytest.approx(5548.42)


def test_thousands_separators_do_not_break_the_numbers(frame):
    assert frame["reserve_surplus_mn"] == pytest.approx(5916.5)
    assert frame["oci_mn"] == 0.0


def test_the_disclosure_urls_are_kept_because_an_event_study_needs_them(frame):
    assert frame["financials_url"].endswith("/financials/")
    assert "price-sensitive" in frame["price_sensitive_url"]


def test_personal_contact_details_are_never_extracted(frame):
    """The page prints a named individual's mobile and personal e-mail. A research
    store has no use for them, so no field may carry them."""
    blob = " ".join(str(v) for v in frame.values())
    for leak in ("Mostafizur", "01708467600", "mostafizur.rahman@", "Hajiganj", "02226605101"):
        assert leak not in blob, f"personal/premises detail leaked into the table: {leak}"
    for field in ("secretary", "cell", "phone", "fax", "email", "e_mail", "factory", "address"):
        assert not any(field in k.lower() for k in frame), field


def test_a_right_issue_that_never_happened_is_not_invented():
    html = ACI_LIKE.replace(b"1R:1(At Par) 1997", b"n/a")
    f = parse(html).frames[0]
    assert f["right_issue"] == "n/a"
    assert f["cash_dividend_history"], "and the rest of the page still parses"


def test_a_company_with_no_bonus_history_gets_an_empty_list_not_a_zero():
    html = ACI_LIKE.replace(b"15% 2024, 3.50% 2018", b"-")
    f = parse(html).frames[0]
    assert f["bonus_dividend_history"] == []
    assert f["bonus_dividend"] == "-", "the raw '-' is still recorded"


def test_a_page_with_none_of_these_labels_is_a_named_problem():
    p = parse(b"<html><body><table><tr><td>Nope</td><td>1</td></tr></table></body></html>", "TB10Y0127")
    assert p.frames == []
    assert p.problems and "TB10Y0127" in p.problems[0]


def test_replay_routes_the_second_reading_without_adding_a_source():
    """It must cost no request: the parser is an extra reading of dse_ownership."""
    from seeing.replay import SOURCE_TABLE, TABLES, _extra_parsers
    extra = _extra_parsers()
    assert "dse_ownership" in extra
    tables = [t for t, _ in extra["dse_ownership"]]
    assert tables == ["company_profile"] and "company_profile" in TABLES
    assert "dse_company_profile" not in SOURCE_TABLE, "not a source — it never fetches"
    assert not hasattr(DSECompanyProfileParser(), "fetch")
