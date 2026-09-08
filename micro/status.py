#!/usr/bin/env python3
"""Live status of the micro capture, in one command and one readable page.

The capture already pushes a durable checkpoint every 15 minutes, so its
progress *is* on the branch — but as a stage machine inside a JSON file, which
is not something you can glance at from a phone. This renders the same facts as
`micro/STATUS.md`, committed to the branch, so the answer to "is it running and
how far along" is a page on GitHub rather than a question to me.

Read-only. It never writes to `micro/staging/`, never touches the runner, and
never changes a threshold, session or frozen artefact.

    python3 micro/status.py                 # print
    python3 micro/status.py --write         # also refresh micro/STATUS.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DHAKA = timezone(timedelta(hours=6))

# DSE continuous session, Dhaka wall clock.
OPEN_UTC = "04:00"
CLOSE_UTC = "08:10"
CHECKPOINT_SECS = 900


def _run(*args: str) -> str:
    try:
        return subprocess.check_output(args, cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:                                            # noqa: BLE001
        return ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _ago(then: Optional[datetime], now: datetime) -> Optional[str]:
    if not then:
        return None
    s = int((now - then).total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m {s % 60}s ago"
    return f"{s // 3600}h {(s % 3600) // 60}m ago"


def capture_alive() -> Optional[int]:
    """The runner's pid, or None. Matched on the script path, not a bare name."""
    out = _run("bash", "-lc", "ps -eo pid,args | grep '[d]aily_run.py' | head -1")
    m = re.match(r"\s*(\d+)\s", out)
    return int(m.group(1)) if m else None


def read_state(date: str) -> Optional[Dict[str, Any]]:
    p = os.path.join(HERE, "staging", date, "STATE.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        # A half-written file is a real state, not a crash: say so.
        return {"_torn": True}


def read_index() -> Dict[str, Any]:
    p = os.path.join(HERE, "sessions", "INDEX.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return {}


def checkpoints(date: str) -> List[Dict[str, str]]:
    """Commits this session made, newest first.

    Anchored on the session's own preflight commit rather than on subject text.
    The runner writes its checkpoints as plain ``micro: raw checkpoint`` with no
    date in the subject, so a date-matching filter saw only the preflight and
    reported a healthy capture as STALLED. Every commit at or after the preflight
    belongs to this session; the preflight itself is the anchor and is counted.
    """
    raw = _run("git", "log", "--pretty=%h|%cI|%s", "-80")
    entries = []
    for line in raw.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            entries.append({"sha": parts[0], "utc": parts[1], "subject": parts[2]})
    anchor = re.compile(rf"^micro:\s*{re.escape(date)}\b.*capture STARTED")
    out = []
    for e in entries:                                  # newest first
        out.append(e)
        if anchor.match(e["subject"]):
            return [x for x in out if x["subject"].startswith("micro:")]
    return []                                          # no preflight for this date yet


def collect(date: Optional[str] = None) -> Dict[str, Any]:
    now = _utc_now()
    date = date or now.strftime("%Y-%m-%d")
    state = read_state(date) or {}
    # The runner keeps the stage machine under "history" and the current stage
    # at the top level. Reading a "stages" key that does not exist reported the
    # stage as unknown while the capture was plainly in CAPTURING.
    hist = state.get("history") if isinstance(state.get("history"), list) else []
    last_stage = hist[-1] if hist else None
    cps = checkpoints(date)
    last_cp = _parse(cps[0]["utc"]) if cps else None

    local = _run("git", "rev-parse", "HEAD")
    remote = ""
    ls = _run("git", "ls-remote", "origin", "claude/micro-evidence-data")
    if ls:
        remote = ls.split()[0]

    open_dt = _parse(f"{date}T{OPEN_UTC}:00+00:00")
    close_dt = _parse(f"{date}T{CLOSE_UTC}:00+00:00")
    if now < open_dt:
        phase, to_go = "BEFORE OPEN", open_dt - now
    elif now <= close_dt:
        phase, to_go = "MARKET OPEN", close_dt - now
    else:
        phase, to_go = "AFTER CLOSE", now - close_dt

    pid = capture_alive()
    due = (last_cp + timedelta(seconds=CHECKPOINT_SECS)) if last_cp else None
    overdue = bool(due and now > due + timedelta(seconds=180))

    idx = read_index()
    counts = idx.get("counts", {}) if isinstance(idx, dict) else {}

    return {
        "date": date, "now_utc": now.isoformat(timespec="seconds"),
        "now_dhaka": now.astimezone(DHAKA).isoformat(timespec="seconds"),
        "phase": phase,
        "phase_note": (f"{int(to_go.total_seconds() // 60)} min "
                       + ("to open" if phase == "BEFORE OPEN" else
                          "to close" if phase == "MARKET OPEN" else "since close")),
        "running": pid is not None, "pid": pid,
        "torn_state_file": bool(state.get("_torn")),
        "stages": [h.get("stage") for h in hist if isinstance(h, dict)],
        # the top-level "stage" is the live one; history lags it by one write
        "stage": state.get("stage") or (last_stage or {}).get("stage"),
        "stage_at": state.get("updated_utc") or (last_stage or {}).get("at"),
        "checkpoints": len(cps),
        "last_checkpoint": cps[0] if cps else None,
        "last_checkpoint_ago": _ago(last_cp, now),
        "next_checkpoint_due_utc": due.isoformat(timespec="seconds") if due else None,
        "checkpoint_overdue": overdue,
        "local_head": local[:12], "remote_head": remote[:12],
        "pushed": bool(local and remote and local == remote),
        "index_counts": counts,
        "next_slot": idx.get("next_slot"),
        "sessions_until_holdout_opens": idx.get("sessions_until_holdout_opens"),
    }


def verdict(s: Dict[str, Any]) -> str:
    if s["torn_state_file"]:
        return "WRITING — the state file was mid-write when read; look again in a few seconds"
    if s["phase"] == "AFTER CLOSE" and not s["running"]:
        return "FINISHED — capture is no longer running; check the session record"
    if not s["running"]:
        return "NOT RUNNING — no daily_run.py process found"
    if s["checkpoint_overdue"]:
        return "STALLED — running, but a checkpoint is overdue"
    return "RUNNING — healthy"


def render(s: Dict[str, Any]) -> str:
    v = verdict(s)
    mark = {"RUNNING": "🟢", "FINISHED": "🔵", "WRITING": "🟡"}.get(v.split(" —")[0], "🔴")
    cp = s["last_checkpoint"]
    lines = [
        f"# Micro capture — {s['date']}",
        "",
        f"## {mark} {v}",
        "",
        "_Auto-generated by `python3 micro/status.py --write`. Read-only; it never",
        "touches the running capture._",
        "",
        "| | |",
        "|---|---|",
        f"| checked at | {s['now_utc']} UTC · {s['now_dhaka'][11:19]} Dhaka |",
        f"| market | **{s['phase']}** — {s['phase_note']} |",
        f"| process | {'alive, pid ' + str(s['pid']) if s['running'] else '**not running**'} |",
        f"| stage | **{s['stage'] or 'unknown'}** |",
        f"| stages so far | {' → '.join(s['stages']) or '—'} |",
        f"| checkpoints pushed | {s['checkpoints']} |",
        f"| last checkpoint | {(cp['sha'] + ' · ' + s['last_checkpoint_ago']) if cp else '—'} |",
        f"| next due | {s['next_checkpoint_due_utc'] or '—'}"
        f"{' **OVERDUE**' if s['checkpoint_overdue'] else ''} |",
        f"| local == remote | {'yes ✅' if s['pushed'] else 'NO ⚠️'} "
        f"({s['local_head']} / {s['remote_head']}) |",
        "",
        "## Session ledger",
        "",
        "| | |",
        "|---|---|",
        f"| accepted sessions | {s['index_counts'].get('accepted', 0)} |",
        f"| DEV filled | {s['index_counts'].get('dev_filled', 0)} of 8 |",
        f"| VAL filled | {s['index_counts'].get('val_filled', 0)} of 4 |",
        f"| HOLDOUT filled | {s['index_counts'].get('holdout_filled', 0)} of 5 |",
        f"| next slot | {s['next_slot'] or '—'} |",
        f"| sessions until holdout opens | {s['sessions_until_holdout_opens'] if s['sessions_until_holdout_opens'] is not None else '—'} |",
        "",
        "A session is counted only when it is ACCEPTED. Today's run is not in these",
        "numbers until it finishes and passes its own quality gate.",
        "",
    ]
    if cp:
        lines += ["## Last checkpoint commit", "",
                  f"`{cp['sha']}` — {cp['subject']}", f"at {cp['utc']}", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None)
    ap.add_argument("--write", action="store_true", help="refresh micro/STATUS.md")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    s = collect(a.date)
    if a.json:
        print(json.dumps(s, indent=1))
    else:
        print(render(s))
    if a.write:
        p = os.path.join(HERE, "STATUS.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(render(s))
        print(f"wrote {os.path.relpath(p, ROOT)}", file=sys.stderr)
    return 0 if s["running"] or s["phase"] == "AFTER CLOSE" else 1


if __name__ == "__main__":
    sys.exit(main())
