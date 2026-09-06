"""Render the experiment outputs of one capture into a Markdown report.

    python3 -m seeing.report --exp results/seeing/2026-09-06 --capture evidence/capture/2026-09-06 \
        --out reports/SEEING_EXPERIMENT_REPORT_2026-09-06.md

The report prints, in this order: the truth map (what is OBSERVED / INFERRED /
NOT_OBSERVABLE in the fused state), the full denominator, the capture health
(sources, gaps, agreement between the two book sensors), the experiment table,
the incremental-vs-baseline table, the falsification table, and the verdict
with the pre-registered rule. Numbers are read from the CSV/JSON outputs; the
report never recomputes anything.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict

import pandas as pd


def _md(df: pd.DataFrame, cols=None, n=None, floatfmt="{:.4f}") -> str:
    if df is None or not len(df):
        return "_(empty)_\n"
    d = df if cols is None else df[[c for c in cols if c in df.columns]]
    if n:
        d = d.head(n)
    d = d.copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda x: "" if pd.isna(x) else floatfmt.format(x))
    lines = ["| " + " | ".join(str(c) for c in d.columns) + " |", "|" + "---|" * len(d.columns)]
    for _, r in d.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines) + "\n"


def render(exp: str, capture: str) -> str:
    def load(name):
        p = os.path.join(exp, name)
        return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()
    den = json.load(open(os.path.join(exp, "DENOMINATOR.json")))
    ver = json.load(open(os.path.join(exp, "VERDICT.json")))
    truth = json.load(open(os.path.join(exp, "TRUTH_MAP.json")))
    man = json.load(open(os.path.join(exp, "MANIFEST.json")))
    res = load("EXPERIMENT_RESULTS.csv")
    inc = load("INCREMENTAL_VS_BASELINES.csv")
    fal = load("FALSIFICATION.csv")
    trans = load("STATE_TRANSITIONS.csv")
    status = {}
    sp = os.path.join(capture, "STATUS.json")
    if os.path.exists(sp):
        status = json.load(open(sp))
    h = man.get("design", {}).get("primary_h")

    out = []
    out.append(f"# SEEING — synchronized DSE market-state experiment · capture `{os.path.basename(capture.rstrip('/'))}`\n")
    out.append(f"**VERDICT: {ver['verdict']}**  \n" + "\n".join(f"- {r}" for r in ver.get("reasons", [])) + "\n")
    out.append("## 1. What is visible in one synchronized state (truth classes)\n")
    out.append(_md(pd.DataFrame([{"field group": k, "truth": v} for k, v in truth.items()])))
    out.append("## 2. Full denominator\n")
    flat = {k: v for k, v in den.items() if not isinstance(v, dict)}
    out.append(_md(pd.DataFrame([{"quantity": k, "value": v} for k, v in flat.items()])))
    for k in ("frames_per_symbol", "frames_per_split", "component_frames", "score_histogram", "baseline_frames", "state_frames"):
        if k in den:
            out.append(f"**{k}**\n\n" + _md(pd.DataFrame([{"key": a, "n": b} for a, b in den[k].items()])))
    out.append("## 3. Capture health\n")
    if status:
        out.append(_md(pd.DataFrame([{"source": s, "ok": c.get("ok"), "err": c.get("err"), "unchanged": c.get("unchanged")}
                                     for s, c in status.get("counts", {}).items()])))
        out.append(f"client: {status.get('client')} · symbols: {', '.join(status.get('symbols', []))}\n")
    out.append(f"replay counts: {man.get('replay_counts')} · replay problems: {man.get('replay_problems')}\n")
    out.append(f"## 4. Experiment — every signal, every split, horizon h={h} frames (primary)\n")
    if len(res):
        r = res[res["h"] == h] if h in set(res["h"]) else res
        out.append(_md(r, ["split", "signal", "n_signal", "episodes", "share_of_frames", "p_up", "p_down", "mean_fwd_ticks",
                           "base_p_up", "ctrl_p_up", "lift_vs_matched", "lift_vs_base"]))
        out.append("### All horizons — holdout\n")
        out.append(_md(res[res["split"] == "holdout"], ["signal", "h", "n_signal", "episodes", "p_up", "ctrl_p_up",
                                                         "lift_vs_matched", "lift_vs_base", "ticks_vs_base"]))
    out.append("## 5. Composite vs each simple baseline (holdout, primary horizon)\n")
    if len(inc):
        i = inc[(inc["split"] == "holdout") & (inc["h"] == h)] if h in set(inc["h"]) else inc
        out.append(_md(i, ["baseline", "n_baseline", "p_up_baseline", "lift_baseline_vs_base", "n_composite", "p_up_composite",
                           "incremental_lift", "n_both", "p_up_both", "n_only_baseline", "p_up_only_baseline", "within_baseline_gain"]))
    out.append("## 6. Falsification battery (holdout)\n")
    out.append(_md(fal, ["test", "variant", "n_frames", "n_signal", "episodes", "lift_vs_base", "incremental_vs_best_baseline", "passed", "note"]))
    out.append("## 7. State transitions (rows: from, columns: to)\n")
    out.append(_md(trans))
    out.append("## 8. Verdict rule (pre-registered)\n")
    out.append("```\n" + ver.get("rule", "").strip() + "\n```\n")
    out.append("## 9. Design parameters\n")
    out.append(_md(pd.DataFrame([{"parameter": k, "value": v} for k, v in man.get("design", {}).items()])))
    out.append(f"\n_git commit {man.get('git_commit')} · written {man.get('written_utc')}_\n")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    md = render(a.exp, a.capture)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        fh.write(md)
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
