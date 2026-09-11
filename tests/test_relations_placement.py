"""Placement tests for relations/relation_watcher.py and relations/relation_replay.py.

Scope: py_compile, importability, and a deterministic replay smoke test.
No strategy, no market data, no research. The event stream below is synthetic
and domain-neutral; the fields are named `a`/`b` and the outcomes `X`/`Y`
precisely so nothing here can be read as a market rule.
"""
from __future__ import annotations

import json
import os
import py_compile

from relations import HistoricalReplay, RelationWatcher, ReplayEvent

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHER = os.path.join(REPO_ROOT, "relations", "relation_watcher.py")
REPLAY = os.path.join(REPO_ROOT, "relations", "relation_replay.py")


def test_py_compile_both_modules(tmp_path):
    for src in (WATCHER, REPLAY):
        out = tmp_path / (os.path.basename(src) + "c")
        py_compile.compile(src, cfile=str(out), doraise=True)
        assert out.exists()


def _stream():
    """A small, strictly causal observation/outcome stream."""
    events = []
    rows = [
        ("o1", {"a": 1, "b": "p"}, "X"),
        ("o2", {"a": 2, "b": "p"}, "X"),
        ("o3", {"a": 9, "b": "q"}, "Y"),
        ("o4", {"a": 8, "b": "q"}, "Y"),
        ("o5", {"a": 3, "b": "p"}, "X"),
        ("o6", {"a": 7, "b": "q"}, "Y"),
    ]
    order = 0
    for oid, data, outcome in rows:
        events.append(ReplayEvent(
            time=f"2020-01-01T00:00:{order:02d}", order=order,
            kind="observation", observation_id=oid, entity="e", data=data,
        ))
        order += 1
        events.append(ReplayEvent(
            time=f"2020-01-01T00:00:{order:02d}", order=order,
            kind="outcome", observation_id=oid, outcome=outcome,
        ))
        order += 1
    return events


def _run(events):
    w = RelationWatcher()
    r = HistoricalReplay(events, watcher=w)
    frames = list(r.play())
    return frames, w


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_replay_is_deterministic_across_identical_runs():
    events = _stream()
    frames_a, w_a = _run(events)
    frames_b, w_b = _run(_stream())

    assert len(frames_a) == len(events) == len(frames_b)
    assert _canon([f.to_dict() for f in frames_a]) == _canon([f.to_dict() for f in frames_b])
    assert _canon(w_a.intelligence_snapshot()) == _canon(w_b.intelligence_snapshot())


def test_seek_rebuilds_the_same_state_as_stepping():
    events = _stream()
    stepped = RelationWatcher()
    r = HistoricalReplay(events, watcher=stepped)
    for _ in range(5):
        r.step()
    at_five = _canon(stepped.intelligence_snapshot())

    sought = RelationWatcher()
    r.seek(5, fresh_watcher=sought)
    assert r.status()["cursor"] == 5
    assert _canon(sought.intelligence_snapshot()) == at_five


def test_outcome_is_invisible_until_its_own_event_arrives():
    events = _stream()
    w = RelationWatcher()
    r = HistoricalReplay(events, watcher=w)
    first = r.step()                      # observation o1 only
    assert first.kind == "observation"
    assert first.revealed_outcome is None
    assert w.intelligence_snapshot()["settled_cases"] == 0
    second = r.step()                     # its outcome
    assert second.kind == "outcome"
    assert w.intelligence_snapshot()["settled_cases"] == 1


def test_stream_validation_rejects_non_causal_order():
    bad = [
        ReplayEvent(time="2020-01-01T00:00:01", order=1, kind="observation",
                    observation_id="o1", entity="e", data={"a": 1}),
        ReplayEvent(time="2020-01-01T00:00:00", order=0, kind="observation",
                    observation_id="o2", entity="e", data={"a": 2}),
    ]
    try:
        HistoricalReplay(bad, watcher=RelationWatcher())
    except ValueError as exc:
        assert "not strictly causal" in str(exc)
    else:
        raise AssertionError("a non-causal stream was accepted")


def test_outcome_before_its_observation_is_rejected():
    bad = [
        ReplayEvent(time="2020-01-01T00:00:00", order=0, kind="outcome",
                    observation_id="ghost", outcome="X"),
    ]
    try:
        HistoricalReplay(bad, watcher=RelationWatcher())
    except ValueError as exc:
        assert "before its observation" in str(exc)
    else:
        raise AssertionError("an orphan outcome was accepted")


def test_run_to_jsonl_writes_one_line_per_event(tmp_path):
    events = _stream()
    r = HistoricalReplay(events, watcher=RelationWatcher())
    out = tmp_path / "frames.jsonl"
    res = r.run_to_jsonl(str(out))
    assert res["frames_written"] == len(events)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(events)
    for line in lines:
        json.loads(line)


def test_placed_files_are_byte_identical_to_what_was_supplied():
    """Guards the placement itself: these modules are vendored verbatim."""
    import hashlib

    expected = {
        WATCHER: "1ca2fc8bf8cae35d55eb701d9172824b686e46e6379c4635ae8a6cbd73382064",
        REPLAY: "8805b07ea53f61f869b44bd2566b935b72d529b34749dbe9cc846d9933a55bf3",
    }
    for path, want in expected.items():
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
        assert got == want, f"{os.path.basename(path)} was modified after placement"
