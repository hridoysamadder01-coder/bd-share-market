"""The engine itself: scheduling, blocking, raw preservation, health.

No network here — adapters are stand-ins that return canned `Fetched` objects,
so the tests are about the engine's contract, not about any source being up.
"""
import json
import os

import pytest

from seeing.capture.engine import ALL_PHASES, PublicMarketEngine, SourceSpec, TRADING_PHASES
from seeing.capture.http_client import Fetched
from seeing.capture.adapters.base import Parsed
from seeing.capture.raw_store import decode_body, iter_segment, verify_store


class FakeAdapter:
    """Returns whatever it is told to, and counts calls."""

    def __init__(self, body=b'{"ok":1}', status=200, frames=None, problems=(), raises=False):
        self.body, self.status, self.raises = body, status, raises
        self.frames = [{"symbol": "X"}] if frames is None else frames
        self.problems = list(problems)
        self.calls = []

    def fetch(self, key=None):
        self.calls.append(key)
        if self.raises:
            raise RuntimeError("adapter exploded")
        return Fetched(ok=200 <= self.status < 300, status=self.status, body=self.body,
                       http={"method": "GET", "url": "https://fake.test/x", "status": self.status})

    def parse(self, body, key=None):
        return Parsed("fake", frames=list(self.frames), problems=list(self.problems))


def engine(tmp_path, specs, symbols=()):
    return PublicMarketEngine(str(tmp_path), specs, symbols=list(symbols))


def test_a_successful_poll_stores_the_bytes_exactly(tmp_path):
    body = b'{"price": 62.3, "note": "\xe0\xa6\xac"}'          # non-ASCII, must survive
    a = FakeAdapter(body=body)
    eng = engine(tmp_path, [SourceSpec("s", "watch", a, 1.0)])
    assert eng.poll(eng.specs[0]) is True
    eng.store.close()

    seg = [p for p in os.listdir(os.path.join(tmp_path, "segments")) if p.startswith("s__")][0]
    datas = [r for r, ok in iter_segment(os.path.join(tmp_path, "segments", seg))
             if ok and r.get("kind") == "DATA"]
    assert len(datas) == 1
    assert decode_body(datas[0]) == body                       # byte-for-byte
    assert verify_store(str(tmp_path))["all_ok"] is True       # hash chain intact


def test_a_failed_poll_writes_a_named_gap_and_keeps_the_error_body(tmp_path):
    a = FakeAdapter(body=b"<h1>404</h1>", status=404)
    eng = engine(tmp_path, [SourceSpec("s", "watch", a, 1.0)])
    assert eng.poll(eng.specs[0]) is False
    eng.store.close()
    seg = [p for p in os.listdir(os.path.join(tmp_path, "segments")) if p.startswith("s__")][0]
    gaps = [r for r, ok in iter_segment(os.path.join(tmp_path, "segments", seg))
            if ok and r.get("kind") == "GAP"]
    assert len(gaps) == 1 and gaps[0]["reason"] == "not_found"
    assert decode_body(gaps[0]) == b"<h1>404</h1>"             # the error page is evidence too


def test_an_adapter_that_raises_is_recorded_not_propagated(tmp_path):
    eng = engine(tmp_path, [SourceSpec("s", "watch", FakeAdapter(raises=True), 1.0)])
    assert eng.poll(eng.specs[0]) is False
    assert eng.health["s"].last_failure_code == "connect_error"
    assert "adapter exploded" in eng.health["s"].last_failure_detail


def test_a_200_that_parses_to_nothing_is_a_failure(tmp_path):
    eng = engine(tmp_path, [SourceSpec("s", "watch", FakeAdapter(frames=[]), 1.0)])
    assert eng.poll(eng.specs[0]) is False
    assert eng.health["s"].last_failure_code == "empty_response"


def test_expect_frames_false_lets_a_frameless_source_succeed(tmp_path):
    spec = SourceSpec("s", "macro", FakeAdapter(frames=[]), 1.0, expect_frames=False)
    eng = engine(tmp_path, [spec])
    assert eng.poll(spec) is True and eng.health["s"].ok == 1


def test_a_blocked_source_is_never_fetched_but_stays_in_the_status(tmp_path):
    a = FakeAdapter()
    spec = SourceSpec("amarstock", "book", a, 1.0, enabled=False,
                      blocked_reason="robots.txt disallows our agent")
    eng = engine(tmp_path, [spec])
    eng.run_once()
    assert a.calls == []                                       # the whole point
    row = eng.status()["sources"][0]
    assert row["status"] == "BLOCKED" and "robots.txt" in row["blocked_reason"]


def test_phase_gating_keeps_trading_sources_off_out_of_hours():
    open_only = SourceSpec("depth", "book", None, 1.0, phases=TRADING_PHASES)
    always = SourceSpec("ref", "reference", None, 1.0, phases=ALL_PHASES)
    assert open_only.runs_in("CONTINUOUS") is True
    assert open_only.runs_in("CLOSED") is False
    assert always.runs_in("CLOSED") is True


def test_a_closed_cadence_keeps_a_source_alive_overnight_but_slower():
    s = SourceSpec("watch", "watch", None, 120.0, phases=TRADING_PHASES, closed_cadence_s=3600.0)
    assert s.runs_in("CLOSED") is True                         # opted in via closed_cadence_s
    assert s.cadence_for("CONTINUOUS") == 120.0
    assert s.cadence_for("CLOSED") == 3600.0
    assert s.due(0.0, 200.0, "CONTINUOUS") is True
    assert s.due(0.0, 200.0, "CLOSED") is False                # polite overnight
    assert s.due(0.0, 4000.0, "CLOSED") is True


def test_a_source_without_a_closed_cadence_uses_one_cadence_everywhere():
    s = SourceSpec("x", "reference", None, 60.0)
    assert s.cadence_for("CLOSED") == 60.0 and s.cadence_for("CONTINUOUS") == 60.0


def test_per_symbol_sources_are_polled_once_per_symbol(tmp_path):
    a = FakeAdapter()
    spec = SourceSpec("d", "book", a, 1.0, per_symbol=True, phases=ALL_PHASES)
    engine(tmp_path, [spec], symbols=["BRACBANK", "GP"]).run_once()
    assert a.calls == ["BRACBANK", "GP"]


def test_repeated_identical_payloads_are_counted_not_hidden(tmp_path):
    a = FakeAdapter(body=b'{"same":1}')
    spec = SourceSpec("s", "watch", a, 0.0)
    eng = engine(tmp_path, [spec])
    for _ in range(4):
        eng.poll(spec)
    h = eng.health["s"]
    assert h.ok == 4 and h.unchanged_polls == 3                # every poll still stored
    eng.store.close()
    seg = [p for p in os.listdir(os.path.join(tmp_path, "segments")) if p.startswith("s__")][0]
    datas = [r for r, ok in iter_segment(os.path.join(tmp_path, "segments", seg))
             if ok and r.get("kind") == "DATA"]
    assert len(datas) == 4                                     # nothing deduplicated at capture


def test_status_file_is_written_and_is_valid_json(tmp_path):
    eng = engine(tmp_path, [SourceSpec("s", "watch", FakeAdapter(), 1.0),
                            SourceSpec("b", "book", None, 1.0, enabled=False,
                                       blocked_reason="no public route")])
    eng.run_once()
    p = os.path.join(tmp_path, "SOURCE_STATUS.json")
    st = json.load(open(p))
    assert st["by_status"]["WORKING"] == 1 and st["by_status"]["BLOCKED"] == 1
    assert st["session_phase"] in ("CLOSED", "PRE_OPEN", "CONTINUOUS", "POST_CLOSE")
    assert {r["source"] for r in st["sources"]} == {"s", "b"}
    assert st["raw_records"] == 1


def test_the_registry_builds_and_declares_its_blocked_sources():
    """The shipped registry must always name AmarStock as blocked, never fetch it."""
    from seeing.capture.engine import build_registry
    from seeing.capture.http_client import PoliteClient
    specs = build_registry(PoliteClient(), ["BRACBANK"])
    by = {s.name: s for s in specs}
    assert by["amarstock"].blocked and by["amarstock"].adapter is None
    assert "robots.txt" in by["amarstock"].blocked_reason
    assert by["bullbd_depth"].blocked and "socket" in by["bullbd_depth"].blocked_reason.lower()
    # and the sources proven live on 2026-09-08 are present and enabled
    for name in ("stocknow_instruments", "cse_current_price", "bullbd_detail",
                 "bb_exchange_rate", "bsec_site", "cdbl_site"):
        assert name in by and by[name].enabled and not by[name].blocked
    assert len({s.name for s in specs}) == len(specs)          # no duplicate names
