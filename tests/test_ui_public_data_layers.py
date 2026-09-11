"""Tests for the read-only UI data layers: source provenance and ownership history.

These cover two defects found by reading the committed evidence against the
code that serves it to the shell:

  1. `_build_sources` defaulted a source's truth class to OBSERVED. No source in
     `SOURCE_STATUS.json` declares a `truth` key at all, so every row on the Data
     Trust screen was stamped with the highest truth class the repository has —
     provenance manufactured by the presentation layer.

  2. `_build_sources` read `last_success` / `t_source` for the last good poll.
     The capture writer records it as `last_ok_utc`, so the field was always
     None and a stale source was indistinguishable from a fresh one on the one
     screen whose entire job is to show that difference.

Nothing here tests engine or research behaviour; these are the UI's read-only
normalisation and the shell's rendering of data the API already returned.
"""
from __future__ import annotations

import json
import os
import re

import pytest

from tower.ui import market_api

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tower", "ui", "static")


# ───────────────────────────────────────────── source provenance
def _sources_from(tmp_path, monkeypatch, payload):
    p = tmp_path / "SOURCE_STATUS.json"
    p.write_text(json.dumps(payload))
    monkeypatch.setattr(market_api, "_find_source_status", lambda: str(p))
    return market_api._build_sources()


def test_source_without_truth_class_is_unknown_not_observed(tmp_path, monkeypatch):
    """A source that declares no truth class must never render as OBSERVED."""
    rows = _sources_from(tmp_path, monkeypatch, {
        "sources": [{"source": "stocknow_instruments", "status": "WORKING", "kind": "watch"}]
    })
    assert len(rows) == 1
    assert rows[0]["truth"] == "UNKNOWN", "an undeclared truth class was upgraded to OBSERVED"


def test_declared_truth_class_is_preserved(tmp_path, monkeypatch):
    rows = _sources_from(tmp_path, monkeypatch, {
        "sources": [{"source": "dsebd_circuit", "status": "WORKING", "truth": "observed"}]
    })
    assert rows[0]["truth"] == "OBSERVED"


def test_last_ok_utc_is_read_as_last_success(tmp_path, monkeypatch):
    """The capture writer records the last good poll as `last_ok_utc`."""
    stamp = "2026-09-08T03:27:27.051066+00:00"
    rows = _sources_from(tmp_path, monkeypatch, {
        "sources": [{"source": "cse_current_price", "status": "WORKING", "last_ok_utc": stamp}]
    })
    assert rows[0]["last_success"] == stamp, "freshness was dropped, so stale looks like fresh"


def test_last_success_falls_back_for_other_writers(tmp_path, monkeypatch):
    """Older/other writers that use `last_success` or `t_source` still work."""
    rows = _sources_from(tmp_path, monkeypatch, {
        "sources": [
            {"source": "a", "last_success": "2026-01-01T00:00:00+00:00"},
            {"source": "b", "t_source": "2026-01-02T00:00:00+00:00"},
            {"source": "c"},
        ]
    })
    by = {r["name"]: r for r in rows}
    assert by["a"]["last_success"] == "2026-01-01T00:00:00+00:00"
    assert by["b"]["last_success"] == "2026-01-02T00:00:00+00:00"
    assert by["c"]["last_success"] is None


def test_real_committed_source_status_yields_a_last_ok_time(tmp_path, monkeypatch):
    """Against the real committed evidence, every WORKING source carries a time."""
    import glob

    files = sorted(glob.glob(os.path.join(market_api.REPO_ROOT, "evidence/public_engine/*/SOURCE_STATUS.json")))
    if not files:
        pytest.skip("no committed SOURCE_STATUS.json in this checkout")
    monkeypatch.setattr(market_api, "_find_source_status", lambda: files[-1])
    rows = market_api._build_sources()
    assert rows, "the committed evidence declares sources but none were normalised"
    working = [r for r in rows if r["status"] == "WORKING"]
    assert working, "expected at least one WORKING source in the committed evidence"
    assert all(r["last_success"] for r in working), \
        "a WORKING source reported no last-OK time — the Data Trust screen cannot show staleness"
    assert all(r["truth"] == "UNKNOWN" for r in rows), \
        "the committed evidence declares no truth class; it must not be upgraded"


# ───────────────────────────────────────────── ownership history
def test_stock_payload_carries_ownership_rows_unmodified():
    """The API already serves ownership history; confirm its shape is intact."""
    import glob

    files = sorted(glob.glob(os.path.join(market_api.REPO_ROOT, "evidence/public_engine/*/extract/ownership.csv")))
    if not files:
        pytest.skip("no committed ownership.csv in this checkout")
    rows = market_api._read_csv(files[-1])
    if not rows:
        pytest.skip("committed ownership.csv is empty")
    sym = (rows[0].get("symbol") or "").upper()
    d = market_api._build_stock(sym)
    own = d.get("ownership")
    assert own and own.get("rows"), f"ownership history vanished for {sym}"
    keys = {"as_on", "sponsor_director", "government", "institution", "foreign", "public"}
    for r in own["rows"]:
        assert keys.issubset(r.keys())


def test_shell_renders_ownership_history():
    """The stock page must actually render the ownership rows the API returns."""
    js = open(os.path.join(STATIC, "shell.js"), encoding="utf-8").read()
    assert "OWNERSHIP HISTORY" in js, "ownership history is served but never rendered"
    assert "detail.ownership" in js, "the stock view does not read the ownership payload"
    for field in ("sponsor_director", "government", "institution", "foreign", "public"):
        assert field in js, f"ownership column {field} is not rendered"


def test_shell_does_not_fill_missing_ownership_fields():
    """A field the source did not publish stays blank; it is never imputed."""
    js = open(os.path.join(STATIC, "shell.js"), encoding="utf-8").read()
    block = js[js.index("OWNERSHIP HISTORY"):]
    block = block[: block.index("</section>")]
    assert "not reported" in block, "a partially reported snapshot must say so"
    # no zero-filling of an absent percentage anywhere in the ownership block
    assert not re.search(r"(sponsor_director|government|institution|foreign|public)\s*\|\|\s*0", block), \
        "a missing ownership percentage was defaulted to 0"


def test_unknown_truth_badge_has_a_style():
    css = open(os.path.join(STATIC, "shell.css"), encoding="utf-8").read()
    assert ".truth.UNKNOWN" in css, "UNKNOWN renders unstyled and reads as a normal badge"
