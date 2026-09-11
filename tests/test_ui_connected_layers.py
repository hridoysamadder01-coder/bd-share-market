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


def test_no_browser_side_quiet_accumulation_rule_is_served():
    """No script the shell loads may carry a browser-side quiet-accumulation rule.

    This test previously asserted the OPPOSITE MECHANISM for the same goal: it
    required connected_layers.js to contain `ATTENTION_RULES.splice(...)`, because
    at the time the invented rule was still defined in labels.js and this bridge
    deleted it at load time. main now removes the rule at its source (PR #13 —
    ATTENTION_RULES no longer exists, OBSERVATIONS replaced it), so demanding the
    splice would pin a stop-gap that can only do harm: the same block also
    overwrote FEATURES.accumulation_proxy's label, and this file loads last.

    The goal is unchanged and is now checked where it actually lives — the served
    scripts must not define the rule at all, and must not reach into the shell's
    globals to fix it after the fact.
    """
    c = TestClient(create_app(None))
    bridge = c.get('/static/connected_layers.js')
    labels = c.get('/static/labels.js')
    assert bridge.status_code == 200 and labels.status_code == 200

    def code(text):
        import re
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"^\s*//.*$", "", text, flags=re.M)

    assert "ATTENTION_RULES" not in code(labels.text), \
        "labels.js still defines the invented rule table"
    assert "Quiet accumulation" not in code(labels.text), \
        "labels.js still names the rejected shape as a rule"
    assert "ATTENTION_RULES" not in code(bridge.text), \
        "connected_layers.js still patches a rule table that no longer exists"
    assert "FEATURES." not in code(bridge.text), \
        "connected_layers.js writes into the shell's feature dictionary at load time"


def test_shell_loads_connected_layer_bridge():
    c = TestClient(create_app(None))
    assert '/static/connected_layers.js' in c.get('/').text
