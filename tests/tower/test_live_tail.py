"""Live tailer: catch-up, tail-only start, and the hard deadline (machinery, synthetic store)."""
import json
import os

from tower.live import Tailer, run

from .test_e2e_replay import _synthetic_store


def test_machinery_tailer_catchup_then_new_lines_only(tmp_path):
    cap = str(tmp_path / "cap")
    _synthetic_store(cap, n=8)
    t = Tailer(cap)
    first = t.poll()
    assert first and all("source" in r for r in first)
    assert t.poll() == []                                            # nothing new → nothing re-read
    # tail-only starts at the current end of every segment
    t2 = Tailer(cap, from_end=True)
    assert t2.poll() == []


def test_machinery_live_once_builds_states_and_run_json(tmp_path):
    cap = str(tmp_path / "cap")
    _synthetic_store(cap, n=12)
    out = str(tmp_path / "out")
    r = run(cap, out, poll_s=0.0, once=True)
    assert r["records"] > 0 and r["events"] > 0 and r["states"] > 0 and r["unprocessed_backlog"] == 0
    run_json = json.load(open(os.path.join(out, "RUN.json")))
    assert run_json["mode"] == "live" and run_json["catchup_records"] == r["records"]
    assert run_json["deadline_hit"] is False and run_json["tail_only"] is False
    assert os.path.exists(os.path.join(out, "metrics.json"))
    assert os.path.exists(os.path.join(out, "states", "SYN.jsonl"))


def test_machinery_live_tail_only_sees_no_history(tmp_path):
    cap = str(tmp_path / "cap")
    _synthetic_store(cap, n=6)
    out = str(tmp_path / "out")
    r = run(cap, out, poll_s=0.0, max_seconds=0.2, tail_only=True)
    assert r["records"] == 0 and r["states"] == 0
    run_json = json.load(open(os.path.join(out, "RUN.json")))
    assert run_json["tail_only"] is True and run_json["unprocessed_backlog"] == 0


def test_machinery_live_deadline_is_honoured_inside_catchup(tmp_path, monkeypatch):
    """A deadline that expires during the catch-up stops processing and reports the backlog
    instead of silently draining everything past the deadline."""
    cap = str(tmp_path / "cap")
    _synthetic_store(cap, n=40)
    out = str(tmp_path / "out")
    import tower.live as live
    clock = {"t": 1000.0}

    def fake_time():
        clock["t"] += 0.05                                           # every call advances 50 ms
        return clock["t"]

    monkeypatch.setattr(live.time, "time", fake_time)
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    r = run(cap, out, poll_s=0.0, once=False, max_seconds=0.3)
    run_json = json.load(open(os.path.join(out, "RUN.json")))
    assert run_json["deadline_hit"] is True
    assert run_json["unprocessed_backlog"] == r["unprocessed_backlog"] > 0
    assert run_json["engine_metrics"]["backlog"] == r["unprocessed_backlog"]   # deferred, visible, not lost
