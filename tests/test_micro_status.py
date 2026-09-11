"""The capture status page must not cry wolf.

The bug this pins: the runner writes its checkpoints as a plain
`micro: raw checkpoint` with no date in the subject, so a date-matching filter
counted only the preflight and reported a perfectly healthy capture as STALLED.
A monitoring tool that raises a false alarm is worse than none — it trains you
to ignore it.
"""
import sys
import types

import pytest

import micro.status as st


@pytest.fixture
def fake_log(monkeypatch):
    """Feed `git log` output straight into the module, newest first."""
    def _set(lines):
        def fake_run(*args):
            if args[:2] == ("git", "log"):
                return "\n".join(lines)
            return ""
        monkeypatch.setattr(st, "_run", fake_run)
    return _set


PREFLIGHT = "aaa1111|2026-09-08T03:42:28+00:00|micro: 2026-09-08 capture STARTED (preflight heartbeat)"


def test_undated_raw_checkpoints_are_counted(fake_log):
    fake_log([
        "ccc3333|2026-09-08T04:27:59+00:00|micro: raw checkpoint",
        "bbb2222|2026-09-08T03:47:00+00:00|micro: one-command live capture status",
        PREFLIGHT,
        "zzz0000|2026-09-07T10:00:00+00:00|micro: 2026-09-07 capture STARTED (preflight heartbeat)",
    ])
    cps = st.checkpoints("2026-09-08")
    assert [c["sha"] for c in cps] == ["ccc3333", "bbb2222", "aaa1111"]
    assert cps[0]["subject"] == "micro: raw checkpoint"      # the subject with no date


def test_a_previous_session_is_not_counted_into_this_one(fake_log):
    fake_log([PREFLIGHT,
              "yyy9999|2026-09-07T14:00:00+00:00|micro: raw checkpoint",
              "zzz0000|2026-09-07T10:00:00+00:00|micro: 2026-09-07 capture STARTED (preflight heartbeat)"])
    assert [c["sha"] for c in st.checkpoints("2026-09-08")] == ["aaa1111"]


def test_no_preflight_for_the_date_means_no_checkpoints(fake_log):
    fake_log(["zzz0000|2026-09-07T10:00:00+00:00|micro: 2026-09-07 capture STARTED (preflight heartbeat)"])
    assert st.checkpoints("2026-09-08") == []


def test_a_healthy_capture_is_not_reported_stalled():
    s = {"torn_state_file": False, "phase": "MARKET OPEN", "running": True,
         "checkpoint_overdue": False}
    assert st.verdict(s).startswith("RUNNING")


def test_an_overdue_checkpoint_is_reported_stalled():
    s = {"torn_state_file": False, "phase": "MARKET OPEN", "running": True,
         "checkpoint_overdue": True}
    assert st.verdict(s).startswith("STALLED")


def test_a_dead_process_during_market_hours_is_reported():
    s = {"torn_state_file": False, "phase": "MARKET OPEN", "running": False,
         "checkpoint_overdue": False}
    assert st.verdict(s).startswith("NOT RUNNING")


def test_a_finished_capture_after_the_close_is_not_an_alarm():
    s = {"torn_state_file": False, "phase": "AFTER CLOSE", "running": False,
         "checkpoint_overdue": False}
    assert st.verdict(s).startswith("FINISHED")


def test_a_half_written_state_file_is_reported_as_such_not_as_a_crash():
    s = {"torn_state_file": True, "phase": "MARKET OPEN", "running": True,
         "checkpoint_overdue": False}
    assert st.verdict(s).startswith("WRITING")


def test_the_status_tool_never_writes_to_the_running_capture(monkeypatch, tmp_path):
    """Read-only: it must not touch micro/staging/ or signal the runner."""
    import inspect
    src = inspect.getsource(st)
    assert 'open(p, "w"' not in src.replace('open(p, "w", encoding="utf-8") as fh:\n            fh.write(render(s))', "")
    assert "kill" not in src and "SIGTERM" not in src
