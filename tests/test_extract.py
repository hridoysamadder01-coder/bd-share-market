"""Sweep → tables → coverage, on a store built from the real fixture pages.

The point of the coverage report is that a breadth pull is only as good as its
worst-covered source, and that number has to be visible without opening half a
gigabyte of HTML. A source that returned 472 of 691 symbols and a source that
returned 691 of 691 look identical in a directory listing.
"""
import json
import os

import pandas as pd
import pytest

from seeing.capture.raw_store import RawStore
from seeing.extract import coverage, extract

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def _http(status=200, url="https://source.test/x"):
    return {"method": "GET", "url": url, "status": status,
            "t_last_byte_utc": "2026-09-08T09:45:00+00:00", "elapsed_ms": 12}


@pytest.fixture
def swept(tmp_path):
    """A miniature whole-market sweep: two symbols found, one missing, one unparseable."""
    store = RawStore(str(tmp_path / "store"), capturer_id="test", software_version="test")
    store.write_data("bullbd_detail", key="BRACBANK",
                     body=_fixture("bullbd_detail_sample.html"), http=_http())
    store.write_data("dse_ownership", key="BRACBANK",
                     body=_fixture("dse_company_ownership_sample.html"), http=_http())
    store.write_data("cse_current_price", key=None,
                     body=_fixture("cse_current_price_sample.html"), http=_http())
    store.write_gap("bullbd_detail", "not_found", detail="http 404", key="TB10Y0127")
    store.write_gap("dse_ownership", "parse_error", detail="no shareholding block", key="TB10Y0127")
    store.close()
    return str(tmp_path / "store")


def test_the_breadth_sources_now_replay_into_tables(swept, tmp_path):
    rep = extract(swept, str(tmp_path / "out"), universe=["BRACBANK", "TB10Y0127"])
    assert rep["tables"]["fundamentals"]["rows"] >= 1, "bullbd used to parse into nothing"
    assert rep["tables"]["ownership"]["rows"] >= 1, "ownership used to parse into nothing"
    assert rep["tables"]["cse"]["rows"] >= 1


def test_a_csv_is_written_for_every_non_empty_table(swept, tmp_path):
    out = tmp_path / "out"
    rep = extract(swept, str(out))
    for name, info in rep["tables"].items():
        p = out / info["path"]
        assert p.exists(), name
        assert len(pd.read_csv(p)) == info["rows"]
    assert (out / "COVERAGE.json").exists()


def test_the_store_is_verified_as_part_of_the_extract(swept, tmp_path):
    rep = extract(swept, str(tmp_path / "out"))
    assert rep["store_verified"]["all_ok"] is True
    assert rep["store_verified"]["chain_ok"] is True


def test_coverage_separates_a_missing_instrument_from_an_unreadable_page(swept, tmp_path):
    rep = extract(swept, str(tmp_path / "out"), universe=["BRACBANK", "TB10Y0127"])
    src = rep["coverage"]["sources"]
    assert src["bullbd_detail"]["gap_reasons"] == {"not_found": 1}, \
        "the source does not list this instrument"
    assert src["dse_ownership"]["gap_reasons"] == {"parse_error": 1}, \
        "the page exists but carries no such block — a different fact"


def test_coverage_counts_the_universe_the_sweep_aimed_at(swept, tmp_path):
    rep = extract(swept, str(tmp_path / "out"), universe=["BRACBANK", "TB10Y0127", "GP"])
    b = rep["coverage"]["sources"]["bullbd_detail"]
    assert rep["coverage"]["universe_size"] == 3
    assert b["universe_covered"] == 1 and b["universe_missing"] == 2
    assert "GP" in b["missing_sample"], "a symbol never attempted is still missing"


def test_a_source_that_returned_nothing_still_gets_a_line():
    """A source that failed every attempt must not vanish from the report."""
    tables = {"gaps": pd.DataFrame([{"source": "dsebd_latest", "reason": "connect_error", "key": None}])}
    cov = coverage(tables, universe=["GP"])
    assert cov["sources"]["dsebd_latest"]["rows"] == 0
    assert cov["sources"]["dsebd_latest"]["gap_reasons"] == {"connect_error": 1}


def test_coverage_survives_a_store_with_no_gaps_at_all(swept, tmp_path):
    cov = coverage({"fundamentals": pd.DataFrame([{"source": "bullbd_detail", "symbol": "GP", "seq": 1}])},
                   universe=["GP"])
    assert cov["sources"]["bullbd_detail"]["gap_reasons"] == {}
    assert cov["sources"]["bullbd_detail"]["universe_missing"] == 0


def test_the_report_is_json_serialisable(swept, tmp_path):
    out = tmp_path / "out"
    extract(swept, str(out), universe=["BRACBANK"])
    json.load(open(out / "COVERAGE.json"))          # would raise on a stray numpy type
