"""Cross-symbol / sector context engine tests.

test_machinery_* build deterministic synthetic MarketState streams;
test_realdata_* run the real closed-market fixture capture through the engine.
"""
import json
import math
import os
import random
from datetime import datetime, timedelta, timezone

import pytest

from tower.cross import CrossEngine
from tower.state import MarketState

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
T0 = datetime(2026, 9, 6, 4, 0, tzinfo=timezone.utc)


def ms(symbol, t, mid=None, seq=0, **kw):
    m = MarketState(symbol=symbol, t=t, seq=seq, session_phase="CONTINUOUS")
    if mid is not None:
        m.mid = mid
        m.best_bid, m.best_ask = mid - 0.05, mid + 0.05
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def random_walk_mids(n, seed=7, start=100.0, step=0.05):
    rng = random.Random(seed)
    out, p = [], start
    for _ in range(n):
        p = max(1.0, p + rng.choice((-2, -1, 1, 2)) * step)
        out.append(p)
    return out


def feed_lead_lag(eng, lag_s=30, total_s=1200, dt=5, seed=7):
    """A is a random walk sampled every dt s; B's mid at t equals A's mid at t − lag_s.
    States are fed in event order (A then B at each instant)."""
    n = total_s // dt + 1
    a = random_walk_mids(n, seed=seed)
    lag_idx = lag_s // dt
    seq = 0
    t = T0
    for i in range(n):
        t = T0 + timedelta(seconds=i * dt)
        seq += 1
        eng.on_state(ms("A", t, a[i], seq=seq))
        if i >= lag_idx:
            seq += 1
            eng.on_state(ms("B", t, a[i - lag_idx], seq=seq))
    return t


def test_machinery_leader_detected_with_right_lag():
    eng = CrossEngine()
    now = feed_lead_lag(eng, lag_s=30)
    cross_b, _ = eng.context_for("B", now)
    assert cross_b["lead_lag_pairs_evaluated"] == 1
    assert cross_b["leaders"], cross_b
    sym, lag, corr = cross_b["leaders"][0]
    assert sym == "A" and lag == 30.0 and corr > 0.99
    # B does not lead A; A has no leaders but one lagger (B at 30 s)
    cross_a, _ = eng.context_for("A", now)
    assert cross_a["leaders"] == []
    assert cross_a["laggers"] and cross_a["laggers"][0][0] == "B" and cross_a["laggers"][0][1] == 30.0
    assert cross_a["laggers"][0][2] > 0.99
    assert cross_b["laggers"] == []
    # return series and the 60-s step-function return are exposed
    assert len(eng.returns_of("A")) > 100
    r_a = eng.return_60s("A", now)
    assert r_a is not None and cross_a["symbol_return_60s"] == r_a


def test_machinery_leader_lag_60s_and_15s():
    for lag in (60, 15):
        eng = CrossEngine()
        now = feed_lead_lag(eng, lag_s=lag, seed=11)
        cross_b, _ = eng.context_for("B", now)
        assert cross_b["leaders"] and cross_b["leaders"][0][:2] == ("A", float(lag)), (lag, cross_b["leaders"])
        assert cross_b["leaders"][0][2] > 0.99


def test_machinery_unrelated_symbols_no_lead_lag():
    eng = CrossEngine()
    n = 1200 // 5 + 1
    a, b = random_walk_mids(n, seed=1), random_walk_mids(n, seed=2)
    t = T0
    for i in range(n):
        t = T0 + timedelta(seconds=i * 5)
        eng.on_state(ms("A", t, a[i], seq=i))
        eng.on_state(ms("B", t, b[i], seq=i))
    cross, _ = eng.context_for("A", t)
    assert cross["lead_lag_pairs_evaluated"] == 1
    assert cross["leaders"] == [] and cross["laggers"] == []


def test_machinery_sector_relative_moves_and_basket_sync():
    eng = CrossEngine()
    for s in ("A", "B", "C"):
        eng.on_reference(s, "Bank")
    eng.on_reference("D", "Pharma")
    eng.on_reference("E", "")           # empty sector is ignored
    assert eng.sector_of("E") is None
    # 10 updates 10 s apart: A rises 1 % across the last 60 s, B and C flat, D falls
    for i in range(10):
        t = T0 + timedelta(seconds=10 * i)
        eng.on_state(ms("A", t, 100.0 * (1.0 + 0.01 * max(0, i - 3) / 6), seq=i))
        eng.on_state(ms("B", t, 50.0, seq=i))
        eng.on_state(ms("C", t, 20.0, seq=i))
        eng.on_state(ms("D", t, 30.0 * (1.0 - 0.005 * i / 9), seq=i))
    now = T0 + timedelta(seconds=90)
    cross, sector = eng.context_for("A", now)
    assert sector["sector"] == "Bank" and sector["n"] == 3 and sector["sector_source"] == "reference"
    own = cross["symbol_return_60s"]
    assert own is not None and abs(own - math.log(1.01)) < 1e-9
    assert sector["peer_return_60s"] == 0.0 and abs(sector["symbol_vs_sector_60s"] - own) < 1e-12
    assert sector["sector_return_60s"] == 0.0            # median of (+1 %, 0, 0)
    assert sector["sector_breadth"] == {"up": 1, "down": 0, "flat": 2, "n": 3, "net": 1 / 3}
    assert cross["basket_sync"] == 0.0 and cross["basket_sync_n"] == 2
    # market: median of the four 60-s returns (A +, B 0, C 0, D −) = 0; A vs others = own − 0
    assert cross["n_symbols_with_return"] == 4 and cross["market_return_60s"] == 0.0
    assert abs(cross["symbol_vs_market_60s"] - own) < 1e-12
    cross_b, sector_b = eng.context_for("B", now)
    assert cross_b["basket_sync"] == 0.5 and sector_b["symbol_vs_sector_60s"] < 0
    cross_d, sector_d = eng.context_for("D", now)
    assert sector_d["sector"] == "Pharma" and sector_d["n"] == 1
    assert sector_d["peer_return_60s"] is None and sector_d["symbol_vs_sector_60s"] is None
    assert cross_d["basket_sync"] is None and cross_d["symbol_vs_market_60s"] < 0


def test_machinery_breadth_from_market_stats():
    eng = CrossEngine()
    t1 = T0 + timedelta(seconds=30)
    eng.on_market_stats(t1, {"up": 127.0, "down": 227.0, "flat": 36.0, "kind": "market_totals"})
    eng.on_market_breadth(t1, 127.0, 227.0, None)
    eng.on_state(ms("A", T0, 10.0))
    before, _ = eng.context_for("A", T0)
    assert before["breadth_up"] is None and before["breadth_n"] is None      # causal: not yet published
    after, _ = eng.context_for("A", t1 + timedelta(seconds=5))
    assert (after["breadth_up"], after["breadth_down"], after["breadth_n"]) == (127.0, 227.0, 390.0)
    assert abs(after["breadth_net"] - (127 - 227) / 390) < 1e-12 and after["breadth_age_s"] == 5.0
    # explicit n wins; without flat and without n → n None (never invented)
    t2 = t1 + timedelta(seconds=60)
    eng.on_market_breadth(t2, 10, 5, 100)
    assert eng.context_for("A", t2)[0]["breadth_n"] == 100.0
    t3 = t2 + timedelta(seconds=60)
    eng.on_market_breadth(t3, 10, 5, None)
    c3 = eng.context_for("A", t3)[0]
    assert c3["breadth_up"] == 10.0 and c3["breadth_n"] is None and c3["breadth_net"] is None


def test_machinery_circuit_clustering():
    eng = CrossEngine()
    for s in ("A", "B", "C", "D"):
        eng.on_reference(s, "Bank")
    eng.on_reference("Z", "Other")
    t = T0
    eng.on_state(ms("A", t, 10.0, circuit={"locked_up": True, "locked_down": False, "dist_up_pct": 0.0, "dist_down_pct": 20.0}))
    eng.on_state(ms("B", t, 10.0, circuit={"locked_up": False, "locked_down": False, "dist_up_pct": 0.8, "dist_down_pct": 18.0}))
    eng.on_state(ms("C", t, 10.0, circuit={"locked_up": False, "locked_down": False, "dist_up_pct": 5.0, "dist_down_pct": 12.0}))
    eng.on_state(ms("D", t, 10.0, circuit={}))                                  # no circuit data → not counted
    eng.on_state(ms("Z", t, 10.0, circuit={"locked_down": True}))
    cross, sector = eng.context_for("C", t)
    assert cross["circuit_cluster"] == {"count": 2, "share": 2 / 3, "n": 3, "locked": 1, "near": 1, "near_pct": 1.0}
    assert sector["circuit_cluster"] == cross["circuit_cluster"]
    # a stale member drops out of the current set
    later = t + timedelta(seconds=400)
    eng.on_state(ms("C", later, 10.0, circuit={"locked_up": False, "locked_down": False, "dist_up_pct": 5.0, "dist_down_pct": 12.0}))
    cross2, _ = eng.context_for("C", later)
    assert cross2["circuit_cluster"] == {"count": 0, "share": 0.0, "n": 1, "locked": 0, "near": 0, "near_pct": 1.0}
    # symbol without sector → None
    eng.on_state(ms("Q", later, 10.0, circuit={"locked_up": True}))
    cq, sq = eng.context_for("Q", later)
    assert cq["circuit_cluster"] is None and sq["sector"] is None and sq["n"] is None


def test_machinery_simultaneous_liquidity_change():
    eng = CrossEngine()
    liq0 = {"A": 1000.0, "B": 2000.0, "C": 500.0, "D": 800.0}
    factor = {"A": 0.7, "B": 0.6, "C": 1.05, "D": 1.25}
    for i in range(8):
        t = T0 + timedelta(seconds=10 * i)
        for s, v in liq0.items():
            f = 1.0 if i < 2 else factor[s]
            eng.on_state(ms(s, t, 10.0, seq=i, visible_bid_liq=v * f / 2, visible_ask_liq=v * f / 2))
    now = T0 + timedelta(seconds=70)
    got = eng.context_for("A", now)[0]["simultaneous_liquidity_change"]
    assert got["n"] == 4 and got["count"] == 3 and got["count_up"] == 1 and got["count_down"] == 2
    assert got["share"] == 0.75 and got["sign"] == -1 and abs(got["own_rel_change"] + 0.3) < 1e-9
    # no earlier window → None; a symbol with only one side observed is excluded
    eng2 = CrossEngine()
    eng2.on_state(ms("A", T0, 10.0, visible_bid_liq=10.0, visible_ask_liq=10.0))
    eng2.on_state(ms("B", T0, 10.0, visible_bid_liq=10.0))
    assert eng2.context_for("A", T0)[0]["simultaneous_liquidity_change"] is None


def test_machinery_synchronized_expansion():
    eng = CrossEngine()
    # 15 calm samples per symbol, then a burst in A and B but not C; K has a constant history
    for i in range(16):
        t = T0 + timedelta(seconds=10 * i)
        burst = i == 15
        eng.on_state(ms("A", t, 10.0, seq=i, price_velocity=(5.0 if burst else 0.2 + 0.01 * i)))
        eng.on_state(ms("B", t, 10.0, seq=i, price_velocity=(-4.0 if burst else -0.1 - 0.01 * i)))
        eng.on_state(ms("C", t, 10.0, seq=i, price_velocity=(0.3 if burst else 0.3 + 0.01 * (i % 3))))
        eng.on_state(ms("K", t, 10.0, seq=i, price_velocity=0.3))
    now = T0 + timedelta(seconds=150)
    got = eng.context_for("A", now)[0]["synchronized_expansion"]
    # K's constant baseline has no defined top decile → excluded (unknown, not "calm")
    assert got["n"] == 3 and got["count"] == 2 and abs(got["share"] - 2 / 3) < 1e-12 and got["own_in_top_decile"] is True
    assert eng.context_for("C", now)[0]["synchronized_expansion"]["own_in_top_decile"] is False
    assert eng.context_for("K", now)[0]["synchronized_expansion"]["own_in_top_decile"] is None
    # too few samples → None
    eng2 = CrossEngine()
    for i in range(5):
        t = T0 + timedelta(seconds=10 * i)
        eng2.on_state(ms("A", t, 10.0, price_velocity=1.0))
        eng2.on_state(ms("B", t, 10.0, price_velocity=1.0))
    assert eng2.context_for("A", t)[0]["synchronized_expansion"] is None


def test_machinery_none_below_thresholds():
    eng = CrossEngine()
    # unknown symbol / nothing seen
    cross, sector = eng.context_for("A", T0)
    assert all(v is None for k, v in cross.items() if k != "lead_lag_pairs_evaluated")
    assert cross["lead_lag_pairs_evaluated"] == 0 and sector["sector"] is None
    # one symbol, one point: no returns, no market, no lead/lag
    eng.on_state(ms("A", T0, 10.0))
    cross, _ = eng.context_for("A", T0)
    assert cross["symbol_return_60s"] is None and cross["market_return_60s"] is None and cross["leaders"] is None
    # two symbols with 10 bins only (< 20 overlapping): lead/lag stays None, but 60-s returns exist
    for i in range(1, 11):
        t = T0 + timedelta(seconds=10 * i)
        eng.on_state(ms("A", t, 10.0 + 0.01 * i, seq=i))
        eng.on_state(ms("B", t, 20.0 - 0.01 * i, seq=i))
    cross, _ = eng.context_for("A", t)
    assert cross["leaders"] is None and cross["laggers"] is None and cross["lead_lag_pairs_evaluated"] == 0
    assert cross["market_return_60s"] is not None and cross["symbol_vs_market_60s"] > 0
    # stale symbol (no update within max_gap) has no current return
    late = t + timedelta(seconds=200)
    eng.on_state(ms("B", late, 19.0))
    cross, _ = eng.context_for("A", late)
    assert cross["symbol_return_60s"] is None and cross["market_return_60s"] is None
    # ltp-only basis (no book) still yields returns; a mid appearing resets the basis
    eng3 = CrossEngine()
    for i in range(8):
        eng3.on_state(ms("A", T0 + timedelta(seconds=10 * i), None, ltp=5.0 + 0.1 * i))
    assert eng3.return_60s("A", T0 + timedelta(seconds=70)) is not None
    eng3.on_state(ms("A", T0 + timedelta(seconds=80), 6.0))
    assert eng3.syms["A"].basis == "mid" and len(eng3.syms["A"].logp) == 1
    assert eng3.return_60s("A", T0 + timedelta(seconds=80)) is None


def test_machinery_context_is_causal_and_deterministic():
    def run():
        eng = CrossEngine()
        eng.on_reference("A", "Bank"); eng.on_reference("B", "Bank")
        now = feed_lead_lag(eng, lag_s=30, seed=3)
        eng.on_market_stats(now, {"up": 3, "down": 1, "flat": 0})
        eng.on_market_breadth(now, 3, 1, None)
        return eng, now
    e1, now = run()
    e2, _ = run()
    c1 = json.dumps(e1.context_for("B", now), sort_keys=True, default=str)
    c2 = json.dumps(e2.context_for("B", now), sort_keys=True, default=str)
    assert c1 == c2
    # a query at an earlier instant only sees data at or before it (B did not exist before 30 s)
    early, _ = e1.context_for("B", T0 + timedelta(seconds=20))
    assert early["symbol_return_60s"] is None and early["breadth_up"] is None
    mid_t = T0 + timedelta(seconds=400)
    mid, _ = e1.context_for("B", mid_t)
    assert mid["leaders"] and mid["leaders"][0][0] == "A" and mid["breadth_up"] is None
    # repeated queries at the same instant are identical (cache is transparent)
    assert json.dumps(e1.context_for("B", now), sort_keys=True) == c1


def test_realdata_fixture_breadth_and_no_invented_context():
    """Real closed-market capture: breadth comes from the lankabd_market poll
    (up 127 / down 227 / flat 36 → n 390); books are empty and prices flat, so
    every return-based field is None and lead/lag has no pair to evaluate."""
    from tower.engine import Engine, EngineConfig
    from tower.normalize import normalize_store

    events, _ = normalize_store(FIXTURE)
    assert events, "fixture produced no events"

    def run():
        eng = Engine(EngineConfig(strict=True))
        out = []
        for ev in events:
            st = eng.process(ev)
            if st is not None:
                out.append((st.symbol, st.t, dict(st.cross), dict(st.sector)))
        return eng, out
    eng, states = run()
    assert states
    t_breadth = min(ev.t_recv for ev in events if ev.event_type.value == "MARKET_STATS")
    after = [s for s in states if s[1] >= t_breadth]
    assert after, "no states after the breadth poll"
    for _, _, cross, _ in after:
        assert (cross["breadth_up"], cross["breadth_down"], cross["breadth_n"]) == (127.0, 227.0, 390.0)
    for _, _, cross, _ in states:
        assert cross["market_return_60s"] is None or isinstance(cross["market_return_60s"], float)
        assert cross["leaders"] is None and cross["lead_lag_pairs_evaluated"] == 0
        assert cross["synchronized_expansion"] is None
    # no REFERENCE (circuit table empty in this capture) → no sector for any symbol
    assert all(sec["sector"] is None for *_, sec in states)
    # determinism across two runs
    _, states2 = run()
    a = json.dumps([(s, t.isoformat(), c, sec) for s, t, c, sec in states], sort_keys=True, default=str)
    b = json.dumps([(s, t.isoformat(), c, sec) for s, t, c, sec in states2], sort_keys=True, default=str)
    assert a == b
