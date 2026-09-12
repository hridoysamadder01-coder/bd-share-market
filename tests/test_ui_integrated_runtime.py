from __future__ import annotations

import os

from fastapi.testclient import TestClient

from tower.ui.server import create_app
from run_market_ui import parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "tower", "ui", "static")


def test_all_integrated_read_only_routes_are_mounted():
    c = TestClient(create_app(None))
    for url in (
        "/api/connected/market",
        "/api/connected/stock/SQURPHARMA",
        "/api/stock/SQURPHARMA/history",
        "/api/data/inventory?limit=5",
        "/api/runtime/info",
    ):
        r = c.get(url)
        assert r.status_code == 200, (url, r.text)


def test_shell_loads_connected_layers_and_history_chart():
    c = TestClient(create_app(None))
    html = c.get("/").text
    assert "/static/connected_layers.js" in html
    assert "/static/history_chart.js" in html
    assert "/static/history_chart.css" in html


def test_history_chart_is_ohlc_not_close_only():
    js = open(os.path.join(STATIC, "history_chart.js"), encoding="utf-8").read()
    for token in ("r.o", "r.h", "r.l", "r.c"):
        assert token in js
    assert "moveTo(xw,Y(hg))" in js and "lineTo(xw,Y(l))" in js
    assert "fillRect(x-cw/2,top" in js
    assert "r.floor===true" in js
    assert "r.annotated===false" in js
    assert "r.locked===true" in js
    assert "r.qa_exclude===true" in js
    for ev in ("'wheel'", "'mousedown'", "'mousemove'", "'touchstart'", "'touchmove'", "'touchend'", "'keydown'"):
        assert ev in js


def test_history_route_never_changes_old_stock_payload():
    c = TestClient(create_app(None))
    d = c.get("/api/stock/SQURPHARMA").json()
    assert "history" in d
    assert d["history"] is None


def test_inventory_is_metadata_only_and_reports_every_data_root():
    c = TestClient(create_app(None))
    d = c.get("/api/data/inventory?limit=20").json()
    assert d["truth"] == "OBSERVED"
    assert {x["root"] for x in d["roots"]} == {"data", "evidence", "results", "qa", "manifests", "reports"}
    for row in d["rows"]:
        assert set(row) >= {"path", "root", "bytes", "mtime_utc", "ext", "content_exposed"}
        assert row["content_exposed"] is False
        assert "content" not in row


def test_runtime_launcher_defaults_to_lan_binding():
    args = parser().parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 8765


def test_runtime_info_explains_pc_and_phone_access_without_claiming_a_device_connected():
    c = TestClient(create_app(None))
    d = c.get("/api/runtime/info").json()
    assert d["bind_for_pc_and_phone"] == "0.0.0.0"
    assert d["default_port"] == 8765
    assert isinstance(d["lan_ipv4"], list)
    assert isinstance(d["phone_url_examples"], list)
