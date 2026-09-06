"""Observation Tower UI: API server over a synthetic state store, the real
fixture replay store, and a headless-browser check of the rendered page.

test_machinery_*  synthetic store written with tower.state.MarketState.to_dict()
test_realdata_*   a store produced by replaying the real closed-market fixture
"""
from __future__ import annotations

import glob
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tower.state import MarketState, MechanismState, SourceStatus, Transition
from tower.ui.server import StoreReader, create_app

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
T0 = datetime(2026, 9, 6, 4, 0, 0, 250, tzinfo=timezone.utc)     # microseconds, like every real t_recv
STEP_S = 10


# ---------------------------------------------------------------------- synthetic store
def _state(sym: str, i: int) -> MarketState:
    """A populated MarketState for update ``i`` (10 s apart): bids build up, the ask
    side thins, absorption is active from i>=2 and resolves at i>=4."""
    t = T0 + timedelta(seconds=STEP_S * i)
    bids = [(10.0, 100.0 + 40 * i), (9.9, 300.0), (9.8, 900.0)]
    asks = [(10.1, max(50.0, 500.0 - 60 * i)), (10.2, 200.0), (10.3, 800.0)]
    vb, va = sum(q for _, q in bids), sum(q for _, q in asks)
    absorption_state = "inactive" if i < 2 else ("active" if i < 4 else "resolved")
    ms = MarketState(
        symbol=sym, t=t, seq=i + 1, session_phase="CONTINUOUS",
        best_bid=10.0, best_ask=10.1, bid_qty1=bids[0][1], ask_qty1=asks[0][1], spread=0.1, spread_ticks=1.0,
        mid=10.05, microprice=10.0 + 0.1 * asks[0][1] / (bids[0][1] + asks[0][1]), ltp=10.1 if i < 3 else 10.2, tick_size=0.1,
        bids=bids, asks=asks, bid_orders=[3, None, 7], ask_orders=None, book_source="lankabd_depth", book_age_s=1.5, empty_book=False,
        imb_l1=(bids[0][1] - asks[0][1]) / (bids[0][1] + asks[0][1]), imb_topk=(vb - va) / (vb + va),
        visible_bid_liq=vb, visible_ask_liq=va, depth_ratio=vb / (vb + va),
        depth_concentration_bid=0.51, hollow_bid=0,
        wall_bid={"price": 9.8, "qty": 900.0, "share": 900.0 / vb, "persistence_s": 30.0 + STEP_S * i, "migrated_ticks": None, "dist_ticks": 2.0},
        wall_ask={"price": 10.3, "qty": 800.0, "share": 800.0 / va, "persistence_s": 12.0, "migrated_ticks": 0.0, "dist_ticks": 2.0},
        ofi=15.0 * i, ofi_window=40.0 * i, book_change_velocity=3.5,
        trade_count=100.0 + 3 * i, trade_volume=5000.0 + 300 * i, interval_trades=3.0, interval_volume=300.0, interval_vwap=10.1,
        trade_flow_direction=0.6, trade_intensity=18.0, trade_acceleration=2.0, signed_flow_window=180.0 * i,
        last_print={"t": t.isoformat(), "price": 10.1, "qty": 100.0, "trade_id": None, "aggressor": None, "direction": 1, "direction_rule": "quote"},
        tape_source="lankabd_tape", tape_age_s=2.0,
        price_velocity=0.5 * i, price_only_response=0.5 * i, volume_only_response=300.0 * i, failed_response=False,
        liquidity_depletion=0.1 * i, liquidity_replenishment=None, liquidity_retreat=False, liquidity_vacuum=False,
        pressure_direction=1, pressure_strength=min(1.0, 0.2 * (i + 1)), pressure_persistence_s=STEP_S * i, pressure_reversal=False,
        book_pressure=min(1.0, 0.2 * (i + 1)), trade_pressure=0.6, combined_pressure=min(1.0, 0.15 * (i + 1)), pressure_divergence=0.1,
        resilience_state="none", recovery_curve=[(0.0, 0.0), (5.0, 0.4), (12.0, 0.9)] if i >= 3 else None,
        circuit={"upper_limit": 11.0, "lower_limit": 9.0, "tick": 0.1, "rule_source": "lankabd_circuit", "unverified": False,
                 "price": 10.05, "price_basis": "mid", "dist_up_ticks": 9.5, "dist_down_ticks": 10.5, "dist_up_pct": 9.45, "dist_down_pct": 10.4,
                 "nearer_limit": "up", "approach_velocity": 0.2 * i, "hit_up": False, "hit_down": False, "locked_up": False, "locked_down": False,
                 "time_locked_s": 0.0, "unlock_count": 0, "relock_count": 0, "queue_at_upper": None, "consecutive_upper_streak": 0,
                 "consecutive_lower_streak": 0, "streak_weakening": None, "break_day": None},
        auction={"phase": "CONTINUOUS", "indicative_price": None, "auction_pressure": None},
        cross={"breadth_up": 150.0, "breadth_down": 120.0, "breadth_n": 300.0, "breadth_net": 0.1, "breadth_age_s": 4.0,
               "symbol_return_60s": 0.002, "market_return_60s": 0.0005, "symbol_vs_market_60s": 0.0015, "n_symbols_with_return": 2,
               "leaders": [["BETA", 10.0, 0.61]], "laggers": [], "lead_lag_pairs_evaluated": 1, "basket_sync": None},
        sector={"sector": "Bank", "sector_source": "lankabd_circuit", "n": 2, "sector_return_60s": None},
        sources={
            "lankabd_depth": SourceStatus("lankabd_depth", last_update=t, freshness_s=1.2, updates=i + 1, cadence_s=6.0,
                                          field_coverage=("bid_levels", "ask_levels", "ltp"), agreement={"book": True, "ltp": i < 4}),
            "dsebd_depth": SourceStatus("dsebd_depth", last_update=t - timedelta(seconds=25), freshness_s=25.0, stale=True, duplicate=True,
                                        updates=i, duplicates=1, gaps=1, cadence_s=6.5,
                                        disagreement={"ltp": {"this": 10.1, "other": 10.2, "other_source": "lankabd_depth"}} if i >= 4 else {}),
        },
        source_agreement={"book": True, "ltp": i < 4}, provenance={"book": "lankabd_depth", "ltp": "lankabd_depth"},
        mechanisms={
            "absorption": MechanismState("absorption", "accumulation", score=min(0.95, 0.3 + 0.2 * i), state=absorption_state,
                                         start_time=T0 + timedelta(seconds=2 * STEP_S) if i >= 2 else None,
                                         duration_s=STEP_S * (i - 2) if i >= 2 else 0.0,
                                         evidence={"direction": 1, "absorbed_volume": 300.0 * i, "touch_qty": bids[0][1], "peak_score": 0.9},
                                         baseline={"imb_l1": 0.1}),
            "ignition": MechanismState("ignition", "breakout", score=0.4, state="building", start_time=t, duration_s=0.0,
                                       evidence={"direction": 1, "velocity": 0.5 * i}),
            "queue_pull_stack": MechanismState("queue_pull_stack", "queue", score=0.0, state="inactive",
                                               evidence={"missing": ["bid_orders", "ask_orders"]}),
        },
        active_mechanisms=["absorption"] if absorption_state == "active" else [],
        layer_states={"pressure": "balanced" if i == 0 else "pressure_building", "liquidity": "normal", "circuit": "free",
                      "accumulation": "none", "streak": "none"},
        layer_since={"pressure": T0 if i == 0 else T0 + timedelta(seconds=STEP_S)},
    )
    if i == 1:
        ms.transitions = [Transition(t, "balanced", "pressure_building", "pressure", STEP_S)]
    if i == 2:
        ms.transitions = [Transition(t, "inactive", "active", "mechanism:absorption", 0.0)]
    if i == 4:
        ms.transitions = [Transition(t, "active", "resolved", "mechanism:absorption", 2 * STEP_S)]
    return ms


def _write_store(root: str, counts=None) -> dict:
    """Write states/<SYM>.jsonl, timeline.jsonl, metrics.json, latest.json, RUN.json; return {sym: [state dicts]}."""
    counts = counts or {"ALPHA": 6, "BETA": 3}
    os.makedirs(os.path.join(root, "states"), exist_ok=True)
    latest, out = {}, {}
    with open(os.path.join(root, "timeline.jsonl"), "w") as tl:
        for sym, n in counts.items():
            rows = []
            with open(os.path.join(root, "states", f"{sym}.jsonl"), "w") as fh:
                for i in range(n):
                    ms = _state(sym, i)
                    d = ms.to_dict()
                    fh.write(json.dumps(d, separators=(",", ":")) + "\n")
                    rows.append(d)
                    for tr in ms.transitions:
                        tl.write(json.dumps({"symbol": sym, "t": tr.t.isoformat(), "from_state": tr.from_state, "to_state": tr.to_state,
                                             "layer": tr.layer, "duration_prev_s": tr.duration_prev_s}) + "\n")
            latest[sym] = rows[-1]
            out[sym] = rows
    json.dump(latest, open(os.path.join(root, "latest.json"), "w"))
    json.dump({"events_in": 120, "states_out": sum(counts.values()), "parse_failures": 0, "sequence_gaps": 1, "duplicates": 4,
               "reconstruction_failures": 0, "backlog": 0, "last_event_lag_s": None, "max_event_lag_s": 0.0,
               "ingest_rate_eps": 12.5, "processing_rate_sps": 9.0, "duplicate_rate": 4 / 120, "stale_sources": ["dsebd_depth"], "symbols": len(counts)},
              open(os.path.join(root, "metrics.json"), "w"))
    json.dump({"capture": "synthetic", "symbols": list(counts), "events": 120, "processed": 120,
               "final_state_hash": {s: "deadbeef" for s in counts}, "states_written": counts}, open(os.path.join(root, "RUN.json"), "w"))
    return out


@pytest.fixture
def store(tmp_path):
    root = str(tmp_path / "store")
    rows = _write_store(root)
    return root, rows


@pytest.fixture
def client(store):
    root, rows = store
    with TestClient(create_app(root)) as c:
        yield c, rows, root


# ---------------------------------------------------------------------- API machinery
def test_machinery_symbols_and_latest_state(client):
    c, rows, _ = client
    r = c.get("/api/symbols")
    assert r.status_code == 200
    syms = {s["symbol"]: s for s in r.json()["symbols"]}
    assert set(syms) == {"ALPHA", "BETA"}
    a = syms["ALPHA"]
    assert a["count"] == 6 and a["last_t"] == rows["ALPHA"][-1]["t"] and a["first_t"] == rows["ALPHA"][0]["t"]
    assert a["session_phase"] == "CONTINUOUS" and a["active_mechanisms"] == [] and a["n_mechanisms"] == 3
    assert a["layer_states"]["pressure"] == "pressure_building" and a["sector"] == "Bank"
    b = syms["BETA"]
    assert b["count"] == 3 and b["active_mechanisms"] == ["absorption"]     # i=2 → absorption active

    r = c.get("/api/state/ALPHA").json()
    assert r["index"] == 5 and r["count"] == 6 and r["is_last"] and r["next_t"] is None and r["prev_t"] == rows["ALPHA"][4]["t"]
    assert r["state"] == rows["ALPHA"][-1]
    assert r["state"]["liquidity_replenishment"] is None                       # null stays null
    assert c.get("/api/state/NOPE").status_code == 404
    assert c.get("/api/state/ALPHA?at=not-a-time").status_code == 400


def test_machinery_state_at_or_before(client):
    c, rows, _ = client
    ts = [r["t"] for r in rows["ALPHA"]]
    # strictly between state 2 and state 3 → state 2
    mid = (T0 + timedelta(seconds=STEP_S * 2 + 4)).isoformat()
    r = c.get(f"/api/state/ALPHA?at={mid}").json()
    assert r["index"] == 2 and r["t"] == ts[2] and r["state"]["seq"] == 3
    assert r["prev_t"] == ts[1] and r["next_t"] == ts[3] and not r["is_last"]
    # exactly at a state time (microseconds intact) → that state (<=)
    assert ".000250" in ts[3]
    r = c.get(f"/api/state/ALPHA?at={ts[3]}").json()
    assert r["index"] == 3
    # the same instant truncated to milliseconds lies BEFORE the state → the previous one (never rounded up)
    assert c.get("/api/state/ALPHA?at=" + ts[3][:23] + "Z").json()["index"] == 2
    # next_t / prev_t round-trip exactly (what the step buttons send)
    assert c.get(f"/api/state/ALPHA?at={r['next_t']}").json()["index"] == 4
    assert c.get(f"/api/state/ALPHA?at={r['prev_t']}").json()["index"] == 2
    # 'Z' suffix accepted; '+' of the offset arriving URL-decoded as a space is restored
    r = c.get("/api/state/ALPHA?at=" + ts[4].replace("+00:00", "Z")).json()
    assert r["index"] == 4
    assert c.get("/api/state/ALPHA?at=" + ts[4].replace("+00:00", " 00:00")).json()["index"] == 4
    # a space date/time separator (ISO-8601 allows it) is a time, not an offset
    assert c.get("/api/state/ALPHA?at=2026-09-06 04:00:10").json()["index"] == 0
    assert c.get("/api/state/ALPHA?at=2026-09-06 04:01").json()["index"] == 5
    assert c.get("/api/state/ALPHA?at=2026-09-06 04:00").status_code == 404      # 250 µs before the first state
    assert c.get("/api/state/ALPHA?at=2026-13-40T00:00:00Z").status_code == 400
    # far future → last; before the first → 404 (nothing observable yet, never a made-up state)
    assert c.get("/api/state/ALPHA?at=2030-01-01T00:00:00Z").json()["index"] == 5
    assert c.get("/api/state/ALPHA?at=2020-01-01T00:00:00Z").status_code == 404
    # seek records the cursor and returns the same shape
    r = c.post(f"/api/replay/seek?symbol=ALPHA&at={mid}")
    assert r.status_code == 200 and r.json()["index"] == 2
    rp = c.get("/api/replay").json()
    assert rp["cursor"]["ALPHA"]["index"] == 2 and rp["count"] == 9
    assert rp["symbols"]["ALPHA"]["count"] == 6 and rp["first_t"] == ts[0] and rp["last_t"] == ts[5]


def test_machinery_history_fields_range_and_downsampling(client, tmp_path):
    c, rows, _ = client
    ts = [r["t"] for r in rows["ALPHA"]]
    r = c.get("/api/history/ALPHA?fields=mid,ltp,circuit.dist_up_pct,wall_bid.qty,liquidity_replenishment,nope").json()
    assert r["n"] == 6 and r["n_total"] == 6 and not r["downsampled"]
    p = r["points"][0]
    assert p["t"] == ts[0] and p["mid"] == 10.05 and p["ltp"] == 10.1 and p["circuit.dist_up_pct"] == 9.45 and p["wall_bid.qty"] == 900.0
    assert p["liquidity_replenishment"] is None and p["nope"] is None
    assert r["points"][-1]["ltp"] == 10.2
    # episodes from the timeline: absorption active at i=2, resolved at i=4
    assert r["episodes"] == [{"name": "absorption", "start": ts[2], "end": ts[4], "peak_state": "active", "outcome": "resolved"}]
    # time range
    r = c.get(f"/api/history/ALPHA?fields=mid&from={ts[1]}&to={ts[3]}").json()
    assert [p["t"] for p in r["points"]] == ts[1:4] and r["n_total"] == 3
    # explicit max_points keeps first and last
    r = c.get("/api/history/ALPHA?fields=mid&max_points=3").json()
    assert r["n"] == 3 and r["downsampled"] and r["points"][0]["t"] == ts[0] and r["points"][-1]["t"] == ts[-1]
    # a big store is capped at 2000 points by default
    big = str(tmp_path / "big")
    _write_store(big, {"BIG": 2500})
    with TestClient(create_app(big)) as c2:
        r = c2.get("/api/history/BIG?fields=mid,ofi").json()
        assert r["n_total"] == 2500 and r["n"] <= 2000 and r["downsampled"]
        assert r["points"][0]["t"] == (T0).isoformat() and r["points"][-1]["t"] == (T0 + timedelta(seconds=STEP_S * 2499)).isoformat()
        assert r["points"][-1]["ofi"] == 15.0 * 2499
        assert c2.get("/api/history/BIG?fields=mid&max_points=5000").status_code == 422   # cannot exceed the cap


def test_machinery_timeline_metrics_cross(client):
    c, rows, _ = client
    ts = [r["t"] for r in rows["ALPHA"]]
    r = c.get("/api/timeline/ALPHA").json()
    assert r["n"] == 3 and [t["layer"] for t in r["transitions"]] == ["pressure", "mechanism:absorption", "mechanism:absorption"]
    assert r["transitions"][0] == {"symbol": "ALPHA", "t": ts[1], "from_state": "balanced", "to_state": "pressure_building",
                                   "layer": "pressure", "duration_prev_s": STEP_S}
    r = c.get(f"/api/timeline/ALPHA?from={ts[2]}&to={ts[3]}").json()
    assert r["n"] == 1 and r["transitions"][0]["to_state"] == "active"
    r = c.get("/api/timeline").json()
    assert r["n"] == 5 and {t["symbol"] for t in r["transitions"]} == {"ALPHA", "BETA"}
    assert c.get("/api/timeline/NOPE").status_code == 404

    m = c.get("/api/metrics").json()
    assert m["metrics_available"] and m["metrics"]["events_in"] == 120 and m["metrics"]["stale_sources"] == ["dsebd_depth"]
    assert m["run"]["final_state_hash"]["ALPHA"] == "deadbeef" and m["states"]["ALPHA"]["count"] == 6 and m["transitions"] == 5

    x = c.get("/api/cross/ALPHA").json()
    assert x["t"] == ts[5] and x["cross"]["breadth_up"] == 150.0 and x["sector"]["sector"] == "Bank"
    rel = {r_["symbol"]: r_ for r_ in x["related"]}
    assert set(rel) == {"BETA"} and rel["BETA"]["roles"] == ["leader", "sector_peer"] and rel["BETA"]["lag_s"] == 10.0 and rel["BETA"]["corr"] == 0.61
    assert rel["BETA"]["present"] and rel["BETA"]["t"] == rows["BETA"][-1]["t"] and rel["BETA"]["mid"] == 10.05
    # causal: cross at ALPHA's first time sees BETA's first state only
    x = c.get(f"/api/cross/ALPHA?at={ts[0]}").json()
    assert x["related"][0]["t"] == rows["BETA"][0]["t"]
    assert c.get("/api/latest").json()["ALPHA"] == rows["ALPHA"][-1]


def test_machinery_tailing_growing_store(client):
    c, rows, root = client
    assert c.get("/api/symbols").json()["symbols"][0]["count"] == 6
    p = os.path.join(root, "states", "ALPHA.jsonl")
    new = _state("ALPHA", 6).to_dict()
    line = json.dumps(new, separators=(",", ":"))
    with open(p, "a") as fh:                       # a partial line (no newline yet) must not be served
        fh.write(line[: len(line) // 2])
    assert c.get("/api/state/ALPHA").json()["index"] == 5
    with open(p, "a") as fh:
        fh.write(line[len(line) // 2:] + "\n")
    r = c.get("/api/state/ALPHA").json()
    assert r["index"] == 6 and r["count"] == 7 and r["state"]["seq"] == 7 and r["t"] == new["t"]
    # a brand-new symbol file appears without restart
    with open(os.path.join(root, "states", "GAMMA.jsonl"), "w") as fh:
        fh.write(json.dumps(_state("GAMMA", 0).to_dict()) + "\n")
    assert "GAMMA" in [s["symbol"] for s in c.get("/api/symbols").json()["symbols"]]
    with open(os.path.join(root, "timeline.jsonl"), "a") as fh:
        fh.write(json.dumps({"symbol": "GAMMA", "t": T0.isoformat(), "from_state": "free", "to_state": "approach", "layer": "circuit", "duration_prev_s": 0.0}) + "\n")
    assert c.get("/api/timeline/GAMMA").json()["n"] == 1


def test_machinery_late_state_and_unusable_lines(tmp_path):
    """A live tailer can release an event late: a state whose t is below its
    predecessor's. 'At or before' must stay causal in event order (a state written
    after one with t > at is never served for at), and lines without a usable frame
    time are skipped rather than turned into a state."""
    root = str(tmp_path / "store")
    os.makedirs(os.path.join(root, "states"))
    order = [0, 10, 10, 20, 15, 30]                   # seconds; two states share 10 s, 15 s arrives after 20 s
    p = os.path.join(root, "states", "LATE.jsonl")
    with open(p, "w") as fh:
        for k, s in enumerate(order):
            ms = MarketState("LATE", T0 + timedelta(seconds=s), seq=k + 1)
            fh.write(json.dumps(ms.to_dict(), separators=(",", ":")) + "\n")
            if k == 1:                                # junk between states: no t, numeric t, broken json
                fh.write('{"symbol":"LATE","t":null,"seq":99}\n{"symbol":"LATE","t":5}\n{"symbol":"LATE",\n')
    with TestClient(create_app(root)) as c:
        at = lambda s: (T0 + timedelta(seconds=s)).isoformat()
        assert c.get("/api/state/LATE").json()["count"] == 6             # junk lines never became states
        r = c.get(f"/api/state/LATE?at={at(10)}").json()
        assert r["index"] == 2 and r["state"]["seq"] == 3 and r["prev_t"] == r["t"]   # a time addresses the last of a same-t group
        r = c.get(f"/api/state/LATE?at={at(15)}").json()
        assert r["index"] == 2 and r["state"]["seq"] == 3                # the 15 s state was not yet produced then
        r = c.get(f"/api/state/LATE?at={at(20)}").json()
        assert r["index"] == 4 and r["state"]["seq"] == 5 and r["t"] == at(15)   # everything up to the late one is <= 20 s
        assert c.get(f"/api/state/LATE?at={at(25)}").json()["index"] == 4
        assert c.get(f"/api/state/LATE?at={at(30)}").json()["index"] == 5
        h = c.get(f"/api/history/LATE?fields=seq&from={at(15)}&to={at(20)}").json()
        assert [pt["seq"] for pt in h["points"]] == [4, 5]
        assert c.get("/api/symbols").json()["symbols"][0]["count"] == 6
        # exact event-order addressing reaches the first of the same-t pair (what the step buttons use)
        r = c.get("/api/state/LATE?index=1").json()
        assert r["index"] == 1 and r["state"]["seq"] == 2 and r["t"] == at(10) and r["at"] == "#1" and r["next_t"] == r["t"]
        assert c.get("/api/state/LATE?index=5").json()["state"]["seq"] == 6
        assert c.get("/api/state/LATE?index=6").status_code == 404
        assert c.get("/api/state/LATE?index=-1").status_code == 422
        assert c.post("/api/replay/seek?symbol=LATE&index=4").json()["state"]["seq"] == 5
        assert c.get("/api/replay").json()["cursor"]["LATE"] == {"at": "#4", "t": at(15), "index": 4, "count": 6}


def test_machinery_episodes_survive_a_late_transition(tmp_path):
    root = str(tmp_path / "store")
    _write_store(root, {"ALPHA": 2})
    at = lambda s: (T0 + timedelta(seconds=s)).isoformat()
    with open(os.path.join(root, "timeline.jsonl"), "w") as fh:    # replace the fixture timeline
        for t, layer, a, b in ((50, "mechanism:x", "inactive", "active"), (200, "mechanism:x", "active", "resolved"),
                               (150, "mechanism:y", "inactive", "building")):          # y's row released late
            fh.write(json.dumps({"symbol": "ALPHA", "t": at(t), "from_state": a, "to_state": b, "layer": layer, "duration_prev_s": 0.0}) + "\n")
    with TestClient(create_app(root)) as c:
        eps = c.get(f"/api/history/ALPHA?fields=mid&to={at(160)}").json()["episodes"]
        assert [(e["name"], e["end"], e["outcome"]) for e in eps] == [("x", None, None), ("y", None, None)]
        eps = c.get("/api/history/ALPHA?fields=mid").json()["episodes"]
        assert [(e["name"], e["end"], e["outcome"]) for e in eps] == [("x", at(200), "resolved"), ("y", None, None)]


def test_machinery_store_reader_direct(store):
    root, rows = store
    rd = StoreReader(root)
    rd.refresh()
    assert rd.symbols() == ["ALPHA", "BETA"]
    f = rd.file("ALPHA")
    assert len(f) == 6 and f.index_at_or_before(T0 - timedelta(seconds=1)) is None and f.index_at_or_before(None) == 5
    assert rd.state_at("ALPHA", T0 + timedelta(seconds=STEP_S * 3 + 1))["index"] == 3
    with pytest.raises(Exception):
        rd.file("NOPE")


def test_machinery_static_index_served(client):
    c, _, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    html = r.text
    assert "/static/app.js" in html and "/static/style.css" in html
    for pid in ("panel-book", "panel-flow", "panel-pressure", "panel-liquidity", "panel-response", "panel-mech", "panel-circuit",
                "panel-timeline", "panel-cross", "panel-sources", "replay", "replay-slider", "ladder", "mech-table", "sources-table"):
        assert f'id="{pid}"' in html, pid
    js = c.get("/static/app.js")
    assert js.status_code == 200 and "/api/state/" in js.text and "/api/history/" in js.text
    assert "cdn" not in js.text.lower() and "cdn" not in html.lower()          # no CDN, no build step
    assert "https://" not in html and "https://" not in js.text                # nothing external
    assert c.get("/static/style.css").status_code == 200
    assert c.get("/favicon.ico").status_code == 200


# ---------------------------------------------------------------------- real data
def test_realdata_fixture_store_served(tmp_path):
    from tower.replay import replay_hashes
    out = str(tmp_path / "out")
    h = replay_hashes(FIXTURE, out)
    assert h
    with TestClient(create_app(out)) as c:
        syms = c.get("/api/symbols").json()["symbols"]
        assert {s["symbol"] for s in syms} == set(h)
        for s in syms:
            r = c.get(f"/api/state/{s['symbol']}").json()
            st = r["state"]
            assert r["count"] == s["count"] and st["symbol"] == s["symbol"]
            assert st["spread"] is None and st["mid"] is None                 # closed market: never invented
            assert st["trade_count"] is not None and st["sources"] and len(st["mechanisms"]) >= 40
            hist = c.get(f"/api/history/{s['symbol']}?fields=ltp,mid,trade_count").json()
            assert hist["n"] == s["count"] and all(p["mid"] is None for p in hist["points"])
            assert hist["points"][-1]["trade_count"] == st["trade_count"]
            # replay position: state at the first time is index 0 or a same-time sibling ≤ it
            first = c.get(f"/api/state/{s['symbol']}?at={s['first_t']}").json()
            assert first["t"] == s["first_t"]
            assert c.get(f"/api/cross/{s['symbol']}").json()["cross"]["breadth_n"] is not None
        m = c.get("/api/metrics").json()
        assert m["metrics"]["events_in"] == 617 and m["run"]["final_state_hash"] == h
        tl = c.get("/api/timeline").json()
        assert tl["n"] >= 1


# ---------------------------------------------------------------------- browser
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch_chromium(p):
    """playwright.chromium.launch(), else the chromium under /opt/pw-browsers; skip when neither launches."""
    try:
        return p.chromium.launch()
    except Exception as e1:  # noqa: BLE001
        exes = sorted(glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/chrome-headless-shell")
                      + glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
        last = None
        for exe in exes:
            try:
                return p.chromium.launch(executable_path=exe)
            except Exception as e2:  # noqa: BLE001
                last = e2
        pytest.skip(f"chromium cannot launch: {str(e1).splitlines()[0]}; fallback executables {exes}: {last}")


def test_machinery_browser_renders_tower_and_scrubs(tmp_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    root = str(tmp_path / "store")
    rows = _write_store(root)
    with open(os.path.join(root, "states", "DUP.jsonl"), "w") as fh:      # two states sharing one frame time
        for seq in (1, 2):
            d = _state("DUP", 0).to_dict()
            d["seq"] = seq
            fh.write(json.dumps(d, separators=(",", ":")) + "\n")
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "tower.ui.server", "--store", root, "--port", str(port), "--host", "127.0.0.1"],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    shot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ui_screenshot.png")
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + "/api/symbols", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        else:
            pytest.fail("uvicorn did not come up: " + (proc.stdout.read().decode(errors="replace") if proc.poll() is not None else "still starting"))
        last, first = rows["ALPHA"][-1], rows["ALPHA"][0]
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            page = browser.new_page(viewport={"width": 1500, "height": 1300})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/")
            page.select_option("#symbol", "ALPHA")
            page.wait_for_selector("#ladder tbody tr.ladder-row", timeout=15000)
            page.wait_for_function("document.querySelector('#state-time').textContent.includes('2026-09-06')", timeout=15000)
            page.wait_for_selector("#sources-table tbody tr.src-row", timeout=15000)
            page.wait_for_selector("#mech-table tbody tr.mech-row", timeout=15000)
            page.wait_for_timeout(300)
            assert page.text_content("#state-time") == last["t"]
            # depth ladder: one row per displayed level, best bid/ask marked, walls highlighted, values from the store
            assert page.locator("#ladder tbody tr.ladder-row").count() == len(last["bids"]) + len(last["asks"])
            assert page.locator("#ladder tbody tr.bid.best td.px").text_content().strip() == "10.00"
            assert page.locator("#ladder tbody tr.ask.best td.px").text_content().strip() == "10.10"
            assert page.locator("#ladder tbody tr.bid.best td.qty span").text_content().strip() == str(int(last["bids"][0][1]))
            assert page.locator("#ladder tbody tr.wall").count() == 2
            assert page.locator("#ladder tbody tr.bid.best td.n").text_content().strip() == "3"           # bid_orders[0]
            assert page.locator("#ladder tbody tr.ask.best td.n").text_content().strip() == "—"           # ask_orders None → NOT_OBSERVABLE
            # mechanisms table: non-inactive rows by default, absorption (resolved at i=5) with its score, ignition building
            names = page.locator("#mech-table tbody tr.mech-row").all_text_contents()
            assert len(names) == 2 and any("absorption" in n and "0.950" in n and "resolved" in n for n in names) and any("ignition" in n and "building" in n for n in names)
            page.check("#mech-all")
            assert page.locator("#mech-table tbody tr.mech-row").count() == 3
            assert "missing: bid_orders,ask_orders" in page.locator("#mech-table tbody tr.mech-row.inactive").text_content()
            page.locator("#mech-table tbody tr.mech-row").first.click()
            assert page.locator("#mech-table tbody tr.mech-ev").count() == 1
            # sources: both rows with updates count, stale flag and disagreement text from the store
            src = {r_.get_attribute("data-source"): r_.text_content() for r_ in page.locator("#sources-table tbody tr.src-row").all()}
            assert set(src) == {"lankabd_depth", "dsebd_depth"}
            assert "STALE" in src["dsebd_depth"] and "10.10 vs 10.20 (lankabd_depth)" in src["dsebd_depth"]
            assert "6" in src["lankabd_depth"] and "STALE" not in src["lankabd_depth"]
            # pressure / circuit / cross values
            assert "1.000" in page.text_content("#pressure-gauges")
            assert "11.00" in page.text_content("#circuit-band") and "9.00" in page.text_content("#circuit-band")
            assert "BETA" in page.text_content("#cross-table") and "0.610" in page.text_content("#cross-table")
            # null renders as '—', never 0
            assert "—" in page.text_content("#liq-kv")
            page.screenshot(path=shot, full_page=True)
            assert os.path.getsize(shot) > 10_000
            # replay: scrub to the start → the state at/before the first time; every panel re-renders from it
            page.evaluate("(() => { const s = document.querySelector('#replay-slider'); s.value = 0; s.dispatchEvent(new Event('input', {bubbles: true})); })()")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(first['t'])}", timeout=15000)
            assert page.text_content("#mode") == "REPLAY"
            assert page.locator("#ladder tbody tr.bid.best td.qty span").text_content().strip() == str(int(first["bids"][0][1]))
            assert page.locator("#mech-table tbody tr.mech-row").count() >= 1
            assert "inactive" in page.locator("#mech-table tbody tr.mech-row", has_text="absorption").text_content()
            # step forward / back one state: exact microsecond times, no off-by-one from ms rounding
            page.click("#btn-next")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(rows['ALPHA'][1]['t'])}", timeout=15000)
            page.click("#btn-next")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(rows['ALPHA'][2]['t'])}", timeout=15000)
            page.click("#btn-prev")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(rows['ALPHA'][1]['t'])}", timeout=15000)
            assert page.text_content("#replay-pos").startswith("2/6")
            page.click("#btn-last")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(last['t'])}", timeout=15000)
            assert page.text_content("#mode") == "REPLAY"
            # the timeline only shows transitions at or before the cursor (µs-exact comparison)
            page.click("#btn-first")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(first['t'])}", timeout=15000)
            assert page.evaluate("window.TOWER.S.tlSegments.filter(s => s.from !== null).length") == 0
            page.click("#btn-next")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(rows['ALPHA'][1]['t'])}", timeout=15000)
            assert page.evaluate("window.TOWER.S.tlSegments.filter(s => s.from !== null).map(s => s.layer + ':' + s.to)") == ["pressure:pressure_building"]
            # back to live
            page.click("#btn-live")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(last['t'])}", timeout=15000)
            assert page.text_content("#mode").startswith("LIVE")
            # play from the first state runs the virtual clock through every state to the end and stops
            page.click("#btn-first")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(first['t'])}", timeout=15000)
            page.select_option("#speed", "5")                                    # 1 s virtual per 200 ms tick, states 10 s apart
            page.click("#btn-play")
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(rows['ALPHA'][2]['t'])}", timeout=20000)
            assert page.evaluate("window.TOWER.S.playing") is True and page.text_content("#mode") == "REPLAY"
            page.wait_for_function(f"document.querySelector('#state-time').textContent === {json.dumps(last['t'])}", timeout=30000)
            page.wait_for_function("window.TOWER.S.playing === false", timeout=5000)
            # two states with one frame time: stepping reaches BOTH (the first is unreachable by time alone)
            page.select_option("#symbol", "DUP")
            page.wait_for_function("document.querySelector('#replay-pos').textContent.startsWith('2/2')", timeout=15000)
            assert page.text_content("#state-seq") == "2"
            page.click("#btn-prev")
            page.wait_for_function("document.querySelector('#replay-pos').textContent.startsWith('1/2')", timeout=15000)
            assert page.text_content("#state-seq") == "1" and page.text_content("#mode") == "REPLAY"
            page.click("#btn-next")
            page.wait_for_function("document.querySelector('#replay-pos').textContent.startsWith('2/2')", timeout=15000)
            assert page.text_content("#state-seq") == "2"
            assert not errors, errors
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.path.exists(shot):
            os.remove(shot)
