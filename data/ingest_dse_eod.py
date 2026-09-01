#!/usr/bin/env python3
"""Ingest the owner-supplied DSE end-of-day CSVs into one normalised bar file.

Input : data/raw/merged_eod/<SYMBOL>.csv with header Date,Open,High,Low,Close,Volume
Output: data/raw/dse_eod.parquet  (symbol, ts, open, high, low, close, volume, turnover)
        data/raw/RAW_MANIFEST.json — per-file sha256, row count and date range, so
        the exact dataset behind any result is identifiable without shipping 36 MB.

Conversions are limited to reshaping. No price is adjusted, no gap is filled, no
row is dropped except the non-equity instrument classes listed below — and that
exclusion is recorded per symbol, not applied silently.

`turnover` is DERIVED as close × volume; the source has no turnover column. Every
report prints `turnover_derived: true` so nothing treats it as exchange-reported.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

# Instrument-class filter, carried over verbatim from prior_rounds/round2.py so
# this workspace's universe matches the universe those findings were measured on.
NONEQ = ("BOND", "SUKUK", "MF", "TB1", "TB2", "TB5", "TB10", "TB15", "TB20",
         "00DS", "ETF", "PBOND", "GBF", "INCOMEF", "GROWTHF")


def looks_equity(sym: str) -> bool:
    return not any(h in sym for h in NONEQ)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(HERE, "raw", "merged_eod"))
    ap.add_argument("--out", default=os.path.join(HERE, "raw", "dse_eod.parquet"))
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.src, "*.csv")))
    if not files:
        raise SystemExit(f"no CSVs under {a.src}")

    frames, manifest, excluded = [], [], []
    for path in files:
        sym = os.path.basename(path)[:-4]
        if sym.startswith("_") or not looks_equity(sym):
            excluded.append(sym)
            continue
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        df = df.rename(columns={"date": "ts"})
        df["symbol"] = sym
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
        frames.append(df[["symbol", "ts", "open", "high", "low", "close", "volume"]])
        manifest.append({
            "symbol": sym, "file": os.path.basename(path), "sha256": sha256(path),
            "rows": int(len(df)),
            "first": str(df["ts"].min()), "last": str(df["ts"].max()),
        })

    bars = pd.concat(frames, ignore_index=True)
    bars["turnover"] = bars["close"] * bars["volume"]   # DERIVED — see module docstring
    bars = bars.sort_values(["symbol", "ts"], kind="mergesort").reset_index(drop=True)
    bars.to_parquet(a.out, index=False)

    meta = {
        "source": "owner-supplied DSE end-of-day CSVs (merged_eod)",
        "bar_frequency": "DAILY",
        "turnover_derived": True,
        "files_seen": len(files),
        "symbols_kept": len(manifest),
        "symbols_excluded_non_equity": sorted(excluded),
        "rows": int(len(bars)),
        "date_range": [str(bars["ts"].min()), str(bars["ts"].max())],
        "per_file": manifest,
    }
    with open(os.path.join(HERE, "raw", "RAW_MANIFEST.json"), "w") as fh:
        json.dump(meta, fh, indent=2)

    print(f"wrote {a.out}")
    print(f"  symbols kept        : {len(manifest)}")
    print(f"  excluded non-equity : {len(excluded)} {sorted(excluded)[:12]}")
    print(f"  rows                : {len(bars):,}")
    print(f"  date range          : {bars['ts'].min().date()} → {bars['ts'].max().date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
