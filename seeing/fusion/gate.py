"""Stage 2 gate: the same raw store must replay to the same state, twice.

    python3 -m seeing.fusion.gate --capture evidence/capture/2026-09-06 \
                                  --out evidence/unified/<date>

Determinism is the whole gate. A state hash is only worth carrying if replaying
the identical bytes reproduces it, so both passes go back to the raw segments —
parse, fuse and unify from scratch each time — rather than the second pass
reusing the first one's tables, which would test nothing but `copy()`.

Six checks, each a measurement:

1. **states_built** — the replay produced states at all.
2. **run_hash_stable** — the two passes agree on the run hash.
3. **every_state_hash_stable** — and on all N state hashes, positionally.
4. **no_future_reference** — no state carries a limit it could not have seen
   (`unified.assert_no_future_reference`); the ROADMAP Stage 2 prohibition.
5. **identity_from_spine** — every state is keyed by a Stage 0 listing, or is
   explicitly marked unresolved. Nothing is guessed onto an exchange.
6. **consensus_reported** — where two sources report a field, agreement is
   recorded rather than resolved by preference.

A check that cannot be evaluated is reported as such and fails the gate. There
is no "assumed pass".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

from ..replay import replay
from .fuse import fuse
from .unified import (IdentityIndex, STATE_SCHEMA, assert_no_future_reference,
                      run_hash, summary, unify)


def build_once(capture: str, identity: Optional[IdentityIndex] = None) -> pd.DataFrame:
    """Raw segments → unified states. The entire pipeline, from bytes."""
    tables = replay(capture)
    return unify(fuse(tables), identity=identity)


def _check(name: str, ok: Optional[bool], detail: Any) -> Dict[str, Any]:
    return {"check": name, "pass": bool(ok) if ok is not None else False,
            "evaluated": ok is not None, "detail": detail}


def gate(capture: str, identity_map: Optional[str] = None) -> Dict[str, Any]:
    idx = IdentityIndex.load(identity_map)
    a = build_once(capture, idx)
    b = build_once(capture, idx)

    checks: List[Dict[str, Any]] = []
    checks.append(_check("states_built", len(a) > 0, {"states": int(len(a))}))

    ha, hb = run_hash(a), run_hash(b)
    checks.append(_check("run_hash_stable", ha is not None and ha == hb,
                         {"pass_1": ha, "pass_2": hb}))

    if len(a) == len(b):
        sa, sb = list(a["state_sha256"]), list(b["state_sha256"])
        mismatch = [i for i, (x, y) in enumerate(zip(sa, sb)) if x != y]
        checks.append(_check("every_state_hash_stable", not mismatch,
                             {"compared": len(sa), "mismatched": len(mismatch),
                              "first_mismatch_index": mismatch[0] if mismatch else None}))
    else:
        checks.append(_check("every_state_hash_stable", False,
                             {"pass_1_states": int(len(a)), "pass_2_states": int(len(b)),
                              "note": "the two passes produced different state counts"}))

    try:
        assert_no_future_reference(a)
        checks.append(_check("no_future_reference", True,
                             {"ref_status": {str(k): int(v) for k, v in
                                             a["ref_status"].value_counts().items()}}))
    except AssertionError as e:
        checks.append(_check("no_future_reference", False, {"error": str(e)}))

    unresolved = int(a["listing_key"].isna().sum())
    notes = sorted({n for n in a.loc[a["listing_key"].isna(), "identity_note"] if n})
    checks.append(_check("identity_from_spine", a["identity_truth"].notna().all(),
                         {"listings": int(a["listing_key"].nunique(dropna=True)),
                          "unresolved_states": unresolved,
                          "unresolved_reasons": notes[:20],
                          "spine_sha256": a.attrs.get("spine_sha256")}))

    has_cons = [c for c in a.columns if c.startswith("cons_")]
    dis = int((a["xsrc_disagreeing"] != "").sum()) if "xsrc_disagreeing" in a else 0
    checks.append(_check("consensus_reported", bool(has_cons),
                         {"consensus_columns": len(has_cons),
                          "states_with_a_disagreement": dis,
                          "policy": "consensus filled only where two reporting sources "
                                    "agree; disagreement keeps both values"}))

    passed = sum(1 for c in checks if c["pass"])
    return {"schema": "stage2_gate/1", "state_schema": STATE_SCHEMA,
            "capture": capture, "checks": checks,
            "passed": passed, "of": len(checks),
            "verdict": "PASS" if passed == len(checks) else "FAIL",
            "summary": summary(a)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--out", default=None, help="directory for STAGE2_GATE.json")
    ap.add_argument("--identity-map", default=None)
    ap.add_argument("--states", default=None, help="also write the unified states to this parquet")
    a = ap.parse_args(argv)

    rep = gate(a.capture, a.identity_map)
    print(json.dumps(rep, indent=1))
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "STAGE2_GATE.json"), "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, sort_keys=True)
        print(f"wrote {a.out}/STAGE2_GATE.json", file=sys.stderr)
    if a.states:
        from .fuse import frames_for_storage
        u = build_once(a.capture, IdentityIndex.load(a.identity_map))
        os.makedirs(os.path.dirname(os.path.abspath(a.states)), exist_ok=True)
        frames_for_storage(u).to_parquet(a.states, index=False)
        print(f"wrote {a.states}", file=sys.stderr)
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
