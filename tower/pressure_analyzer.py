"""Turn the queue-emulator JSON series into a time series and read its trend.

Reads every ``evidence/queue_sim_*.json``, builds one frame, takes 5-window rolling
averages of the inferred queues, and labels the gap between them BUY / SELL / NEUTRAL.
Writes ``data/pressure_analysis.csv``.

The divergence label is a threshold on an INFERRED quantity, not a signal with any
established relationship to price. `micro/test_pressure.py` is what measures whether it
has one; until that runs on real forward outcomes the label carries no claim.

    python3 -m tower.pressure_analyzer [--threshold 1000]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from typing import List, Optional, Sequence

import pandas as pd

__all__ = ["analyze_pressure", "load_series"]

DEFAULT_GLOB = "evidence/queue_sim_*.json"
DEFAULT_OUT = "data/pressure_analysis.csv"
DEFAULT_THRESHOLD = 1000.0
ROLL = 5


def load_series(pattern: str = DEFAULT_GLOB) -> pd.DataFrame:
    """Every snapshot from every matching file, gaps dropped, sorted by symbol and time."""
    rows: List[dict] = []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[skip] {path}: {type(exc).__name__}: {exc}")
            continue
        if isinstance(payload, dict):
            payload = [payload]
        for row in payload:
            if isinstance(row, dict):
                row.setdefault("source_file", os.path.basename(path))
                rows.append(row)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    n_all = len(df)
    if "gap" in df.columns:
        df = df[~df["gap"].fillna(False).astype(bool)]
    n_gap = n_all - len(df)
    if n_gap:
        print(f"[info] dropped {n_gap} gap row(s) of {n_all}")
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df = df[df["timestamp"].notna()]
    if "symbol" not in df.columns:
        df["symbol"] = "UNKNOWN"
    return df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def analyze_pressure(pattern: str = DEFAULT_GLOB, out_path: str = DEFAULT_OUT,
                     threshold: float = DEFAULT_THRESHOLD) -> Optional[pd.DataFrame]:
    """Rolling averages, their difference, and a divergence label. Returns the frame."""
    df = load_series(pattern)
    if df.empty:
        print("No data files found.")
        return None

    # Rolling windows are per symbol: mixing symbols would average unrelated books.
    grouped = df.groupby("symbol", sort=False)
    df["buy_queue_ma"] = grouped["inferred_buy_queue"].transform(
        lambda s: s.rolling(ROLL, min_periods=ROLL).mean())
    df["sell_queue_ma"] = grouped["inferred_sell_queue"].transform(
        lambda s: s.rolling(ROLL, min_periods=ROLL).mean())
    df["pressure_diff"] = df["buy_queue_ma"] - df["sell_queue_ma"]
    df["divergence"] = df["pressure_diff"].apply(
        lambda x: "NEUTRAL" if pd.isna(x) else
        ("BUY" if x > threshold else "SELL" if x < -threshold else "NEUTRAL"))
    df["divergence_threshold"] = threshold

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    df.to_csv(out_path, index=False)

    warm = int(df["pressure_diff"].notna().sum())
    counts = df["divergence"].value_counts().to_dict()
    print(f"Processed {len(df)} records across {df['symbol'].nunique()} symbol(s). "
          f"Saved to {out_path}")
    print(f"  rolling window {ROLL}: {warm} row(s) warmed up, "
          f"{len(df) - warm} still filling")
    print(f"  divergence at |diff| > {threshold:g}: {counts}")
    if warm and not any(k in counts for k in ("BUY", "SELL")):
        print("  no row crossed the threshold — the series is flat or the threshold is high "
              "for these volumes")
    return df


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pattern", default=DEFAULT_GLOB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    a = ap.parse_args(argv)
    return 0 if analyze_pressure(a.pattern, a.out, a.threshold) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
