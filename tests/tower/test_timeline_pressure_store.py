"""Machinery tests for the integration layer: pressure rules, layer timeline
state machines (with durations and history), and the state store."""
import json
import os
from datetime import datetime, timedelta, timezone

from tower.mechanics.base import StateHistory
from tower.pressure import fill_pressure
from tower.state import MarketState, MechanismState
from tower.store import StateStore, read_states, read_timeline
from tower.timeline import Timeline

T0 = datetime(2026, 9, 6, 4, 0, tzinfo=timezone.utc)


def ms_at(i, **kw):
    m = MarketState(symbol="X", t=T0 + timedelta(seconds=15 * i), seq=i, session_phase="CONTINUOUS")
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def test_machinery_pressure_blend_direction_persistence_reversal():
    hist = StateHistory()
    # bid-heavy book, buy flow → +1, strength grows; persistence accumulates
    for i in range(5):
        m = ms_at(i, imb_weighted=0.6, imb_topk=0.5, imb_l1=0.4, signed_flow_window=800.0, volume_only_response=1000.0)
        fill_pressure(m, hist)
        hist.push(m)
    assert m.book_pressure == 0.5 * 0.6 + 0.3 * 0.5 + 0.2 * 0.4
    assert abs(m.trade_pressure - 0.8) < 1e-9
    assert m.pressure_direction == 1 and 0.6 < m.pressure_strength <= 1.0
    assert m.pressure_persistence_s == 60.0 and m.pressure_reversal is False
    assert abs(m.pressure_divergence - (m.book_pressure - m.trade_pressure)) < 1e-12
    # flip: strong sell → reversal flagged within lookback
    m2 = ms_at(5, imb_weighted=-0.7, imb_topk=-0.6, imb_l1=-0.5, signed_flow_window=-900.0, volume_only_response=1000.0)
    fill_pressure(m2, hist)
    assert m2.pressure_direction == -1 and m2.pressure_reversal is True and m2.pressure_persistence_s == 0.0
    # nothing observable → None, never 0
    m3 = ms_at(6)
    fill_pressure(m3, hist)
    assert m3.combined_pressure is None and m3.pressure_direction is None and m3.pressure_strength is None


def test_machinery_pressure_layer_transitions_and_durations():
    tl = Timeline()
    seq = [
        dict(pressure_direction=0, pressure_strength=0.1),
        dict(pressure_direction=1, pressure_strength=0.5),                                    # → building
        dict(pressure_direction=1, pressure_strength=0.6, price_velocity=1.5),                # → expansion
        dict(pressure_direction=1, pressure_strength=0.6, price_velocity=1.5, price_only_response=-2.0),  # → rejection
        dict(pressure_direction=-1, pressure_strength=0.5, pressure_reversal=True),           # → reversal
        dict(pressure_direction=0, pressure_strength=0.0),                                    # → balanced
    ]
    states = []
    for i, kw in enumerate(seq):
        m = ms_at(i, **kw)
        tl.on_state(m)
        states.append(m.layer_states["pressure"])
    assert states == ["balanced", "pressure_building", "expansion", "rejection", "reversal", "balanced"]
    hist = tl.history["X"]
    assert [(t.from_state, t.to_state) for t in hist if t.layer == "pressure"][:2] == \
        [("balanced", "pressure_building"), ("pressure_building", "expansion")]
    assert all(t.duration_prev_s == 15.0 for t in hist if t.layer == "pressure")


def test_machinery_liquidity_and_circuit_layers():
    tl = Timeline()
    liq = [dict(), dict(liquidity_depletion=0.5), dict(resilience_state="recovering"),
           dict(resilience_state="recovered", price_velocity=1.0), dict(liquidity_vacuum=True)]
    got = []
    for i, kw in enumerate(liq):
        m = ms_at(i, **kw)
        tl.on_state(m)
        got.append(m.layer_states["liquidity"])
    assert got == ["normal", "depletion", "recovery", "continuation", "vacuum"]
    tl2 = Timeline()
    circ = [dict(circuit={}), dict(circuit={"dist_up_pct": 1.5, "approach_velocity": 2.0}),
            dict(circuit={"hit_up": True}), dict(circuit={"locked_up": True}), dict(circuit={"locked_up": False, "dist_up_pct": 0.5}),
            dict(circuit={"locked_up": True}), dict(circuit={"locked_up": True})]
    got = []
    for i, kw in enumerate(circ):
        m = ms_at(i, **kw)
        tl2.on_state(m)
        got.append(m.layer_states["circuit"])
    assert got == ["free", "approach", "hit", "lock", "unlock", "relock", "relock"]


def test_machinery_accumulation_streak_and_mechanism_episodes():
    tl = Timeline()
    def mech(name, state):
        return {name: MechanismState(name=name, family="f", score=0.8, state=state)}
    seq = [dict(mechanisms=mech("accumulation_like", "active")),
           dict(mechanisms={**mech("accumulation_like", "active"), **mech("ignition", "active")}, price_velocity=1.0),
           dict(mechanisms=mech("ignition", "confirmed"), price_velocity=1.0),
           dict(mechanisms=mech("false_breakout", "active"), price_velocity=-1.0)]
    got = []
    for i, kw in enumerate(seq):
        m = ms_at(i, **kw)
        tl.on_state(m)
        got.append(m.layer_states["accumulation"])
    assert got == ["accumulation_like", "breakout", "continuation", "failed_pressure"]
    mech_tr = [(t.layer, t.from_state, t.to_state) for t in tl.history["X"] if t.layer.startswith("mechanism:")]
    assert ("mechanism:accumulation_like", "inactive", "active") in mech_tr
    assert ("mechanism:ignition", "active", "confirmed") in mech_tr
    tl3 = Timeline()
    for i, c in enumerate([{"consecutive_upper_streak": 1}, {"consecutive_upper_streak": 2},
                           {"consecutive_upper_streak": 3, "streak_weakening": True}, {"break_day": True}]):
        m = ms_at(i, circuit=c)
        tl3.on_state(m)
        assert m.layer_states["streak"] == ["none", "streak", "weakening", "break"][i]


def test_machinery_state_store_roundtrip(tmp_path):
    st = StateStore(str(tmp_path))
    tl = Timeline()
    hashes = []
    for i in range(3):
        m = ms_at(i, best_bid=10.0, best_ask=10.1 + i * 0.1, pressure_direction=1, pressure_strength=0.5)
        tl.on_state(m)
        st.append(m)
        hashes.append(m.state_hash())
    st.write_run({"x": 1})
    st.close()
    rows = read_states(str(tmp_path), "X")
    assert len(rows) == 3 and abs(rows[2]["best_ask"] - 10.3) < 1e-9 and rows[0]["layer_states"]["pressure"] == "pressure_building"
    tlr = read_timeline(str(tmp_path))
    assert tlr and tlr[0]["layer"] == "pressure" and tlr[0]["symbol"] == "X"
    run = json.load(open(tmp_path / "RUN.json"))
    assert run["final_state_hash"]["X"] == hashes[-1] and run["states_written"]["X"] == 3
    latest = json.load(open(tmp_path / "latest.json"))
    assert latest["X"]["seq"] == 2
    # determinism of the hash: same content → same hash
    assert ms_at(1, best_bid=10.0).state_hash() == ms_at(1, best_bid=10.0).state_hash()
    assert ms_at(1, best_bid=10.0).state_hash() != ms_at(1, best_bid=10.1).state_hash()
