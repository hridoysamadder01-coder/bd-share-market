"""Tests for intent-native relation discovery and exact causal replay.

Synthetic fields are deliberately domain-neutral.  There are no supplied
outcome labels: intent occurrence is resolved mechanically from later raw
observations only after the requested horizon has elapsed.
"""
from __future__ import annotations

import json
import os
import py_compile

from relations import HistoricalReplay, IntentProgram, RelationWatcher, ReplayEvent, events_from_rows

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTENT = os.path.join(REPO_ROOT, "relations", "intent.py")
WATCHER = os.path.join(REPO_ROOT, "relations", "relation_watcher.py")
REPLAY = os.path.join(REPO_ROOT, "relations", "relation_replay.py")


def _intent():
    return IntentProgram(
        text="raw.target increases by at least 10 percent on the next authoritative observation",
        horizon_steps=1,
        expression={
            "op": "gte",
            "left": {"op": "pct_change", "path": "raw.target", "to": "latest"},
            "right": {"op": "const", "value": 10.0},
        },
    )


def _stream():
    # next target movement is generated from the PREVIOUS raw.a value.
    a_values = [1, 9, 2, 8, 3, 7, 4, 6]
    target = 100.0
    rows = []
    for i, a in enumerate(a_values):
        if i:
            prior_a = a_values[i - 1]
            target = target * 1.20 if prior_a <= 4 else target
        rows.append((a, target))
    return [
        ReplayEvent(
            time=f"2020-01-01T00:00:{i:02d}+00:00",
            order=i,
            source="raw",
            entity="e",
            data={"a": a, "target": target},
            observation_id=f"o{i}",
        )
        for i, (a, target) in enumerate(rows)
    ]


def _run(events=None):
    watcher = RelationWatcher(intent=_intent())
    replay = HistoricalReplay(events or _stream(), watcher=watcher)
    frames = list(replay.play())
    return frames, watcher


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_py_compile_relation_modules(tmp_path):
    for src in (INTENT, WATCHER, REPLAY):
        out = tmp_path / (os.path.basename(src) + "c")
        py_compile.compile(src, cfile=str(out), doraise=True)
        assert out.exists()


def test_replay_stream_has_no_outcome_event_or_outcome_label_field():
    assert "outcome" not in ReplayEvent.__dataclass_fields__
    for event in _stream():
        assert "outcome" not in event.to_dict()


def test_intent_is_not_resolved_before_its_future_horizon_exists():
    watcher = RelationWatcher(intent=_intent())
    replay = HistoricalReplay(_stream(), watcher=watcher)
    first = replay.step()
    assert first is not None
    assert first.resolved_case_count == 0
    assert watcher.intelligence_snapshot()["resolved_cases"] == 0

    second = replay.step()
    assert second is not None
    assert second.resolved_case_count == 1
    case = watcher.cases[0]
    assert case.observation_id == "o0"
    assert case.intent_matched is True
    assert case.resolved_time == _stream()[1].time


def test_mdl_discovers_a_raw_data_path_to_the_intent():
    _, watcher = _run()
    snapshot = watcher.intelligence_snapshot()
    assert snapshot["resolved_cases"] == len(_stream()) - 1
    assert snapshot["relation_tree"]["type"] == "relation"
    paths = snapshot["relation_paths"]
    assert paths
    assert any(
        any(condition["field"] == "raw.a" for condition in path["conditions"])
        for path in paths
    )
    assert sum(p["intent_true"] for p in paths) == 4
    assert sum(p["intent_false"] for p in paths) == 3


def test_replay_is_deterministic_across_identical_runs():
    frames_a, watcher_a = _run()
    frames_b, watcher_b = _run(_stream())
    assert _canon([f.to_dict() for f in frames_a]) == _canon([f.to_dict() for f in frames_b])
    assert _canon(watcher_a.intelligence_snapshot()) == _canon(watcher_b.intelligence_snapshot())


def test_seek_rebuilds_same_state_from_zero():
    events = _stream()
    stepped = RelationWatcher(intent=_intent())
    replay = HistoricalReplay(events, watcher=stepped)
    for _ in range(5):
        replay.step()
    at_five = _canon(stepped.audit_snapshot())

    sought = RelationWatcher(intent=_intent())
    replay.seek(5, fresh_watcher=sought)
    assert replay.status()["cursor"] == 5
    assert _canon(sought.audit_snapshot()) == at_five


def test_global_public_context_is_visible_only_to_later_entity_anchors():
    watcher = RelationWatcher(intent=_intent())
    watcher.observe(
        time="2020-01-01T00:00:00+00:00",
        order=0,
        source="market",
        scope="global",
        advance=False,
        data={"index": 123.0},
        observation_id="g0",
    )
    watcher.observe(
        time="2020-01-01T00:00:01+00:00",
        order=1,
        source="raw",
        entity="e",
        data={"a": 1, "target": 100.0},
        observation_id="o0",
    )
    assert watcher.entity_history["e"][0].state["global.market.index"] == 123.0


def test_nested_raw_book_is_flattened_without_handmade_features():
    watcher = RelationWatcher(intent=_intent())
    watcher.observe(
        time="2020-01-01T00:00:00+00:00",
        order=0,
        source="depth",
        entity="e",
        advance=False,
        data={"book": {"bids": [[10.0, 100.0], [9.9, 200.0]]}},
        observation_id="d0",
    )
    watcher.observe(
        time="2020-01-01T00:00:01+00:00",
        order=1,
        source="raw",
        entity="e",
        data={"a": 1, "target": 100.0},
        observation_id="o0",
    )
    state = watcher.entity_history["e"][0].state
    assert state["depth.book.bids[0][0]"] == 10.0
    assert state["depth.book.bids[0][1]"] == 100.0
    assert state["depth.book.bids[1][1]"] == 200.0


def test_forbidden_future_key_is_rejected_before_state_entry():
    watcher = RelationWatcher(intent=_intent(), forbidden_keys=("future_secret",))
    try:
        watcher.observe(
            time="2020-01-01T00:00:00+00:00",
            order=0,
            source="raw",
            entity="e",
            data={"a": 1, "target": 100.0, "nested": {"future_secret": 9}},
        )
    except ValueError as exc:
        assert "forbidden/future field" in str(exc)
    else:
        raise AssertionError("future field entered relation state")


def test_stream_validation_rejects_non_causal_order():
    bad = [
        ReplayEvent(time="2020-01-01T00:00:01+00:00", order=1, source="raw", entity="e", data={"a": 1}),
        ReplayEvent(time="2020-01-01T00:00:00+00:00", order=0, source="raw", entity="e", data={"a": 2}),
    ]
    try:
        HistoricalReplay(bad, watcher=RelationWatcher(intent=_intent()))
    except ValueError as exc:
        assert "not strictly causal" in str(exc)
    else:
        raise AssertionError("a non-causal stream was accepted")


def test_global_event_cannot_advance_entity_horizon():
    bad = [
        ReplayEvent(
            time="2020-01-01T00:00:00+00:00",
            order=0,
            source="market",
            scope="global",
            advance=True,
            data={"index": 1},
        )
    ]
    try:
        HistoricalReplay(bad, watcher=RelationWatcher(intent=_intent()))
    except ValueError as exc:
        assert "global event" in str(exc)
    else:
        raise AssertionError("global source changed the entity intent horizon")


def test_events_from_rows_is_mechanical_and_does_not_create_targets():
    rows = [
        {"t": "2020-01-01T00:00:00+00:00", "ord": 0, "src": "raw", "sym": "e", "a": 1, "target": 100.0},
        {"t": "2020-01-01T00:00:01+00:00", "ord": 1, "src": "raw", "sym": "e", "a": 2, "target": 120.0},
    ]
    events = events_from_rows(rows, time_col="t", order_col="ord", source_col="src", entity_col="sym")
    assert [e.data for e in events] == [{"a": 1, "target": 100.0}, {"a": 2, "target": 120.0}]
    assert all("outcome" not in e.to_dict() for e in events)


def test_run_to_jsonl_writes_one_frame_per_raw_event(tmp_path):
    replay = HistoricalReplay(_stream(), watcher=RelationWatcher(intent=_intent()))
    out = tmp_path / "frames.jsonl"
    result = replay.run_to_jsonl(str(out))
    assert result["frames_written"] == len(_stream())
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(_stream())
    for line in lines:
        row = json.loads(line)
        assert "outcome" not in row
