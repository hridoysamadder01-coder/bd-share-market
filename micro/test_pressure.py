"""Does inferred buy pressure predict the next move?

Reads ``data/pressure_analysis.csv``, takes every row whose divergence label is BUY or
SELL, and checks the mid price recorded later in the SAME captured series at
``--horizon`` seconds ahead. Writes ``evidence/test_results.json``.

Exploratory. This file is NOT part of ``micro/MICRO_PREREG.json`` and touches nothing
inside it: no threshold, feature, model, outcome, universe, split or gate of the frozen
prospective experiment is read or written here.

Two things the obvious implementation gets wrong, and what is done instead:

* **Circular label.** Comparing a row's prediction against ``buy_pressure_index >
  sell_pressure_index`` on that same row scores the signal against itself and always
  reports near-perfect accuracy. The outcome here is the forward MID PRICE, an
  independent quantity the signal does not contain.
* **Sleeping for the horizon.** Waiting 300 s per row takes days over a real series and
  measures nothing extra. The series is already on disk with timestamps, so the forward
  point is looked up, not waited for.

A number is only reported when it can mean something. If no row has a forward point, or
the mid never moves across the whole window (a closed-market capture), the verdict is
BLOCKED with the exact reason instead of an accuracy figure. Accuracy is always reported
next to the base rate of UP moves, since accuracy alone is not evidence of skill.

    python3 -m micro.test_pressure --horizon 300
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd

__all__ = ["test_pressure"]

DEFAULT_CSV = "data/pressure_analysis.csv"
DEFAULT_OUT = "evidence/test_results.json"
DEFAULT_HORIZON_S = 300.0


def test_pressure(csv_path: str = DEFAULT_CSV, out_path: str = DEFAULT_OUT,
                  horizon_s: float = DEFAULT_HORIZON_S) -> Dict[str, Any]:
    """Score the divergence label against the forward mid move. Never invents a number."""
    results: Dict[str, Any] = {
        "csv": csv_path, "horizon_seconds": horizon_s,
        "total": 0, "correct": 0, "accuracy": 0.0,
        "verdict": "BLOCKED", "reason": "", "truth": "INFERRED signal vs OBSERVED forward mid",
    }
    if not os.path.exists(csv_path):
        results["reason"] = f"{csv_path} not found — run tower.pressure_analyzer first"
        return _write(results, out_path)

    df = pd.read_csv(csv_path)
    if df.empty:
        results["reason"] = f"{csv_path} is empty"
        return _write(results, out_path)
    for col in ("timestamp", "divergence", "mid", "symbol"):
        if col not in df.columns:
            results["reason"] = f"column {col!r} missing from {csv_path}"
            return _write(results, out_path)

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["mid"] = pd.to_numeric(df["mid"], errors="coerce")
    df = df[df["timestamp"].notna() & df["mid"].notna()].sort_values(["symbol", "timestamp"])
    if df.empty:
        results["reason"] = "no row carries both a timestamp and a mid price"
        return _write(results, out_path)

    span = (df["timestamp"].max() - df["timestamp"].min()).total_seconds()
    results["capture_span_seconds"] = round(float(span), 1)
    results["rows_with_mid"] = int(len(df))
    results["symbols"] = sorted(df["symbol"].astype(str).unique().tolist())

    if df["mid"].nunique() <= 1:
        results["reason"] = (f"the mid price never changed across {span:.0f}s of capture "
                             f"({df['mid'].nunique()} distinct value) — nothing to predict. "
                             f"Re-run the capture while the market is open.")
        return _write(results, out_path)
    if span < horizon_s:
        results["reason"] = (f"capture spans {span:.0f}s, shorter than the {horizon_s:.0f}s "
                             f"horizon — no row has a forward point")
        return _write(results, out_path)

    total = correct = up_moves = graded = 0
    per_symbol: Dict[str, Dict[str, int]] = {}
    for symbol, grp in df.groupby("symbol", sort=False):
        grp = grp.reset_index(drop=True)
        secs = (grp["timestamp"] - grp["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
        mids = grp["mid"].to_numpy(dtype=float)
        # First frame at or after t + horizon; nothing is stretched across the end.
        fwd_idx = np.searchsorted(secs, secs + horizon_s, side="left")
        for i, label in enumerate(grp["divergence"].astype(str)):
            j = int(fwd_idx[i])
            if j >= len(mids):
                continue
            move = float(mids[j] - mids[i])
            graded += 1
            if move > 0:
                up_moves += 1
            if label not in ("BUY", "SELL") or move == 0:
                continue          # no directional call, or a flat outcome to score against
            total += 1
            hit = (label == "BUY" and move > 0) or (label == "SELL" and move < 0)
            correct += int(hit)
            s = per_symbol.setdefault(str(symbol), {"total": 0, "correct": 0})
            s["total"] += 1
            s["correct"] += int(hit)

    results["rows_with_forward_point"] = graded
    results["base_rate_up"] = round(up_moves / graded, 4) if graded else None
    results["total"] = total
    results["correct"] = correct
    results["per_symbol"] = per_symbol
    if total == 0:
        results["reason"] = (f"{graded} row(s) had a forward point but none carried a BUY/SELL "
                             f"label with a non-flat outcome — no directional call to score")
        return _write(results, out_path)

    results["accuracy"] = round(correct / total * 100, 2)
    results["verdict"] = "MEASURED"
    results["reason"] = (f"{correct}/{total} directional calls correct at {horizon_s:.0f}s. "
                         f"Compare against the base rate of up moves, not against 50%. "
                         f"A single capture is not evidence of an edge: this needs many "
                         f"sessions and the falsification battery before any claim.")
    return _write(results, out_path)


def _write(results: Dict[str, Any], out_path: str) -> Dict[str, Any]:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    if results["verdict"] == "MEASURED":
        print(f"Test complete. Accuracy: {results['accuracy']}% "
              f"({results['correct']}/{results['total']}), "
              f"base rate up: {results['base_rate_up']}")
    else:
        print(f"BLOCKED — {results['reason']}")
    print(f"  written to {out_path}")
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--horizon", type=float, default=DEFAULT_HORIZON_S,
                    help="forward horizon in seconds (default 300)")
    a = ap.parse_args(argv)
    return 0 if test_pressure(a.csv, a.out, a.horizon)["verdict"] == "MEASURED" else 1


if __name__ == "__main__":
    sys.exit(main())
