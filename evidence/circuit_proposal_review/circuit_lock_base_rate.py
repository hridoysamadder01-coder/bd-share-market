#!/usr/bin/env python3
"""Base rate of an upper-circuit lock on DSE, measured from the extended EOD table.

Written to answer one question about a proposed "circuit pre-detection engine": is the
event it wants to predict frequent enough to model at all? Frequency is the only thing
measured here. Nothing in this file predicts anything, and no candidate is scored — the
predictive question was already run and rejected as P45-8 in `REJECTED_CANDIDATES.md`.

Two definitions are reported, deliberately:

* the **naive** proxy, `return >= band - tol` with no upper bound. This is the exact
  definition rejected as **I-008**: ex-date and bonus reference-price resets clear the
  band without any lock having happened, so the count is inflated.
* the **at-band** definition, `band - tol <= return <= band + tol`. A move beyond the
  band cannot be a lock, because the band is where trading stops.

The gap between them is reported rather than hidden, so the size of the I-008 effect on
the UP side is on the record (it is much smaller than the 27-50 % that contaminated the
DOWN side).

The percentage band comes from the exchange's own published ladder, not a guess — see
`evidence/public/2026-09-06/normalized/circuit_limits.parquet`, whose 379 equity rows
carry exactly one `breaker_pct` per price band. The historical table has no per-date
circuit limits, so the ladder is applied to each bar's previous close; this is an
approximation, and it is the reason these are base rates and not an event list.

    python3 evidence/circuit_proposal_review/circuit_lock_base_rate.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXTENDED = os.path.join(ROOT, "data", "raw", "dse_eod_extended.parquet")
BASE = os.path.join(ROOT, "results", "dse_eod_bars_annotated.parquet")

# The published equity ladder, measured from circuit_limits.parquet (379 equity rows):
# <=200 -> 10.00 (334 rows), 200-500 -> 8.75 (23), 500-1000 -> 7.50 (12), >1000 -> 3.75-6.25 (10).
# The >1000 band is not single-valued, so the most permissive value is used there; it covers
# 10 instruments and cannot move the aggregate.
TOL = 0.35  # tick rounding: a 0.1 tick on a ~20 Tk stock is ~0.5 % of the move


def band_pct(prev_close: float) -> float:
    if prev_close <= 200:
        return 10.00
    if prev_close <= 500:
        return 8.75
    if prev_close <= 1000:
        return 7.50
    return 6.25


def main() -> int:
    path = EXTENDED if os.path.exists(EXTENDED) else BASE
    d = pd.read_parquet(path).sort_values(["symbol", "ts"])
    print(f"source: {os.path.relpath(path, ROOT)}")
    print(f"{len(d):,} bars, {d['symbol'].nunique()} symbols, "
          f"{d['ts'].min().date()} -> {d['ts'].max().date()}\n")

    d["prev_close"] = d.groupby("symbol")["close"].shift(1)
    d = d.dropna(subset=["prev_close"])
    d = d[d["prev_close"] > 0].copy()
    d["limit"] = d["prev_close"].map(band_pct)
    d["ret_pct"] = (d["close"] / d["prev_close"] - 1) * 100
    d["hi_pct"] = (d["high"] / d["prev_close"] - 1) * 100

    naive = d["ret_pct"] >= d["limit"] - TOL
    at_band = naive & (d["ret_pct"] <= d["limit"] + TOL)
    beyond = naive & ~at_band
    touched = (d["hi_pct"] >= d["limit"] - TOL) & (d["hi_pct"] <= d["limit"] + TOL)

    n = len(d)
    print(f"bars with a previous close: {n:,}\n")
    print(f"  high touched the band (at-band) : {touched.sum():>7,}  {touched.mean() * 100:.3f} %")
    print(f"  CLOSED at the band (at-band)    : {at_band.sum():>7,}  {at_band.mean() * 100:.3f} %")
    print(f"  naive I-008 proxy (unbounded)   : {naive.sum():>7,}  {naive.mean() * 100:.3f} %")
    print(f"  beyond band, corp-action suspect: {beyond.sum():>7,}  "
          f"= {beyond.sum() / max(int(naive.sum()), 1) * 100:.1f} % of the naive count\n")

    print(f"symbols that ever closed at the band: {d[at_band]['symbol'].nunique()} "
          f"of {d['symbol'].nunique()}\n")
    print("at-band locks per year:")
    print(d[at_band].groupby(d[at_band]["ts"].dt.year).size().to_string())

    last = d[d["ts"] >= d["ts"].max() - pd.Timedelta(days=365)]
    lb = at_band[last.index]
    print(f"\nlast 12 months: {len(last):,} bars, {lb.sum():,} locks "
          f"({lb.mean() * 100:.3f} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
