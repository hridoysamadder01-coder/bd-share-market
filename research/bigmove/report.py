"""Generate BIG_MOVE_REPORT.md from the ranking CSV — numbers only, no hand-copying."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

import numpy as np
import pandas as pd


def fmt(x, n=3):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if isinstance(x, float):
        return f"{x:.{n}f}"
    return str(x)


def pct(x, n=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{100*x:.{n}f}%"


def table(df: pd.DataFrame, cols, headers) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c, kind in cols:
            v = r.get(c)
            cells.append(pct(v) if kind == "pct" else fmt(v) if kind == "num" else str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def build(out_dir: str) -> str:
    rank = pd.read_csv(os.path.join(out_dir, "CANDIDATE_RANKING.csv"))
    man = json.load(open(os.path.join(out_dir, "MANIFEST.json")))
    curves = pd.read_csv(os.path.join(out_dir, "RESPONSE_CURVES.csv"))
    osum = pd.read_csv(os.path.join(out_dir, "OUTCOME_SUMMARY.csv"))

    good = rank[rank.status == "PROMISING"].sort_values("t_nw_date", ascending=False)
    weak = rank[rank.status == "WEAK"]
    dead = rank[rank.status == "KILLED"]
    thin = rank[rank.status == "INSUFFICIENT_SAMPLE"]

    cols = [("candidate", "s"), ("outcome", "s"), ("n_signal", "s"), ("n_symbols", "s"),
            ("hit_rate", "pct"), ("control_rate_mean", "pct"), ("lift", "num"),
            ("t_nw_date", "num"), ("ci_lo", "pct"), ("year_consistency", "num")]
    headers = ["candidate", "outcome", "N", "symbols", "hit", "control", "lift",
               "NW t", "CI lo", "yr cons"]

    L = []
    L.append("# Big-move discovery — results\n")
    L.append(f"Run `{man['run_id']}` · branch `{man['branch']}` · commit `{man['commit'][:12]}` "
             f"· parent `{man['parent_commit'][:12]}`\n")
    L.append(f"Panel {man['rows']:,} rows · {man['symbols']} symbols · "
             f"{man['date_min']} … {man['date_max']} · dataset sha256 `{man['dataset_hash'][:16]}`\n")
    L.append(f"Sealed holdout {man['sealed_holdout'][0]} … {man['sealed_holdout'][1]} and the slice "
             f"from {man['reserved_from']} are dropped at load and asserted absent.\n")

    L.append("\n## A. What survived\n")
    if len(good):
        L.append(f"{good.candidate.nunique()} candidates in {len(good)} candidate x outcome cells "
                 f"reached PROMISING (lift >= 1.25, NW t >= 2.5, bootstrap CI above control, "
                 f"year consistency >= 0.6).\n")
        L.append(table(good.head(25), cols, headers))
    else:
        L.append("**Nothing reached PROMISING.** No candidate cleared lift >= 1.25 with "
                 "NW t >= 2.5, a bootstrap CI above its matched control, and year "
                 "consistency >= 0.6 simultaneously.\n")
        best = rank[rank.status != "INSUFFICIENT_SAMPLE"].nlargest(15, "lift")
        L.append("\nThe highest lifts observed, none of which qualified:\n")
        L.append(table(best, cols, headers))

    L.append("\n## B. What died\n")
    L.append(f"KILLED {len(dead)} cells · WEAK {len(weak)} · INSUFFICIENT_SAMPLE {len(thin)}\n")
    if len(dead):
        worst = dead.nsmallest(15, "lift")
        L.append("\nThe most decisively negative, by lift:\n")
        L.append(table(worst, cols, headers))

    L.append("\n## C. Response surface — relative volume x prior move\n")
    L.append("The whole surface, not one tuned cell. Rows are the abnormal-volume "
             "threshold; `prior3_max` 1.0 means no pre-move filter at all.\n")
    for outcome in sorted(curves.outcome.unique()):
        sub = curves[curves.outcome == outcome]
        L.append(f"\n**{outcome}**\n")
        piv = sub.pivot_table(index="rel_volume_thr", columns="prior3_max",
                              values="lift", aggfunc="first")
        npiv = sub.pivot_table(index="rel_volume_thr", columns="prior3_max",
                               values="n_signal", aggfunc="first")
        hdr = ["relvol >="] + [f"prior3 < {c}" for c in piv.columns]
        L.append("| " + " | ".join(hdr) + " |")
        L.append("|" + "|".join(["---"] * len(hdr)) + "|")
        for i in piv.index:
            cells = [f"{i:g}"]
            for c in piv.columns:
                lv, nn = piv.loc[i, c], npiv.loc[i, c]
                cells.append(f"{lv:.3f} (n={int(nn):,})" if np.isfinite(lv) else "—")
            L.append("| " + " | ".join(cells) + " |")

    L.append("\n## D. Population outcome rates\n")
    L.append("What a randomly chosen row achieves, the number every lift is against.\n")
    piv = osum.pivot_table(index="band_pct", columns="horizon", values="population_rate")
    hdr = ["target"] + [f"{c}d" for c in piv.columns]
    L.append("| " + " | ".join(hdr) + " |")
    L.append("|" + "|".join(["---"] * len(hdr)) + "|")
    for i in piv.index:
        L.append("| +" + f"{int(i)}% | " + " | ".join(pct(piv.loc[i, c]) for c in piv.columns) + " |")

    L.append("\n## E. Method\n")
    L.append(f"- Matched controls on {', '.join(man['strata'])}, "
             f"{man['control_draws']} draws with replacement; the reported control is the "
             f"mean over draws and `control_rate_sd` its spread. A single draw is one sample "
             f"of a random quantity, not the quantity.\n")
    L.append(f"- Significance on a per-DATE difference series with Newey-West lags equal to "
             f"the horizon. Overlapping horizons make the row count the wrong inferential "
             f"unit.\n")
    L.append(f"- {man['bootstrap_draws']}-draw symbol-block bootstrap CI; symbols are the "
             f"cluster, not rows.\n")
    L.append("- Every result carries MFE, MAE and, where reached, median days to +10 %.\n")
    return "\n".join(L)


if __name__ == "__main__":
    d = sys.argv[1]
    text = build(d)
    with open(os.path.join(d, "BIG_MOVE_REPORT.md"), "w") as fh:
        fh.write(text)
    print(f"wrote {os.path.join(d, 'BIG_MOVE_REPORT.md')} ({len(text)} chars)")
