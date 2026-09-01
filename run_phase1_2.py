#!/usr/bin/env python3
"""Reproducible driver for Phases 1–2 plus both proofs.

  python3 bd_research/run_phase1_2.py                      # synthetic fixture
  python3 bd_research/run_phase1_2.py --input data/raw/x.parquet --tag dse_v1

Order matters: fixture → detector self-test → QA audit → feature build →
leakage proof. Any step failing stops the run; a green run means every artifact
in reports/, results/ and manifests/ came from the same inputs and code.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def step(title: str, cmd: list[str]) -> None:
    print("\n" + "=" * 72)
    print(f"▶ {title}")
    print("=" * 72)
    rc = subprocess.call([sys.executable] + cmd, cwd=HERE)
    if rc != 0:
        raise SystemExit(f"STEP FAILED ({rc}): {title}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None, help="real bar file; omit to use the fixture")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--frequency", choices=["DAILY", "MINUTE"], default=None)
    ap.add_argument("--leak-symbols", type=int, default=0,
                    help="subsample N symbols for the leakage proof (large datasets)")
    a = ap.parse_args()

    synthetic = a.input is None
    tag = a.tag or ("synthetic" if synthetic else "run")
    src = a.input or "data/synthetic/synthetic_minute_bars.parquet"
    freq = a.frequency or ("MINUTE" if synthetic else "DAILY")

    if synthetic:
        step("regenerate the synthetic fixture (deterministic seed)",
             ["data/make_synthetic.py"])
        step("QA SELF-TEST — every planted defect must be detected",
             ["qa/verify_detectors.py"])

    step("PHASE 1 — data audit",
         ["qa/run_qa.py", "--input", src, "--tag", tag, "--frequency", freq])
    step("PHASE 2 — adaptive features + separate outcome labels",
         ["features/run_features.py", "--input", f"results/{tag}_bars_annotated.parquet",
          "--tag", tag])
    leak = ["features/leakage_test.py", "--input", src, "--cuts", "8",
            "--frequency", freq]
    if a.leak_symbols:
        leak += ["--symbols", str(a.leak_symbols)]
    step("NO-LOOKAHEAD PROOF — future corruption + positive control", leak)

    print("\n" + "=" * 72)
    print("PHASES 1–2 COMPLETE — machinery verified.")
    print("No research claim is made or implied by this run.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
