#!/usr/bin/env python3
"""PHASE 3 — market state formation engine (rungs 1 and 2 of the designed ladder).

Emits OBSERVATIONS, never instructions. There is no BUY/SELL anywhere in this
file, and no outcome label is read while a state is being defined.

Ladder position (see STATE_ENGINE_DESIGN.md):
  rung 1  univariate departure  — explainable baseline every later rung must beat
  rung 2  multivariate novelty  — how far the whole feature vector sits from this
          symbol's own recent cloud, as RMS of its already-per-symbol-normalised
          z components (a diagonal Mahalanobis: independence assumed, deliberately,
          because a full rolling covariance is rung 2b and must earn its cost)

Everything is causal: the components are the Phase-2 trailing-baseline z-scores,
and the novelty percentile is taken against the symbol's OWN trailing novelty
history — never the full sample.

Panels are never pooled. The 2024-02-22 coverage break changes what a
cross-sectional number means, so each panel is fitted and written separately.

  python3 bd_research/state_engine/run_states.py --tag dse_eod
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bdlib import config as C  # noqa: E402
from bdlib import io as bio  # noqa: E402
from bdlib import panels as P  # noqa: E402

# The rung-2 vector: only components already normalised per symbol against their
# own trailing baseline, so RMS is meaningful without further scaling.
NOVELTY_COMPONENTS = ["rel_volume_z", "rel_turnover_z", "range_z", "amihud_z"]
MIN_COMPONENTS = 3            # fewer than this ⇒ novelty undefined, not imputed
NOVELTY_WINDOW = 250          # trailing window for the symbol's own novelty history
STATE_EDGES = (0.50, 0.90, 0.99)   # percentile cuts → CALM / DRIFT / DEPARTURE / EXTREME
STATE_NAMES = ("CALM", "DRIFT", "DEPARTURE", "EXTREME")


def _trailing_pct_rank(s: pd.Series, w: int) -> pd.Series:
    """Percentile of x_t within the STRICTLY TRAILING window [t-w, t-1]."""
    def rank_last(a: np.ndarray) -> float:
        cur, past = a[-1], a[:-1]
        past = past[np.isfinite(past)]
        if not np.isfinite(cur) or len(past) < 30:
            return np.nan
        return float((past < cur).mean())
    return s.rolling(w + 1, min_periods=31).apply(rank_last, raw=True)


def _run_length_true(mask: pd.Series) -> pd.Series:
    out = np.zeros(len(mask), dtype=int)
    run = 0
    for i, v in enumerate(mask.to_numpy()):
        run = run + 1 if v else 0
        out[i] = run
    return pd.Series(out, index=mask.index)


def build_states(feat: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, g in feat.groupby("symbol", sort=False):
        g = g.sort_values("ts", kind="mergesort")
        comp = g[NOVELTY_COMPONENTS]
        n_ok = comp.notna().sum(axis=1)
        # RUNG 2: root-mean-square of the available z components.
        novelty = np.sqrt((comp ** 2).mean(axis=1, skipna=True)).where(n_ok >= MIN_COMPONENTS)

        out = pd.DataFrame(index=g.index)
        out["symbol"] = g["symbol"].values
        out["ts"] = g["ts"].values
        out["novelty"] = novelty
        out["novelty_components"] = n_ok
        out["novelty_pct"] = _trailing_pct_rank(novelty, NOVELTY_WINDOW)

        bucket = pd.cut(out["novelty_pct"], bins=[-0.001, *STATE_EDGES, 1.001],
                        labels=list(STATE_NAMES))
        out["state"] = bucket.astype(object)

        # RUNG 1: the explainable univariate baseline every later rung must beat.
        out["rung1_volume_departure"] = (g["rel_volume_z"] > 2).fillna(False).values
        out["rung1_range_compression"] = (g["range_compression"] > 2).fillna(False).values
        out["rung1_quiet_accumulation"] = (
            (g["volume_price_divergence"] > 2).fillna(False).values)

        # Formation, not a snapshot: how long the current state has held, and
        # whether this bar is the moment it changed.
        st = out["state"].fillna("UNKNOWN")
        out["is_transition"] = st.ne(st.shift()).fillna(False) & st.shift().notna()
        out["state_age"] = _run_length_true(st.eq(st.shift()).fillna(False)).values + 1

        elevated = out["state"].isin(("DEPARTURE", "EXTREME"))
        out["elevated_run"] = _run_length_true(elevated).values

        # Which component drove it — an observation must be explainable.
        # (idxmax raises on all-NaN rows, and all-NaN is the normal case for a
        # symbol with no usable baseline, so this is done explicitly.)
        arr = comp.abs().to_numpy(dtype=float)
        has_any = np.isfinite(arr).any(axis=1)
        filled = np.where(np.isfinite(arr), arr, -np.inf)
        pick = np.full(len(arr), -1, dtype=int)
        if has_any.any():
            pick[has_any] = filled[has_any].argmax(axis=1)
        top = np.where(pick >= 0,
                       np.array(NOVELTY_COMPONENTS + [None], dtype=object)[pick], None)
        out["top_component"] = pd.Series(top, index=comp.index).where(
            n_ok >= MIN_COMPONENTS)
        parts.append(out)
    return pd.concat(parts).sort_values(["symbol", "ts"], kind="mergesort")


def cross_sectional(states: pd.DataFrame, panel: str) -> pd.DataFrame:
    """Same-day, within-panel context. Never computed across the coverage break."""
    P.assert_single_panel(states, f"cross-sectional novelty for panel {panel}")
    by = states.groupby("ts", sort=False)
    states = states.copy()
    states["xs_novelty_rank"] = by["novelty"].rank(pct=True)
    states["xs_share_departure"] = by["state"].transform(
        lambda s: s.isin(("DEPARTURE", "EXTREME")).mean())
    states["xs_symbols"] = by["novelty"].transform("size")
    states["panel"] = panel
    return states


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="dse_eod")
    a = ap.parse_args()
    paths = bio.paths()

    feat = pd.read_parquet(os.path.join(paths["results"], f"{a.tag}_features.parquet"))
    print(f"features in: {len(feat):,} rows · {feat['symbol'].nunique()} symbols")
    print(f"rung-2 novelty vector: {NOVELTY_COMPONENTS} (>= {MIN_COMPONENTS} required)")
    print(f"state buckets by trailing-{NOVELTY_WINDOW} percentile of the symbol's own "
          f"novelty: {list(zip(STATE_NAMES, ('<50%', '50-90%', '90-99%', '>99%')))}")

    all_states, summary = [], []
    for panel in ("PRIMARY", "POSTBREAK"):
        d = P.select(feat, panel)
        if d.empty:
            continue
        st = cross_sectional(build_states(d), panel)
        all_states.append(st)

        counts = st["state"].value_counts(dropna=False)
        row = {"panel": panel, "rows": len(st), "symbols": st["symbol"].nunique(),
               "novelty_defined": int(st["novelty"].notna().sum()),
               "transitions": int(st["is_transition"].sum())}
        for name in STATE_NAMES:
            row[name] = int(counts.get(name, 0))
        row["UNDEFINED"] = int(st["state"].isna().sum())
        row["rung1_volume_departure"] = int(st["rung1_volume_departure"].sum())
        summary.append(row)

        print("\n" + "-" * 76)
        print(f"PANEL {panel}: {len(st):,} rows · {st['symbol'].nunique()} symbols")
        print(f"  novelty defined on {st['novelty'].notna().mean():.1%} of rows "
              f"(undefined = degenerate baseline, propagated as NaN)")
        print("  state distribution: " + ", ".join(
            f"{n}={int(counts.get(n, 0)):,}" for n in STATE_NAMES))
        print(f"  state transitions: {int(st['is_transition'].sum()):,}")
        el = st[st["state"].isin(("DEPARTURE", "EXTREME"))]
        if len(el):
            print(f"  elevated states: {len(el):,} · median elevated run "
                  f"{int(el['elevated_run'].median())} sessions · "
                  f"longest {int(el['elevated_run'].max())}")
            print("  driven by: " + ", ".join(
                f"{k}={v:,}" for k, v in el["top_component"].value_counts().items()))

    log = pd.concat(all_states, ignore_index=True)
    out = os.path.join(paths["results"], "STATE_EVENT_LOG.parquet")
    log.to_parquet(out, index=False)
    pd.DataFrame(summary).to_csv(
        os.path.join(paths["results"], "state_engine_summary.csv"), index=False)

    bio.write_manifest("state_engine_manifest.json", {
        "phase": "3_state_formation_engine",
        "rungs_built": ["1_univariate_departure", "2_multivariate_novelty_rms"],
        "rungs_not_built": ["2b_full_covariance", "3_change_point",
                            "4_online_clustering", "5_transition_analysis"],
        "novelty_components": NOVELTY_COMPONENTS,
        "min_components": MIN_COMPONENTS,
        "novelty_window": NOVELTY_WINDOW,
        "state_edges": list(STATE_EDGES),
        "panels": summary,
        "emits_orders": False,
        "labels_consulted": False,
        "outputs": ["results/STATE_EVENT_LOG.parquet",
                    "results/state_engine_summary.csv"],
    })
    print(f"\nwrote {out} — {len(log):,} state observations. "
          "No order, target or direction is emitted by this stage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
