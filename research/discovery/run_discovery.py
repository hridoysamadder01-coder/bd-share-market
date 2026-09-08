#!/usr/bin/env python3
"""Run the candidate library over every DEV slice and rank what shows structure.

    python3 -m research.discovery.run_discovery --out results/discovery
"""
from __future__ import annotations
import argparse, json, os, sys
import pandas as pd
from .panel import load_dev, add_regime_and_room, dev_slices, HORIZONS, BAND_LADDER_TRUTH
from .candidates import build_library, build_mutations, evaluate, vol_quintile

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="results/discovery")
    ap.add_argument("--min-events", type=int, default=30)
    a = ap.parse_args(argv)

    d = load_dev()
    print(f"DEV rows {len(d):,}  (reserved removed: {d.attrs['rows_removed_as_reserved']:,})")
    d = add_regime_and_room(d)
    slices = dev_slices(d)
    lib = build_library() + build_mutations()
    print(f"{len(lib)} candidates x {len(HORIZONS)} horizons x {len(slices)} slices")
    for s in slices:
        print("  slice:", s.note)

    rows = []
    for sl in slices:
        # the control dimension is a property of the slice, not of a candidate:
        # computing it once here instead of per candidate x horizon.
        sl.frame["_vq"] = vol_quintile(sl.frame)
        for c in lib:
            for h in HORIZONS:
                r = evaluate(sl.frame, c, h, min_events=a.min_events)
                r["slice"] = sl.note.split(":")[0]
                r["panel"] = sl.panel
                rows.append(r)
    df = pd.DataFrame(rows)
    os.makedirs(a.out, exist_ok=True)
    df.to_csv(os.path.join(a.out, "CANDIDATES.csv"), index=False)

    order = {"PROMISING": 0, "WEAK": 1, "KILLED": 2, "NOT_OBSERVABLE": 3}
    df["_o"] = df["status"].map(order).fillna(9)
    df = df.sort_values(["_o", "lift"], ascending=[True, False])
    with open(os.path.join(a.out, "SUMMARY.json"), "w") as fh:
        json.dump({"band_ladder_truth": BAND_LADDER_TRUTH,
                   "slices": [s.note for s in slices],
                   "by_status": df["status"].value_counts().to_dict(),
                   "n_candidates": len(lib), "horizons": list(HORIZONS)}, fh, indent=1)
    print("\nby status:", df["status"].value_counts().to_dict())
    cols = ["cid","slice","horizon","n_events","coverage_pct","fwd_move_pct","adverse_pct",
            "hit_rate","ctrl_hit_rate","lift","excess_pct","status"]
    print("\n=== top 25 by lift ===")
    print(df[df.status.isin(["PROMISING","WEAK"])][cols].head(25).to_string(index=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
