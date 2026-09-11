"""Smoke tests for the BD Market Intelligence OS shell API.

Every endpoint returns 200 with a well-typed body; NaN in features is scrubbed
to null before JSON encoding; missing data files degrade to empty rows, never
to a 500.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from tower.ui.server import create_app


def _client() -> TestClient:
    return TestClient(create_app(None))


def test_index_serves_shell_html():
    c = _client()
    r = c.get("/")
    assert r.status_code == 200
    assert "BD MARKET" in r.text.upper() or "bd-market" in r.text.lower()


def test_static_assets():
    c = _client()
    for path in ("/static/shell.html", "/static/shell.css", "/static/shell.js"):
        r = c.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"
        assert len(r.content) > 100


def test_market_summary():
    c = _client()
    r = c.get("/api/market/summary")
    assert r.status_code == 200
    d = r.json()
    assert set(d).issuperset(
        {"trading_date", "session_phase", "market_trades", "market_volume",
         "market_value", "advancing", "declining", "unchanged", "commit"}
    )


def test_market_universe_returns_rows_with_truth_class_shape():
    c = _client()
    r = c.get("/api/market/universe")
    assert r.status_code == 200
    d = r.json()
    assert "rows" in d and "at" in d and "count" in d
    if d["rows"]:
        row = d["rows"][0]
        for k in ("symbol", "ltp", "ycp", "upper_limit", "lower_limit"):
            assert k in row
        # every numeric slot is either a real number or None; NEVER a string of 'nan'
        for v in row.values():
            assert not (isinstance(v, str) and v.lower() == "nan")


def test_market_sectors_have_change_pct_or_none():
    c = _client()
    r = c.get("/api/market/sectors")
    assert r.status_code == 200
    d = r.json()
    for s in d["rows"]:
        assert "name" in s
        assert s["change_pct"] is None or isinstance(s["change_pct"], (int, float))


def test_stock_endpoint_scrubs_nan_and_returns_200():
    c = _client()
    r = c.get("/api/stock/BEXIMCO")
    # if data files are absent this becomes 404; if present, 200 with scrubbed floats
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        d = r.json()
        assert set(d).issuperset({"instrument", "fundamentals", "features", "state"})
        # every float in the response is finite or None
        def _all_finite(x: Any) -> bool:
            if isinstance(x, float):
                import math
                return math.isfinite(x)
            if isinstance(x, dict):
                return all(_all_finite(v) for v in x.values())
            if isinstance(x, list):
                return all(_all_finite(v) for v in x)
            return True
        assert _all_finite(d), "found NaN or inf in stock response"


def test_features_latest_is_json_compliant():
    c = _client()
    r = c.get("/api/features/latest")
    assert r.status_code == 200
    # the response must be valid JSON — this is the actual regression we fixed
    json.loads(r.content)


def test_observation_tower_route_is_served_but_notes_missing_store():
    c = _client()
    r = c.get("/observe")
    assert r.status_code == 200
    # without --store the shell explains the requirement rather than crashing
    assert "state store" in r.text.lower() or "observation tower" in r.text.lower()


def test_favicon():
    c = _client()
    r = c.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg")
