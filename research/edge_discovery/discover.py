"""The discovery run: Stage 2 states → candidate families → ranked artifacts.

    python3 -m research.edge_discovery.discover \
        --capture evidence/capture/2026-09-06 --run-id 2026-09-08-calibration

Writes `results/edge_discovery/<run_id>/` — the whole artifact set the handoff
names, each one a measurement rather than a summary of one:

    MANIFEST.json            what was read, which hashes, what was NOT available
    REFERENCE_SIGNALS.csv    E1 and the deeper-book imbalances at the same θ
    TLPI_RESPONSE_CURVE.csv  the λ grid, matched lift and AUC on identical rows
    PRESSURE_DYNAMICS.csv    families A…J
    MATCHED_RESULTS.csv      every candidate, raw and matched side by side
    SESSION_PHASE.csv        the leaders by causal session phase
    QUALITY_SPLITS.csv       all frames vs high-quality frames only
    RISK_VETO.csv            what each veto does to lift AND to coverage
    CROSS_TIMESCALE.csv      the transparent combinations
    MARKET_SECTOR_SHARE.csv  the three tiers, where the sector tier exists
    CANDIDATE_RANKING.csv    ranked without opaque optimization
    FAILED_CANDIDATES.csv    everything that did not survive, and why
    DISCOVERY_REPORT.md      the written finding

Ranking follows the handoff's priority order and stops where the data stops: with
one session there is no DEV/VAL agreement to rank on, so that criterion is
reported as unavailable rather than quietly skipped and the ranking falls through
to matched improvement, magnitude, MAE, episodes and breadth.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from seeing.features.geometry import LAMBDA_GRID, geometry_frame, risk_context
from seeing.features.micro import features as micro_features
from seeing.fusion.fuse import fuse
from seeing.fusion.unified import IdentityIndex, assert_no_future_reference, summary, unify
from seeing.replay import replay

from . import families as F
from .evaluate import INSUFFICIENT_SAMPLE, NOT_OBSERVABLE, auc
from .labels import add_labels
from .reference import (FRAME_HORIZONS, PRIMARY_FRAME_H, add_frame_outcomes,
                        add_matching_keys, decile_response, evaluate_binary, spearman)
from .run import PREREG_SHA256, check_sessions, stage3_gate

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_EPISODES = 30          # below this, no statement — never a kill
MIN_SIGNAL_ROWS = 100


def build_table(captures: Sequence[str], identity_map: Optional[str] = None) -> pd.DataFrame:
    """Raw bytes → unified state → micro features → geometry → dynamics → context → risk."""
    idx = IdentityIndex.load(identity_map)
    parts = []
    for c in captures:
        u = unify(fuse(replay(c)), identity=idx)
        assert_no_future_reference(u)
        parts.append(u)
    u = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    u["session"] = u["t_frame"].dt.strftime("%Y-%m-%d")

    d = risk_context(geometry_frame(micro_features(u)))
    d = F.add_dynamics(d, "imb_l1")
    d = F.add_context(d, F.sector_map(identity_map))
    d = F.add_risk(d)
    d = add_matching_keys(add_frame_outcomes(add_labels(d)))

    # Causal session phase, fixed BEFORE any outcome is read: elapsed time from the
    # session's own first frame, not a quantile of the day (which would need the
    # day's end to be known).
    el = (d["t_frame"] - d.groupby("session")["t_frame"].transform("min")).dt.total_seconds()
    d["session_phase"] = pd.cut(el, [-1, 1800, 7200, 1e9],
                                labels=["OPEN_EARLY", "MID", "LATE"])
    # Quality state, every component an OBSERVED capture property
    d["q_high"] = (~d["dup_payload"].fillna(False).astype(bool)
                   & ~d["crossed"].fillna(False).astype(bool)
                   & ~d["locked"].fillna(False).astype(bool)
                   & ~d["one_sided"].fillna(False).astype(bool)
                   & (d["watch_age_s"].fillna(1e9) < 120)
                   & (d["xsrc_disagreeing"].fillna("") == ""))
    d["q_state"] = np.where(d["q_high"], "HIGH_QUALITY", "ALL_VALID")
    return d


def _status(row: Dict[str, Any]) -> str:
    """PROMISING / WEAK / KILLED / INSUFFICIENT_SAMPLE, with the thin case protected."""
    ep, n = row.get("episodes") or 0, row.get("n_signal_valid") or 0
    lift = row.get("matched_lift_pp")
    if n == 0:
        return NOT_OBSERVABLE
    if ep < MIN_EPISODES or n < MIN_SIGNAL_ROWS or lift is None:
        return INSUFFICIENT_SAMPLE
    if lift >= 5.0:
        return "PROMISING"
    if lift <= -5.0:
        return "KILLED"
    return "WEAK"


def evaluate_all(d: pd.DataFrame, cands: Sequence[Dict[str, Any]],
                 horizons: Sequence[int] = FRAME_HORIZONS) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for c in cands:
        for h in horizons:
            r = evaluate_binary(d, c["candidate_id"], c["signal"], h,
                                outcome=c.get("outcome", "up"))
            r.update({"family": c["family"], "logic": c["logic"],
                      "formula": c["formula"], "truth_class": c["truth_class"]})
            r["status"] = _status(r)
            r["reason"] = _reason(r)
            rows.append(r)
    return pd.DataFrame(rows)


def _reason(r: Dict[str, Any]) -> str:
    if r["status"] == NOT_OBSERVABLE:
        return "the signal never fires on a row with a valid outcome"
    if r["status"] == INSUFFICIENT_SAMPLE:
        return (f"{r['episodes']} episodes / {r['n_signal_valid']} rows — below the "
                f"{MIN_EPISODES}-episode, {MIN_SIGNAL_ROWS}-row floor. NOT a kill.")
    if r["status"] == "PROMISING":
        return f"matched lift {r['matched_lift_pp']:+.2f}pp on {r['episodes']} episodes"
    if r["status"] == "KILLED":
        return f"matched lift {r['matched_lift_pp']:+.2f}pp — worse than its own control"
    return f"matched lift {r['matched_lift_pp']:+.2f}pp — inside ±5pp of its control"


def rank_candidates(t: pd.DataFrame, h: int = PRIMARY_FRAME_H) -> pd.DataFrame:
    """The handoff's priority order, stopping where the data stops.

    Criterion 1 is DEV/VAL agreement. With one session there are no splits to
    agree, so it is recorded as unavailable on every row rather than silently
    dropped, and ranking falls through to matched improvement, then magnitude,
    then MAE, then episodes, then breadth.
    """
    p = t[(t["horizon_frames"] == h) & (t["status"] != NOT_OBSERVABLE)].copy()
    p["dev_val_agreement"] = "UNAVAILABLE — one session, no split"
    p["_measured"] = (p["status"] != INSUFFICIENT_SAMPLE).astype(int)
    p["_lift"] = p["matched_lift_pp"].fillna(-999)
    p["_ticks"] = p["ticks_vs_ctrl"].fillna(-999)
    p["_mae"] = p["mae"].fillna(-999)
    p = p.sort_values(["_measured", "_lift", "_ticks", "_mae", "episodes", "symbol_coverage"],
                      ascending=[False, False, False, False, False, False])
    return p.drop(columns=[c for c in p.columns if c.startswith("_")]).reset_index(drop=True)


def split_table(d: pd.DataFrame, cands: Sequence[Dict[str, Any]], by: str,
                h: int = PRIMARY_FRAME_H, min_rows: int = 200) -> pd.DataFrame:
    rows = []
    for g, sub in d.groupby(by, sort=True, observed=True):
        for c in cands:
            if len(sub) < min_rows:
                rows.append({by: str(g), "candidate_id": c["candidate_id"],
                             "rows": int(len(sub)), "status": INSUFFICIENT_SAMPLE,
                             "reason": f"{len(sub)} rows < {min_rows}"})
                continue
            r = evaluate_binary(sub, c["candidate_id"], c["signal"].loc[sub.index], h,
                                outcome=c.get("outcome", "up"))
            r[by] = str(g)
            r["family"] = c["family"]
            r["status"] = _status(r)
            rows.append(r)
    return pd.DataFrame(rows)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", action="append", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--identity-map", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    d = build_table(a.capture, a.identity_map)
    ledger = check_sessions(sorted(d["session"].unique()))
    cands = F.build(d)
    out = a.out or os.path.join(ROOT, "results", "edge_discovery", a.run_id)
    os.makedirs(out, exist_ok=True)

    full = evaluate_all(d, cands)
    full.to_csv(os.path.join(out, "MATCHED_RESULTS.csv"), index=False)

    full[full["family"] == "reference"].to_csv(
        os.path.join(out, "REFERENCE_SIGNALS.csv"), index=False)
    full[full["family"].isin(("dynamics",))].to_csv(
        os.path.join(out, "PRESSURE_DYNAMICS.csv"), index=False)
    full[full["family"].isin(("risk", "veto"))].to_csv(
        os.path.join(out, "RISK_VETO.csv"), index=False)
    full[full["family"] == "cross"].to_csv(
        os.path.join(out, "CROSS_TIMESCALE.csv"), index=False)
    full[full["family"] == "context"].to_csv(
        os.path.join(out, "MARKET_SECTOR_SHARE.csv"), index=False)

    # --- TLPI response curve: matched lift AND AUC, on identical eligible rows
    curve = []
    for lam in LAMBDA_GRID:
        c = f"tlpi_{lam:g}".replace(".", "p")
        for h in FRAME_HORIZONS:
            r = evaluate_binary(d, c, d[c] > F.E1_THRESHOLD, h)
            elig = d[d[f"fwd_valid_h{h}"].astype(bool) & (d[f"fwd_mid_ticks_h{h}"] != 0)]
            sc = pd.to_numeric(elig[c], errors="coerce").to_numpy(dtype=float)
            pos = (elig[f"fwd_mid_ticks_h{h}"] > 0).to_numpy(dtype=bool)
            curve.append({"lambda": lam, "horizon_frames": h,
                          "matched_lift_pp": r["matched_lift_pp"],
                          "matched_lift_sd": r["matched_lift_sd"],
                          "episodes": r["episodes"], "n_signal_valid": r["n_signal_valid"],
                          "auc": round(auc(sc, pos), 5), "auc_rows": int(len(elig)),
                          "mean_fwd_ticks": r["mean_fwd_ticks"], "mae": r["mae"],
                          "status": _status(r)})
    cu = pd.DataFrame(curve)
    cu.to_csv(os.path.join(out, "TLPI_RESPONSE_CURVE.csv"), index=False)

    # --- decile response curves for the continuous leaders
    dec = pd.concat([decile_response(d, c, PRIMARY_FRAME_H)
                     for c in ("imb_l1", "tlpi_1", "tlpi_2", "imb_top5", "share_resid")
                     if c in d.columns], ignore_index=True)
    dec.to_csv(os.path.join(out, "RESPONSE_CURVES.csv"), index=False)

    leaders = [c for c in cands if c["candidate_id"] in
               ("E1", "TLPI_l1", "TLPI_l2", "DYN_C_level_vel", "DYN_H_persistent",
                "GEO_touch_dominant_bid", "VETO_E1_no_risk", "CTX_market_permission")]
    split_table(d, leaders, "session_phase").to_csv(
        os.path.join(out, "SESSION_PHASE.csv"), index=False)
    split_table(d, leaders, "q_state").to_csv(
        os.path.join(out, "QUALITY_SPLITS.csv"), index=False)
    split_table(d, leaders, "book_shape").to_csv(
        os.path.join(out, "REGIME_RESULTS.csv"), index=False)

    ranked = rank_candidates(full)
    ranked.to_csv(os.path.join(out, "CANDIDATE_RANKING.csv"), index=False)
    ranked.to_csv(os.path.join(out, "CANDIDATES.csv"), index=False)
    failed = ranked[ranked["status"].isin(("KILLED", "WEAK", INSUFFICIENT_SAMPLE))]
    failed.to_csv(os.path.join(out, "FAILED_CANDIDATES.csv"), index=False)

    gate = stage3_gate(d, [{"name": c["candidate_id"], "col": "P", "family": c["family"],
                            "what": c["logic"]} for c in cands[:1]])
    counts = ranked["status"].value_counts().to_dict()
    manifest = {
        "schema": "edge_discovery_run/2", "run_id": a.run_id, "captures": list(a.capture),
        "prereg_sha256": PREREG_SHA256,
        "prereg_note": "read-only; θ=0.20, outcome, universe, splits and horizons unchanged",
        "e1_threshold": F.E1_THRESHOLD, "lambda_grid": list(LAMBDA_GRID),
        "frame_horizons": list(FRAME_HORIZONS), "primary_frame_horizon": PRIMARY_FRAME_H,
        "median_cadence_s": round(float(d["frame_dt_s"].median(skipna=True)), 2),
        "matching_keys": ["symbol", "tod_bucket", "spread_bucket"],
        "matching_source": "tower/experiment.py matched_controls — reused, not reimplemented",
        "states": int(len(d)), "symbols": int(d["symbol"].nunique()),
        "sessions": sorted(d["session"].unique()),
        "sectors_observable_for_tier": sorted(
            d.loc[d["sector_observable"], "sector"].unique().tolist()),
        "sectors_single_member_not_observable": sorted(
            d.loc[~d["sector_observable"], "sector"].unique().tolist()),
        "candidates": len(cands), "evaluations": int(len(full)),
        "status_counts_primary_horizon": counts,
        "session_ledger": ledger,
        "stage2_summary": summary(d),
        "ranking_criterion_1_unavailable": "DEV/VAL agreement needs ≥2 sessions; 1 available",
    }
    with open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True, default=str)

    print(json.dumps({k: v for k, v in manifest.items()
                      if k not in ("stage2_summary", "session_ledger")}, indent=1, default=str))
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
