#!/usr/bin/env python3
"""Generate a SYNTHETIC DSE-shaped minute dataset for MACHINERY VALIDATION ONLY.

This file exists so that the QA detectors and the leakage test can be proven to
work before real data arrives. It plants known defects at known rows; a QA run
that does not find them is a broken QA run.

⚠ NOTHING generated here is evidence about the Bangladeshi market. No research
conclusion may cite this data. It is a test fixture, and every artifact produced
from it is written under a `synthetic_` prefix.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION_START, BARS_PER_DAY = "10:00", 270
TRADING_WEEKDAYS = (6, 0, 1, 2, 3)  # Sun..Thu


def session_grid(start_date: datetime, n_days: int) -> list[pd.Timestamp]:
    days, d = [], start_date
    while len(days) < n_days:
        if d.weekday() in TRADING_WEEKDAYS:
            days.append(d)
        d += timedelta(days=1)
    stamps = []
    h, m = (int(x) for x in SESSION_START.split(":"))
    for day in days:
        base = day.replace(hour=h, minute=m, second=0, microsecond=0)
        stamps += [pd.Timestamp(base + timedelta(minutes=i)) for i in range(BARS_PER_DAY)]
    return stamps


def generate(n_symbols: int = 10, n_days: int = 25, seed: int = 20260901) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    stamps = session_grid(datetime(2026, 1, 4), n_days)
    rows, planted = [], {}

    for si in range(n_symbols):
        sym = f"SYN{si:02d}"
        n = len(stamps)
        # Regime-switching volatility so the feature layer has something to see.
        regime = rng.integers(0, 2, size=n // 60 + 1).repeat(60)[:n]
        sigma = np.where(regime == 1, 0.0018, 0.0006)
        ret = rng.normal(0, sigma)
        price = 100.0 * np.exp(np.cumsum(ret))
        base_vol = rng.lognormal(mean=7.0, sigma=0.6, size=n)
        # Occasional genuine activity bursts (no forward information involved).
        for _ in range(6):
            j = int(rng.integers(400, n - 400))
            base_vol[j:j + 30] *= rng.uniform(4, 9)
        vol = np.maximum(np.round(base_vol), 0)

        intrabar = np.abs(rng.normal(0, sigma * 1.5))
        close = price
        open_ = np.concatenate([[price[0]], price[:-1]])
        high = np.maximum(open_, close) * (1 + intrabar)
        low = np.minimum(open_, close) * (1 - intrabar)

        g = pd.DataFrame({"symbol": sym, "ts": stamps, "open": open_, "high": high,
                          "low": low, "close": close, "volume": vol})
        g["turnover"] = g["close"] * g["volume"]
        rows.append(g)

    df = pd.concat(rows, ignore_index=True)

    # ---------------- planted defects (exact row targets recorded) ----------
    def rows_of(sym, lo, hi):
        idx = df.index[(df["symbol"] == sym)]
        return list(idx[lo:hi])

    r = rows_of("SYN00", 500, 505)                       # bad OHLC: high below close
    df.loc[r, "high"] = df.loc[r, "close"] * 0.98
    planted["OHLC_INCONSISTENT"] = len(r)

    r = rows_of("SYN01", 700, 703)                       # NaN field
    df.loc[r, "close"] = np.nan
    planted["NAN_FIELD"] = len(r)

    r = rows_of("SYN02", 900, 902)                       # non-positive price
    df.loc[r, "low"] = -1.0
    planted["NONPOS_PRICE"] = len(r)

    dup = df.loc[rows_of("SYN03", 1000, 1004)].copy()     # duplicate (symbol, ts)
    planted["DUP_BAR"] = 2 * len(dup)                     # both copies are flagged
    df = pd.concat([df, dup], ignore_index=True)

    r = rows_of("SYN04", 1100, 1140)                      # zero-volume run
    df.loc[r, ["volume", "turnover"]] = 0.0
    planted["ZERO_VOLUME"] = len(r)

    r = rows_of("SYN05", 1200, 1215)                      # locked / one-price bars
    px = float(df.loc[r[0], "close"])
    df.loc[r, ["open", "high", "low", "close"]] = px
    planted["LOCKED_BAR"] = len(r)

    r = rows_of("SYN06", 1300, 1345)                      # stale close run
    stale_px = float(df.loc[r[0], "close"])
    df.loc[r, "close"] = stale_px
    # Keep the bars internally VALID: a stale close is a market state, not a data
    # error, so high/low must still bracket it or we would plant a second defect
    # by accident and corrupt the planted-vs-detected accounting.
    df.loc[r, "high"] = df.loc[r, ["high", "open", "close"]].max(axis=1)
    df.loc[r, "low"] = df.loc[r, ["low", "open", "close"]].min(axis=1)
    planted["STALE_RUN_rows"] = len(r)

    # corporate-action-like overnight gap: scale one symbol's prices from a day on
    sym7 = df["symbol"] == "SYN07"
    day_cut = sorted(df.loc[sym7, "ts"].dt.date.unique())[10]
    m = sym7 & (df["ts"].dt.date >= day_cut)
    df.loc[m, ["open", "high", "low", "close"]] *= 0.5    # 1:2 split, unadjusted
    planted["LARGE_OVERNIGHT_GAP"] = 1

    # out-of-session timestamps
    r = rows_of("SYN08", 1500, 1503)
    df.loc[r, "ts"] = df.loc[r, "ts"] + pd.Timedelta(hours=6)
    planted["OUT_OF_SESSION"] = len(r)

    # late listing (survivorship) — SYN09 only exists from day 6 onwards
    keep_from = sorted(df["ts"].dt.date.unique())[6]
    drop = df.index[(df["symbol"] == "SYN09") & (df["ts"].dt.date < keep_from)]
    planted["late_listing_rows_removed"] = len(drop)
    df = df.drop(index=drop)

    # thin day: delete 80% of one symbol's bars on one day
    thin_day = sorted(df["ts"].dt.date.unique())[12]
    cand = df.index[(df["symbol"] == "SYN02") & (df["ts"].dt.date == thin_day)]
    drop = list(cand[: int(len(cand) * 0.8)])
    planted["thin_day_rows_removed"] = len(drop)
    df = df.drop(index=drop)

    df = df.sort_values(["symbol", "ts"], kind="mergesort").reset_index(drop=True)
    return df, planted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=10)
    ap.add_argument("--days", type=int, default=25)
    ap.add_argument("--seed", type=int, default=20260901)
    a = ap.parse_args()

    df, planted = generate(a.symbols, a.days, a.seed)
    out_dir = os.path.join(HERE, "synthetic")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "synthetic_minute_bars.parquet")
    df.to_parquet(path, index=False)
    with open(os.path.join(out_dir, "PLANTED_DEFECTS.json"), "w") as fh:
        json.dump({"seed": a.seed, "rows": len(df), "symbols": a.symbols,
                   "days": a.days, "planted": planted}, fh, indent=2)
    print(f"wrote {path}  rows={len(df):,}  symbols={df['symbol'].nunique()}")
    print("planted defects:", json.dumps(planted, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
