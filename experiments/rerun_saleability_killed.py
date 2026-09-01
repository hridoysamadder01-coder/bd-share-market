#!/usr/bin/env python3
"""RE-RUN of the one candidate Round 2 closed SOLELY on an assumed T+2 sell block.

Why this exists
---------------
Round 2 measured a panic-regime cross-sectional mean-reversion candidate at
+1.05% net (t = 1.69) for a same-session exit, then closed it because it assumed
"earliest legal sale = entry + 2 sessions". Settlement and broker-level
saleability are different mechanics, and the second is UNKNOWN until confirmed
against real LankaBangla / DSE account behaviour. A candidate must not stay dead
on an unverified assumption, so it is re-measured here with:

  · every exit horizon reported, including the same-session round trip, each
    labelled with the saleability question it depends on — NOT pre-filtered;
  · verified 0.8% brokerage separated from estimated additional costs;
  · results split by coverage panel (never pooled across 2024-02-22) and by
    floor regime.

This re-opens a MEASUREMENT, not a conclusion. Nothing here is a signal.

  python3 bd_research/experiments/rerun_saleability_killed.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bdlib import config as C  # noqa: E402
from bdlib import costs as K  # noqa: E402
from bdlib import io as bio  # noqa: E402
from bdlib import panels as P  # noqa: E402

# Definition re-implemented from prior_rounds/round2.py §S4.
LOOKBACK = 5          # sessions over which the oversold move is measured
OVERSOLD = -0.15      # 5-session return at or below this
BREADTH = 0.05        # share of the liquid universe oversold ⇒ a regime event
COOLDOWN = 10         # sessions before another event can fire
LIQUID_TOP = 0.40     # universe = most liquid 40% by trailing median turnover
LIQ_WINDOW = 60
GAP_GUARD = -0.15     # 1-day drop beyond this = ex-date suspect, stock excluded
# Hold measured in SESSIONS AFTER ENTRY. 0 = buy at the open, sell at that same
# session's close. Whether a broker permits that is exactly the open question.
HOLDS = (0, 1, 2, 4)

HOLD_NOTE = {
    0: "same-session round trip — needs intraday netting / same-day sell",
    1: "sell at the next session's close",
    2: "sell two sessions after entry",
    4: "sell four sessions after entry",
}


def build_matrices(bars: pd.DataFrame) -> dict:
    piv = {f: bars.pivot_table(index="ts", columns="symbol", values=f, aggfunc="last")
           for f in ("open", "high", "low", "close", "turnover")}
    locked = (bars.assign(_l=bars["flag_locked_bar"].astype(float))
              .pivot_table(index="ts", columns="symbol", values="_l", aggfunc="last"))
    piv["locked"] = locked.fillna(1.0) > 0
    return piv


def find_events(m: dict) -> pd.DataFrame:
    close = m["close"]
    # Liquidity universe: trailing median turnover, strictly past-only.
    liq = close.notna() & (m["turnover"].shift(1).rolling(LIQ_WINDOW, min_periods=20)
                           .median().rank(axis=1, pct=True) >= (1 - LIQUID_TOP))
    ret5 = np.log(close / close.shift(LOOKBACK))
    oversold = liq & (ret5 <= OVERSOLD)
    breadth = oversold.sum(axis=1) / liq.sum(axis=1).replace(0, np.nan)

    raw = breadth[breadth >= BREADTH].index
    fa, fb = pd.Timestamp(C.FLOOR_ERA[0]), pd.Timestamp(C.FLOOR_ERA[1])
    kept, last = [], None
    for d in raw:                                   # first crossing + cooldown
        if fa <= d <= fb:
            continue                                # floor era excluded, as in Round 2
        if last is not None and (close.index.get_loc(d) - close.index.get_loc(last)) < COOLDOWN:
            continue
        kept.append(d)
        last = d
    return pd.DataFrame({"event_day": kept,
                         "breadth": [float(breadth.loc[d]) for d in kept],
                         "oversold_names": [int(oversold.loc[d].sum()) for d in kept]})


def event_returns(m: dict, events: pd.DataFrame) -> pd.DataFrame:
    """Equal-weight cohort return per event per hold. Unit of analysis = the event."""
    close, open_ = m["close"], m["open"]
    ret5 = np.log(close / close.shift(LOOKBACK))
    ret1 = np.log(close / close.shift(1))
    idx = close.index
    rows = []
    for _, ev in events.iterrows():
        d = ev["event_day"]
        i = idx.get_loc(d)
        if i + 1 >= len(idx):
            continue
        entry_day = idx[i + 1]                      # entry at the NEXT session's open
        cohort = ret5.loc[d]
        cohort = cohort[cohort <= OVERSOLD].index
        # Tradeable at entry: an open exists and the stock is not locked.
        ok = [s for s in cohort
              if np.isfinite(open_.at[entry_day, s]) and not m["locked"].at[entry_day, s]
              and not (ret1.at[d, s] <= GAP_GUARD)]   # ex-date suspect guard
        if len(ok) < 5:
            continue
        entry_px = open_.loc[entry_day, ok]
        # Round 2 reported a Q1-Q5 spread, so the cohort is also split by HOW
        # oversold each name is: Q1 = most oversold fifth, Q5 = least.
        depth = ret5.loc[d, ok].sort_values()
        q = max(len(ok) // 5, 1)
        q1, q5 = list(depth.index[:q]), list(depth.index[-q:])
        row = {"event_day": d, "entry_day": entry_day, "names": len(ok),
               "breadth": ev["breadth"],
               "regime": "PRE_FLOOR" if d < pd.Timestamp(C.FLOOR_ERA[0]) else "POST_FLOOR"}
        for h in HOLDS:
            j = i + 1 + h
            if j >= len(idx):
                for tag in ("all", "q1", "q5", "spread"):
                    row[f"{tag}_h{h}"] = np.nan
                continue
            exit_px = close.loc[idx[j], ok]
            r = np.log(exit_px / entry_px)
            row[f"all_h{h}"] = float(r.mean(skipna=True))
            row[f"q1_h{h}"] = float(r[q1].mean(skipna=True))
            row[f"q5_h{h}"] = float(r[q5].mean(skipna=True))
            row[f"spread_h{h}"] = row[f"q1_h{h}"] - row[f"q5_h{h}"]
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "results",
        "dse_eod_bars_annotated.parquet"))
    a = ap.parse_args()

    bars = pd.read_parquet(a.input)
    bars = bars[~bars["qa_exclude"]].copy()

    print("=" * 78)
    print("RE-RUN — candidate closed by Round 2 on an ASSUMED T+2 sell block")
    print("=" * 78)
    print(f"saleability: EARLIEST_SALEABILITY_DAYS = {C.EARLIEST_SALEABILITY_DAYS} "
          f"(UNKNOWN; SALEABILITY_VERIFIED = {C.SALEABILITY_VERIFIED})")
    print("  ⇒ no exit horizon is excluded a priori; each is labelled instead.")
    print("costs:")
    print(K.header())
    print()
    print(P.summary(bars).to_string(index=False))

    out_rows, per_panel = [], {}
    for panel in ("PRIMARY", "POSTBREAK"):
        d = P.select(bars, panel)
        P.assert_single_panel(d, f"panel {panel}")
        m = build_matrices(d)
        events = find_events(m)
        er = event_returns(m, events) if len(events) else pd.DataFrame()
        per_panel[panel] = {"events_detected": int(len(events)),
                            "events_usable": int(len(er))}
        print("\n" + "-" * 78)
        print(f"PANEL {panel}   {C.PANEL_PRIMARY if panel == 'PRIMARY' else C.PANEL_POSTBREAK}")
        print(f"  regime events detected: {len(events)} · usable (≥5 tradeable names): {len(er)}")
        if len(er) == 0:
            print("  no usable events — nothing to measure, and nothing is inferred "
                  "from an absence of events.")
            continue
        print(f"  median names per event: {int(er['names'].median())}")
        for regime in ("PRE_FLOOR", "POST_FLOOR"):
            sub = er[er["regime"] == regime]
            if sub.empty:
                continue
            print(f"\n  ── {regime}: {len(sub)} events "
                  f"({sub['event_day'].min().date()} → {sub['event_day'].max().date()})")
            table = []
            for tag, tname in (("all", "whole oversold cohort"),
                               ("q1", "Q1 = most oversold fifth"),
                               ("q5", "Q5 = least oversold fifth"),
                               ("spread", "Q1−Q5 spread [NOT long-only capturable]")):
                for h in HOLDS:
                    lay = K.layered(sub[f"{tag}_h{h}"])
                    # A long/short spread pays brokerage on both legs and cannot
                    # be traded here at all; showing a "net" for it would imply
                    # it is executable. Gross only, flagged.
                    row = lay.as_row(f"{tname} · hold {h}")
                    row.update(panel=panel, regime=regime, cohort_kind=tag, hold=h,
                               saleability_note=HOLD_NOTE[h])
                    if tag == "spread":
                        for k in list(row):
                            if k.startswith("net_"):
                                row[k] = np.nan
                    table.append(row)
                    out_rows.append(row)
            t = pd.DataFrame(table)
            show = ["cohort", "n", "gross", "t_gross", "net_verified_0.8%",
                    "t_net_verified", "hit_net_verified"] + \
                   [c for c in t.columns if c.startswith("net_est_")]
            with pd.option_context("display.float_format", lambda v: f"{v:+.4f}"):
                print(t[show].to_string(index=False))

    res_dir = bio.paths()["results"]
    if out_rows:
        pd.DataFrame(out_rows).to_csv(
            os.path.join(res_dir, "rerun_saleability_killed.csv"), index=False)
    bio.write_manifest("rerun_saleability_killed_manifest.json", {
        "phase": "re-measurement of a candidate closed on an unverified assumption",
        "definition": {"lookback": LOOKBACK, "oversold": OVERSOLD, "breadth": BREADTH,
                       "cooldown": COOLDOWN, "liquid_top": LIQUID_TOP,
                       "gap_guard": GAP_GUARD, "holds": list(HOLDS),
                       "floor_era_excluded": True,
                       "entry": "next session open", "unit_of_analysis": "event day"},
        "saleability": {"earliest_saleability_days": C.EARLIEST_SALEABILITY_DAYS,
                        "verified": C.SALEABILITY_VERIFIED,
                        "settlement_cycle_days": C.SETTLEMENT_CYCLE_DAYS},
        "costs": {"brokerage_round_trip_verified": C.BROKERAGE_ROUND_TRIP_VERIFIED,
                  "estimated_additional": list(C.ESTIMATED_ADDITIONAL_COSTS),
                  "not_modelled": ["capital_gains_tax", "slippage", "impact"]},
        "panels": per_panel,
    })
    print("\n" + "=" * 78)
    print("This is a MEASUREMENT. No promotion decision is made here; the promotion")
    print("gate needs saleability verified, both panels, and the estimate band.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
