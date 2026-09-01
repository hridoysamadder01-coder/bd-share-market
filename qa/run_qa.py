#!/usr/bin/env python3
"""PHASE 1 runner — audit a bar dataset and write the QA artifacts.

  python3 bd_research/qa/run_qa.py --input <path> [--tag synthetic]

Outputs (all under bd_research/):
  reports/DATA_QA_REPORT.md      human-readable audit
  qa/EXCLUSIONS.csv              one row per (row, reason code) — nothing hidden
  results/<tag>_bars_annotated.parquet   every input row + flags + qa_exclude
  manifests/<tag>_qa_manifest.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bdlib import config as C  # noqa: E402
from bdlib import io as bio  # noqa: E402
from bdlib import qa as Q  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--frequency", choices=["DAILY", "MINUTE"], default=None,
                    help="override the bar frequency for this run")
    a = ap.parse_args()

    if a.frequency:
        C.BAR_FREQUENCY = a.frequency
    p = bio.paths()
    df = bio.load_bars(a.input)
    annotated, exclusions, summary = Q.audit(df, C.DEFAULT)

    md = Q.report_markdown(summary, a.input, bio.sha256_file(a.input))
    with open(os.path.join(p["reports"], "DATA_QA_REPORT.md"), "w") as fh:
        fh.write(md)
    exclusions.to_csv(os.path.join(p["qa"], "EXCLUSIONS.csv"), index=False)
    out_bars = os.path.join(p["results"], f"{a.tag}_bars_annotated.parquet")
    annotated.to_parquet(out_bars, index=False)

    manifest = bio.write_manifest(f"{a.tag}_qa_manifest.json", {
        "phase": "1_data_foundation",
        "bar_frequency": C.BAR_FREQUENCY,
        "input": {"path": a.input, "sha256": bio.sha256_file(a.input)},
        "thresholds": C.DEFAULT.qa.__dict__,
        "summary": summary,
        "outputs": ["reports/DATA_QA_REPORT.md", "qa/EXCLUSIONS.csv",
                    f"results/{a.tag}_bars_annotated.parquet"],
    })
    print(json.dumps({"excluded": summary["rows_excluded"],
                      "by_code": summary["exclusions_by_code"],
                      "flags": summary["flags"],
                      "manifest": os.path.basename(manifest)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
