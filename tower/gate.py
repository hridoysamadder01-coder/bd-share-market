"""Completion gate: one command that produces the evidence behind the receipt.

    python3 -m tower.gate --capture DIR --out DIR [--skip-tests]

Steps (each writes into <out>/GATE.json):
  1. test suite counts (pytest -q tests, machinery vs real-data split by test name)
  2. deterministic replay of the committed fixture twice → identical final hashes
  3. replay of the given capture → states, non-empty-book states, failures, mechanisms with
     varying scores, circuit fields present, source freshness present, transitions per layer
  4. placeholder sweep over tower/ (TODO / FIXME / NotImplementedError / pass-only bodies)
  5. Go ingest: go vet + go test (if go is present)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd: List[str], cwd: str = ROOT, timeout: int = 3600) -> Dict[str, Any]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return {"cmd": " ".join(cmd), "rc": p.returncode, "tail": (p.stdout + p.stderr)[-1500:]}


def pytest_counts(extra: List[str]) -> Dict[str, Any]:
    r = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rN", "tests"] + extra, timeout=5400)
    m = re.search(r"(\d+) passed", r["tail"]); f = re.search(r"(\d+) failed", r["tail"]); s = re.search(r"(\d+) skipped", r["tail"])
    e = re.search(r"(\d+) error", r["tail"])
    return {"passed": int(m.group(1)) if m else None, "failed": int(f.group(1)) if f else 0,
            "skipped": int(s.group(1)) if s else 0, "errors": int(e.group(1)) if e else 0, "rc": r["rc"],
            "summary_line": r["tail"].strip().splitlines()[-1] if r["tail"].strip() else ""}


def collect_split() -> Dict[str, int]:
    col = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "tests"],
                         cwd=ROOT, capture_output=True, text=True, timeout=600).stdout
    names = [ln.strip() for ln in col.splitlines() if "::" in ln]
    return {"total_collected": len(names), "realdata": sum("realdata" in n for n in names),
            "machinery": sum("machinery" in n for n in names),
            "other": sum(("realdata" not in n and "machinery" not in n) for n in names)}


def replay_checks(capture: str, out: str) -> Dict[str, Any]:
    from .replay import Replayer
    from .store import read_states, read_timeline
    fixture = os.path.join(ROOT, "tests", "fixtures", "capture_closed")
    res: Dict[str, Any] = {}
    h = []
    for i in range(2):
        r = Replayer(fixture, os.path.join(out, f"gate_fixture_{i}"))
        r.load(); r.run(); h.append(dict(r.store.hashes))
    res["fixture_determinism"] = {"identical": h[0] == h[1], "symbols": sorted(h[0])}
    r = Replayer(capture, os.path.join(out, "gate_capture"))
    n = r.load(); r.run()
    m = r.engine.metrics_snapshot()
    syms = sorted(r.store.hashes)
    per: Dict[str, Any] = {}
    mech_var: Dict[str, set] = defaultdict(set)
    layers = Counter()
    for s in syms:
        rows = read_states(os.path.join(out, "gate_capture"), s)
        ne = sum(1 for x in rows if not x["empty_book"])
        two = sum(1 for x in rows if x["spread"] is not None)
        circ = sum(1 for x in rows if (x.get("circuit") or {}).get("upper_limit") is not None)
        fresh = sum(1 for x in rows if any(v.get("freshness_s") is not None for v in (x.get("sources") or {}).values()))
        act = Counter(a for x in rows for a in x.get("active_mechanisms", []))
        for x in rows:
            for k, v in (x.get("mechanisms") or {}).items():
                mech_var[k].add(round(v["score"], 3))
        per[s] = {"states": len(rows), "nonempty_book": ne, "two_sided": two, "circuit_limits_present": circ,
                  "source_freshness_present": fresh, "active_mechanisms": dict(act.most_common(8))}
    for t in read_timeline(os.path.join(out, "gate_capture")):
        layers[t["layer"].split(":")[0]] += 1
    res["capture"] = {"path": os.path.abspath(capture), "events": n, "states": m["states_out"],
                      "reconstruction_failures": m["reconstruction_failures"], "errors": m["errors"][:5],
                      "quote_only_suppressed": m.get("quote_only_states_suppressed", 0),
                      "previous_session_tape_rows": m.get("previous_session_tape_rows", 0), "symbols": per,
                      "mechanisms_with_varying_scores": sum(1 for k, v in mech_var.items() if len(v) > 1),
                      "mechanisms_total": len(mech_var),
                      "mechanisms_constant": sorted(k for k, v in mech_var.items() if len(v) <= 1),
                      "transitions_per_layer": dict(layers), "final_state_hash": dict(r.store.hashes)}
    return res


def placeholder_sweep() -> Dict[str, Any]:
    hits: List[str] = []
    pat = re.compile(r"\bTODO\b|\bFIXME\b|NotImplementedError|placeholder|\bpass\s*$", re.I)
    for dp, _, fs in os.walk(os.path.join(ROOT, "tower")):
        for fn in fs:
            if not fn.endswith((".py", ".go", ".js")):
                continue
            p = os.path.join(dp, fn)
            for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                if pat.search(line):
                    hits.append(f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()[:120]}")
    return {"n": len(hits), "hits": hits[:80]}


def go_checks() -> Dict[str, Any]:
    if not shutil.which("go"):
        return {"skipped": "go not installed"}
    d = os.path.join(ROOT, "tower", "ingest")
    return {"vet": run(["go", "vet", "./..."], cwd=d)["rc"], "test": run(["go", "test", "./..."], cwd=d),
            "build": run(["go", "build", "-o", "ingest", "."], cwd=d)["rc"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    gate: Dict[str, Any] = {}
    if not a.skip_tests:
        gate["tests"] = pytest_counts([])
        gate["tests"]["split"] = collect_split()
    gate["replay"] = replay_checks(a.capture, a.out)
    gate["placeholders"] = placeholder_sweep()
    gate["go"] = go_checks()
    with open(os.path.join(a.out, "GATE.json"), "w") as fh:
        json.dump(gate, fh, indent=1, default=str)
    print(json.dumps({k: (v if k != "replay" else {kk: vv for kk, vv in v["capture"].items() if kk != "symbols"})
                      for k, v in gate.items()}, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
