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
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs", "tests"] + extra,
                       cwd=ROOT, capture_output=True, text=True, timeout=5400)
    out = p.stdout + p.stderr
    tail = out[-4000:]
    m = re.search(r"(\d+) passed", tail); f = re.search(r"(\d+) failed", tail); s = re.search(r"(\d+) skipped", tail)
    e = re.search(r"(\d+) error", tail)
    return {"passed": int(m.group(1)) if m else None, "failed": int(f.group(1)) if f else 0,
            "skipped": int(s.group(1)) if s else 0, "errors": int(e.group(1)) if e else 0, "rc": p.returncode,
            "failed_tests": re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M),
            "skipped_tests": re.findall(r"^SKIPPED \[\d+\] (\S+)", out, re.M),
            "summary_line": tail.strip().splitlines()[-1] if tail.strip() else ""}


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
    """Every TODO/FIXME/NotImplementedError/placeholder/bare-``pass`` line in tower/ (own code only:
    vendored Go modules and this script are skipped). A ``pass`` that is the body of an ``except``
    clause and an abstract-base ``raise NotImplementedError`` are classified as benign and listed
    separately, so the ``n`` that matters is ``n_suspicious``."""
    suspicious: List[str] = []
    benign: List[str] = []
    pat = re.compile(r"\bTODO\b|\bFIXME\b|NotImplementedError|placeholder|\bpass\s*$", re.I)
    for dp, _, fs in os.walk(os.path.join(ROOT, "tower")):
        if os.sep + "vendor" in dp or os.sep + "node_modules" in dp:
            continue
        for fn in fs:
            if not fn.endswith((".py", ".go", ".js")) or fn == "gate.py":
                continue
            p = os.path.join(dp, fn)
            lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            for i, line in enumerate(lines, 1):
                if not pat.search(line):
                    continue
                rec = f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()[:120]}"
                prev = lines[i - 2].strip() if i >= 2 else ""
                s = line.strip()
                if s == "pass" and (prev.startswith("except") or prev.startswith("try")):
                    benign.append(rec + "  [except-clause no-op]")
                elif "NotImplementedError" in s and "raise" in s and os.path.basename(p) == "base.py":
                    benign.append(rec + "  [abstract base method]")
                else:
                    suspicious.append(rec)
    return {"n_suspicious": len(suspicious), "suspicious": suspicious[:80], "n_benign": len(benign), "benign": benign[:40]}


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
    ap.add_argument("--skip-tests", action="store_true", help="replay/placeholder/go checks only; merges <out>/TESTS.json if present")
    ap.add_argument("--tests-only", action="store_true", help="run the suite only and write <out>/TESTS.json (run in parallel with --skip-tests)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    gate: Dict[str, Any] = {}
    tests_json = os.path.join(a.out, "TESTS.json")
    if a.tests_only:
        t = pytest_counts([]); t["split"] = collect_split()
        with open(tests_json, "w") as fh:
            json.dump(t, fh, indent=1, default=str)
        print(json.dumps(t, indent=1, default=str)[:3000])
        return 0 if t.get("rc") == 0 else 1
    if not a.skip_tests:
        gate["tests"] = pytest_counts([])
        gate["tests"]["split"] = collect_split()
    gate["replay"] = replay_checks(a.capture, a.out)
    gate["placeholders"] = placeholder_sweep()
    gate["go"] = go_checks()
    if a.skip_tests and os.path.exists(tests_json):          # a parallel --tests-only run that has finished
        gate["tests"] = json.load(open(tests_json))
    with open(os.path.join(a.out, "GATE.json"), "w") as fh:
        json.dump(gate, fh, indent=1, default=str)
    print(json.dumps({k: (v if k != "replay" else {kk: vv for kk, vv in v["capture"].items() if kk != "symbols"})
                      for k, v in gate.items()}, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
