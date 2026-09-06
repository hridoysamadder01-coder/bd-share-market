"""Exercise the tape reconstruction and the price-response labels on REAL,
previously captured dynamic DSE data (Muntasib-creator/DSE_dataset trade-minute
files, 2015-10 → 2024-01) — the only historical dynamic DSE data found in
either repository or on this machine.

This is a validation of the tape path on real data, not the main experiment:
the minute files carry no book, so every book-dependent component of the
composite is NOT_OBSERVABLE here and nothing about the hypothesis is tested.

    python3 experiments/seeing_minute_tape_validation.py --input /home/user/data_ext/dse_minute \
        --symbols BRACBANK,GP,BXPHARMA,BEXIMCO,SQURPHARMA,LHBL --year 2023

Writes results/seeing/MINUTE_TAPE_VALIDATION.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from seeing.capture.adapters.minute_dataset import MinuteDatasetAdapter  # noqa: E402
from seeing.clock import session_phase  # noqa: E402
from seeing.truth import truth_summary  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--symbols", default="BRACBANK,GP,BXPHARMA,BEXIMCO,SQURPHARMA,LHBL")
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                  "results", "seeing", "MINUTE_TAPE_VALIDATION.json"))
    a = ap.parse_args()
    ad = MinuteDatasetAdapter()
    out = {"source": "Muntasib-creator/DSE_dataset (MIT) trade-minute files", "year": a.year, "symbols": {},
           "note": ("phase labels use the session table in force in 2026 (10:00-14:00 / 14:00-14:10, dsebd.org/hts.php); "
                    "older years traded to 14:30, so late prints appear as CLOSED here — a dated session journal (D-16) "
                    "is the fix, not a relabelling"),
           "truth": truth_summary(ad.parse(b"timestamp,closing,opening,high,low,volume\n", "X").truth)}
    for sym in a.symbols.split(","):
        path = os.path.join(a.input, "minute_price_unadjusted", f"{sym}.csv")
        if not os.path.exists(path):
            out["symbols"][sym] = {"missing": True}
            continue
        with open(path, "rb") as fh:
            p = ad.parse(fh.read(), sym)
        df = pd.DataFrame(p.frames)
        df["t"] = pd.to_datetime(df["t_source_utc"], utc=True)
        df = df[df["t"].dt.year == a.year].sort_values("t")
        if not len(df):
            out["symbols"][sym] = {"rows_in_year": 0}
            continue
        df["date"] = (df["t"] + pd.Timedelta(hours=6)).dt.date
        df["phase"] = [session_phase(t.to_pydatetime()) for t in df["t"]]
        per_day = df.groupby("date").agg(prints=("close", "size"), volume=("minute_volume", "sum"),
                                         first=("t", "min"), last=("t", "max"))
        df["dt_s"] = df.groupby("date")["t"].diff().dt.total_seconds()
        # price response after a minute with abnormally large volume (descriptive, not a test)
        df["vol_z"] = (df["minute_volume"] - df.groupby("date")["minute_volume"].transform("median")) / \
            (df.groupby("date")["minute_volume"].transform("std").replace(0, np.nan))
        df["fwd_close_4"] = df.groupby("date")["close"].shift(-4)
        df["fwd_ret_4"] = (df["fwd_close_4"] - df["close"]) / df["close"]
        big = df[df["vol_z"] > 3]
        out["symbols"][sym] = {
            "rows_in_year": int(len(df)), "trading_days": int(per_day.shape[0]),
            "prints_per_day_median": float(per_day["prints"].median()),
            "median_gap_between_prints_s": float(df["dt_s"].median()),
            "share_multi_price_minutes": float((df["high"] > df["low"]).mean()),
            "phase_share": df["phase"].value_counts(normalize=True).round(4).to_dict(),
            "fwd4_after_volume_spike_mean_bp": float(big["fwd_ret_4"].mean() * 1e4) if len(big) else None,
            "fwd4_unconditional_mean_bp": float(df["fwd_ret_4"].mean() * 1e4),
            "n_volume_spikes": int(len(big)), "parse_problems": len(p.problems),
        }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out, indent=1, default=str)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
