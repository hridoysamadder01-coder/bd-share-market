from fastapi.testclient import TestClient
from tower.ui.server import create_app


def test_connected_stock_exposes_existing_layers():
    c = TestClient(create_app(None))
    r = c.get('/api/connected/stock/BEXIMCO')
    assert r.status_code == 200
    d = r.json()
    assert set(d) >= {'depth','history','events','company_profile','ownership_history','market_context'}
    if d['depth']:
        assert d['depth']['truth'] == 'OBSERVED'
        assert d['depth']['best_bid'] is None or d['depth']['best_bid'] > 0
    assert d['events']['truth'] == 'OBSERVED'


def test_bridge_removes_browser_side_quiet_accumulation_rule():
    c = TestClient(create_app(None))
    r = c.get('/static/connected_layers.js')
    assert r.status_code == 200
    assert "ATTENTION_RULES.splice" in r.text
    assert "id === 'accumulation'" in r.text


def test_shell_loads_connected_layer_bridge():
    c = TestClient(create_app(None))
    assert '/static/connected_layers.js' in c.get('/').text
