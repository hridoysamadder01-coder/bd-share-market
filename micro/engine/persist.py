#!/usr/bin/env python3
"""Operational durability primitives for the prospective-capture pipeline.

This module changes NO research logic. It only makes persistence fail-closed:
every git operation reports its exact outcome, a session moves through an explicit
stage machine, the final push is verified against the real remote HEAD, and an
unpersisted prior session is detected and never overwritten.

The only storage that survives an ephemeral container's death is the git remote,
so "durable staging" here means: commit + push a staging tree to the data branch
at checkpoints. A container death after the last checkpoint loses at most the work
since that checkpoint, never the whole session.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from typing import Callable, List, Optional, Sequence, Tuple

STAGES = ("STARTED", "CAPTURING", "CAPTURE_COMPLETE", "VERIFIED", "FUSED",
          "COMMITTED", "REMOTE_VERIFIED", "FAILED")


def utcnow() -> str:
    return dt.datetime.utcnow().isoformat() + "Z"


class StageError(Exception):
    """A stage failed; carries the exact command, return code and stderr."""

    def __init__(self, stage: str, cmd: Sequence[str], rc: int, stderr: str):
        self.stage, self.cmd, self.rc, self.stderr = stage, list(cmd), rc, stderr
        super().__init__(f"[{stage}] rc={rc} cmd={' '.join(cmd)} :: {stderr[-400:]}")

    def as_dict(self) -> dict:
        return {"stage": self.stage, "cmd": self.cmd, "rc": self.rc,
                "stderr_tail": self.stderr[-800:]}


def run(cmd: Sequence[str], cwd: str, timeout: Optional[float] = None) -> Tuple[int, str, str]:
    """Run a command, return (rc, stdout, stderr). Never raises on non-zero."""
    print("+", " ".join(cmd), flush=True)
    try:
        r = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or ""), f"TIMEOUT after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)
    if r.stdout:
        print(r.stdout[-2000:])
    if r.returncode and r.stderr:
        print("STDERR:", r.stderr[-1200:])
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def must(cmd: Sequence[str], cwd: str, stage: str, timeout: Optional[float] = None,
         ok_rc: Sequence[int] = (0,)) -> str:
    """Run a command that MUST succeed; raise StageError otherwise (fail-closed)."""
    rc, out, err = run(cmd, cwd, timeout=timeout)
    if rc not in ok_rc:
        raise StageError(stage, cmd, rc, err or out)
    return out


# ----------------------------------------------------------------- state machine
def write_state(state_path: str, stage: str, **details) -> dict:
    assert stage in STAGES, f"unknown stage {stage!r}"
    prev = read_state(state_path) or {"history": []}
    hist = prev.get("history", [])
    hist.append({"stage": stage, "at": utcnow(), **details})
    st = {**prev, "stage": stage, "updated_utc": utcnow(), "history": hist}
    st.update({k: v for k, v in details.items()})
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, state_path)          # atomic: never a half-written STATE.json
    return st


def read_state(state_path: str) -> Optional[dict]:
    if not os.path.exists(state_path):
        return None
    try:
        with open(state_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ----------------------------------------------------------------- git helpers
def git_head(repo: str, ref: str = "HEAD") -> Optional[str]:
    rc, out, _ = run(["git", "rev-parse", ref], repo)
    return out.strip() if rc == 0 else None


def git_commit(repo: str, paths: Sequence[str], msg: str, stage: str) -> Optional[str]:
    """Stage `paths` and commit. Returns the new HEAD SHA, or None if there was
    nothing to commit. Raises StageError on a real add/commit failure."""
    must(["git", "add", *paths], repo, stage)
    rc, out, _ = run(["git", "diff", "--cached", "--quiet"], repo)
    if rc == 0:
        return None                       # nothing staged -> no-op, not an error
    before = git_head(repo)
    must(["git", "commit", "-m", msg], repo, stage)
    after = git_head(repo)
    if not after or after == before:
        raise StageError(stage, ["git", "commit"], 1, "commit did not advance HEAD")
    return after


def push_verify(repo: str, branch: str, expected_sha: str, stage: str = "REMOTE_VERIFIED",
                retries: Sequence[float] = (2, 4, 8, 16), sleep: Callable[[float], None] = time.sleep
                ) -> Tuple[bool, str]:
    """Push with bounded backoff, then FETCH and confirm origin/<branch> HEAD is
    EXACTLY `expected_sha`. Returns (ok, detail). Fail-closed: a push that appears
    to succeed but whose remote HEAD does not match is reported as failure."""
    pushed = False
    last = ""
    for i, delay in enumerate((0, *retries)):
        if delay:
            sleep(delay)
        rc, out, err = run(["git", "push", "-u", "origin", branch], repo)
        if rc == 0:
            pushed = True
            break
        last = err or out
    if not pushed:
        return False, f"push failed after {len(retries)} retries: {last[-400:]}"
    run(["git", "fetch", "origin", branch], repo)
    rc, out, err = run(["git", "rev-parse", f"origin/{branch}"], repo)
    remote = out.strip() if rc == 0 else None
    if remote == expected_sha:
        return True, remote
    return False, f"remote HEAD {remote} != local session commit {expected_sha}"


# ----------------------------------------------------------------- recovery
def detect_unpersisted(repo: str, staging_rel: str = "micro/staging") -> List[dict]:
    """A prior session is unpersisted if its staging STATE.json exists and its
    stage is neither REMOTE_VERIFIED (fully persisted) nor a finalized FAILED that
    was itself pushed. Returns the list of such sessions, newest last. Never
    deletes or overwrites anything."""
    root = os.path.join(repo, staging_rel)
    out = []
    if not os.path.isdir(root):
        return out
    for date in sorted(os.listdir(root)):
        sp = os.path.join(root, date, "STATE.json")
        st = read_state(sp)
        if not st:
            continue
        if st.get("stage") == "REMOTE_VERIFIED":
            continue
        if st.get("stage") == "FAILED" and st.get("failure_persisted"):
            continue
        out.append({"date": date, "stage": st.get("stage"), "state_path": sp, "state": st})
    return out
