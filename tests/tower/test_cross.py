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


def test_machinery_market_volume_from_polls():
    """market_volume_60s / market_trades_60s: increment over the latest completed poll
    interval, normalised to 60 s, with the real span and poll age; None with one poll,
    after a reset (negative increment) or when the last poll is stale. String payload
    values (raw feed) are parsed, never summed as text."""
    eng = CrossEngine()
    eng.on_state(ms("A", T0, 10.0))
    t0, t1, t2 = T0, T0 + timedelta(seconds=62), T0 + timedelta(seconds=124)
    eng.on_market_stats(t0, {"market_volume": 1_000_000.0, "market_trades": 500.0, "up": "127", "down": "227", "flat": "36"})
    eng.on_market_breadth(t0, "127", "227", None)
    c = eng.context_for("A", t0 + timedelta(seconds=10))[0]
    assert c["market_volume_60s"] is None and c["market_trades_60s"] is None      # a single poll: no increment
    assert (c["breadth_up"], c["breadth_down"], c["breadth_n"]) == (127.0, 227.0, 390.0)
    eng.on_market_stats(t1, {"market_volume": 1_006_200.0, "market_trades": 562.0})
    before, _ = eng.context_for("A", t1 - timedelta(seconds=1))                     # causal: poll not yet seen
    assert before["market_volume_60s"] is None
    c = eng.context_for("A", t1 + timedelta(seconds=8))[0]
    assert abs(c["market_volume_60s"] - 6200.0 * 60.0 / 62.0) < 1e-9
    assert c["market_volume_span_s"] == 62.0 and c["market_volume_age_s"] == 8.0
    assert abs(c["market_trades_60s"] - 62.0 * 60.0 / 62.0) < 1e-9
    # the value holds until the next poll (step function), then moves to the new interval
    eng.on_market_stats(t2, {"market_volume": 1_030_000.0, "market_trades": 700.0})
    assert abs(eng.context_for("A", t2 - timedelta(seconds=1))[0]["market_volume_60s"] - 6000.0) < 1e-9
    c2 = eng.context_for("A", t2)[0]
    assert abs(c2["market_volume_60s"] - 23_800.0 * 60.0 / 62.0) < 1e-9 and c2["market_volume_age_s"] == 0.0
    # stale last poll → None; a reset (cumulative total went down) → None
    assert eng.context_for("A", t2 + timedelta(seconds=181))[0]["market_volume_60s"] is None
    t3 = t2 + timedelta(seconds=60)
    eng.on_market_stats(t3, {"market_volume": 5_000.0, "market_trades": 3.0})
    c3 = eng.context_for("A", t3 + timedelta(seconds=1))[0]
    assert c3["market_volume_60s"] is None and c3["market_trades_60s"] is None
    # a poll without totals (watch-derived breadth) does not create a volume point
    eng2 = CrossEngine()
    eng2.on_state(ms("A", T0, 10.0))
    eng2.on_market_stats(t0, {"up": 3, "down": 1, "flat": 0, "kind": "breadth_from_watch"})
    eng2.on_market_stats(t1, {"up": 3, "down": 1, "flat": 0, "kind": "breadth_from_watch"})
    assert eng2.context_for("A", t1)[0]["market_volume_60s"] is None


def test_machinery_out_of_order_state_keeps_latest_snapshot():
    """An older state arriving after a newer one is inserted into the time-indexed
    paths (kept sorted) but must not overwrite the latest circuit / pressure snapshot."""
    eng = CrossEngine()
    for s in ("A", "B"):
        eng.on_reference(s, "Bank")
    t_new, t_old = T0 + timedelta(seconds=100), T0 + timedelta(seconds=30)
    eng.on_state(ms("B", t_new, 10.0, circuit={"locked_up": False, "dist_up_pct": 9.0}, pressure_direction=1, pressure_strength=0.5))
    eng.on_state(ms("A", t_new, 11.0, seq=2, circuit={"locked_up": True, "dist_up_pct": 0.0}, pressure_direction=1, pressure_strength=1.0))
    eng.on_state(ms("A", t_old, 10.0, seq=1, circuit={"locked_up": False, "dist_up_pct": 9.0}, pressure_direction=-1, pressure_strength=1.0))
    assert eng.syms["A"].logp.ts == sorted(eng.syms["A"].logp.ts) and eng.syms["A"].last_t == (t_new - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
    cross, sector = eng.context_for("A", t_new)
    assert cross["circuit_cluster"]["locked"] == 1 and cross["circuit_cluster"]["n"] == 2
    assert sector["sector_pressure"] == 0.75 and sector["sector_pressure_n"] == 2
    # the older point is visible to a query at its own time (causal path), not to the snapshot
    assert eng.return_60s("A", t_new) is not None and abs(eng.return_60s("A", t_new) - math.log(11.0 / 10.0)) < 1e-12


def test_machinery_duplicate_timestamp_velocity_uses_last_sample():
    """Two velocity samples at the same instant: the later one is the current sample and
    the cached top-decile verdict is refreshed (path version, not length, keys the cache)."""
    eng = CrossEngine()
    for i in range(15):
        t = T0 + timedelta(seconds=10 * i)
        eng.on_state(ms("A", t, 10.0, seq=i, price_velocity=0.2 + 0.01 * i))
        eng.on_state(ms("B", t, 10.0, seq=i, price_velocity=0.2 + 0.01 * i))
    t = T0 + timedelta(seconds=150)
    eng.on_state(ms("A", t, 10.0, seq=15, price_velocity=0.2))
    eng.on_state(ms("B", t, 10.0, seq=15, price_velocity=0.2))
    assert eng.context_for("A", t)[0]["synchronized_expansion"]["own_in_top_decile"] is False
    eng.on_state(ms("A", t, 10.0, seq=16, price_velocity=9.0))
    got = eng.context_for("A", t)[0]["synchronized_expansion"]
    assert got["own_in_top_decile"] is True and got["count"] == 1 and got["n"] == 2


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
    # market volume: two identical polls 22.5 s apart → a completed interval with zero increment
    # (observed, the market is closed) from the second poll on; None before it and once stale
    polls = sorted(ev.t_recv for ev in events if ev.event_type.value == "MARKET_STATS")
    assert len(polls) == 2
    for _, t, cross, _ in states:
        if t < polls[1]:
            assert cross["market_volume_60s"] is None and cross["market_volume_span_s"] is None
        elif (t - polls[1]).total_seconds() <= 180:
            assert cross["market_volume_60s"] == 0.0 and cross["market_trades_60s"] == 0.0
            assert abs(cross["market_volume_span_s"] - (polls[1] - polls[0]).total_seconds()) < 1e-6
        else:
            assert cross["market_volume_60s"] is None
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


def test_machinery_breadth_history_between_polls_and_payload_t_key():
    """Breadth is the latest poll at or before ``now``: a query between two polls sees the
    earlier one (not None), and a payload carrying its own ``t`` key cannot clobber the
    poll time used for the same-instant ``flat`` fallback."""
    eng = CrossEngine()
    eng.on_state(ms("A", T0, 10.0))
    t1 = T0 + timedelta(seconds=60)
    eng.on_market_stats(T0, {"up": 1, "down": 2, "flat": 3, "t": "2026-09-06T04:00:00Z"})
    eng.on_market_breadth(T0, 1, 2, None)
    eng.on_market_stats(t1, {"up": 5, "down": 6, "flat": 7, "t": "junk"})
    eng.on_market_breadth(t1, 5, 6, None)
    between = eng.context_for("A", T0 + timedelta(seconds=30))[0]
    assert (between["breadth_up"], between["breadth_down"], between["breadth_n"]) == (1.0, 2.0, 6.0)
    assert between["breadth_age_s"] == 30.0
    after = eng.context_for("A", t1 + timedelta(seconds=1))[0]
    assert (after["breadth_up"], after["breadth_n"], after["breadth_age_s"]) == (5.0, 18.0, 1.0)
    assert eng.context_for("A", T0 - timedelta(seconds=1))[0]["breadth_up"] is None
    # an out-of-order (older) poll is inserted at its own time, never as the latest
    t_old = T0 + timedelta(seconds=20)
    eng.on_market_stats(t_old, {"up": 9, "down": 9, "flat": 0})
    eng.on_market_breadth(t_old, 9, 9, None)
    assert eng.context_for("A", T0 + timedelta(seconds=25))[0]["breadth_up"] == 9.0
    assert eng.context_for("A", t1 + timedelta(seconds=1))[0]["breadth_up"] == 5.0


def test_machinery_nonfinite_prices_are_not_observations():
    """A NaN / inf / non-positive mid or ltp adds no log-price point, so no NaN can leak
    into the 60-s returns or the market median (None, never NaN)."""
    eng = CrossEngine()
    for i, bad in enumerate((float("nan"), float("inf"), 0.0, -5.0)):
        eng.on_state(ms("A", T0 + timedelta(seconds=10 * i), bad, seq=i))
        eng.on_state(ms("B", T0 + timedelta(seconds=10 * i), None, seq=i, ltp=bad))
    assert len(eng.syms["A"].logp) == 0 and eng.syms["A"].basis is None
    assert len(eng.syms["B"].logp) == 0 and eng.syms["B"].basis is None
    for i in range(4, 12):
        t = T0 + timedelta(seconds=10 * i)
        eng.on_state(ms("A", t, 10.0 + 0.01 * i, seq=i))
        eng.on_state(ms("B", t, 20.0, seq=i))
    eng.on_state(ms("A", t, float("inf"), seq=99))      # a later bad value is ignored, the path stays finite
    cross, _ = eng.context_for("A", t)
    assert cross["symbol_return_60s"] is not None and math.isfinite(cross["symbol_return_60s"])
    assert cross["market_return_60s"] is not None and math.isfinite(cross["market_return_60s"])
    assert math.isfinite(cross["symbol_vs_market_60s"])
    # NaN velocity / liquidity are not samples either
    eng.on_state(ms("A", t, 10.5, seq=100, price_velocity=float("nan"), visible_bid_liq=float("nan"), visible_ask_liq=5.0))
    assert len(eng.syms["A"].velocity) == 0 and len(eng.syms["A"].liq) == 0


def test_machinery_one_sided_book_counts_as_liquidity_drop():
    """When a book side disappears (``one_sided``), its visible liquidity is an observed zero:
    the symbol's liquidity halves and it counts as a changer — it is not read as unchanged.
    A side missing without the one-sided flag (unobserved) still adds no point."""
    eng = CrossEngine()
    for i in range(8):
        t = T0 + timedelta(seconds=10 * i)
        eng.on_state(ms("A", t, 10.0, seq=i, visible_bid_liq=500.0, visible_ask_liq=500.0))
        gone = i >= 4
        eng.on_state(ms("B", t, 10.0, seq=i, visible_bid_liq=500.0, visible_ask_liq=(None if gone else 500.0),
                        one_sided=gone))
        eng.on_state(ms("C", t, 10.0, seq=i, visible_bid_liq=500.0, visible_ask_liq=(None if gone else 500.0)))
    got = eng.context_for("B", T0 + timedelta(seconds=70))[0]["simultaneous_liquidity_change"]
    # A unchanged, B halved (one-sided), C's last two-sided point (t = 30 s, within max_gap) reads unchanged
    assert got["n"] == 3 and got["count"] == 1 and got["count_down"] == 1 and got["sign"] == -1
    assert abs(got["own_rel_change"] + 0.5) < 1e-12
    assert len(eng.syms["C"].liq) == 4 and len(eng.syms["B"].liq) == 8
    assert eng.context_for("C", T0 + timedelta(seconds=70))[0]["simultaneous_liquidity_change"]["own_rel_change"] == 0.0


def test_machinery_pearson_constant_series_is_undefined_and_bounded():
    """A constant (even non-zero) series on the overlap has no correlation (NaN, never a
    rounding residue), identical series give exactly 1, and |corr| never exceeds 1."""
    import numpy as np

    n = 30
    x = np.zeros(90)
    x[:n] = 3.3e-4
    vx = np.zeros(90, dtype=bool)
    vx[:n] = True
    rng = np.random.default_rng(5)
    y = np.zeros(90)
    y[:n] = rng.normal(size=n) * 1e-3
    Y = np.stack([y, x, -y, y * 2.0 + 1e-3])
    M = np.stack([vx] * 4)
    corr, cnt = CrossEngine._masked_corr(x, vx, Y, M)
    assert cnt.tolist() == [30.0] * 4 and all(math.isnan(c) for c in corr)
    corr, _ = CrossEngine._masked_corr(y, vx, Y, M)
    assert math.isnan(corr[1]) and corr[0] == 1.0 and corr[2] == -1.0 and abs(corr[3] - 1.0) < 1e-12
    assert all(abs(c) <= 1.0 for c in corr if not math.isnan(c))
    # engine level: a symbol whose price never moves has no lead/lag relation with anyone
    eng = CrossEngine()
    a = random_walk_mids(1200 // 5 + 1, seed=4)
    for i, p in enumerate(a):
        t = T0 + timedelta(seconds=5 * i)
        eng.on_state(ms("A", t, p, seq=i))
        eng.on_state(ms("F", t, 42.0, seq=i))
    cross, _ = eng.context_for("A", t)
    assert cross["lead_lag_pairs_evaluated"] == 1 and cross["leaders"] == [] and cross["laggers"] == []
