#!/usr/bin/env python3
"""NO-LOOKAHEAD PROOF for the Phase-2 feature layer.

The claim to be proved: a feature value at bar t depends only on bars <= t.

Method — future perturbation. Compute features on the real frame. Then destroy
everything after a cut bar k (shuffle it, scale it, replace it with noise) and
recompute. If any feature value at a bar <= k changes, that feature read the
future. Repeated over many random cuts and symbols.

A test that can only pass is worthless, so the run also includes a POSITIVE
CONTROL: a deliberately leaky column (`close.shift(-1)`). The suite fails if the
detector does NOT catch it — that is what makes the passes meaningful.

  python3 bd_research/features/leakage_test.py [--cuts 12] [--seed 7]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bdlib import config as C  # noqa: E402

from bdlib import features as F  # noqa: E402
from bdlib import io as bio  # noqa: E402
from bdlib import labels as L  # noqa: E402

TOL = 1e-9


def corrupt_future(df: pd.DataFrame, cut_ts: pd.Timestamp, rng) -> pd.DataFrame:
    """Replace every bar strictly after cut_ts with garbage, for every symbol."""
    d = df.copy()
    m = d["ts"] > cut_ts
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


def compare_prefix(a: pd.DataFrame, b: pd.DataFrame, cut_ts, cols) -> list[str]:
    """Feature columns whose values differ at any bar <= cut_ts."""
    key = ["symbol", "ts"]
    pa = a.loc[a["ts"] <= cut_ts, key + cols].set_index(key).sort_index()
    pb = b.loc[b["ts"] <= cut_ts, key + cols].set_index(key).sort_index()
    common = pa.index.intersection(pb.index)
    pa, pb = pa.loc[common], pb.loc[common]
    bad = []
    for c in cols:
        x, y = pa[c].to_numpy(dtype=float), pb[c].to_numpy(dtype=float)
        both_nan = np.isnan(x) & np.isnan(y)
        diff = np.abs(np.where(both_nan, 0.0, np.nan_to_num(x - y, nan=np.inf)))
        if np.nanmax(diff, initial=0.0) > TOL:
            bad.append(c)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "synthetic",
        "synthetic_minute_bars.parquet"))
    ap.add_argument("--cuts", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--frequency", choices=["DAILY", "MINUTE"], default="MINUTE")
    ap.add_argument("--symbols", type=int, default=0,
                    help="test on a random subsample of N symbols (0 = all). "
                         "Causality is a per-symbol + same-timestamp property, so a "
                         "subsample is a valid proof; the sample is reported.")
    a = ap.parse_args()

    C.BAR_FREQUENCY = a.frequency
    rng = np.random.default_rng(a.seed)
    raw = bio.load_bars(a.input)
    if a.symbols:
        syms = np.sort(raw["symbol"].unique())
        pick = rng.choice(syms, size=min(a.symbols, len(syms)), replace=False)
        raw = raw[raw["symbol"].isin(pick)].reset_index(drop=True)
        print(f"subsample: {len(pick)} of {len(syms)} symbols, {len(raw):,} bars — "
              f"{', '.join(sorted(pick)[:8])}...")
    from bdlib import qa as Q
    annotated, _, _ = Q.audit(raw, C.DEFAULT)

    base = F.build(annotated, C.DEFAULT)
    cols = [c for c in F.FEATURE_COLUMNS if c in base.columns]
    ts_pool = np.sort(base["ts"].dropna().unique())
    lo, hi = int(len(ts_pool) * 0.3), int(len(ts_pool) * 0.9)

    print(f"features under test: {len(cols)}")
    print(f"{'cut timestamp':<24}{'bars checked':>14}   result")
    print("-" * 62)

    failures = {}
    for i in range(a.cuts):
        cut_ts = pd.Timestamp(ts_pool[int(rng.integers(lo, hi))])
        pert = F.build(*(lambda d: (Q.audit(d, C.DEFAULT)[0], C.DEFAULT))(
            corrupt_future(raw, cut_ts, rng)))
        bad = compare_prefix(base, pert, cut_ts, cols)
        n = int((base["ts"] <= cut_ts).sum())
        print(f"{str(cut_ts):<24}{n:>14,}   {'clean' if not bad else 'LEAK ' + str(bad)}")
        for c in bad:
            failures.setdefault(c, 0)
            failures[c] += 1

    # ---- positive control: a column that DOES look ahead must be caught -----
    print("-" * 62)
    cut_ts = pd.Timestamp(ts_pool[int(len(ts_pool) * 0.6)])

    def with_leak(d):
        ann, _, _ = Q.audit(d, C.DEFAULT)
        out = F.build(ann, C.DEFAULT)
        out["__leaky_control"] = out.groupby("symbol")["close"].shift(-1)
        return out

    ctrl_base, ctrl_pert = with_leak(raw), with_leak(corrupt_future(raw, cut_ts, rng))
    caught = compare_prefix(ctrl_base, ctrl_pert, cut_ts, ["__leaky_control"])
    print(f"positive control (close.shift(-1)): "
          f"{'CAUGHT — detector works' if caught else 'NOT CAUGHT — TEST IS VACUOUS'}")

    # ---- namespace separation ---------------------------------------------
    label_cols = L.label_columns(C.DEFAULT)
    overlap = set(cols) & set(label_cols)
    fwd_named = [c for c in cols if c.startswith("fwd_")]
    print(f"feature/label overlap: {overlap or 'none'} · features named fwd_*: "
          f"{fwd_named or 'none'}")

    print("-" * 62)
    ok = (not failures) and bool(caught) and not overlap and not fwd_named
    if ok:
        print(f"PASS — {len(cols)} features unchanged under {a.cuts} future-corruption "
              f"cuts; leaky control caught; namespaces disjoint.")
        return 0
    print(f"FAIL — leaking features: {failures}; control caught: {bool(caught)}; "
          f"overlap: {overlap}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
