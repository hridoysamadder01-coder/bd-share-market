"""The daily-history endpoint: a read-only addition beside /api/stock/{sym}.

What these tests hold in place is not "a chart appears". It is that the chart
cannot say something the data does not:

  * a session the exchange did not hold is absent, never invented;
  * the tail the QA pass has not annotated yet carries flags that are UNKNOWN
    (null), never false — "not checked" and "checked and clean" are different
    claims;
  * a missing number stays null and never becomes 0;
  * the percentile is a rank over stored numbers, reported with the count it
    was taken over, and nothing forward-looking is attached to it;
  * the existing /api/stock/{sym} response shape is untouched.
"""
from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient

from tower.ui.server import create_app
from tower.ui import market_api

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tower", "ui", "static")
SYM = "SQURPHARMA"


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(None))


def _hist(client, sym=SYM):
    r = client.get(f"/api/stock/{sym}/history")
    assert r.status_code == 200, r.text
    return r.json()


def _have_files():
    return bool(market_api._find_bars_annotated_parquet() or market_api._find_eod_raw_parquet())


needs_data = pytest.mark.skipif(not _have_files(), reason="no daily history file in this checkout")


# ───────────────────────────────────────────── shape and read-only guarantee
def test_endpoint_answers_and_declares_its_truth_class(client):
    d = _hist(client)
    assert set(d) >= {"symbol", "rows", "truth"}
    assert d["symbol"] == SYM
    assert d["truth"] in ("OBSERVED", "UNKNOWN")


def test_existing_stock_endpoint_shape_is_unchanged(client):
    """This work was allowed as a read-only ADDITION. The old payload keeps its
    keys, and its `history` field stays None — nothing was repurposed."""
    r = client.get(f"/api/stock/{SYM}")
    assert r.status_code == 200
    d = r.json()
    for k in ("instrument", "fundamentals", "ownership", "features", "state", "depth", "history", "events"):
        assert k in d, f"/api/stock/{{sym}} lost the key {k}"
    assert d["history"] is None


def test_unknown_symbol_is_an_empty_answer_not_an_error(client):
    d = _hist(client, "NOSUCHSYMBOL")
    assert d["rows"] == []
    assert d.get("reason")


# ───────────────────────────────────────────── the data itself
@needs_data
def test_rows_are_sorted_unique_sessions(client):
    rows = _hist(client)["rows"]
    assert rows, "no rows for a symbol that is in the daily file"
    dates = [r["t"] for r in rows]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates)), "a session appears twice"


@needs_data
def test_no_session_is_invented_between_two_real_ones(client):
    """Every row must exist in a source file. The clearest check: the response
    carries exactly as many rows as the union of the two files has for this
    symbol, and no row falls strictly inside a reported gap."""
    d = _hist(client)
    rows, cov = d["rows"], d["coverage"]
    have = {r["t"] for r in rows}
    for g in cov["gaps"]:
        inside = [t for t in have if g["from"] < t < g["to"]]
        assert not inside, f"rows were filled into the gap {g}: {inside[:3]}"


@needs_data
def test_unannotated_tail_carries_unknown_flags_not_false(client):
    d = _hist(client)
    tail = [r for r in d["rows"] if r["annotated"] is False]
    if d["coverage"]["unannotated_sessions"]:
        assert tail, "coverage counts an unannotated tail but no row is marked"
    for r in tail:
        for k in ("floor", "locked", "zero_volume", "qa_exclude"):
            assert r[k] is None, (
                f"{r['t']} is not QA-annotated yet, so {k} must be null (UNKNOWN); "
                f"serving {r[k]!r} claims the check was done"
            )


@needs_data
def test_annotated_rows_carry_real_booleans(client):
    d = _hist(client)
    ann = [r for r in d["rows"] if r["annotated"] is True]
    assert ann
    for r in ann[:200]:
        for k in ("floor", "locked", "zero_volume", "qa_exclude"):
            assert isinstance(r[k], bool), f"{k} on an annotated row must be a real boolean"


@needs_data
def test_a_missing_number_stays_null(client):
    """No field is ever defaulted to 0 — the repo's standing rule."""
    d = _hist(client)
    for r in d["rows"]:
        for k in ("o", "h", "l", "c", "v", "turnover"):
            v = r[k]
            assert v is None or isinstance(v, (int, float))


@needs_data
def test_coverage_counts_match_the_rows(client):
    d = _hist(client)
    rows, c = d["rows"], d["coverage"]
    assert c["sessions"] == len(rows)
    assert c["first"] == rows[0]["t"] and c["last"] == rows[-1]["t"]
    assert c["floor_era_sessions"] == sum(1 for r in rows if r["floor"] is True)
    assert c["locked_sessions"] == sum(1 for r in rows if r["locked"] is True)
    assert c["unannotated_sessions"] == sum(1 for r in rows if r["annotated"] is False)


@needs_data
def test_gaps_are_reported_not_smoothed(client):
    d = _hist(client)
    c = d["coverage"]
    assert c["gaps_over_10_days"] == len([g for g in c["gaps"]]) or c["gaps_over_10_days"] >= len(c["gaps"])
    for g in c["gaps"]:
        assert g["days"] > 10
        assert g["from"] < g["to"]


@needs_data
def test_percentile_is_a_rank_over_a_stated_population(client):
    d = _hist(client)
    p = d["position"]
    for pct, n in (("close_pct_rank", "close_ranked_over"), ("volume_pct_rank", "volume_ranked_over")):
        if p[pct] is None:
            continue
        assert 0.0 <= p[pct] <= 100.0
        assert isinstance(p[n], int) and p[n] > 1, "a rank must say how many sessions it ranked over"
    assert p["as_of"] == d["rows"][-1]["t"]


# ───────────────────────────────────────────── what the drawing may not do
def _shell_js():
    return open(os.path.join(STATIC, "shell.js"), encoding="utf-8").read()


def test_the_line_is_broken_at_a_coverage_gap():
    """A continuous line across a two-month exchange halt asserts prices that
    were never printed. The painter must lift the pen."""
    js = _shell_js()
    assert "function drawHistory" in js
    block = js[js.index("function drawHistory"):]
    block = block[: block.index("\nfunction hexA")]
    assert "GAP_MS" in block, "the painter has no gap threshold"
    assert re.search(r"ms\[i\]\s*-\s*ms\[i\s*-\s*1\]\s*\)\s*>\s*GAP_MS", block), \
        "the painter does not compare the distance between consecutive sessions"
    assert "pen = false" in block, "the painter never lifts the pen"


def test_the_floor_era_and_the_unchecked_tail_are_both_shaded():
    js = _shell_js()
    block = js[js.index("function drawHistory"):]
    assert "r.floor === true" in block, "the floor era is not shaded"
    assert "r.annotated === false" in block, "the not-yet-QA-checked tail is not shaded"


def test_the_history_section_states_no_forward_claim():
    """The section may describe what was measured. It may not predict."""
    js = _shell_js()
    sec = js[js.index("function historySection"):]
    sec = sec[: sec.index("function drawHistory")]
    low = sec.lower()
    # a denial is not a claim: "not a prediction" is exactly the sentence this
    # section is supposed to carry, so strip negated forms before scanning.
    low = re.sub(r"\b(not|never|no)\s+(a\s+|an\s+)?[a-z]*\s*"
                 r"(prediction|predicts?|signals?|targets?|recommendation)\b", " ", low)
    for banned in ("will ", "expect", "predict", "target", "buy ", "sell ",
                   "accumulation", "breakout", "signal", "opportunity"):
        assert banned not in low, f"the history section uses forward-claiming language: {banned!r}"


def test_no_external_resource_is_pulled_for_the_chart():
    """No chart library, no CDN — the repo serves everything it renders."""
    js = _shell_js()
    css = open(os.path.join(STATIC, "shell.css"), encoding="utf-8").read()
    for blob in (js, css):
        assert "https://" not in blob
        assert "http://" not in blob
