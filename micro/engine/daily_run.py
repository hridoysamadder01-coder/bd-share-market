#!/usr/bin/env python3
"""ONE prospective session, end to end, then persist to the data branch.

capture -> verify hash chain -> fuse -> frozen features -> acceptance record -> commit.
Repo research code is READ-ONLY; only micro/ is written.
Nothing here fits, tunes or evaluates a model: collection only. The holdout stays sealed.
"""
import argparse, datetime as dt, json, os, subprocess, sys, tarfile

REPO = "/home/user/bd-share-market"
MICRO = os.path.join(REPO, "micro")
BRANCH = "claude/micro-evidence-data"
sys.path.insert(0, os.path.join(MICRO, "engine")); sys.path.insert(0, REPO)


def sh(cmd, cwd=REPO, check=False):
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.stdout: print(r.stdout[-2500:])
    if r.returncode and r.stderr: print("STDERR:", r.stderr[-1500:])
    if check and r.returncode: raise SystemExit(f"failed: {' '.join(cmd)}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="trading date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--start", default="03:50"); ap.add_argument("--end", default="08:20")
    ap.add_argument("--skip-capture", action="store_true")
    a = ap.parse_args()
    date = a.date or dt.datetime.utcnow().strftime("%Y-%m-%d")

    # DSE trades Sunday..Thursday
    wd = dt.date.fromisoformat(date).weekday()          # Mon=0 .. Sun=6
    if wd in (4, 5):                                     # Fri, Sat
        print(f"{date} is a DSE weekend (Fri/Sat) — nothing to capture."); return 0
    if date == "2026-09-06":
        print("2026-09-06 is the SEEN calibration session and can never be a prospective session."); return 0

    sh(["git", "fetch", "origin", BRANCH])
    sh(["git", "checkout", BRANCH]); sh(["git", "pull", "--ff-only", "origin", BRANCH])

    cap = f"/tmp/dse_micro_capture/{date}"
    sess = os.path.join(MICRO, "sessions", date); os.makedirs(sess, exist_ok=True)
    from micro_features import UNIVERSE, build

    if not a.skip_capture:
        sh(["python3", "-m", "seeing.capture.runner", "--out", cap, "--date", date,
            "--start", a.start, "--end", a.end, "--symbols", ",".join(UNIVERSE)])
    ver = sh(["python3", "-m", "seeing", "verify", "--capture", cap])
    hash_ok = ver.returncode == 0 and "FAIL" not in (ver.stdout or "").upper()
    sh(["python3", "-m", "seeing", "fuse", "--capture", cap, "--out", sess])

    import pandas as pd
    fp = os.path.join(sess, "frames.parquet")
    if not os.path.exists(fp):
        rec = {"date": date, "accepted": False, "reject_reasons": ["no frames.parquet produced"],
               "hash_chain_ok": hash_ok}
    else:
        d = build(pd.read_parquet(fp)); d["session"] = date
        d.to_parquet(os.path.join(sess, "features.parquet"), index=False)
        span = (d["t_frame"].max() - d["t_frame"].min()).total_seconds() / 3600.0
        rec = {"date": date, "hash_chain_ok": bool(hash_ok), "n_frames": int(len(d)),
               "n_symbols": int(d["symbol"].nunique()), "span_hours": round(span, 2),
               "quality_fail_share": round(float(d["q_exclude"].mean()), 4),
               "dup_share": round(float(d["r_dup"].mean()), 4),
               "eligible": int(d["eligible"].sum())}
        checks = [("hash_chain", rec["hash_chain_ok"]), ("min_frames", rec["n_frames"] >= 2000),
                  ("min_symbols", rec["n_symbols"] >= 12), ("min_span", rec["span_hours"] >= 3.0),
                  ("quality", rec["quality_fail_share"] <= 0.40)]
        rec["accepted"] = all(c for _, c in checks)
        rec["reject_reasons"] = [k for k, c in checks if not c]
        os.remove(fp)                                   # features.parquet supersedes it
    json.dump(rec, open(os.path.join(sess, "SESSION.json"), "w"), indent=2)

    # raw evidence, compressed
    if os.path.isdir(cap):
        os.makedirs(os.path.join(MICRO, "raw"), exist_ok=True)
        with tarfile.open(os.path.join(MICRO, "raw", f"{date}.tar.gz"), "w:gz") as t:
            t.add(cap, arcname=date)

    # authoritative counter
    ip = os.path.join(MICRO, "sessions", "INDEX.json")
    idx = json.load(open(ip))
    for k in ("accepted_sessions", "rejected_sessions"):
        idx[k] = [s for s in idx.get(k, []) if s != date]
    idx["accepted_sessions" if rec.get("accepted") else "rejected_sessions"].append(date)
    idx["accepted_sessions"].sort(); idx["rejected_sessions"].sort()
    n = len(idx["accepted_sessions"])
    idx["counts"] = {"accepted": n, "dev_filled": min(n, 8),
                     "val_filled": max(0, min(n - 8, 4)), "holdout_filled": max(0, n - 12)}
    idx["next_slot"] = "DEV" if n < 8 else ("VALIDATION" if n < 12 else "FINAL_HOLDOUT")
    idx["sessions_until_holdout_opens"] = max(0, 12 - n)
    json.dump(idx, open(ip, "w"), indent=2)

    print(json.dumps(rec, indent=2)); print(json.dumps(idx["counts"], indent=2))
    sh(["git", "add", "micro/"])
    sh(["git", "commit", "-m",
        f"micro: prospective session {date} "
        f"({'ACCEPTED' if rec.get('accepted') else 'REJECTED'}; {n} accepted so far, next slot {idx['next_slot']})\n\n"
        "Collection only. No model was fitted, tuned or evaluated. Holdout outcomes remain sealed.\n\n"
        "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01Y5oSPtMaU6mDMzki4Ti6K5"])
    for i, delay in enumerate((2, 4, 8, 16)):
        if sh(["git", "push", "-u", "origin", BRANCH]).returncode == 0: break
        import time; time.sleep(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
