"""Matched-control evaluation for big-move candidates.

Three things this file exists to prevent.

**A single control draw is not the control.** Controls are drawn with replacement
from a matched stratum, so one draw is one sample of a random quantity. An earlier
round of this project reported E1 at -2.10 pp in a morning bucket from a single
draw; twenty draws gave +4.04. Every lift here is a mean over `CONTROL_DRAWS`
draws with the spread reported beside it.

**Overlapping horizons are not independent observations.** A 30-day outcome
measured every day shares 29 days with its neighbour. Significance is therefore
computed on a per-DATE difference series with Newey-West lags set to the horizon,
never on the row count.

**A big move is not a hit rate.** A signal that reaches +10 % after a -20 %
excursion is not the same as one that grinds there, so MFE, MAE and time-to-target
travel with every result and `clean_*` requires the target BEFORE the stop.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

EPS = 1e-12
CONTROL_DRAWS = 20
BOOT_DRAWS = 400

# Strata for the matched control. A control must be plausibly the same kind of row
# as the signal on everything except the signal itself: same era, same liquidity,
# same price band, and — critically for this project — the same prior move, so a
# candidate cannot win merely by selecting names that already ran.
STRATA = ("year", "liq_bucket", "price_bucket", "prior5_bucket")


def add_strata(d: pd.DataFrame) -> pd.DataFrame:
    """Bucket the controls, and build the join key ONCE.

    The key is an integer code, not a joined string: a `astype(str).agg("|".join)`
    over 580k rows silently turned a categorical NaN into a float and crashed, and
    it cost more per candidate than the evaluation it served.
    """
    f = d.copy()
    f["liq_bucket"] = pd.qcut(f["adv20_mn"].rank(method="first"), 5,
                              labels=[f"L{i}" for i in range(5)])
    f["prior5_bucket"] = pd.cut(f["prior_5d_ret"], [-np.inf, -0.05, -0.01, 0.01, 0.05, np.inf],
                                labels=["<-5", "-5..-1", "-1..1", "1..5", ">5"])
    key = np.zeros(len(f), dtype=np.int64)
    for c in STRATA:
        # NaN factorizes to -1, which is a stratum of its own: a row whose bucket
        # is unknown may only be matched against other rows with the same unknown.
        codes, uniq = pd.factorize(f[c], use_na_sentinel=True)
        key = key * (len(uniq) + 1) + (codes + 1)
    f["strata_key"] = key
    return f


def _nw_t(x: np.ndarray, lag: int) -> float:
    """Newey-West t on a mean, lags set by the outcome horizon."""
    x = x[np.isfinite(x)]
    n = x.size
    if n < 8:
        return np.nan
    m = x.mean()
    e = x - m
    g0 = float(e @ e) / n
    s = g0
    for L in range(1, min(lag, n - 1) + 1):
        gL = float(e[L:] @ e[:-L]) / n
        s += 2.0 * (1.0 - L / (lag + 1.0)) * gL
    if s <= 0:
        return np.nan
    return float(m / np.sqrt(s / n))


def _symbol_block_bootstrap(sig: pd.DataFrame, outcome: str, draws: int,
                            rng: np.random.Generator) -> Tuple[float, float]:
    """CI on the signal rate, resampling whole SYMBOLS — the real cluster.

    Resampling symbols with replacement and pooling their rows is exactly a mean
    weighted by multinomial symbol counts, so the rows never need to be gathered:
    per-symbol sums and counts are enough, and the whole bootstrap becomes two
    matrix products.
    """
    codes, uniq = pd.factorize(sig["symbol"])
    y = sig[outcome].to_numpy(dtype=float)
    ok = np.isfinite(y)
    k = len(uniq)
    sums = np.bincount(codes[ok], weights=y[ok], minlength=k)
    cnts = np.bincount(codes[ok], minlength=k).astype(float)
    if cnts.sum() == 0:
        return np.nan, np.nan
    m = rng.multinomial(k, np.full(k, 1.0 / k), size=draws).astype(float)
    num = m @ sums
    den = m @ cnts
    vals = np.where(den > 0, num / np.maximum(den, EPS), np.nan)
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    return float(lo), float(hi)


def evaluate(d: pd.DataFrame, mask: pd.Series, outcome: str, *, horizon: int,
             name: str = "", draws: int = CONTROL_DRAWS, seed: int = 0,
             min_signal: int = 30) -> Dict[str, Any]:
    """One candidate, one outcome. Returns everything a verdict needs."""
    ok = d[outcome].notna()
    pop = d[ok]
    sig = pop[mask.reindex(pop.index).fillna(False).to_numpy()]
    n = len(sig)
    res: Dict[str, Any] = {
        "candidate": name, "outcome": outcome, "horizon": horizon,
        "n_signal": n, "n_population": int(len(pop)),
        "n_symbols": int(sig["symbol"].nunique()) if n else 0,
        "n_dates": int(sig["date"].nunique()) if n else 0,
        "n_years": int(sig["year"].nunique()) if n else 0,
    }
    if n < min_signal:
        res["status"] = "INSUFFICIENT_SAMPLE"
        return res

    y = sig[outcome].to_numpy(dtype=float)
    res["hit_rate"] = float(np.nanmean(y))
    res["base_rate_all"] = float(np.nanmean(pop[outcome].to_numpy(dtype=float)))

    # ---- matched controls, many draws ---------------------------------------
    rng = np.random.default_rng(seed)
    pop_key = pop["strata_key"].to_numpy()
    is_sig = np.zeros(len(pop), dtype=bool)
    is_sig[pop.index.get_indexer(sig.index)] = True
    pool_y = pop[outcome].to_numpy(dtype=float)[~is_sig]
    pool_key = pop_key[~is_sig]

    # Group the control pool by stratum once, as contiguous slices of a sorted
    # array. Rebuilding a dict of index arrays per candidate cost more than the
    # evaluation it served.
    order = np.argsort(pool_key, kind="stable")
    pk_sorted, py_sorted = pool_key[order], pool_y[order]
    bounds = np.flatnonzero(np.r_[True, pk_sorted[1:] != pk_sorted[:-1]]) if pk_sorted.size else np.array([], int)
    ends = np.r_[bounds[1:], pk_sorted.size] if bounds.size else np.array([], int)
    uniq_keys = pk_sorted[bounds] if bounds.size else np.array([], dtype=pool_key.dtype)

    # Every draw for every signal row in one gather. The row-by-row loop this
    # replaces cost 13 s per candidate, which at 40 candidates x 8 outcomes is an
    # hour of doing nothing but drawing random integers.
    sig_keys = pop_key[is_sig]
    starts = np.zeros(n, dtype=np.int64)
    widths = np.zeros(n, dtype=np.int64)
    if uniq_keys.size:
        pos = np.searchsorted(uniq_keys, sig_keys)
        pos_c = np.clip(pos, 0, uniq_keys.size - 1)
        found = uniq_keys[pos_c] == sig_keys        # a stratum with no control pool
        starts[found] = bounds[pos_c[found]]
        widths[found] = ends[pos_c[found]] - bounds[pos_c[found]]
    has = widths > 0
    ctrl_matched = int(has.sum())
    if ctrl_matched:
        off = (rng.random((draws, ctrl_matched)) * widths[has]).astype(np.int64)
        picks = py_sorted[starts[has] + off]                 # (draws, n_matched)
        ctrl_rates = np.nanmean(picks, axis=1)
    else:
        ctrl_rates = np.array([np.nan])
    res["control_rate_mean"] = float(np.nanmean(ctrl_rates))
    res["control_rate_sd"] = float(np.nanstd(ctrl_rates, ddof=1)) if draws > 1 else np.nan
    res["n_matched"] = ctrl_matched
    res["match_share"] = ctrl_matched / max(n, 1)
    res["lift"] = res["hit_rate"] / (res["control_rate_mean"] + EPS)
    res["excess_pp"] = 100.0 * (res["hit_rate"] - res["control_rate_mean"])

    # ---- date-clustered Newey-West on the daily signal-minus-population diff --
    by_date_sig = sig.groupby("date")[outcome].mean()
    by_date_pop = pop.groupby("date")[outcome].mean()
    diff = (by_date_sig - by_date_pop.reindex(by_date_sig.index)).to_numpy(dtype=float)
    res["t_nw_date"] = _nw_t(diff, lag=horizon)
    res["n_diff_dates"] = int(np.isfinite(diff).sum())

    # ---- symbol-block bootstrap CI on the signal rate ------------------------
    lo, hi = _symbol_block_bootstrap(sig, outcome, BOOT_DRAWS, np.random.default_rng(seed + 1))
    res["ci_lo"], res["ci_hi"] = lo, hi
    res["ci_excludes_control"] = bool(lo > res["control_rate_mean"])

    # ---- path, always ---------------------------------------------------------
    for c in (f"mfe_{horizon}d", f"mae_{horizon}d", f"fwd_ret_{horizon}d"):
        if c in sig.columns:
            res[f"median_{c}"] = float(np.nanmedian(sig[c]))
            res[f"mean_{c}"] = float(np.nanmean(sig[c]))
    if "days_to_p10" in sig.columns:
        res["median_days_to_p10"] = float(np.nanmedian(sig["days_to_p10"]))

    # ---- consistency ----------------------------------------------------------
    yr = sig.groupby("year")[outcome].mean()
    yr_pop = pop.groupby("year")[outcome].mean().reindex(yr.index)
    beats = (yr > yr_pop)
    res["years_total"] = int(len(yr))
    res["years_beat"] = int(beats.sum())
    res["year_consistency"] = float(beats.mean()) if len(yr) else np.nan

    # leave-one-symbol-out: the worst case when the best symbol is removed
    if res["n_symbols"] >= 3:
        worst = np.inf
        for s in sig["symbol"].value_counts().head(12).index:
            sub = sig[sig["symbol"] != s]
            if len(sub) >= min_signal:
                worst = min(worst, float(np.nanmean(sub[outcome])))
        res["loso_worst_rate"] = None if not np.isfinite(worst) else worst
        top = sig["symbol"].value_counts()
        res["top3_symbol_share"] = float(top.head(3).sum() / len(sig))
    return res


def status_of(r: Dict[str, Any], *, min_lift: float = 1.25, min_t: float = 2.5) -> str:
    """A verdict, with INSUFFICIENT_SAMPLE never collapsing into KILLED."""
    if r.get("status") == "INSUFFICIENT_SAMPLE":
        return "INSUFFICIENT_SAMPLE"
    if r.get("n_signal", 0) < 30 or r.get("n_symbols", 0) < 5:
        return "INSUFFICIENT_SAMPLE"
    lift = r.get("lift", np.nan)
    t = r.get("t_nw_date", np.nan)
    if not np.isfinite(lift) or not np.isfinite(t):
        return "NOT_OBSERVABLE"
    if lift >= min_lift and t >= min_t and r.get("ci_excludes_control") and \
            r.get("year_consistency", 0) >= 0.6:
        return "PROMISING"
    if lift >= 1.10 and t >= 1.5:
        return "WEAK"
    return "KILLED"
