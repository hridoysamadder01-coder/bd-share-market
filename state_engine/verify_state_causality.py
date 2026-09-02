#!/usr/bin/env python3
"""NO-LOOKAHEAD PROOF for the Phase-3 state engine.

STATE_ENGINE_DESIGN.md requires that the leakage test extend to state labels:
corrupting the future must not change any earlier state. Same method as the
feature proof — destroy every bar after a cut date, recompute, require the
prefix to be identical — applied to `novelty`, `novelty_pct`, `state`,
`state_age` and `elevated_run`, plus a positive control that must be caught.

  python3 state_engine/verify_state_causality.py [--symbols 25]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from bdlib import config as C  # noqa: E402
from bdlib import features as F  # noqa: E402
from bdlib import io as bio  # noqa: E402
from bdlib import panels as P  # noqa: E402
from bdlib import qa as Q  # noqa: E402
from run_states import build_states, cross_sectional  # noqa: E402

CHECK = ["novelty", "novelty_pct", "state_age", "elevated_run"]
TOL = 1e-9


def pipeline(raw: pd.DataFrame, panel: str) -> pd.DataFrame:
    ann, _, _ = Q.audit(raw, C.DEFAULT)
    feat = F.build(ann, C.DEFAULT)
    d = P.select(feat, panel)
    return cross_sectional(build_states(d), panel)


def corrupt_future(df: pd.DataFrame, cut: pd.Timestamp, rng) -> pd.DataFrame:
    d = df.copy()
    m = d["ts"] > cut
    n = int(m.sum())
    if n == 0:
        return d
    d.loc[m, "close"] = d.loc[m, "close"].to_numpy() * rng.uniform(0.2, 5.0, n)
    d.loc[m, "open"] = d.loc[m, "close"].to_numpy() * rng.uniform(0.9, 1.1, n)
    d.loc[m, "high"] = d.loc[m, ["open", "close"]].max(axis=1) * 1.05
    d.loc[m, "low"] = d.loc[m, ["open", "close"]].min(axis=1) * 0.95
    d.loc[m, "volume"] = rng.integers(0, 10_000_000, n).astype(float)
    d.loc[m, "turnover"] = d.loc[m, "close"].to_numpy() * d.loc[m, "volume"].to_numpy()
    return d


def diff_prefix(a: pd.DataFrame, b: pd.DataFrame, cut) -> list[str]:
    key = ["symbol", "ts"]
    pa = a[a["ts"] <= cut].set_index(key).sort_index()
    pb = b[b["ts"] <= cut].set_index(key).sort_index()
    common = pa.index.intersection(pb.index)
    pa, pb = pa.loc[common], pb.loc[common]
    bad = []
    for c in CHECK:
        x, y = pa[c].to_numpy(dtype=float), pb[c].to_numpy(dtype=float)
        both_nan = np.isnan(x) & np.isnan(y)
        d = np.abs(np.where(both_nan, 0.0, np.nan_to_num(x - y, nan=np.inf)))
        if np.nanmax(d, initial=0.0) > TOL:
            bad.append(c)
    sa = pa["state"].fillna("NA").to_numpy()
    sb = pb["state"].fillna("NA").to_numpy()
    if not (sa == sb).all():
        bad.append("state")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(HERE, "..", "data", "raw",
                                                    "dse_eod.parquet"))
    ap.add_argument("--symbols", type=int, default=25)
    ap.add_argument("--cuts", type=int, default=4)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--panel", default="PRIMARY")
    a = ap.parse_args()

    C.BAR_FREQUENCY = "DAILY"
    rng = np.random.default_rng(a.seed)
    raw = bio.load_bars(a.input)
    syms = np.sort(raw["symbol"].unique())
    pick = rng.choice(syms, size=min(a.symbols, len(syms)), replace=False)
    raw = raw[raw["symbol"].isin(pick)].reset_index(drop=True)
    print(f"panel {a.panel} · subsample {len(pick)} symbols · {len(raw):,} bars")

    base = pipeline(raw, a.panel)
    pool = np.sort(base["ts"].dropna().unique())
    lo, hi = int(len(pool) * 0.35), int(len(pool) * 0.9)

    print(f"{'cut date':<14}{'rows checked':>14}   result")
    print("-" * 52)
    failures = {}
    for _ in range(a.cuts):
        cut = pd.Timestamp(pool[int(rng.integers(lo, hi))])
        pert = pipeline(corrupt_future(raw, cut, rng), a.panel)
        bad = diff_prefix(base, pert, cut)
        n = int((base["ts"] <= cut).sum())
        print(f"{str(cut.date()):<14}{n:>14,}   {'clean' if not bad else 'LEAK ' + str(bad)}")
        for c in bad:
            failures[c] = failures.get(c, 0) + 1

    # Positive control: a state column that DOES look ahead must be caught.
    print("-" * 52)
    cut = pd.Timestamp(pool[int(len(pool) * 0.6)])

    def leaky(d):
        out = pipeline(d, a.panel)
        out["novelty"] = out.groupby("symbol")["novelty"].shift(-1)
        return out

    caught = diff_prefix(leaky(raw), leaky(corrupt_future(raw, cut, rng)), cut)
    print(f"positive control (novelty.shift(-1)): "
          f"{'CAUGHT — detector works' if caught else 'NOT CAUGHT — TEST IS VACUOUS'}")
    print("-" * 52)

    if failures or not caught:
        print(f"FAIL — leaking: {failures}; control caught: {bool(caught)}")
        return 1
    print(f"PASS — states unchanged under {a.cuts} future-corruption cuts "
          f"({', '.join(CHECK)}, state); leaky control caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
