#!/usr/bin/env python3
"""PHASE 4 — precursor research: do state transitions carry economic information?

Discipline this file is built around
------------------------------------
1. FULL DENOMINATOR. Every occurrence of a cohort enters the count, including
   every time nothing happened. Occurrences lost to untradeability are reported
   as a separate line, never quietly dropped from the base.
2. PRE-REGISTERED, MECHANICAL COHORT LIST. States, all 1-step transitions, all
   2-step paths above a minimum count, and the rung-1 baselines — enumerated by
   the code, not hand-picked after looking. The number of hypotheses is printed
   and an FDR correction is applied across them.
3. CLUSTERED INFERENCE. Occurrences cluster on market-wide days, so the naive
   per-occurrence t is optimistic. The reported t is computed across DATES
   (average within a date first) — the honest unit. Both are shown.
4. COSTS IN LAYERS. Gross · net of VERIFIED 0.8% brokerage · then an ESTIMATED
   band. Never summed into one number.
5. EXECUTION STATUS PER HORIZON. Saleability is UNKNOWN, so short horizons are
   LABELLED, not excluded, and never silently treated as tradeable.
6. NO BUY/SELL. This measures what followed a state. It does not convert
   anything into a position, a direction or a rule.
7. PANELS NEVER POOLED.

These are FULL-SAMPLE DESCRIPTIVE statistics. Nothing here is validated until
Phase 5 walk-forward; a cohort that looks good here has not survived anything.

  python3 experiments/phase4_precursors.py --tag dse_eod
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

HORIZONS = (1, 2, 3, 5, 10)

# A state is read at the CLOSE of day t, so the earliest possible action is the
# NEXT session's open. Horizon h exits at the close of session t+h. h=1 is
# therefore a same-session round trip from that entry.
EXEC_STATUS = {
    1: ("UNKNOWN", "same-session round trip — needs intraday netting / same-day sell"),
    2: ("UNKNOWN", "sell 1 session after purchase — depends on broker saleability"),
    3: ("OK", "sell 2 sessions after purchase — unaffected by the settlement question"),
    5: ("OK", "sell 4 sessions after purchase"),
    10: ("OK", "sell 9 sessions after purchase"),
}

MIN_N = 200          # a cohort below this is reported but never called a candidate
MIN_DATES = 30       # and needs this many distinct dates for a clustered t
FDR_Q = 0.10


# --------------------------------------------------------------------------- #
def forward_outcomes(bars: pd.DataFrame, entry_at: str = "open") -> pd.DataFrame:
    """Outcome after a state read at the CLOSE of day t.

    entry_at="open"  : buy the next session's OPEN, exit at close(t+h).
    entry_at="close" : buy the next session's CLOSE, exit at close(t+1+h).

    The second variant exists because this dataset's `open` field carries a
    structural asymmetry — the mean overnight gap is +0.385% while the median is
    exactly 0, and 66% of nonzero gaps are upward — which is stable across price
    buckets (so not tick rounding) and survives trimming (so not outliers). Every
    open-entry number therefore inherits a ~-0.4% intraday drag whose provenance
    is unresolved. The close-entry variant touches `open` nowhere, so agreement
    between the two means a finding does not depend on that open question.
    """
    piv = {f: bars.pivot_table(index="ts", columns="symbol", values=f, aggfunc="last")
           for f in ("open", "high", "low", "close")}
    locked = (bars.assign(_l=bars["flag_locked_bar"].astype(float))
              .pivot_table(index="ts", columns="symbol", values="_l", aggfunc="last"))
    lag = 1 if entry_at == "open" else 1        # both act on session t+1
    entry = (piv["open"] if entry_at == "open" else piv["close"]).shift(-lag)
    tradeable = entry.notna() & (locked.shift(-lag).fillna(1.0) <= 0)
    off = 0 if entry_at == "open" else 1        # close-entry exits h sessions later

    out = {"entry_open": entry, "tradeable": tradeable}
    for h in HORIZONS:
        exit_close = piv["close"].shift(-(h + off))
        out[f"fwd_{h}"] = np.log(exit_close / entry)
        out[f"mfe_{h}"] = np.log(piv["high"].rolling(h).max().shift(-(h + off)) / entry)
        out[f"mae_{h}"] = np.log(piv["low"].rolling(h).min().shift(-(h + off)) / entry)

    frames = []
    for name, mat in out.items():
        s = mat.stack(future_stack=True).rename(name)
        frames.append(s)
    df = pd.concat(frames, axis=1).reset_index()
    df.columns = ["ts", "symbol"] + list(out)
    return df


def add_paths(st: pd.DataFrame) -> pd.DataFrame:
    """Previous states per symbol — the 'formation' part: a path, not a snapshot."""
    st = st.sort_values(["symbol", "ts"], kind="mergesort").copy()
    g = st.groupby("symbol", sort=False)["state"]
    st["prev_state"] = g.shift(1)
    st["prev2_state"] = g.shift(2)
    st["path1"] = st["prev_state"].astype(str) + "→" + st["state"].astype(str)
    st["path2"] = (st["prev2_state"].astype(str) + "→" + st["prev_state"].astype(str)
                   + "→" + st["state"].astype(str))
    return st


def cohorts(st: pd.DataFrame) -> dict:
    """Mechanically enumerated. No cohort is chosen after seeing an outcome."""
    out = {}
    for s in ("CALM", "DRIFT", "DEPARTURE", "EXTREME"):
        out[f"state={s}"] = st["state"].eq(s)
        out[f"state={s} (entering)"] = st["state"].eq(s) & st["is_transition"]
        out[f"state={s} (held ≥3)"] = st["state"].eq(s) & st["state_age"].ge(3)
    for p in sorted(st["path1"].dropna().unique()):
        if "nan" in p or "None" in p:
            continue
        out[f"path1={p}"] = st["path1"].eq(p)
    for p, n in st["path2"].value_counts().items():
        if n < MIN_N or "nan" in p or "None" in p:
            continue
        out[f"path2={p}"] = st["path2"].eq(p)
    for c in ("rung1_volume_departure", "rung1_range_compression",
              "rung1_quiet_accumulation"):
        out[f"rung1={c}"] = st[c].astype(bool)
    out["ALL (base rate)"] = pd.Series(True, index=st.index)
    return out


def clustered_t(df: pd.DataFrame, col: str) -> tuple[float, int]:
    """Average within a date, then t across dates — occurrences cluster on days."""
    per_date = df.groupby("ts")[col].mean().dropna()
    if len(per_date) < 2:
        return float("nan"), len(per_date)
    sd = per_date.std(ddof=1)
    if sd == 0:
        return float("nan"), len(per_date)
    return float(per_date.mean() / (sd / np.sqrt(len(per_date)))), len(per_date)


def bh_fdr(p: np.ndarray, q: float) -> np.ndarray:
    """Benjamini–Hochberg: which p-values survive at FDR q."""
    ok = np.isfinite(p)
    idx = np.where(ok)[0]
    if len(idx) == 0:
        return np.zeros_like(p, dtype=bool)
    order = idx[np.argsort(p[idx])]
    m = len(order)
    thresh = q * (np.arange(1, m + 1) / m)
    passed = p[order] <= thresh
    keep = np.zeros_like(p, dtype=bool)
    if passed.any():
        last = np.max(np.where(passed)[0])
        keep[order[:last + 1]] = True
    return keep


def two_sided_p(t: np.ndarray) -> np.ndarray:
    """Normal approximation — adequate at these date counts, and stated as such."""
    from math import erfc, sqrt
    return np.array([erfc(abs(v) / sqrt(2)) if np.isfinite(v) else np.nan for v in t])


# --------------------------------------------------------------------------- #
def analyse_panel(st: pd.DataFrame, panel: str) -> pd.DataFrame:
    rows = []
    coh = cohorts(st)
    # Base rate per DATE per horizon: what the average tradeable name did that
    # day. A cohort only carries information if it beats THAT, on the same days.
    tradeable_all = st[st["tradeable"].fillna(False)]
    base_by_date = {h: tradeable_all.groupby("ts")[f"fwd_{h}"].mean()
                    for h in HORIZONS}
    print(f"\n  cohorts enumerated: {len(coh)} × {len(HORIZONS)} horizons "
          f"= {len(coh) * len(HORIZONS)} hypotheses")
    for name, mask in coh.items():
        sub_all = st[mask]
        n_total = len(sub_all)
        if n_total == 0:
            continue
        sub = sub_all[sub_all["tradeable"].fillna(False)]
        for h in HORIZONS:
            col = f"fwd_{h}"
            d = sub[sub[col].notna()]
            n = len(d)
            gross = d[col]
            netv = gross - C.BROKERAGE_ROUND_TRIP_VERIFIED
            t_cl, n_dates = clustered_t(d.assign(_n=netv), "_n")
            # EXCESS vs the same-day base rate, tested date-paired. This is the
            # information question: did this cohort beat the market that day?
            per_date = d.groupby("ts")[col].mean()
            paired = (per_date - base_by_date[h].reindex(per_date.index)).dropna()
            if len(paired) > 1 and paired.std(ddof=1) > 0:
                excess_mean = float(paired.mean())
                t_excess = float(paired.mean() / (paired.std(ddof=1) / np.sqrt(len(paired))))
            else:
                excess_mean, t_excess = np.nan, np.nan
            t_naive = (float(netv.mean() / (netv.std(ddof=1) / np.sqrt(n)))
                       if n > 1 and netv.std(ddof=1) > 0 else float("nan"))
            status, note = EXEC_STATUS[h]
            row = {
                "panel": panel, "cohort": name, "horizon": h,
                "exec_status": status, "exec_note": note,
                # --- the full denominator, and where it leaks ---
                "n_occurrences": n_total,
                "n_untradeable": int(n_total - len(sub)),
                "n_no_outcome": int(len(sub) - n),
                "n_measured": n, "n_dates": n_dates,
                # --- outcome distribution ---
                "gross_mean": float(gross.mean()) if n else np.nan,
                "gross_median": float(gross.median()) if n else np.nan,
                "net_verified_mean": float(netv.mean()) if n else np.nan,
                "t_clustered": t_cl, "t_naive": t_naive,
                "excess_vs_base": excess_mean, "t_excess": t_excess,
                # --- FAILED FOOTPRINTS: the share that did not pay ---
                "fail_share_gross": float((gross <= 0).mean()) if n else np.nan,
                "fail_share_net_verified": float((netv <= 0).mean()) if n else np.nan,
                "p05": float(gross.quantile(0.05)) if n else np.nan,
                "p95": float(gross.quantile(0.95)) if n else np.nan,
                "mfe_mean": float(d[f"mfe_{h}"].mean()) if n else np.nan,
                "mae_mean": float(d[f"mae_{h}"].mean()) if n else np.nan,
            }
            for add in C.ESTIMATED_ADDITIONAL_COSTS:
                if add > 0:
                    row[f"net_est_+{add:.1%}"] = float(netv.mean() - add) if n else np.nan
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="dse_eod")
    ap.add_argument("--entry", choices=["open", "close"], default="open",
                    help="open = buy next session's open (default); "
                         "close = buy next session's close, avoiding the `open` "
                         "field entirely (robustness variant)")
    a = ap.parse_args()
    paths = bio.paths()

    states = pd.read_parquet(os.path.join(paths["results"], "STATE_EVENT_LOG.parquet"))
    bars = pd.read_parquet(os.path.join(paths["results"],
                                        f"{a.tag}_bars_annotated.parquet"))
    bars = bars[~bars["qa_exclude"]]

    print("=" * 78)
    print("PHASE 4 — precursor research (FULL-SAMPLE DESCRIPTIVE; not validated)")
    print("=" * 78)
    print("saleability UNKNOWN ⇒ horizons are labelled, never excluded:")
    for h in HORIZONS:
        s, note = EXEC_STATUS[h]
        print(f"  h={h:<3} [{s:<7}] {note}")
    print(f"entry convention: {a.entry} of session t+1"
          + ("  [robustness variant — does not touch the `open` field]"
             if a.entry == "close" else ""))
    print(f"\ncosts: verified brokerage {C.BROKERAGE_ROUND_TRIP_VERIFIED:.1%} round trip; "
          f"estimate band {[f'+{x:.1%}' for x in C.ESTIMATED_ADDITIONAL_COSTS if x > 0]}; "
          "CGT and slippage UNKNOWN, not modelled")

    all_rows = []
    for panel in ("PRIMARY", "POSTBREAK"):
        stp = P.select(states, panel)
        if stp.empty:
            continue
        barp = P.select(bars, panel)
        print("\n" + "-" * 78)
        print(f"PANEL {panel}: {len(stp):,} state rows · {stp['symbol'].nunique()} symbols")
        fwd = forward_outcomes(barp, a.entry)
        stp = add_paths(stp).merge(fwd, on=["symbol", "ts"], how="left")
        res = analyse_panel(stp, panel)
        all_rows.append(res)

    res = pd.concat(all_rows, ignore_index=True)

    # Multiple testing across every hypothesis in a panel — counted, not ignored.
    res["p_clustered"] = np.nan
    for panel in res["panel"].unique():
        m = res["panel"].eq(panel)
        t = res.loc[m, "t_clustered"].to_numpy(dtype=float)
        p = two_sided_p(t)
        res.loc[m, "p_clustered"] = p
        res.loc[m, "survives_fdr10"] = bh_fdr(p, FDR_Q)
        te = res.loc[m, "t_excess"].to_numpy(dtype=float)
        pe = two_sided_p(te)
        res.loc[m, "p_excess"] = pe
        res.loc[m, "excess_survives_fdr10"] = bh_fdr(pe, FDR_Q)

    res["eligible"] = (res["n_measured"] >= MIN_N) & (res["n_dates"] >= MIN_DATES)
    suffix = "" if a.entry == "open" else "_closeentry"
    res.to_csv(os.path.join(paths["results"],
                            f"FAILED_FOOTPRINT_ANALYSIS{suffix}.csv"), index=False)

    # A "candidate" here means only: positive after VERIFIED cost, survives FDR,
    # and has enough independent dates. It is a shortlist for Phase 5, not a rule.
    cand = res[res["eligible"] & (res["net_verified_mean"] > 0)
               & res["survives_fdr10"].fillna(False)].copy()
    cand = cand.sort_values("t_clustered", ascending=False)
    cand.to_csv(os.path.join(paths["results"],
                             f"PRECURSOR_CANDIDATES{suffix}.csv"), index=False)

    print("\n" + "=" * 78)
    print(f"hypotheses tested: {len(res)} · eligible (n≥{MIN_N}, dates≥{MIN_DATES}): "
          f"{int(res['eligible'].sum())}")
    print(f"positive after VERIFIED brokerage AND surviving BH-FDR {FDR_Q:.0%}: "
          f"{len(cand)}")
    if len(cand):
        show = ["panel", "cohort", "horizon", "exec_status", "n_measured", "n_dates",
                "gross_mean", "net_verified_mean", "t_clustered",
                "fail_share_net_verified"]
        with pd.option_context("display.float_format", lambda v: f"{v:+.4f}",
                               "display.width", 200):
            print("\n" + cand[show].head(25).to_string(index=False))
    else:
        print("  → NONE. No state or transition path in either panel produced a "
              "positive expectation after verified brokerage that survives "
              "multiple-testing correction.")

    # THE ACTUAL PHASE-4 QUESTION: which transitions carry information, i.e.
    # differ from the same-day base rate by more than multiple testing explains?
    info = res[res["eligible"] & res["excess_survives_fdr10"].fillna(False)].copy()
    print(f"\ncohorts whose EXCESS vs the same-day base rate survives BH-FDR "
          f"{FDR_Q:.0%}: {len(info)} of {int(res['eligible'].sum())} eligible")
    if len(info):
        info = info.reindex(info["t_excess"].abs().sort_values(ascending=False).index)
        cols = ["panel", "cohort", "horizon", "n_measured", "n_dates",
                "gross_mean", "excess_vs_base", "t_excess", "net_verified_mean",
                "fail_share_net_verified"]
        with pd.option_context("display.float_format", lambda v: f"{v:+.4f}",
                               "display.width", 220):
            print("\n  strongest information (|t| on the date-paired excess):")
            print(info[cols].head(15).to_string(index=False))
            pos = info[info["excess_vs_base"] > 0]
            print(f"\n  of those, POSITIVE excess: {len(pos)}; "
                  f"positive AND net-of-verified-brokerage positive: "
                  f"{int((pos['net_verified_mean'] > 0).sum())}")

    # Rung 2 must beat rung 1 to justify its complexity — reported either way.
    print("\n--- rung 1 (univariate baseline) vs rung 2 (novelty states), PRIMARY ---")
    cmp = res[(res["panel"] == "PRIMARY") & res["eligible"]
              & res["cohort"].str.startswith(("rung1=", "state=", "ALL"))]
    show = ["cohort", "horizon", "n_measured", "gross_mean", "net_verified_mean",
            "t_clustered", "fail_share_net_verified"]
    with pd.option_context("display.float_format", lambda v: f"{v:+.4f}",
                           "display.width", 200):
        print(cmp[cmp["horizon"].isin((1, 3, 10))][show].to_string(index=False))

    bio.write_manifest(f"phase4_manifest{suffix}.json", {
        "phase": "4_precursor_research",
        "entry_convention_variant": a.entry,
        "status": "FULL-SAMPLE DESCRIPTIVE — no walk-forward, nothing validated",
        "horizons": list(HORIZONS),
        "execution_status_by_horizon": {str(k): v[0] for k, v in EXEC_STATUS.items()},
        "saleability": {"earliest_saleability_days": C.EARLIEST_SALEABILITY_DAYS,
                        "verified": C.SALEABILITY_VERIFIED},
        "entry_convention": "state read at close(t) ⇒ entry at open(t+1); "
                            "exit at close(t+h)",
        "inference": "t computed across DATES (occurrences cluster on days); "
                     "naive per-occurrence t also reported",
        "multiple_testing": {"hypotheses": int(len(res)), "method": "BH-FDR",
                             "q": FDR_Q,
                             "survivors": int(res["survives_fdr10"].fillna(False).sum())},
        "min_n": MIN_N, "min_dates": MIN_DATES,
        "candidates_shortlisted": int(len(cand)),
        "excess_information_survivors": int(res["excess_survives_fdr10"].fillna(False).sum()),
        "emits_orders": False,
        "outputs": ["results/FAILED_FOOTPRINT_ANALYSIS.csv",
                    "results/PRECURSOR_CANDIDATES.csv"],
    })
    print("\nwrote FAILED_FOOTPRINT_ANALYSIS.csv and PRECURSOR_CANDIDATES.csv")
    print("No state was converted into a BUY/SELL. Nothing here is validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
