"""Command line.

    python3 -m seeing status     --capture DIR                 live capture health
    python3 -m seeing verify     --capture DIR                 hash-chain / CRC verification of the raw store
    python3 -m seeing replay     --capture DIR --out DIR       raw → parsed tables (parquet + csv summaries)
    python3 -m seeing fuse       --capture DIR --out DIR       raw → fused frames + features + states
    python3 -m seeing experiment --capture DIR --out DIR       full pipeline → experiment + falsification + verdict
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

from . import replay as rp
from .capture.raw_store import verify_store
from .experiment.design import DESIGN
from .experiment.falsify import run_falsifications
from .experiment.run_experiment import assign_splits, denominator, evaluate, incremental
from .features.micro import features, labels
from .fusion.fuse import frames_for_storage, fuse
from .state_machine.machine import run_state_machine, transition_matrix
from .truth import truth_summary


def _write_manifest(out: str, payload: Dict[str, Any]) -> None:
    from .capture.raw_store import sha256_file
    payload = dict(payload)
    payload["design"] = DESIGN.describe()
    payload["written_utc"] = pd.Timestamp.utcnow().isoformat()
    try:
        import subprocess
        payload["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        payload["git_commit"] = "unknown"
    files = {}
    for root, _, names in os.walk(out):
        for n in names:
            if n.endswith((".csv", ".json", ".parquet")) and n != "MANIFEST.json":
                p = os.path.join(root, n)
                files[os.path.relpath(p, out)] = {"sha256": sha256_file(p), "bytes": os.path.getsize(p)}
    payload["outputs"] = files
    with open(os.path.join(out, "MANIFEST.json"), "w") as fh:
        json.dump(payload, fh, indent=1, default=str)


def cmd_status(a: argparse.Namespace) -> int:
    p = os.path.join(a.capture, "STATUS.json")
    if not os.path.exists(p):
        print("no STATUS.json yet")
        return 1
    s = json.load(open(p))
    print(json.dumps(s, indent=1))
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    v = verify_store(a.capture)
    print(json.dumps({k: v[k] for k in ("n", "all_ok", "chain_ok")}, indent=1))
    bad = [r for r in v["segments"] if not r["ok"]]
    for r in bad:
        print("NOT OK:", r)
    return 0 if v["all_ok"] else 2


def _replay_tables(a: argparse.Namespace) -> Dict[str, Any]:
    t = rp.replay(a.capture)
    return t


def cmd_replay(a: argparse.Namespace) -> int:
    os.makedirs(a.out, exist_ok=True)
    t = _replay_tables(a)
    s = rp.summary(t)
    print(json.dumps(s, indent=1, default=str))
    for k in ("books", "watch", "tape", "market", "block", "circuit", "gaps", "heartbeats", "meta", "hts", "latest"):
        df = t.get(k)
        if df is None or not len(df):
            continue
        g = frames_for_storage(df)
        g.to_parquet(os.path.join(a.out, f"{k}.parquet"), index=False)
    with open(os.path.join(a.out, "REPLAY_PROBLEMS.txt"), "w") as fh:
        fh.write("\n".join(t["problems"]))
    with open(os.path.join(a.out, "REPLAY_SUMMARY.json"), "w") as fh:
        json.dump(s, fh, indent=1, default=str)
    return 0


def build_frames(a: argparse.Namespace) -> Dict[str, Any]:
    t = _replay_tables(a)
    f = fuse(t)
    if not len(f):
        return {"tables": t, "frames": f}
    f = features(f, DESIGN)
    f = labels(f, DESIGN)
    f = assign_splits(f, DESIGN)
    f = run_state_machine(f, DESIGN)
    return {"tables": t, "frames": f}


def cmd_fuse(a: argparse.Namespace) -> int:
    os.makedirs(a.out, exist_ok=True)
    r = build_frames(a)
    f = r["frames"]
    if not len(f):
        print("no book frames replayed — nothing to fuse")
        return 1
    frames_for_storage(f).drop(columns=["level_events"], errors="ignore").to_parquet(os.path.join(a.out, "frames.parquet"), index=False)
    cols = [c for c in f.columns if c not in ("bid_levels", "ask_levels", "level_events")]
    f[cols].head(2000).to_csv(os.path.join(a.out, "frames_head.csv"), index=False)
    tm = transition_matrix(f)
    tm.to_csv(os.path.join(a.out, "STATE_TRANSITIONS.csv"))
    ev = f.attrs.get("events")
    if ev is not None and len(ev):
        ev.to_parquet(os.path.join(a.out, "events.parquet"), index=False)
    truth = {k: (v.value if hasattr(v, "value") else str(v)) for k, v in f.attrs.get("truth", {}).items()}
    with open(os.path.join(a.out, "TRUTH_MAP.json"), "w") as fh:
        json.dump(truth, fh, indent=1)
    print(json.dumps({"frames": len(f), "symbols": int(f["symbol"].nunique()), "states": f["state"].value_counts().to_dict()}, indent=1))
    return 0


def cmd_experiment(a: argparse.Namespace) -> int:
    os.makedirs(a.out, exist_ok=True)
    r = build_frames(a)
    f = r["frames"]
    if not len(f):
        print("no book frames replayed — nothing to test")
        return 1
    # frames raw (before features) are needed by the largest-wall removal
    raw_frames = fuse(r["tables"])
    frames_for_storage(f).drop(columns=["level_events"], errors="ignore").to_parquet(os.path.join(a.out, "frames.parquet"), index=False)
    den = denominator(f, DESIGN)
    ev = evaluate(f, DESIGN)
    inc = pd.concat([incremental(f, DESIGN, s, h) for s in ("dev", "val", "holdout") for h in DESIGN.horizons], ignore_index=True)
    liq = None
    meta = r["tables"].get("meta")
    if meta is not None and len(meta):
        for p in meta["payload"]:
            try:
                d = json.loads(p)
            except Exception:  # noqa: BLE001
                continue
            u = d.get("universe")
            if u and u.get("top"):
                liq = {s: "top" for s in u["top"]}
                liq.update({s: "mid" for s in u.get("mid", [])})
    fal = run_falsifications(f, raw_frames, DESIGN, liquidity_groups=liq)
    ev.to_csv(os.path.join(a.out, "EXPERIMENT_RESULTS.csv"), index=False)
    inc.to_csv(os.path.join(a.out, "INCREMENTAL_VS_BASELINES.csv"), index=False)
    fal["table"].to_csv(os.path.join(a.out, "FALSIFICATION.csv"), index=False)
    transition_matrix(f).to_csv(os.path.join(a.out, "STATE_TRANSITIONS.csv"))
    with open(os.path.join(a.out, "DENOMINATOR.json"), "w") as fh:
        json.dump(den, fh, indent=1, default=str)
    with open(os.path.join(a.out, "VERDICT.json"), "w") as fh:
        json.dump(fal["verdict"], fh, indent=1, default=str)
    truth = {k: (v.value if hasattr(v, "value") else str(v)) for k, v in f.attrs.get("truth", {}).items()}
    with open(os.path.join(a.out, "TRUTH_MAP.json"), "w") as fh:
        json.dump(truth, fh, indent=1)
    _write_manifest(a.out, {"capture": a.capture, "replay_counts": r["tables"]["counts"],
                            "replay_problems": len(r["tables"]["problems"]), "denominator": den})
    print("VERDICT:", fal["verdict"]["verdict"])
    for reason in fal["verdict"].get("reasons", []):
        print("  -", reason)
    print(json.dumps({k: den[k] for k in ("n_frames", "n_symbols", "composite_frames", "composite_episodes_total",
                                          "composite_episodes_holdout", "frames_per_split")}, indent=1, default=str))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="seeing", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("status", cmd_status), ("verify", cmd_verify), ("replay", cmd_replay), ("fuse", cmd_fuse),
                     ("experiment", cmd_experiment)):
        sp = sub.add_parser(name)
        sp.add_argument("--capture", required=True)
        if name in ("replay", "fuse", "experiment"):
            sp.add_argument("--out", required=True)
        sp.set_defaults(fn=fn)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
