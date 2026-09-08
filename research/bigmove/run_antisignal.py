"""Execute the twelve-step adversarial validation of `D_shallow_pullback`."""
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

from research.bigmove import antisignal as A
from research.bigmove import evaluate as EV
from research.bigmove import panel as P

OUTCOMES = [("hit_p10_20d", 20), ("hit_p20_30d", 30), ("hit_p10_10d", 10),
            ("hit_p5_5d", 5), ("hit_p10_30d", 30)]
PRIMARY = ("hit_p10_20d", 20)


def _sh(c):
    try:
        return subprocess.check_output(c.split(), text=True).strip()
    except Exception:
        return "unknown"


def ev(f, mask, outcome, h, name, strata_key_col="strata_key", seed=17, min_signal=30):
    """Evaluate against whichever strata_key is currently attached to `f`."""
    r = EV.evaluate(f, mask, outcome, horizon=h, name=name, seed=seed, min_signal=min_signal)
    r["p_nw"] = A.two_sided_p(r.get("t_nw_date", np.nan))
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel-cache", required=True)
    ap.add_argument("--run-id", default="2026-09-08-antisignal")
    ap.add_argument("--out-root", default="results/big_move_discovery")
    a = ap.parse_args(argv)
    out_dir = os.path.join(a.out_root, a.run_id)
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()

    f = pd.read_parquet(a.panel_cache)
    f = f.sort_values(["symbol", "date"], kind="mergesort").reset_index(drop=True)
    f["regime"] = A.regime_of(f["date"])
    f["corp_action_near"] = A.corp_action_flag(f)
    base_mask = A.signal_mask(f)
    f["_sig"] = base_mask
    f["episode"] = A.episode_id(f, base_mask, min_gap=20)
    print(f"panel {f.shape} · signal {int(base_mask.sum()):,} rows · "
          f"episodes {int((f.episode >= 0).sum()):,} · {time.time()-t0:.0f}s", flush=True)

    rows: List[Dict[str, Any]] = []

    # ---- 1 + 7. reproduce, then progressively harden the matching -------------
    for label, strata in [("1_original_strata", A.STRATA_ORIGINAL),
                          ("7a_plus_volatility", A.STRATA_PLUS_VOL),
                          ("7b_plus_dist20", A.STRATA_PLUS_DIST),
                          ("7c_full_matching", A.STRATA_FULL)]:
        g = EV.add_strata(f, strata)
        for outcome, h in OUTCOMES:
            r = ev(g, base_mask, outcome, h, label)
            r.update(check=label, strata=".".join(strata), unit="row")
            rows.append(r)
        print(f"  {label} done", flush=True)

    # ---- 2 + 3. independent episodes, under the hardest matching -------------
    g_full = EV.add_strata(f, A.STRATA_FULL)
    g_orig = EV.add_strata(f, A.STRATA_ORIGINAL)
    epi = f["episode"] >= 0
    for label, gg in [("2a_episodes_original_strata", g_orig),
                      ("2b_episodes_full_matching", g_full)]:
        for outcome, h in OUTCOMES:
            r = ev(gg, epi, outcome, h, label)
            r.update(check=label, strata="full" if "full" in label else "original", unit="episode")
            rows.append(r)
    ep = f[epi]
    episode_stats = {
        "n_signal_rows": int(base_mask.sum()),
        "n_episodes": int(epi.sum()),
        "overlap_inflation_x": round(float(base_mask.sum()) / max(int(epi.sum()), 1), 2),
        "episode_symbols": int(ep["symbol"].nunique()),
        "episode_years": int(ep["year"].nunique()),
        "episode_dates": int(ep["date"].nunique()),
        "median_run_length": float(base_mask.sum() / max(int(epi.sum()), 1)),
    }
    print(f"  episodes: {episode_stats}", flush=True)

    # ---- 5. regime, volatility, liquidity, price, circuit splits -------------
    for col, name in [("regime", "5a_regime"), ("vol_bucket", "5b_volatility"),
                      ("liq_bucket", "5c_liquidity"), ("price_bucket", "5d_price"),
                      ("at_limit_up", "5e_at_limit")]:
        if col not in g_full.columns:
            continue
        for val, sub_idx in g_full.groupby(col, observed=True).groups.items():
            sub = g_full.loc[sub_idx]
            m = base_mask.reindex(sub.index).fillna(False)
            if m.sum() < 100:
                continue
            r = ev(sub, m, *PRIMARY, f"{name}={val}")
            r.update(check=name, split_value=str(val), unit="row", strata="full")
            rows.append(r)
    print("  splits done", flush=True)

    # ---- 6. exclude suspected corporate-action windows -----------------------
    clean = g_full[~g_full["corp_action_near"]]
    m = base_mask.reindex(clean.index).fillna(False)
    for outcome, h in OUTCOMES:
        r = ev(clean, m, outcome, h, "6_no_corp_action_window")
        r.update(check="6_corp_action", strata="full", unit="row")
        rows.append(r)
    n_excluded = int(g_full["corp_action_near"].sum())

    # ---- 4. leave-one-year-out and leave-one-symbol-out ----------------------
    loyo = []
    for y in sorted(g_full["year"].unique()):
        sub = g_full[g_full["year"] != y]
        m = base_mask.reindex(sub.index).fillna(False)
        r = ev(sub, m, *PRIMARY, f"LOYO_drop_{y}")
        r.update(check="4a_loyo", split_value=str(y), unit="row", strata="full")
        loyo.append(r)
    rows.extend(loyo)

    top_syms = f.loc[base_mask, "symbol"].value_counts().head(20).index
    loso = []
    for s in top_syms:
        sub = g_full[g_full["symbol"] != s]
        m = base_mask.reindex(sub.index).fillna(False)
        r = ev(sub, m, *PRIMARY, f"LOSO_drop_{s}")
        r.update(check="4b_loso", split_value=str(s), unit="row", strata="full")
        loso.append(r)
    rows.extend(loso)
    print("  leave-outs done", flush=True)

    # ---- 8 + 9. the response surface, and whether it is monotone -------------
    surface = []
    for dh in (-0.20, -0.15, -0.10, -0.05, -0.02, 0.0):
        for dex in (-0.10, -0.07, -0.05, -0.03, -0.02, -0.01):
            m = A.signal_mask(f, dex_thr=dex, dh_thr=dh)
            for strata_label, gg in [("original", g_orig), ("full", g_full)]:
                r = ev(gg, m, *PRIMARY, f"surface dh>={dh} dex>={dex}")
                r.update(check="8_surface", dh_thr=dh, dex_thr=dex,
                         strata=strata_label, unit="row")
                surface.append(r)
    rows.extend(surface)
    print(f"  surface: {len(surface)} cells", flush=True)

    # ---- 10. mechanism: expansion, downside, recovery, or mean reversion -----
    mech = []
    sig = f[base_mask]
    rest = f[~base_mask]
    for col in ("mfe_20d", "mae_20d", "fwd_ret_5d", "fwd_ret_20d", "fwd_ret_30d",
                "days_to_p10", "vol20", "adv20_mn", "prior_3d_ret", "prior_5d_ret",
                "prior_10d_ret", "range_pos_20d"):
        if col not in f.columns:
            continue
        mech.append({"metric": col,
                     "signal_median": float(np.nanmedian(sig[col])),
                     "rest_median": float(np.nanmedian(rest[col])),
                     "signal_mean": float(np.nanmean(sig[col])),
                     "rest_mean": float(np.nanmean(rest[col])),
                     "signal_n": int(sig[col].notna().sum())})
    # is it mean reversion after a completed rally? split by prior 10-day return
    for lo, hi, lab in [(-np.inf, 0.0, "prior10 <= 0"), (0.0, 0.10, "prior10 0..10%"),
                        (0.10, 0.30, "prior10 10..30%"), (0.30, np.inf, "prior10 > 30%")]:
        sub = g_full[(g_full["prior_10d_ret"] > lo) & (g_full["prior_10d_ret"] <= hi)]
        m = base_mask.reindex(sub.index).fillna(False)
        if m.sum() < 100:
            continue
        r = ev(sub, m, *PRIMARY, f"10_after_rally {lab}")
        r.update(check="10_mean_reversion", split_value=lab, unit="row", strata="full")
        rows.append(r)

    res = pd.DataFrame(rows)

    # ---- 11. BH correction over the focused surface --------------------------
    surf = res[res.check == "8_surface"].copy()
    for lab in ("original", "full"):
        sel = surf.strata == lab
        rej, pmax = A.benjamini_hochberg(surf.loc[sel, "p_nw"].to_numpy(), q=0.10)
        surf.loc[sel, "bh_reject_q10"] = rej
        surf.loc[sel, "bh_p_threshold"] = pmax
    res = pd.concat([res[res.check != "8_surface"], surf], ignore_index=True)

    res.to_csv(os.path.join(out_dir, "ANTISIGNAL_RESULTS.csv"), index=False)
    pd.DataFrame(mech).to_csv(os.path.join(out_dir, "MECHANISM.csv"), index=False)
    surf.to_csv(os.path.join(out_dir, "RESPONSE_SURFACE.csv"), index=False)

    manifest = {
        "run_id": a.run_id, "target": "D_shallow_pullback",
        "definition": {"downside_excursion_5 >=": A.DEX_THRESHOLD,
                       "dist_from_20d_high >=": A.DH_THRESHOLD},
        "branch": _sh("git rev-parse --abbrev-ref HEAD"),
        "commit": _sh("git rev-parse HEAD"),
        "parent_commit": _sh("git rev-parse HEAD~1"),
        "panel_cache": a.panel_cache,
        "rows": int(len(f)), "symbols": int(f.symbol.nunique()),
        "date_min": str(f.date.min().date()), "date_max": str(f.date.max().date()),
        "episode_stats": episode_stats,
        "corp_action_rows_excluded": n_excluded,
        "strata_sets": {"original": list(A.STRATA_ORIGINAL), "full": list(A.STRATA_FULL)},
        "outcomes": [o for o, _ in OUTCOMES],
        "surface_cells": int(len(surf)),
        "control_draws": EV.CONTROL_DRAWS, "bootstrap_draws": EV.BOOT_DRAWS,
        "command": "python3 -m research.bigmove.run_antisignal",
        "elapsed_s": round(time.time() - t0, 1),
    }
    with open(os.path.join(out_dir, "MANIFEST.json"), "w") as fh:
        json.dump(manifest, fh, indent=1, default=str)
    print(json.dumps({"out": out_dir, "checks": int(res.check.nunique()),
                      "rows": int(len(res))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
