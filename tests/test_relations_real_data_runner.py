from __future__ import annotations

import csv
import json

import pandas as pd

from relations import IntentProgram
from relations.real_data_runner import (
    iter_eod_events,
    iter_minute_events,
    load_intent,
    run_stream,
)


def _intent():
    return IntentProgram(
        text="x rises on the next raw observation",
        horizon_steps=1,
        expression={
            "op": "gt",
            "left": {"op": "field", "path": "raw.x", "at": "latest"},
            "right": {"op": "field", "path": "raw.x", "at": "anchor"},
        },
    )


def test_intent_file_is_fail_closed_on_extra_keys(tmp_path):
    p = tmp_path / "intent.json"
    p.write_text(json.dumps({
        "text": "x",
        "horizon_steps": 1,
        "expression": {"op": "eq", "left": {"op": "const", "value": 1}, "right": {"op": "const", "value": 1}},
        "hidden": "not allowed",
    }), encoding="utf-8")
    try:
        load_intent(str(p))
    except ValueError as exc:
        assert "unexpected intent keys" in str(exc)
    else:
        raise AssertionError("hidden intent key was accepted")


def test_eod_wiring_preserves_values_but_not_source_metadata(tmp_path):
    p = tmp_path / "eod.parquet"
    pd.DataFrame([
        {"symbol": "BBB", "ts": "2020-01-01", "open": 20.0, "close": 21.0, "volume": 200, "source": "old"},
        {"symbol": "AAA", "ts": "2020-01-01", "open": 10.0, "close": 11.0, "volume": 100, "source": "old"},
        {"symbol": "AAA", "ts": "2020-01-02", "open": 11.0, "close": 12.0, "volume": 120, "source": "new"},
    ]).to_parquet(p, index=False)

    events = list(iter_eod_events(str(p)))
    assert [e.entity for e in events] == ["AAA", "BBB", "AAA"]
    assert all(e.source == "dse_eod" for e in events)
    assert events[0].data == {"open": 10.0, "close": 11.0, "volume": 100}
    assert "source" not in events[0].data
    assert "symbol" not in events[0].data
    assert "ts" not in events[0].data
    assert all("outcome" not in e.to_dict() for e in events)


def _write_minute(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=["timestamp", "closing", "opening", "high", "low", "volume"])
        wr.writeheader()
        wr.writerows(rows)


def test_minute_wiring_is_kway_timestamp_ordered(tmp_path):
    _write_minute(tmp_path / "BBB.csv", [
        {"timestamp": "2020-01-01 10:01:00", "closing": 2, "opening": 2, "high": 2, "low": 2, "volume": 20},
        {"timestamp": "2020-01-01 10:03:00", "closing": 3, "opening": 3, "high": 3, "low": 3, "volume": 30},
    ])
    _write_minute(tmp_path / "AAA.csv", [
        {"timestamp": "2020-01-01 10:00:00", "closing": 1, "opening": 1, "high": 1, "low": 1, "volume": 10},
        {"timestamp": "2020-01-01 10:03:00", "closing": 4, "opening": 4, "high": 4, "low": 4, "volume": 40},
    ])
    events = list(iter_minute_events(str(tmp_path)))
    assert [(e.time, e.entity) for e in events] == [
        ("2020-01-01 10:00:00", "AAA"),
        ("2020-01-01 10:01:00", "BBB"),
        ("2020-01-01 10:03:00", "AAA"),
        ("2020-01-01 10:03:00", "BBB"),
    ]
    assert [e.order for e in events] == [0, 1, 2, 3]
    assert all(e.source == "minute_dataset" for e in events)


def test_stream_runner_writes_no_outcome_packet(tmp_path):
    from relations import ReplayEvent

    events = [
        ReplayEvent(time="2020-01-01T00:00:00", order=0, source="raw", entity="e", data={"x": 1}),
        ReplayEvent(time="2020-01-01T00:00:01", order=1, source="raw", entity="e", data={"x": 2}),
        ReplayEvent(time="2020-01-01T00:00:02", order=2, source="raw", entity="e", data={"x": 1}),
    ]
    out = tmp_path / "out"
    run = run_stream(events, intent=_intent(), out_dir=str(out), input_info={"lane": "test"})
    assert run["events_processed"] == 3
    assert run["resolved_cases"] == 2
    assert run["outcome_packets"] == 0
    snapshot = json.loads((out / "RELATION_SNAPSHOT.json").read_text(encoding="utf-8"))
    assert snapshot["resolved_cases"] == 2
    assert "outcome" not in json.dumps(snapshot).lower()


def test_stream_runner_rejects_noncausal_input(tmp_path):
    from relations import ReplayEvent

    events = [
        ReplayEvent(time="2020-01-01T00:00:02", order=1, source="raw", entity="e", data={"x": 1}),
        ReplayEvent(time="2020-01-01T00:00:01", order=2, source="raw", entity="e", data={"x": 2}),
    ]
    try:
        run_stream(events, intent=_intent(), out_dir=str(tmp_path / "out"), input_info={"lane": "test"})
    except ValueError as exc:
        assert "not strictly causal" in str(exc)
    else:
        raise AssertionError("noncausal real-data stream was accepted")
