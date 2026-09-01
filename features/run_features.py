#!/usr/bin/env python3
"""PHASE 2 runner — build the feature matrix and the (separate) outcome labels.

Features and labels are written to DIFFERENT FILES on purpose: the only way to
use a label as an input is to load it deliberately, which shows up in a diff.

  python3 bd_research/features/run_features.py --input <annotated.parquet> --tag synthetic
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bdlib import config as C  # noqa: E402
from bdlib import features as F  # noqa: E402
from bdlib import io as bio  # noqa: E402
from bdlib import labels as L  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="QA-annotated parquet from run_qa.py")
    ap.add_argument("--tag", default="run")
    a = ap.parse_args()

    p = bio.paths()
    df = pd.read_parquet(a.input)
    feat = F.build(df, C.DEFAULT)

    keys = ["symbol", "ts"]
    fcols = [c for c in F.FEATURE_COLUMNS if c in feat.columns]
    flags = [c for c in feat.columns if c.startswith("flag_")]

    features_out = os.path.join(p["results"], f"{a.tag}_features.parquet")
    feat[keys + flags + fcols].to_parquet(features_out, index=False)

    lab = L.build(feat, C.DEFAULT)
    labels_out = os.path.join(p["results"], f"{a.tag}_labels.parquet")
    pd.concat([feat[keys], lab], axis=1).to_parquet(labels_out, index=False)

    cov = pd.DataFrame({
        "feature": fcols,
        "non_null": [int(feat[c].notna().sum()) for c in fcols],
        "coverage": [round(float(feat[c].notna().mean()), 4) for c in fcols],
        "mean": [round(float(feat[c].mean(skipna=True)), 6) for c in fcols],
        "std": [round(float(feat[c].std(skipna=True)), 6) for c in fcols],
        "p01": [round(float(feat[c].quantile(0.01)), 6) for c in fcols],
        "p99": [round(float(feat[c].quantile(0.99)), 6) for c in fcols],
    })
    cov_out = os.path.join(p["results"], f"{a.tag}_feature_coverage.csv")
    cov.to_csv(cov_out, index=False)

    assert not (set(fcols) & set(L.label_columns(C.DEFAULT))), "feature/label overlap"
    assert not [c for c in fcols if c.startswith("fwd_")], "fwd_ column in feature set"

    # Numeric-sanity gate: a degenerate baseline must yield NaN, never a huge
    # finite number that silently poisons every downstream statistic.
    import numpy as np
    infinite = {c: int(np.isinf(feat[c].to_numpy(dtype=float)).sum()) for c in fcols}
    exploded = {c: float(np.nanmax(np.abs(feat[c].to_numpy(dtype=float)), initial=0.0))
                for c in fcols}
    bad_inf = {c: n for c, n in infinite.items() if n}
    bad_big = {c: v for c, v in exploded.items() if v > 1e6}
    if bad_inf or bad_big:
        raise SystemExit(f"FEATURE SANITY FAILED — infinite: {bad_inf}; |value|>1e6: {bad_big}")
    top = sorted(exploded.items(), key=lambda kv: -kv[1])[:5]
    clipped = feat.attrs.get("clipped", {})
    tot = int(sum(clipped.values()))
    print("feature sanity: no infinities · largest |values|: "
          + ", ".join(f"{c}={v:.4g}" for c, v in top))
    print(f"winsorised at ±{C.DEFAULT.features.z_clip:g}: {tot:,} values "
          f"({tot / max(len(feat) * len(fcols), 1):.4%} of cells) {clipped}")

    manifest = bio.write_manifest(f"{a.tag}_features_manifest.json", {
        "phase": "2_adaptive_features",
        "input": {"path": a.input},
        "params": {"features": C.DEFAULT.features.__dict__,
                   "labels": {**C.DEFAULT.labels.__dict__,
                              "horizons": list(C.DEFAULT.labels.horizons)}},
        "rows_in": int(len(df)),
        "rows_after_qa_exclusion": int(len(feat)),
        "n_features": len(fcols),
        "winsorised_counts": feat.attrs.get("clipped", {}),
        "z_clip": C.DEFAULT.features.z_clip,
        "n_label_columns": len(L.label_columns(C.DEFAULT)),
        "outputs": [os.path.basename(features_out), os.path.basename(labels_out),
                    os.path.basename(cov_out)],
    })
    print(json.dumps({"rows": len(feat), "features": len(fcols),
                      "labels": len(L.label_columns(C.DEFAULT)),
                      "manifest": os.path.basename(manifest)}, indent=2))
    print(cov.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
