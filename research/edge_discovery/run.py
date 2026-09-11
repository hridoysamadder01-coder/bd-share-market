"""Stage 3 run: raw capture → unified state → geometry → ranked candidates.

    python3 -m research.edge_discovery.run --capture evidence/capture/2026-09-06 \
                                           --run-id 2026-09-08-calibration

Writes `results/edge_discovery/<run_id>/`:

    MANIFEST.json            what was read, which hashes, what was NOT available
    STAGE3_GATE.json         computable / varies / documented — the ROADMAP gate
    CANDIDATES.csv           the ranking, with status and sample size
    TLPI_RESPONSE_CURVE.csv  AUC across the λ grid on identical eligible rows
    CONTROLS.csv/.json       C1..C7 — nulls, past move, per symbol, time split,
                             the incremental gate, episodes, and the distance ablation
    LABEL_COVERAGE.json      how many states each horizon can speak about
    SPLITS.csv               the same candidates inside quality and phase buckets

Which sessions may be read
--------------------------
`micro/sessions/INDEX.json` is the authority and is consulted, not assumed. The
2026-09-06 session is marked in the prereg as calibration — **already seen**,
never counted as DEV, VALIDATION or HOLDOUT — which is exactly why it is the
right substrate for building and gating mathematics: it is already spent, so
measuring on it costs no unseen data. It is equally why nothing measured on it
is evidence for an edge, and every result carries the sample status that says so.

Any session listed as FINAL_HOLDOUT is refused by `check_sessions` rather than
filtered, so a holdout session cannot be read by forgetting to exclude it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from seeing.features.geometry import (E1_THRESHOLD, LAMBDA_GRID, MAX_DT_S, MIN_DT_S,
                                      TRUTH as GEOM_TRUTH, geometry_frame, risk_context)
from seeing.features.micro import features as micro_features
from seeing.fusion.fuse import fuse
from seeing.fusion.unified import IdentityIndex, assert_no_future_reference, summary, unify
from seeing.replay import replay

from .controls import controls_table, run_controls
from .evaluate import (INSUFFICIENT_SAMPLE, NOT_OBSERVABLE, evaluate, rank,
                       response_curve, split_by)
from .labels import HORIZONS_S, PRIMARY_HORIZON_S, add_labels, label_coverage

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MICRO_INDEX = os.path.join(ROOT, "micro", "sessions", "INDEX.json")
PREREG_SHA256 = "169935bdb2ef772c6944da12d1afd691c0c2a1b685281ff16b01085d3c7ac01d"


MICRO_BRANCH = "claude/micro-evidence-data"


def _read_ledger(index_path: str = MICRO_INDEX) -> Dict[str, Any]:
    """The session ledger, from the working tree or from the micro branch.

    The ledger lives on `claude/micro-evidence-data`, checked out in a different
    worktree, so a plain path lookup finds nothing and a seal check built on it
    would silently pass. It is read out of the branch with `git show` when it is
    not on disk, and the *source* is reported either way, so a run whose seal
    could not be verified says so rather than looking clean.
    """
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as fh:
            return {"source": index_path, "index": json.load(fh)}
    import subprocess
    rel = os.path.relpath(index_path, ROOT)
    try:
        out = subprocess.check_output(["git", "show", f"{MICRO_BRANCH}:{rel}"],
                                      cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
                                      timeout=60)
        return {"source": f"git show {MICRO_BRANCH}:{rel}", "index": json.loads(out)}
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as e:  # noqa: BLE001
        return {"source": None, "index": {}, "error": f"{type(e).__name__}: {e}"}


def check_sessions(session_dates: Sequence[str], index_path: str = MICRO_INDEX) -> Dict[str, Any]:
    """Refuse to proceed if any session being read is sealed, or unverifiable.

    The sealed set is read from the ledger rather than hard-coded, so a session
    that becomes HOLDOUT tomorrow is refused tomorrow without editing this file.
    An unreadable ledger is not treated as "nothing is sealed" — that is the
    failure mode that opens a holdout by accident — so it refuses too.
    """
    got = _read_ledger(index_path)
    idx = got["index"]
    if got["source"] is None:
        raise SystemExit(
            "REFUSED: the micro session ledger could not be read "
            f"({got.get('error')}), so it is not known which sessions are sealed. "
            "Discovery does not proceed on an unverifiable seal.")
    accepted = idx.get("accepted_sessions") or []
    sealed = {s.get("date") for s in accepted
              if isinstance(s, dict) and str(s.get("split", "")).upper().startswith("HOLDOUT")}
    breach = sorted(set(session_dates) & sealed)
    if breach:
        raise SystemExit(f"REFUSED: {breach} are sealed FINAL_HOLDOUT sessions "
                         f"({got['source']}). Discovery must not read them.")
    counts = idx.get("counts") or {}
    return {"ledger_source": got["source"],
            "accepted_sessions": len(accepted),
            "counts": counts,
            "sealed_dates": sorted(sealed),
            "sessions_read": sorted(set(session_dates)),
            "dev_sessions_available": counts.get("dev_filled", 0),
            "calibration_session_excluded": idx.get("calibration_session_excluded"),
            "note": "2026-09-06 is prereg calibration — already seen, never DEV/VAL/HOLDOUT. "
                    "Measuring on it spends no unseen data and establishes nothing."}


# --------------------------------------------------------------------------- candidates
def build_candidates(d: pd.DataFrame) -> List[Dict[str, str]]:
    """Every score to be ranked, with what it is and where it comes from.

    Baselines are carried deliberately: a new quantity that cannot beat `imb_l1`
    has not earned its complexity, and `DISCOVERY_ROUND1.md` records what happens
    when a high score is not compared against the obvious thing.
    """
    c: List[Dict[str, str]] = []

    def add(name: str, col: str, family: str, what: str) -> None:
        if col in d.columns:
            c.append({"name": name, "col": col, "family": family, "what": what})

    # --- baselines the prereg names (B1..B9). Reproduced, not re-derived.
    add("B1_imb_l1", "imb_l1", "baseline", "L1 imbalance — this is E1")
    add("B2_imb_top5", "imb_top5", "baseline", "top-5 imbalance")
    add("B3_imb_weighted", "imb_weighted", "baseline", "tick-weighted imbalance")
    add("B5_one_frame_pressure", "one_frame_pressure", "baseline", "one-frame depth pressure")
    add("B6_signed_flow", "trade_pressure", "baseline", "signed interval flow / volume")
    add("B9_signed_int_volume", "signed_int_volume", "baseline", "signed interval volume")

    # --- E1, reproduced against its own preregistered threshold
    add("E1", "e1", "e1", "the L1 imbalance the θ=0.20 rule reads")
    add("E1_fires_up_score", "e1_fires_up_score", "e1", f"+1 above θ={E1_THRESHOLD}, −1 below −θ, else 0")

    # --- the λ family
    for lam in LAMBDA_GRID:
        k = f"tlpi_{lam:g}".replace(".", "p")
        add(f"TLPI_l{lam:g}", k, "tlpi", f"distance-decayed imbalance, λ={lam:g}")
        add(f"TLPI_l{lam:g}_rel", k + "_rel", "tlpi_rel", f"λ={lam:g}, distance relative to mid")

    # --- rates: how fast the shape is changing, per second
    for lam in (0.0, 1.0, 8.0):
        k = f"tlpi_{lam:g}".replace(".", "p")
        add(f"TLPI_l{lam:g}_vel", k + "_vel", "velocity", f"d/dt of TLPI({lam:g}), per second")
        add(f"TLPI_l{lam:g}_acc", k + "_acc", "acceleration", f"d²/dt² of TLPI({lam:g})")
    add("E1_vel", "e1_vel", "velocity", "d/dt of E1, per second")
    add("E1_acc", "e1_acc", "acceleration", "d²/dt² of E1")

    # --- touch-vs-deep geometry
    add("GEO_touch_imb", "geom_touch_imb", "geometry", "imbalance at the touch")
    add("GEO_deep_imb", "geom_deep_imb", "geometry", "imbalance behind the touch (levels 2..N)")
    add("GEO_divergence", "geom_divergence", "geometry", "touch imbalance minus deep imbalance")
    add("GEO_centroid_gap", "geom_centroid_gap", "geometry", "how much further the ask mass sits")
    add("GEO_slope_gap", "geom_slope_gap", "geometry", "bid thickening minus ask thickening")
    add("GEO_touch_share_gap", "geom_touch_share_gap", "geometry", "bid touch share minus ask")
    add("GEO_divergence_vel", "geom_divergence_vel", "velocity", "d/dt of touch-deep divergence")

    # --- cross-timescale: a fast reading conditioned on a slower one
    add("X_e1_x_tlpi0", "x_e1_x_tlpi0", "cross_timescale",
        "E1 (touch, fast) multiplied by TLPI(0) (whole book, slow) — agreement across scales")
    add("X_e1_minus_tlpi0", "x_e1_minus_tlpi0", "cross_timescale",
        "E1 minus TLPI(0) — the touch disagreeing with the book behind it")
    add("X_tlpi1_x_flow", "x_tlpi1_x_flow", "cross_timescale",
        "TLPI(1) multiplied by signed flow — book and tape agreeing")

    # --- market / share conditioning
    add("C_xs_rank", "xs_pressure_rank", "conditioning",
        "this symbol's pressure rank across the universe at the same instant")
    add("C_breadth", "mkt_breadth", "conditioning", "market breadth at the same instant")
    add("C_e1_minus_market", "c_e1_minus_market", "conditioning",
        "E1 minus the universe's mean E1 — the share-specific part")

    # --- risk-vetoed variants: the same score, suppressed where the exit is bad
    add("V_E1_vetoed", "v_e1_vetoed", "risk_veto", "E1, zeroed where risk_veto fires")
    add("V_TLPI1_vetoed", "v_tlpi1_vetoed", "risk_veto", "TLPI(1), zeroed where risk_veto fires")
    return c


def derive(d: pd.DataFrame) -> pd.DataFrame:
    """The combination columns the candidate list refers to. All point-in-time."""
    f = d.copy()
    f["e1_fires_up_score"] = np.where(f["e1"] > E1_THRESHOLD, 1.0,
                                      np.where(f["e1"] < -E1_THRESHOLD, -1.0, 0.0))
    f["x_e1_x_tlpi0"] = f["e1"] * f["tlpi_0"]
    f["x_e1_minus_tlpi0"] = f["e1"] - f["tlpi_0"]
    flow = f["trade_pressure"] if "trade_pressure" in f else f["signed_int_volume"]
    f["x_tlpi1_x_flow"] = f["tlpi_1"] * flow

    # cross-sectional context, computed WITHIN a timestamp bucket so nothing from a
    # later instant enters. The bucket is the market's own poll cadence.
    f["_tbin"] = f["t_frame"].dt.floor("60s")
    f["c_market_e1"] = f.groupby("_tbin")["e1"].transform("mean")
    f["c_e1_minus_market"] = f["e1"] - f["c_market_e1"]
    f["xs_pressure_rank"] = f.groupby("_tbin")["one_frame_pressure"].rank(pct=True) - 0.5
    if "mkt_up" in f and "mkt_n" in f:
        f["mkt_breadth"] = (f["mkt_up"] - f["mkt_down"]) / f["mkt_n"].clip(lower=1)

    veto = f["risk_veto"].fillna(False).to_numpy(dtype=bool)
    f["v_e1_vetoed"] = np.where(veto, 0.0, f["e1"])
    f["v_tlpi1_vetoed"] = np.where(veto, 0.0, f["tlpi_1"])

    # session phase, from the frame's own elapsed time — never from the day's end
    start = f.groupby("session")["t_frame"].transform("min")
    el = (f["t_frame"] - start).dt.total_seconds()
    f["session_phase"] = pd.cut(el, [-1, 1800, 5400, 12600, 1e9],
                                labels=["open_30m", "morning", "midday", "close_hour"])
    # quality buckets — every one is an OBSERVED capture property, not an outcome
    f["q_book_agree"] = f["book_agree"].map({1.0: "agree", 0.0: "disagree"}).fillna("no_xcheck")
    f["q_spread"] = pd.cut(f["spread_ticks"], [-0.01, 1.01, 3.01, 1e9],
                           labels=["1_tick", "2_3_ticks", "wide"])
    f["q_liquidity"] = pd.qcut(f["liquidity_top5"].rank(method="first"), 3,
                               labels=["thin", "mid", "thick"])
    return f.drop(columns=["_tbin"])


# -------------------------------------------------------------------------- the gate
def stage3_gate(f: pd.DataFrame, cands: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    """ROADMAP Stage 3: computable on real data, varies, documented.

    "Varies" is the check that matters — a quantity that is constant across a
    real session is not a measurement, and a NaN column is not a quantity at all.
    """
    rows = []
    for c in cands:
        s = pd.to_numeric(f[c["col"]], errors="coerce")
        n = int(s.notna().sum())
        sd = float(s.std()) if n > 1 else float("nan")
        rows.append({"candidate": c["name"], "column": c["col"], "family": c["family"],
                     "computable": n > 0, "present": n,
                     "present_pct": round(100.0 * n / len(f), 2) if len(f) else None,
                     "sd": round(sd, 8) if np.isfinite(sd) else None,
                     "varies": bool(np.isfinite(sd) and sd > 0),
                     "documented": bool(c["what"])})
    t = pd.DataFrame(rows)
    checks = [
        {"check": "all_computable", "pass": bool(t["computable"].all()),
         "detail": sorted(t.loc[~t["computable"], "candidate"])},
        {"check": "all_vary", "pass": bool(t["varies"].all()),
         "detail": sorted(t.loc[~t["varies"], "candidate"])},
        {"check": "all_documented", "pass": bool(t["documented"].all()),
         "detail": sorted(t.loc[~t["documented"], "candidate"])},
        {"check": "tlpi_lambda0_equals_imb_all", "pass": None, "detail": None},
        {"check": "tlpi_high_lambda_approaches_e1", "pass": None, "detail": None},
    ]
    # the two structural identities that make the λ family testable rather than decorative
    both = f[["tlpi_0", "imb_all"]].dropna()
    d0 = float((both["tlpi_0"] - both["imb_all"]).abs().max()) if len(both) else float("nan")
    checks[3] = {"check": "tlpi_lambda0_equals_imb_all",
                 "pass": bool(np.isfinite(d0) and d0 < 1e-9),
                 "detail": {"max_abs_diff": d0, "rows": int(len(both))}}
    hi = f[["tlpi_8", "e1"]].dropna()
    corr = float(hi.corr().iloc[0, 1]) if len(hi) > 2 else float("nan")
    checks[4] = {"check": "tlpi_high_lambda_approaches_e1",
                 "pass": bool(np.isfinite(corr) and corr > 0.99),
                 "detail": {"pearson_r": round(corr, 6) if np.isfinite(corr) else None,
                            "rows": int(len(hi))}}
    passed = sum(1 for c in checks if c["pass"])
    return {"schema": "stage3_gate/1", "checks": checks, "passed": passed, "of": len(checks),
            "verdict": "PASS" if passed == len(checks) else "FAIL",
            "quantities": rows, "truth": GEOM_TRUTH,
            "rate_window_s": [MIN_DT_S, MAX_DT_S]}


# ------------------------------------------------------------------------------ run
def build_states(capture: str, identity_map: Optional[str] = None) -> pd.DataFrame:
    u = unify(fuse(replay(capture)), identity=IdentityIndex.load(identity_map))
    assert_no_future_reference(u)
    u["session"] = u["t_frame"].dt.strftime("%Y-%m-%d")
    return u


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", action="append", required=True,
                    help="a raw capture directory; repeat for several sessions")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--identity-map", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    parts = [build_states(c, a.identity_map) for c in a.capture]
    u = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    ledger = check_sessions(sorted(u["session"].unique()))

    f = derive(risk_context(geometry_frame(micro_features(u))))
    f = add_labels(f)
    cands = build_candidates(f)

    gate = stage3_gate(f, cands)
    cov = label_coverage(f)

    results = [evaluate(f, c["name"], c["col"], H) for H in HORIZONS_S for c in cands]
    ranked = rank(results)
    fam = {c["name"]: c["family"] for c in cands}
    what = {c["name"]: c["what"] for c in cands}
    ranked["family"] = ranked["candidate"].map(fam)
    ranked["what"] = ranked["candidate"].map(what)

    lam_cols = [f"tlpi_{l:g}".replace(".", "p") for l in LAMBDA_GRID]
    curve = response_curve(f, lam_cols, LAMBDA_GRID, PRIMARY_HORIZON_S)
    curve_rel = response_curve(f, [c + "_rel" for c in lam_cols], LAMBDA_GRID, PRIMARY_HORIZON_S)
    curve_rel["scale"] = "relative_to_mid"
    curve["scale"] = "ticks"

    # The controls, for the leaders of each new family against the baselines they
    # must beat. A candidate that is not put through these is not reported as
    # anything, however high it ranks.
    baselines = [c["col"] for c in cands if c["family"] == "baseline"] + ["e1"]
    control_for = [c for c in ("tlpi_2", "tlpi_1", "geom_divergence", "c_e1_minus_market",
                               "x_e1_minus_tlpi0", "v_e1_vetoed") if c in f.columns]
    control_reports = [run_controls(f, c, baselines, PRIMARY_HORIZON_S) for c in control_for]

    splits = []
    for col in ("book_shape", "session_phase", "q_book_agree", "q_spread", "q_liquidity"):
        for cn, cc in (("E1", "e1"), ("TLPI_l1", "tlpi_1"), ("GEO_divergence", "geom_divergence")):
            s = split_by(f, cn, cc, col, PRIMARY_HORIZON_S)
            s.insert(0, "split", col)
            s = s.rename(columns={col: "bucket"})
            splits.append(s)

    out = a.out or os.path.join(ROOT, "results", "edge_discovery", a.run_id)
    os.makedirs(out, exist_ok=True)
    ranked.to_csv(os.path.join(out, "CANDIDATES.csv"), index=False)
    pd.concat([curve, curve_rel], ignore_index=True).to_csv(
        os.path.join(out, "TLPI_RESPONSE_CURVE.csv"), index=False)
    pd.concat(splits, ignore_index=True).to_csv(os.path.join(out, "SPLITS.csv"), index=False)
    controls_table(control_reports).to_csv(os.path.join(out, "CONTROLS.csv"), index=False)
    for nm, obj in (("STAGE3_GATE.json", gate), ("LABEL_COVERAGE.json", cov),
                    ("CONTROLS.json", control_reports)):
        with open(os.path.join(out, nm), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=1, sort_keys=True, default=str)

    manifest = {
        "schema": "edge_discovery_run/1", "run_id": a.run_id,
        "captures": list(a.capture),
        "prereg_sha256": PREREG_SHA256,
        "prereg_note": "read-only; no threshold, feature, outcome, universe or split was changed",
        "e1_threshold": E1_THRESHOLD, "lambda_grid": list(LAMBDA_GRID),
        "horizons_s": list(HORIZONS_S), "primary_horizon_s": PRIMARY_HORIZON_S,
        "state_summary": summary(u),
        "session_ledger": ledger,
        "sessions": sorted(u["session"].unique()),
        "candidates": len(cands), "evaluations": len(results),
        "status_counts": ranked["status"].value_counts().to_dict(),
        "stage3_gate": gate["verdict"],
        "controls_run_for": control_for,
        "controls_beating_strongest_baseline": [r["candidate"] for r in control_reports
                                                if r.get("C5_beats_strongest_established")],
        "label_coverage": cov,
    }
    with open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True, default=str)

    print(json.dumps({k: v for k, v in manifest.items()
                      if k not in ("state_summary", "label_coverage")}, indent=1, default=str))
    print(f"\nwrote {out}", file=sys.stderr)
    return 0 if gate["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
