#!/usr/bin/env python3
"""Operational-durability tests for persist.py. NO market session is run.

Every git guarantee is exercised against a REAL local bare repo acting as "origin",
so push / commit / remote-HEAD verification are genuinely tested, not mocked.
Prints expected vs actual for each case and exits non-zero if any guarantee is violated.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import persist as P
from persist import StageError, git_commit, push_verify, write_state, read_state, detect_unpersisted

PASS = True


def check(name, cond, extra=""):
    global PASS
    PASS = PASS and bool(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def make_origin_and_clone(tmp):
    """A bare repo = fake origin, plus a working clone on the data branch."""
    origin = os.path.join(tmp, "origin.git")
    work = os.path.join(tmp, "work")
    sh(["git", "init", "--bare", "-b", "claude/micro-evidence-data", origin], tmp)
    sh(["git", "clone", origin, work], tmp)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        sh(["git", "config", k, v], work)
    os.makedirs(os.path.join(work, "micro", "sessions"), exist_ok=True)
    open(os.path.join(work, "micro", "sessions", ".keep"), "w").close()
    sh(["git", "add", "-A"], work)
    sh(["git", "commit", "-m", "init"], work)
    sh(["git", "push", "-u", "origin", "claude/micro-evidence-data"], work)
    return origin, work


BR = "claude/micro-evidence-data"


def test_commit_returns_sha_and_none(tmp):
    print("\n[1] commit captures the exact SHA; nothing-to-commit -> None (not an error)")
    _, work = make_origin_and_clone(tmp)
    open(os.path.join(work, "micro", "sessions", "a.json"), "w").write("{}")
    sha = git_commit(work, ["micro/"], "add a", "COMMITTED")
    head = P.git_head(work)
    check("commit returns the new HEAD sha", sha == head, f"sha={sha}")
    sha2 = git_commit(work, ["micro/"], "noop", "COMMITTED")
    check("nothing-to-commit returns None", sha2 is None)


def test_commit_failure_raises(tmp):
    print("\n[2] a real commit failure raises StageError (fail-closed), never silent")
    _, work = make_origin_and_clone(tmp)
    # break committing by pointing commit at a bogus author via an env-less hook: use a failing pre-commit
    hook = os.path.join(work, ".git", "hooks", "pre-commit")
    open(hook, "w").write("#!/bin/sh\nexit 1\n")
    os.chmod(hook, 0o755)
    open(os.path.join(work, "micro", "sessions", "b.json"), "w").write("{}")
    raised = False
    try:
        git_commit(work, ["micro/"], "should fail", "COMMITTED")
    except StageError as e:
        raised = True
        check("StageError carries stage/rc", e.stage == "COMMITTED" and e.rc != 0, f"rc={e.rc}")
    check("commit failure raised, not swallowed", raised)


def test_push_verify_success(tmp):
    print("\n[3] push_verify: on success, origin HEAD == local session SHA")
    origin, work = make_origin_and_clone(tmp)
    open(os.path.join(work, "micro", "sessions", "c.json"), "w").write("{}")
    sha = git_commit(work, ["micro/"], "c", "COMMITTED")
    ok, info = push_verify(work, BR, sha, retries=(1,))
    remote = sh(["git", "rev-parse", BR], origin).stdout.strip()
    check("push_verify returns ok", ok, f"info={info}")
    check("origin HEAD equals the pushed session SHA", info == sha == remote)


def test_push_failure_fails_closed(tmp):
    print("\n[4] push to an unreachable origin -> push_verify FAILS (no fake success)")
    _, work = make_origin_and_clone(tmp)
    sh(["git", "remote", "set-url", "origin", "/nonexistent/repo.git"], work)
    open(os.path.join(work, "micro", "sessions", "d.json"), "w").write("{}")
    sha = git_commit(work, ["micro/"], "d", "COMMITTED")
    ok, info = push_verify(work, BR, sha, retries=(0, 0), sleep=lambda s: None)
    check("push_verify returns NOT ok when push cannot reach origin", not ok, f"info={info[:80]}")


def test_remote_head_mismatch_fails(tmp):
    print("\n[5] remote HEAD != local session SHA -> push_verify FAILS")
    origin, work = make_origin_and_clone(tmp)
    # push a real commit, then ask push_verify to confirm a DIFFERENT (wrong) expected sha
    open(os.path.join(work, "micro", "sessions", "e.json"), "w").write("{}")
    real = git_commit(work, ["micro/"], "e", "COMMITTED")
    wrong = "deadbeef" * 5
    ok, info = push_verify(work, BR, wrong, retries=(1,))
    check("mismatch detected: ok is False", not ok, f"info={info[:90]}")
    check("failure names the real remote != expected", real[:8] in info or "!=" in info)


def test_state_machine_atomic(tmp):
    print("\n[6] stage machine records stages + timestamps; STATE.json write is atomic")
    sp = os.path.join(tmp, "STATE.json")
    for stg in ("STARTED", "CAPTURING", "CAPTURE_COMPLETE", "VERIFIED", "FUSED", "COMMITTED", "REMOTE_VERIFIED"):
        write_state(sp, stg, note=f"at {stg}")
    st = read_state(sp)
    stages = [h["stage"] for h in st["history"]]
    check("all stages recorded in order", stages == ["STARTED", "CAPTURING", "CAPTURE_COMPLETE",
          "VERIFIED", "FUSED", "COMMITTED", "REMOTE_VERIFIED"], f"{stages}")
    check("every history entry has a timestamp", all("at" in h for h in st["history"]))
    check("current stage is the last written", st["stage"] == "REMOTE_VERIFIED")
    check("no leftover .tmp file (atomic replace)", not os.path.exists(sp + ".tmp"))


def test_recovery_detects_unpersisted(tmp):
    print("\n[7] an interrupted session (CAPTURE_COMPLETE, never pushed) is detected, not overwritten")
    repo = os.path.join(tmp, "repo")
    d = os.path.join(repo, "micro", "staging", "2026-09-09")
    os.makedirs(d, exist_ok=True)
    write_state(os.path.join(d, "STATE.json"), "CAPTURE_COMPLETE", note="container died before push")
    open(os.path.join(d, "raw_checkpoint.tar.gz"), "w").write("partial")
    pend = detect_unpersisted(repo)
    check("exactly one unpersisted session found", len(pend) == 1, f"{[p['date'] for p in pend]}")
    check("it is the interrupted date at the right stage",
          pend and pend[0]["date"] == "2026-09-09" and pend[0]["stage"] == "CAPTURE_COMPLETE")
    check("its raw checkpoint still exists (not overwritten/deleted)",
          os.path.exists(os.path.join(d, "raw_checkpoint.tar.gz")))
    # a REMOTE_VERIFIED session must NOT be flagged
    d2 = os.path.join(repo, "micro", "staging", "2026-09-08")
    os.makedirs(d2, exist_ok=True)
    write_state(os.path.join(d2, "STATE.json"), "REMOTE_VERIFIED", note="done")
    pend2 = detect_unpersisted(repo)
    check("a fully-persisted session is NOT flagged", {p["date"] for p in pend2} == {"2026-09-09"})


def test_end_to_end_simulated(tmp):
    print("\n[8] full pipeline via daily_run --simulate against a real bare origin: reaches REMOTE_VERIFIED")
    origin, work = make_origin_and_clone(tmp)
    # seed INDEX.json + engine files into the work clone, run daily_run against it
    idx = {"accepted_sessions": [], "rejected_sessions": [], "counts": {}, "next_slot": "DEV"}
    json.dump(idx, open(os.path.join(work, "micro", "sessions", "INDEX.json"), "w"))
    for f in ("persist.py", "daily_run.py", "micro_features.py"):
        src = os.path.join(HERE, f)
        os.makedirs(os.path.join(work, "micro", "engine"), exist_ok=True)
        if os.path.exists(src):
            open(os.path.join(work, "micro", "engine", f), "w").write(open(src).read())
    sh(["git", "add", "-A"], work)
    sh(["git", "commit", "-m", "seed"], work)
    sh(["git", "push", "origin", BR], work)
    env = dict(os.environ, MICRO_TEST_REPO=work)
    # run each failure injection + the success path
    results = {}
    for fail_at in (None, "capture", "verify", "fuse", "commit", "push", "remote"):
        args = [sys.executable, os.path.join(work, "micro", "engine", "daily_run.py"),
                "--simulate", "--date", "2026-09-10"]
        if fail_at:
            args += ["--fail-at", fail_at]
        # patch REPO/BRANCH by running with cwd=work and a shim: daily_run uses REPO const, so
        # exercise persist-level guarantees here and the success/fail EXIT CODES via a subprocess shim
        r = _run_daily_in(work, origin, fail_at)
        results[fail_at or "success"] = r
    check("success path exits 0", results["success"]["rc"] == 0, f"rc={results['success']['rc']}")
    for stg in ("capture", "verify", "fuse", "commit", "push", "remote"):
        check(f"failure at {stg} exits NON-zero (fail-closed)", results[stg]["rc"] != 0,
              f"rc={results[stg]['rc']}")
    # after the success run, origin HEAD must carry the session commit and STATE REMOTE_VERIFIED
    remote_log = sh(["git", "log", "--oneline", "-5", BR], origin).stdout
    check("origin carries the ACCEPTED/REJECTED session commit after success",
          "prospective session 2026-09-10" in remote_log, remote_log.replace("\n", " | ")[:160])


def _run_daily_in(work, origin, fail_at):
    """Run daily_run.py with REPO/BRANCH pointed at the test clone via a tiny shim module."""
    shim = os.path.join(work, "run_shim.py")
    open(shim, "w").write(
        "import sys, os\n"
        f"sys.path.insert(0, {os.path.join(work, 'micro', 'engine')!r})\n"
        "import daily_run as D\n"
        f"D.REPO = {work!r}\n"
        f"D.MICRO = {os.path.join(work, 'micro')!r}\n"
        "import persist as P\n"
        "argv = ['daily_run', '--simulate', '--date', '2026-09-10']\n"
        f"fa = {fail_at!r}\n"
        "if fa: argv += ['--fail-at', fa]\n"
        "sys.argv = argv\n"
        "raise SystemExit(D.main())\n")
    r = subprocess.run([sys.executable, shim], cwd=work, capture_output=True, text=True)
    return {"rc": r.returncode, "out": r.stdout[-1500:], "err": r.stderr[-800:]}


def test_checkpoint_during_capture(tmp):
    print("\n[9] raw is checkpointed to durable origin DURING capture (container death != total loss)")
    import daily_run as D
    origin, work = make_origin_and_clone(tmp)
    D.REPO = work
    cap = os.path.join(tmp, "capdir")
    stg = os.path.join(work, "micro", "staging", "2026-09-11")
    os.makedirs(stg, exist_ok=True)
    sp = os.path.join(stg, "STATE.json")
    # a fake capture: write a file, sleep past the checkpoint interval, write another, then exit 0
    fake = os.path.join(tmp, "fake_capture.sh")
    open(fake, "w").write(f"#!/bin/sh\nmkdir -p {cap}\necho a > {cap}/a.raw\nsleep 3\necho b > {cap}/b.raw\n")
    os.chmod(fake, 0o755)
    rc = D.checkpoint_capture(["sh", fake], work, cap, stg, sp, interval=1.0)
    check("fake capture returned 0", rc == 0, f"rc={rc}")
    # origin must carry at least one raw checkpoint commit made mid-capture
    log = sh(["git", "log", "--oneline", BR], origin).stdout
    check("a raw checkpoint commit reached origin", "raw checkpoint" in log,
          log.replace("\n", " | ")[:140])
    st = read_state(sp)
    check("STATE recorded CAPTURING with a checkpoint", st and st.get("stage") == "CAPTURING"
          and any(h.get("checkpoint_utc") for h in st.get("history", [])))


def main():
    print("=" * 90)
    print("OPERATIONAL DURABILITY TESTS — real local bare git repos, no market session")
    print("=" * 90)
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (test_commit_returns_sha_and_none, test_commit_failure_raises, test_push_verify_success,
                   test_push_failure_fails_closed, test_remote_head_mismatch_fails, test_state_machine_atomic,
                   test_recovery_detects_unpersisted, test_checkpoint_during_capture):
            with tempfile.TemporaryDirectory() as sub:
                fn(sub)
        with tempfile.TemporaryDirectory() as sub:
            test_end_to_end_simulated(sub)
    print("\n" + "=" * 90)
    print("DURABILITY TESTS:", "ALL PASS" if PASS else "FAILURES PRESENT")
    print("=" * 90)
    return 0 if PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
