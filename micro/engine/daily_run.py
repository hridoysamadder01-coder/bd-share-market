#!/usr/bin/env python3
"""ONE prospective session, end to end, then DURABLY persist to the data branch.

capture -> verify hash chain -> fuse -> frozen features -> acceptance record -> commit -> push.
Repo research code is READ-ONLY; only micro/ is written.
Nothing here fits, tunes or evaluates a model: collection only. The holdout stays sealed.

OPERATIONAL DURABILITY (no research logic touched):
  * every stage fails CLOSED: a non-zero capture / verify / fuse / commit / push aborts the
    pipeline with a non-zero exit and a persisted FAILED state — never a silent continue.
  * explicit stage machine: STARTED -> CAPTURING -> CAPTURE_COMPLETE -> VERIFIED -> FUSED ->
    COMMITTED -> REMOTE_VERIFIED (or FAILED), each with timestamps + failure detail.
  * the final push is VERIFIED: after pushing, origin/<branch> HEAD must equal the exact
    session commit SHA, else the pipeline FAILS (no fake success).
  * raw capture is checkpointed to the durable branch during the session, so a container
    death near the end does not destroy the whole session.
  * a prior unpersisted session is detected and reported before a new one starts, and never
    overwritten.
The 2026-09-06 calibration session is never a prospective session. The acceptance criteria,
universe, features, splits and holdout rules are unchanged and live in the frozen prereg.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tarfile
import time

REPO = "/home/user/bd-share-market"
MICRO = os.path.join(REPO, "micro")
BRANCH = "claude/micro-evidence-data"
sys.path.insert(0, os.path.join(MICRO, "engine"))
sys.path.insert(0, REPO)

import persist as P  # noqa: E402
from persist import StageError, must, run, write_state, git_commit, push_verify  # noqa: E402

ACCEPT_MIN_FRAMES = 2000        # frozen acceptance criteria — unchanged
ACCEPT_MIN_SYMBOLS = 12
ACCEPT_MIN_SPAN_H = 3.0
ACCEPT_MAX_QFAIL = 0.40
CHECKPOINT_SECS = 900           # push a durable raw checkpoint every 15 min during capture


def commit_footer() -> str:
    return ("\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_01Y5oSPtMaU6mDMzki4Ti6K5")


def stage_paths(date: str):
    sess = os.path.join(MICRO, "sessions", date)
    stg = os.path.join(MICRO, "staging", date)
    os.makedirs(sess, exist_ok=True)
    os.makedirs(stg, exist_ok=True)
    return sess, stg, os.path.join(stg, "STATE.json")


def fail(state_path: str, repo: str, stage_detail, exit_code: int) -> int:
    """Persist FAILED (with detail), commit+push it so the failure is durable, exit non-zero."""
    detail = stage_detail.as_dict() if isinstance(stage_detail, StageError) else {"detail": str(stage_detail)}
    write_state(state_path, "FAILED", failure=detail)
    try:
        sha = git_commit(repo, ["micro/staging/", "micro/sessions/"],
                         f"micro: session FAILED at {detail.get('stage', '?')}{commit_footer()}", "FAILED")
        if sha:
            ok, info = push_verify(repo, BRANCH, sha)
            if ok:
                # mark that the FAILED record itself reached the remote (so recovery won't re-flag it)
                write_state(state_path, "FAILED", failure=detail, failure_persisted=True, remote=info)
                git_commit(repo, ["micro/staging/"], f"micro: FAILED record persisted{commit_footer()}", "FAILED")
                push_verify(repo, BRANCH, P.git_head(repo))
    except StageError as e:
        print("could not durably persist the FAILED state:", e)
    print(f"PIPELINE FAILED (exit {exit_code}):", json.dumps(detail)[:600])
    return exit_code


def recover_prior(repo: str) -> None:
    """Detect and REPORT any prior unpersisted session. Never overwrite it."""
    pend = P.detect_unpersisted(repo)
    if not pend:
        print("recovery: no unpersisted prior session in micro/staging/.")
        return
    for s in pend:
        print(f"recovery: PRIOR UNPERSISTED SESSION {s['date']} left at stage {s['stage']} "
              f"— its evidence is preserved under micro/staging/{s['date']}/ and will NOT be "
              f"overwritten. A human should finalize or discard it.")


def checkpoint_capture(cmd, repo: str, cap: str, stg: str, state_path: str,
                       interval: float = CHECKPOINT_SECS) -> int:
    """Run the (blocking) capture as a child process, and every `interval` seconds tar the
    PARTIAL raw dir into the durable staging tree and push it. Returns the capture rc.
    A container death loses at most `interval` of work, never the whole session."""
    write_state(state_path, "CAPTURING", capture_cmd=cmd, started=P.utcnow())
    proc = subprocess.Popen(cmd, cwd=repo)
    last = time.monotonic()
    poll = min(5.0, max(0.5, interval))
    while True:
        try:
            rc = proc.wait(timeout=poll)
            break
        except subprocess.TimeoutExpired:
            rc = None
        if time.monotonic() - last >= interval:
            last = time.monotonic()
            _push_checkpoint(repo, cap, stg, state_path)
    _push_checkpoint(repo, cap, stg, state_path, final=True)
    return rc if rc is not None else proc.returncode


def _push_checkpoint(repo: str, cap: str, stg: str, state_path: str, final: bool = False) -> None:
    """Tar whatever raw exists so far into staging and push it. Best-effort: a failed
    checkpoint is logged but does not kill the capture (the final push is the hard gate)."""
    if not os.path.isdir(cap):
        return
    ckpt = os.path.join(stg, "raw_checkpoint.tar.gz")
    try:
        with tarfile.open(ckpt + ".tmp", "w:gz") as t:
            t.add(cap, arcname=os.path.basename(cap))
        os.replace(ckpt + ".tmp", ckpt)
        write_state(state_path, "CAPTURING", checkpoint_utc=P.utcnow(),
                    checkpoint_bytes=os.path.getsize(ckpt), final_checkpoint=final)
        sha = git_commit(repo, ["micro/staging/"], f"micro: raw checkpoint{commit_footer()}", "CAPTURING")
        if sha:
            ok, info = push_verify(repo, BRANCH, sha, retries=(2, 4))
            print(f"checkpoint push: {'OK ' + info if ok else 'DEFERRED ' + info}")
    except (StageError, OSError) as e:
        print("checkpoint deferred (non-fatal):", str(e)[:200])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="trading date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--start", default="03:50")
    ap.add_argument("--end", default="08:20")
    ap.add_argument("--skip-capture", action="store_true", help="reuse an already-captured raw dir")
    # ---- TEST HOOKS (default off; the Routine invokes with no flags = real path) ----
    ap.add_argument("--simulate", action="store_true", help="TEST ONLY: fake fast capture, no market")
    ap.add_argument("--fail-at", default=None,
                    help="TEST ONLY: force a failure at capture|verify|fuse|commit|push|remote")
    ap.add_argument("--checkpoint-secs", type=float, default=CHECKPOINT_SECS)
    a = ap.parse_args()
    date = a.date or dt.datetime.utcnow().strftime("%Y-%m-%d")

    wd = dt.date.fromisoformat(date).weekday()          # Mon=0 .. Sun=6 (DSE trades Sun..Thu)
    if wd in (4, 5):
        print(f"{date} is a DSE weekend (Fri/Sat) — nothing to capture.")
        return 0
    if date == "2026-09-06" and not a.simulate:
        print("2026-09-06 is the SEEN calibration session and can never be a prospective session.")
        return 0

    if not a.simulate:
        must(["git", "fetch", "origin", BRANCH], REPO, "SYNC")
        must(["git", "checkout", BRANCH], REPO, "SYNC")
        run(["git", "pull", "--ff-only", "origin", BRANCH], REPO)

    # 0) recover/report any prior unpersisted session BEFORE starting a new one
    recover_prior(REPO)

    cap = f"/tmp/dse_micro_capture/{date}"
    sess, stg, state_path = stage_paths(date)

    # 1) PREFLIGHT heartbeat (kept from 23ed6a1): prove push access before the 4.5 h capture.
    write_state(state_path, "STARTED", date=date, started_utc=P.utcnow(),
                note="preflight heartbeat proves push access; NOT session success")
    json.dump({"date": date, "phase": "STARTED", "started_utc": P.utcnow()},
              open(os.path.join(sess, "HEARTBEAT.json"), "w"), indent=2)
    try:
        sha = git_commit(REPO, ["micro/sessions/", "micro/staging/"],
                         f"micro: {date} capture STARTED (preflight heartbeat){commit_footer()}", "STARTED")
    except StageError as e:
        return fail(state_path, REPO, e, 2)
    if sha is not None:
        ok, info = push_verify(REPO, BRANCH, sha)
        if not ok:
            write_state(state_path, "FAILED", failure={"stage": "PREFLIGHT", "detail": info})
            print("PREFLIGHT FAILED: cannot durably push to the data branch. Aborting BEFORE the "
                  f"4.5 h capture so no session time is wasted. Detail: {info}")
            return 3
        print("PREFLIGHT OK:", info)

    from micro_features import UNIVERSE, build

    try:
        # 2) CAPTURE — fail closed
        if a.fail_at == "capture":
            raise StageError("CAPTURING", ["capture"], 1, "injected capture failure")
        if a.simulate:
            _simulate_capture(cap, fail=(a.fail_at == "capture"))
            write_state(state_path, "CAPTURE_COMPLETE", simulated=True)
        elif not a.skip_capture:
            cmd = ["python3", "-m", "seeing.capture.runner", "--out", cap, "--date", date,
                   "--start", a.start, "--end", a.end, "--symbols", ",".join(UNIVERSE)]
            rc = checkpoint_capture(cmd, REPO, cap, stg, state_path, interval=a.checkpoint_secs)
            if rc != 0:
                raise StageError("CAPTURING", cmd, rc, "capture runner exited non-zero")
            write_state(state_path, "CAPTURE_COMPLETE", capture_rc=rc)
        else:
            write_state(state_path, "CAPTURE_COMPLETE", skipped=True)

        # 3) VERIFY — fail closed on a bad hash chain
        if a.fail_at == "verify":
            raise StageError("VERIFIED", ["verify"], 1, "injected verify failure")
        if a.simulate:
            hash_ok = True
        else:
            rc, out, err = run(["python3", "-m", "seeing", "verify", "--capture", cap], REPO)
            hash_ok = rc == 0 and "FAIL" not in (out or "").upper()
            if not hash_ok:
                raise StageError("VERIFIED", ["seeing", "verify"], rc or 1,
                                 "hash-chain verification failed: " + (out or err)[-400:])
        write_state(state_path, "VERIFIED", hash_chain_ok=bool(hash_ok))

        # 4) FUSE + FEATURES — fail closed
        if a.fail_at == "fuse":
            raise StageError("FUSED", ["fuse"], 1, "injected fuse failure")
        fp = os.path.join(sess, "frames.parquet")
        if a.simulate:
            rec = _simulate_features(sess, date, build)
        else:
            rc, out, err = run(["python3", "-m", "seeing", "fuse", "--capture", cap, "--out", sess], REPO)
            if rc != 0:
                raise StageError("FUSED", ["seeing", "fuse"], rc, "fuse failed: " + (err or out)[-400:])
            if not os.path.exists(fp):
                raise StageError("FUSED", ["seeing", "fuse"], 1,
                                 "fuse produced no frames.parquet — cannot build features")
            rec = _build_features(sess, date, fp, hash_ok, build)
        write_state(state_path, "FUSED", accepted=rec.get("accepted"),
                    n_frames=rec.get("n_frames"), reject_reasons=rec.get("reject_reasons"))

        json.dump(rec, open(os.path.join(sess, "SESSION.json"), "w"), indent=2)

        # 5) raw evidence into the session tree (final, complete tar)
        if os.path.isdir(cap):
            os.makedirs(os.path.join(MICRO, "raw"), exist_ok=True)
            with tarfile.open(os.path.join(MICRO, "raw", f"{date}.tar.gz"), "w:gz") as t:
                t.add(cap, arcname=date)

        # 6) INDEX counter — unchanged split semantics
        _update_index(date, rec)

        # 7) COMMIT — fail closed, capture the exact SHA
        if a.fail_at == "commit":
            raise StageError("COMMITTED", ["git", "commit"], 1, "injected commit failure")
        msg = (f"micro: prospective session {date} "
               f"({'ACCEPTED' if rec.get('accepted') else 'REJECTED'}; next slot "
               f"{json.load(open(os.path.join(MICRO, 'sessions', 'INDEX.json')))['next_slot']})\n\n"
               "Collection only. No model was fitted, tuned or evaluated. Holdout sealed." + commit_footer())
        sha = git_commit(REPO, ["micro/"], msg, "COMMITTED")
        if sha is None:
            raise StageError("COMMITTED", ["git", "commit"], 1, "nothing was committed for the session")
        write_state(state_path, "COMMITTED", commit_sha=sha)

        # 8) FINAL PUSH — verified against the real remote HEAD (no fake success)
        if a.fail_at in ("push", "remote"):
            ok, info = (False, "injected push failure") if a.fail_at == "push" \
                else (False, f"remote HEAD deadbeef != local session commit {sha}")
        else:
            ok, info = push_verify(REPO, BRANCH, sha)
        if not ok:
            return fail(state_path, REPO, StageError("REMOTE_VERIFIED", ["git", "push"], 1, info), 4)
        write_state(state_path, "REMOTE_VERIFIED", commit_sha=sha, remote_head=info)
        # clear the staging tree for this date now that it is fully persisted
        _clear_staging(date, sha)

        print(json.dumps({"date": date, "accepted": rec.get("accepted"),
                          "commit_sha": sha, "remote_head": info,
                          "stage": "REMOTE_VERIFIED"}, indent=2))
        return 0

    except StageError as e:
        return fail(state_path, REPO, e, 5)


# ---------------------------------------------------------------- feature build (unchanged logic)
def _build_features(sess: str, date: str, fp: str, hash_ok: bool, build) -> dict:
    import pandas as pd
    d = build(pd.read_parquet(fp))
    d["session"] = date
    d.to_parquet(os.path.join(sess, "features.parquet"), index=False)
    span = (d["t_frame"].max() - d["t_frame"].min()).total_seconds() / 3600.0
    rec = {"date": date, "hash_chain_ok": bool(hash_ok), "n_frames": int(len(d)),
           "n_symbols": int(d["symbol"].nunique()), "span_hours": round(span, 2),
           "quality_fail_share": round(float(d["q_exclude"].mean()), 4),
           "dup_share": round(float(d["r_dup"].mean()), 4),
           "eligible": int(d["eligible"].sum())}
    checks = [("hash_chain", rec["hash_chain_ok"]), ("min_frames", rec["n_frames"] >= ACCEPT_MIN_FRAMES),
              ("min_symbols", rec["n_symbols"] >= ACCEPT_MIN_SYMBOLS),
              ("min_span", rec["span_hours"] >= ACCEPT_MIN_SPAN_H),
              ("quality", rec["quality_fail_share"] <= ACCEPT_MAX_QFAIL)]
    rec["accepted"] = all(c for _, c in checks)
    rec["reject_reasons"] = [k for k, c in checks if not c]
    os.remove(fp)
    return rec


def _update_index(date: str, rec: dict) -> None:
    ip = os.path.join(MICRO, "sessions", "INDEX.json")
    idx = json.load(open(ip))
    if rec.get("simulated"):
        # A --simulate run carries no market data. It must never occupy a DEV/VAL/HOLDOUT
        # slot. This guard exists because a smoke test on 2026-09-07 did exactly that:
        # it wrote 2099-01-01 into accepted_sessions and filled the first DEV slot.
        idx.setdefault("simulated_sessions_never_counted", [])
        note = f"{date} (--simulate; no market data)"
        if note not in idx["simulated_sessions_never_counted"]:
            idx["simulated_sessions_never_counted"].append(note)
        json.dump(idx, open(ip, "w"), indent=2)
        print(f"INDEX not advanced: {date} is a --simulate run, never counted as a session")
        return
    for k in ("accepted_sessions", "rejected_sessions"):
        idx[k] = [s for s in idx.get(k, []) if s != date]
    idx["accepted_sessions" if rec.get("accepted") else "rejected_sessions"].append(date)
    idx["accepted_sessions"].sort()
    idx["rejected_sessions"].sort()
    n = len(idx["accepted_sessions"])
    idx["counts"] = {"accepted": n, "dev_filled": min(n, 8),
                     "val_filled": max(0, min(n - 8, 4)), "holdout_filled": max(0, n - 12)}
    idx["next_slot"] = "DEV" if n < 8 else ("VALIDATION" if n < 12 else "FINAL_HOLDOUT")
    idx["sessions_until_holdout_opens"] = max(0, 12 - n)
    json.dump(idx, open(ip, "w"), indent=2)


def _clear_staging(date: str, sha: str) -> None:
    import shutil
    stg = os.path.join(MICRO, "staging", date)
    if os.path.isdir(stg):
        shutil.rmtree(stg, ignore_errors=True)
    try:
        s = git_commit(REPO, ["micro/staging/"], f"micro: {date} persisted at {sha[:12]}, staging cleared"
                       + commit_footer(), "REMOTE_VERIFIED")
        if s:
            push_verify(REPO, BRANCH, s, retries=(2, 4))
    except StageError as e:
        print("staging cleanup deferred (non-fatal):", str(e)[:200])


# ---------------------------------------------------------------- test-only simulators
def _simulate_capture(cap: str, fail: bool = False) -> None:
    os.makedirs(cap, exist_ok=True)
    for i in range(3):
        with open(os.path.join(cap, f"seg_{i}.raw"), "w") as f:
            f.write(f"simulated raw segment {i}\n")
    if fail:
        raise StageError("CAPTURING", ["sim"], 1, "injected")


def _simulate_features(sess: str, date: str, build) -> dict:
    # a plausible ACCEPTED record without any market data, for durability tests only
    with open(os.path.join(sess, "features.parquet"), "w") as f:
        f.write("SIMULATED")
    return {"date": date, "hash_chain_ok": True, "n_frames": 4000, "n_symbols": 14,
            "span_hours": 4.4, "quality_fail_share": 0.2, "dup_share": 0.5, "eligible": 3000,
            "accepted": True, "reject_reasons": [], "simulated": True}


if __name__ == "__main__":
    raise SystemExit(main())
