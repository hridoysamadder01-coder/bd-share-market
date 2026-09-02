#!/usr/bin/env python3
"""Causality proof for the Phase 4.5 footprint layer.

Two properties, each with a POSITIVE CONTROL so a pass cannot be vacuous:

  A. FOOTPRINTS ARE CAUSAL. Corrupt every input row strictly after a cut date
     (prices, features, states — all of it) and rebuild. Every footprint value
     at every row on or before the cut must be bit-identical. A deliberately
     leaky footprint (F01 shifted one session INTO THE FUTURE) must be caught.

  B. OUTCOMES ARE BOUNDED. An outcome over (t, t+k] may depend on rows t..t+k
     only. Corrupt every row after the cut; every outcome at rows at least
     max(k)+6 sessions before the cut must be unchanged. A deliberately
     unbounded outcome (max over the whole future) must be caught.

  python3 bd_research/experiments/verify_footprint_causality.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from bdlib import io as bio  # noqa: E402
import phase45_footprints as PH  # noqa: E402

CUTS = ("2014-06-30", "2016-03-31", "2017-12-28")
N_SYMBOLS = 25
NUMERIC_INPUTS = ["close", "high", "low", "ret_1", "realized_vol", "rel_volume_z",
                  "rel_turnover_z", "range_z", "close_location", "market_ret",
                  "market_relative_ret", "xs_breadth_abnormal", "accumulation_proxy",
                  "volume_price_divergence", "abnormal_persistence"]


def corrupt_after(d: pd.DataFrame, cut: pd.Timestamp, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    d = d.copy()
    m = d["ts"] > cut
    for c in NUMERIC_INPUTS:
        vals = d.loc[m, c].to_numpy(dtype=float)
        d.loc[m, c] = vals * rng.uniform(0.3, 3.0, len(vals)) + rng.normal(0, 1.0, len(vals))
    d.loc[m, "state"] = rng.choice(["CALM", "DRIFT", "DEPARTURE", "EXTREME"], int(m.sum()))
    return d


def main() -> int:
    regimes, _ = PH.load_regimes("dse_eod")
    d = regimes["DISCOVERY"]
    syms = sorted(d["symbol"].unique())[:N_SYMBOLS]
    d = d[d["symbol"].isin(syms)].sort_values(["symbol", "ts"], kind="mergesort")
    d = d.reset_index(drop=True)
    print(f"footprint causality proof on {len(syms)} symbols · {len(d):,} rows · "
          f"cuts {CUTS}")

    def leaky(frame: pd.DataFrame) -> np.ndarray:
        # a footprint that reads NEXT session's close — must change under corruption
        return frame.groupby("symbol", sort=False)["close"].shift(-1).to_numpy(dtype=float)

    def unbounded(frame: pd.DataFrame) -> pd.Series:
        # an outcome that reads the max close over the WHOLE future — must change
        piv = frame.pivot_table(index="ts", columns="symbol", values="close", aggfunc="last")
        s = (np.log(piv[::-1].cummax()[::-1]) - np.log(piv)).stack(future_stack=True)
        s.index = s.index.set_names(["ts", "symbol"])
        return s.reorder_levels(["symbol", "ts"]).sort_index()

    # outcomes are compared on the rows that EXIST (the main script merges the
    # date-aligned grid back onto real rows the same way)
    real_rows = pd.MultiIndex.from_frame(d[["symbol", "ts"]])
    base_fp = PH.build_footprints(d)
    base_fp["LEAKY_control"] = leaky(d)
    fwd_base = PH.forward_outcomes(d.join(base_fp.drop(columns=["symbol", "ts"])))
    fwd_base = fwd_base.set_index(["symbol", "ts"]).reindex(real_rows)
    fwd_base["UNBOUNDED_control"] = unbounded(d).reindex(fwd_base.index)
    assert fwd_base["UNBOUNDED_control"].notna().mean() > 0.99, "control did not align"

    failures = 0
    for cut in CUTS:
        cut = pd.Timestamp(cut)
        dc = corrupt_after(d, cut, seed=int(cut.value % 2**31))
        fp = PH.build_footprints(dc)
        fp["LEAKY_control"] = leaky(dc)
        before = d["ts"] <= cut
        n_before = int(before.sum())
        # A. footprints
        bad = []
        for c in PH.FP_IDS + ["PLACEBO_" + x for x in PH.FP_IDS] + ["guard"]:
            a, b = base_fp.loc[before, c].to_numpy(), fp.loc[before, c].to_numpy()
            if not np.array_equal(a, b):
                bad.append(c)
        la = np.nan_to_num(base_fp.loc[before, "LEAKY_control"].to_numpy(dtype=float), nan=-9)
        lb = np.nan_to_num(fp.loc[before, "LEAKY_control"].to_numpy(dtype=float), nan=-9)
        leaky_caught = not np.array_equal(la, lb)
        # B. outcomes bounded
        fwd_c = PH.forward_outcomes(dc.join(fp.drop(columns=["symbol", "ts", "LEAKY_control"])))
        fwd_c = fwd_c.set_index(["symbol", "ts"]).reindex(real_rows)
        fwd_c["UNBOUNDED_control"] = unbounded(dc).reindex(fwd_c.index)
        dates = np.array(sorted(d["ts"].unique()))
        safe_cut = dates[max(0, np.searchsorted(dates, np.datetime64(cut)) - (max(PH.HORIZONS) + 6))]
        rows = fwd_base.index[fwd_base.index.get_level_values("ts") <= safe_cut]
        ycols = [c for c in fwd_base.columns
                 if c.startswith(("y_", "off_", "door_", "recent_", "ca_day"))]
        bad_y = []
        for c in ycols:
            a = fwd_base.loc[rows, c].to_numpy(dtype=float)
            b = fwd_c.reindex(rows)[c].to_numpy(dtype=float)
            if not np.array_equal(np.nan_to_num(a, nan=-9), np.nan_to_num(b, nan=-9)):
                bad_y.append(c)
        a = fwd_base.loc[rows, "UNBOUNDED_control"].to_numpy(dtype=float)
        b = fwd_c.reindex(rows)["UNBOUNDED_control"].to_numpy(dtype=float)
        unbounded_caught = not np.array_equal(np.nan_to_num(a, nan=-9), np.nan_to_num(b, nan=-9))

        ok = (not bad) and leaky_caught and (not bad_y) and unbounded_caught
        failures += 0 if ok else 1
        print(f"\ncut {cut.date()}: {n_before:,} rows on/before cut")
        print(f"  A footprints identical before cut: {'YES' if not bad else 'NO — ' + str(bad)}"
              f" · leaky control caught: {'YES' if leaky_caught else 'NO'}")
        print(f"  B outcomes identical ≥{max(PH.HORIZONS) + 6} sessions before cut "
              f"({len(rows):,} rows): {'YES' if not bad_y else 'NO — ' + str(bad_y)}"
              f" · unbounded control caught: {'YES' if unbounded_caught else 'NO'}")

    # C. SINGLE-CELL LOCALITY (review LK-5): perturb ONE close at (symbol, D)
    #    and assert the exact change-set — outcomes may change only at grid
    #    offsets [D-k, D] for that symbol (windows containing D or D+1, or
    #    based at D), door/ca flags only at D or D+1, recent_* only at
    #    [D, D+6], and nothing at all for any other symbol.
    dates = np.array(sorted(d["ts"].unique()))
    sym = syms[len(syms) // 2]
    D = dates[len(dates) // 2]
    dc = d.copy()
    cell = (dc["symbol"] == sym) & (dc["ts"] == D)
    assert cell.sum() == 1, "perturbation cell not found"
    dc.loc[cell, "close"] = dc.loc[cell, "close"] * 1.37
    fwd_c = PH.forward_outcomes(dc.join(base_fp.drop(columns=["symbol", "ts", "LEAKY_control"])))
    fwd_c = fwd_c.set_index(["symbol", "ts"]).reindex(real_rows)
    Dpos = int(np.searchsorted(dates, D))
    viol = []
    for c in ycols:
        a = np.nan_to_num(fwd_base[c].to_numpy(dtype=float), nan=-9)
        b = np.nan_to_num(fwd_c[c].to_numpy(dtype=float), nan=-9)
        changed = np.where(a != b)[0]
        if len(changed) == 0:
            continue
        idx = fwd_base.index[changed]
        other = [x for x in idx if x[0] != sym]
        if other:
            viol.append((c, f"{len(other)} rows of other symbols changed"))
            continue
        offs = np.array([int(np.searchsorted(dates, np.datetime64(x[1]))) - Dpos for x in idx])
        if c.startswith(("y_", "off_")):
            k = int(c.rsplit("_", 1)[1])
            lo, hi = -k, 0
        elif c.startswith(("door_", "ca_day")):
            lo, hi = 0, 1
        else:                                   # recent_*
            lo, hi = 0, PH.FP["win"] + 1
        if offs.min() < lo or offs.max() > hi:
            viol.append((c, f"changed at offsets {sorted(set(offs.tolist()))}, allowed [{lo},{hi}]"))
    c_ok = not viol
    failures += 0 if c_ok else 1
    print(f"\nsingle-cell perturbation ({sym} @ {pd.Timestamp(D).date()}, close ×1.37):")
    print("  C change-set confined to the expected offsets: "
          + ("YES" if c_ok else "NO — " + "; ".join(f"{c}: {m}" for c, m in viol)))

    bio.write_manifest("phase45_causality_manifest.json", {
        "phase": "4.5_footprint_causality_proof", "symbols": N_SYMBOLS, "cuts": list(CUTS),
        "footprints_checked": PH.FP_IDS + ["guard"],
        "positive_controls": ["next session's close as a footprint (leaky)",
                              "max future close (unbounded outcome)"],
        "single_cell_locality": "PASS" if c_ok else "FAIL",
        "result": "PASS" if failures == 0 else f"FAIL ({failures} cuts)",
    })
    print("\nRESULT:", "PASS — every footprint is causal, every outcome is bounded, "
          "both positive controls caught" if failures == 0 else f"FAIL on {failures} cut(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
