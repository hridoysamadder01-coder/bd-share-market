#!/usr/bin/env python3
"""QA SELF-TEST — proves the Phase-1 detectors actually fire.

A clean QA report on clean data proves nothing. This compares what
`data/make_synthetic.py` PLANTED against what `bdlib.qa.audit` DETECTED and
fails loudly on any miss. Run it whenever a detector or a threshold changes.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bdlib import config as C  # noqa: E402
from bdlib import io as bio  # noqa: E402
from bdlib import qa as Q  # noqa: E402

SYNTH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "data", "synthetic")


def main() -> int:
    with open(os.path.join(SYNTH, "PLANTED_DEFECTS.json")) as fh:
        planted = json.load(fh)["planted"]
    df = bio.load_bars(os.path.join(SYNTH, "synthetic_minute_bars.parquet"))
    annotated, exclusions, summary = Q.audit(df, C.DEFAULT)

    got_codes = summary["exclusions_by_code"]
    got_flags = summary["flags"]

    # (label, expected, actual, exact?) — a run-length flag counts one fewer row
    # than the fixture length because the first bar of a run has no predecessor.
    checks = [
        ("OHLC_INCONSISTENT", planted["OHLC_INCONSISTENT"], got_codes.get("OHLC_INCONSISTENT", 0), True),
        ("NAN_FIELD", planted["NAN_FIELD"], got_codes.get("NAN_FIELD", 0), True),
        ("NONPOS_PRICE", planted["NONPOS_PRICE"], got_codes.get("NONPOS_PRICE", 0), True),
        ("DUP_BAR", planted["DUP_BAR"], got_codes.get("DUP_BAR", 0), True),
        ("OUT_OF_SESSION", planted["OUT_OF_SESSION"], got_codes.get("OUT_OF_SESSION", 0), True),
        ("ZERO_VOLUME", planted["ZERO_VOLUME"], got_flags["zero_volume"], True),
        ("LOCKED_BAR", planted["LOCKED_BAR"], got_flags["locked_bar"], True),
        ("LARGE_OVERNIGHT_GAP", planted["LARGE_OVERNIGHT_GAP"], got_flags["large_overnight_gap"], True),
        ("STALE_RUN", planted["STALE_RUN_rows"] - 1, got_flags["stale_run"], True),
    ]

    failures = []
    print(f"{'detector':<24}{'planted':>9}{'detected':>10}   verdict")
    print("-" * 60)
    for name, exp, got, exact in checks:
        ok = (got == exp) if exact else (got >= exp)
        print(f"{name:<24}{exp:>9}{got:>10}   {'ok' if ok else 'MISS'}")
        if not ok:
            failures.append((name, exp, got))

    # Structural checks that do not have a single planted count.
    sv = summary["survivorship"]
    struct = [
        ("late listing detected", len(sv["late_listings"]) >= 1),
        ("thin day detected", summary["thin_days_count"] >= 1),
        ("coverage shortfall detected", len(sv["symbols_below_coverage"]) >= 1),
        ("nothing silently dropped", len(annotated) == len(df)),
        ("every exclusion has a reason code", exclusions["code"].notna().all()
         if len(exclusions) else True),
    ]
    print("-" * 60)
    for name, ok in struct:
        print(f"{name:<44}{'ok' if ok else 'FAIL'}")
        if not ok:
            failures.append((name, True, False))

    print("-" * 60)
    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL DETECTORS VERIFIED — every planted defect was found, "
          "no input row was silently dropped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
