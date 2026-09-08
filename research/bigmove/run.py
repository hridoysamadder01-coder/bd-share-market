"""Run the big-move discovery and write the artifact set."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from research.bigmove import candidates as CAND
from research.bigmove import evaluate as EV
from research.bigmove import panel as P
from research.bigmove.pit import asof_cross_section

PRIMARY_OUTCOMES = [("hit_p10_10d", 10), ("hit_p10_20d", 20), ("hit_p20_20d", 20),
                    ("hit_p10_30d", 30), ("hit_p20_30d", 30), ("hit_p5_5d", 5),
                    ("clean_p10_20d", 20), ("clean_p20_30d", 30)]


def add_causal_xs(f: pd.DataFrame) -> pd.DataFrame:
    """Causal cross-sectional state on relative volume, per panel.

    Per panel because the 2024-02-22 coverage break takes the universe from 381
    symbols to 88; a rank that spans it compares a share against a market that
    changed size, not against the market.
    """
    parts = []
    for pan, sub in f.groupby("panel", sort=False):
        sub = sub.sort_values(["date", "symbol"], kind="mergesort")
        parts.append(asof_cross_section(sub, value="rel_volume", entity="symbol",
                                        time="date", min_others=20))
    return pd.concat(parts, ignore_index=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=time.strftime("%Y-%m-%d-bigmove"))
    ap.add_argument("--out-root", default="results/big_move_discovery")
    ap.add_argument("--panel-cache", default="")
    a = ap.parse_args(argv)

    out_dir = os.path.join(a.out_root, a.run_id)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()

    if a.panel_cache and os.path.exists(a.panel_cache):
        f = pd.read_parquet(a.panel_cache)
    else:
        f = P.build()
        if a.panel_cache:
            f.to_parquet(a.panel_cache)
    raw_hash = P.dataset_hash(f)

    f = add_causal_xs(f)
    f = EV.add_strata(f)
    print(f"panel {f.shape} in {time.time()-t0:.0f}s", flush=True)

    rows: List[Dict[str, Any]] = []
    fam = CAND.families(f)
    for i, (name, family, desc, mask) in enumerate(fam):
        for outcome, h in PRIMARY_OUTCOMES:
            r = EV.evaluate(f, mask, outcome, horizon=h, name=name, seed=17)
            r["family"] = family
            r["description"] = desc
            r["status"] = EV.status_of(r)
            rows.append(r)
        print(f"  [{i+1}/{len(fam)}] {name}", flush=True)

    res = pd.DataFrame(rows)
    survivors = sorted(res[res.status == "PROMISING"]["candidate"].unique().tolist())
    for name, family, desc, mask in CAND.combinations(f, survivors):
        for outcome, h in PRIMARY_OUTCOMES:
            r = EV.evaluate(f, mask, outcome, horizon=h, name=name, seed=17)
            r["family"] = family
            r["description"] = desc
            r["status"] = EV.status_of(r)
            rows.append(r)
        print(f"  chain {name}", flush=True)

    res = pd.DataFrame(rows)
    res = res.sort_values(["lift", "t_nw_date"], ascending=False)
    res.to_csv(os.path.join(out_dir, "CANDIDATE_RANKING.csv"), index=False)
    res[res.status.isin(["KILLED", "WEAK"])].to_csv(
        os.path.join(out_dir, "FAILED_CANDIDATES.csv"), index=False)

    # response curves: the whole surface, never one tuned threshold
    curves = []
    rv = pd.to_numeric(f["rel_volume"], errors="coerce")
    p3 = pd.to_numeric(f["prior_3d_ret"], errors="coerce")
    for thr in (1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 8.0):
        for pm in (0.02, 0.05, 1.0):
            m = (rv >= thr) & (p3 < pm)
            for outcome, h in [("hit_p10_20d", 20), ("hit_p20_30d", 30)]:
                r = EV.evaluate(f, m, outcome, horizon=h,
                                name=f"relvol>={thr}|p3<{pm}", seed=17)
                r["rel_volume_thr"] = thr
                r["prior3_max"] = pm
                curves.append(r)
    pd.DataFrame(curves).to_csv(os.path.join(out_dir, "RESPONSE_CURVES.csv"), index=False)

    # outcome summary across the population
    osum = []
    for h in P.HORIZONS:
        for b in P.TARGET_BANDS:
            tag = int(round(b * 100))
            c = f"hit_p{tag}_{h}d"
            if c in f.columns:
                osum.append({"horizon": h, "band_pct": tag,
                             "population_rate": float(np.nanmean(f[c])),
                             "n": int(f[c].notna().sum())})
    pd.DataFrame(osum).to_csv(os.path.join(out_dir, "OUTCOME_SUMMARY.csv"), index=False)

    manifest = {
        "run_id": a.run_id,
        "branch": _sh("git rev-parse --abbrev-ref HEAD"),
        "commit": _sh("git rev-parse HEAD"),
        "parent_commit": _sh("git rev-parse HEAD~1"),
        "dataset": P.EOD_PATH,
        "dataset_hash": raw_hash,
        "rows": int(len(f)), "symbols": int(f.symbol.nunique()),
        "date_min": str(f.date.min().date()), "date_max": str(f.date.max().date()),
        "sealed_holdout": [str(x.date()) for x in P.SEALED_HOLDOUT],
        "reserved_from": str(P.VIRGIN_SLICE_START.date()),
        "horizons": list(P.HORIZONS), "bands": list(P.TARGET_BANDS),
        "control_draws": EV.CONTROL_DRAWS, "bootstrap_draws": EV.BOOT_DRAWS,
        "strata": list(EV.STRATA),
        "n_candidates": int(res.candidate.nunique()),
        "command": "python3 -m research.bigmove.run",
        "elapsed_s": round(time.time() - t0, 1),
    }
    with open(os.path.join(out_dir, "MANIFEST.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    print(json.dumps({"out": out_dir, "candidates": manifest["n_candidates"],
                      "rows": manifest["rows"]}, indent=1))
    return 0


def _sh(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd.split(), text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    sys.exit(main())
