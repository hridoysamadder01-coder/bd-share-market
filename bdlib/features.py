"""PHASE 2 — adaptive, self-normalised feature layer.

CAUSALITY CONTRACT (enforced by features/leakage_test.py):
  A feature value at bar t is a function of bars <= t ONLY.
  Every baseline statistic uses the STRICTLY TRAILING window [t-W, t-1], so the
  current observation never dilutes the baseline it is being judged against
  ("how unusual is this bar, given what this symbol looked like before it").
  Cross-sectional features use other symbols AT THE SAME TIMESTAMP — same-time
  information, never later.

NAMING CONTRACT:
  features never start with `fwd_`; outcome labels always do (see labels.py).
  The two sets are asserted disjoint before anything is written to disk.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

MAD_TO_SIGMA = 1.4826  # makes MAD comparable to a standard deviation for normal data


def _trailing(s: pd.Series, w: int):
    """Rolling window over [t-w, t-1] — shift(1) BEFORE rolling is the whole trick."""
    return s.shift(1).rolling(w, min_periods=max(10, w // 4))


def _robust_z(s: pd.Series, w: int, eps: float, rel_floor: float = 0.01) -> pd.Series:
    """(x_t − median of trailing window) / robust scale of that trailing window.

    Robust rather than mean/σ because DSE minute activity is heavy-tailed: one
    past spike would otherwise inflate σ and hide the next one.

    DEGENERATE BASELINES are the trap here. During a locked/halted/zero-volume
    stretch the trailing MAD is exactly 0, and dividing by ~eps produces z-scores
    of 1e12 that then poison every downstream mean, threshold and model. So:
      · the scale is floored at `rel_floor` × |trailing median| (a 1% relative
        floor — still sensitive, never explosive), and
      · when BOTH the MAD and the median are zero the baseline carries no
        information at all and the result is NaN, not a giant number.
    The NaN is deliberate: "this symbol had no measurable activity to be unusual
    against" is a fact worth propagating, not one worth papering over.
    """
    roll = _trailing(s, w)
    med = roll.median()
    mad = roll.apply(lambda a: np.nanmedian(np.abs(a - np.nanmedian(a))), raw=True)
    scale = np.maximum(MAD_TO_SIGMA * mad, rel_floor * med.abs())
    z = (s - med) / scale
    return z.where(scale > eps)


def _robust_z_log(s: pd.Series, w: int, eps: float) -> pd.Series:
    """Robust z of a MULTIPLICATIVE quantity, taken in log space.

    Volume, turnover and impact span orders of magnitude and sit against zero.
    Z-scoring their raw levels is a category error on a market like the DSE:
    an illiquid symbol whose trailing median volume is 1 share produces
    z ≈ 5·10⁶ the first day something actually trades — a number that says
    "the denominator was a floor", not "this is unusual".

    In log space the same event is a few units, comparable across a 100-share
    symbol and a 10-million-share symbol, which is what "unusual for THIS stock"
    is supposed to mean. Zero stays representable via log1p.
    """
    return _robust_z(np.log1p(s.clip(lower=0)), w, eps, rel_floor=0.05)


def _run_length_true(mask: pd.Series) -> pd.Series:
    """Consecutive-True count ENDING at each row (0 when False). Causal by construction."""
    out = np.zeros(len(mask), dtype=int)
    run = 0
    for i, v in enumerate(mask.to_numpy()):
        run = run + 1 if v else 0
        out[i] = run
    return pd.Series(out, index=mask.index)


def per_symbol_features(g: pd.DataFrame, p: C.FeatureParams) -> pd.DataFrame:
    """All single-symbol features. `g` must be one symbol, sorted by ts ascending."""
    eps = p.eps
    out = pd.DataFrame(index=g.index)

    close, high, low = g["close"], g["high"], g["low"]
    vol, turn = g["volume"], g["turnover"]

    # --- A. price / range -------------------------------------------------
    out["ret_1"] = np.log(close / close.shift(1))
    out["range_pct"] = (high - low) / close.replace(0, np.nan)
    out["close_location"] = ((close - low) / (high - low).replace(0, np.nan))
    out["gap_open"] = np.log(g["open"] / close.shift(1))

    # --- B. activity, normalised against this symbol's own trailing baseline
    out["rel_volume_z"] = _robust_z_log(vol, p.baseline_window, eps)
    out["rel_turnover_z"] = _robust_z_log(turn, p.baseline_window, eps)
    # Same degeneracy rule as activity: a symbol whose trailing daily range is
    # pinned at zero (floor era, locked stretches) has no range baseline to be
    # unusual against — real DSE data produced z = 1600 before this guard.
    med_range = _trailing(out["range_pct"], p.baseline_window).median()
    out["range_z"] = _robust_z(out["range_pct"], p.baseline_window, eps).where(
        med_range > p.min_meaningful_vol)
    out["range_compression"] = -out["range_z"]          # positive ⇒ tighter than usual

    base_med_vol = _trailing(vol, p.baseline_window).median()
    above = (vol > base_med_vol).astype(float)
    out["volume_persistence"] = above.rolling(p.short_window, min_periods=p.short_window).mean()

    vshare = vol.rolling(p.short_window, min_periods=p.short_window)
    out["activity_concentration"] = (
        vol.pow(2).rolling(p.short_window, min_periods=p.short_window).sum()
        / (vshare.sum().pow(2) + eps))        # Herfindahl of volume over the last k bars

    # --- C. volatility regime --------------------------------------------
    rv_short = out["ret_1"].rolling(p.short_window, min_periods=p.short_window).std()
    rv_long = out["ret_1"].rolling(p.vol_window, min_periods=p.vol_window).std()
    # A pinned price (floor era, locked stretches) makes rv_long exactly 0, and
    # rv_short/0 produced ratios of 4363 on real DSE data. Below the resolution
    # of the price grid the ratio is undefined, not enormous.
    out["vol_regime_ratio"] = rv_short / rv_long.where(rv_long > p.min_meaningful_vol)
    out["realized_vol"] = rv_long

    # --- D. impact / liquidity -------------------------------------------
    # Price move per unit turnover. Undefined — NOT infinite — on a bar with no
    # turnover: "impact per unit of trading" has no meaning when nothing traded.
    amihud = (out["ret_1"].abs() / turn.where(turn > 0))
    # Also multiplicative and spanning orders of magnitude — same log rule.
    out["amihud_z"] = _robust_z(np.log(amihud.where(amihud > 0)),
                                p.baseline_window, eps, rel_floor=0.05)
    out["hl_spread_proxy"] = 2.0 * (high - low) / (high + low + eps)
    out["illiquidity_persistence"] = (
        (out["amihud_z"] > p.abnormal_z).astype(float)
        .rolling(p.short_window, min_periods=p.short_window).mean())

    # --- E. divergence / accumulation proxies -----------------------------
    ret_k = np.log(close / close.shift(p.short_window))
    out["ret_k"] = ret_k
    # Abnormal volume WITHOUT a matching price move — the accumulation /
    # distribution suspicion. `quiet` is 1 when the k-bar move is small relative
    # to what this symbol's own volatility would predict, 0 when it is large.
    expected_move = out["realized_vol"] * np.sqrt(p.short_window)
    quiet = 1.0 - (ret_k.abs() / (expected_move + eps)).clip(upper=1.0)
    out["volume_price_divergence"] = out["rel_volume_z"] * quiet
    out["accumulation_proxy"] = (
        ((out["close_location"] - 0.5) * out["rel_volume_z"])
        .rolling(p.short_window, min_periods=p.short_window).mean())
    # Lag-1 autocorrelation over a trailing window, vectorised: corr(r_t, r_{t-1}).
    out["ret_autocorr_1"] = out["ret_1"].rolling(
        p.autocorr_window, min_periods=p.autocorr_window).corr(out["ret_1"].shift(1))

    # --- F. state persistence --------------------------------------------
    abnormal = (out["rel_volume_z"] > p.abnormal_z).fillna(False)
    out["abnormal_persistence"] = _run_length_true(abnormal)
    out["bars_since_abnormal"] = _run_length_true(~abnormal)

    # VALIDITY GUARD (not a signal threshold): a trailing window in which the
    # symbol barely traded carries no information about what is unusual for it.
    # Activity z-scores are withheld there rather than reported as huge numbers.
    active = (vol > 0).astype(float)
    active_days = _trailing(active, p.baseline_window).sum()
    thin = active_days < p.min_active_baseline
    for col in ("rel_volume_z", "rel_turnover_z", "amihud_z",
                "volume_price_divergence", "accumulation_proxy"):
        out[col] = out[col].where(~thin)
    out["baseline_active_days"] = active_days

    # History guard: nothing is emitted before the symbol has enough of its own past.
    out.loc[out.index[:p.min_history], :] = np.nan
    return out


def cross_sectional_features(df: pd.DataFrame, p: C.FeatureParams) -> pd.DataFrame:
    """Same-timestamp comparisons across symbols. No future information."""
    out = pd.DataFrame(index=df.index)
    by_ts = df.groupby("ts", sort=False)

    out["xs_rank_rel_volume"] = by_ts["rel_volume_z"].rank(pct=True)
    out["xs_rank_rel_turnover"] = by_ts["rel_turnover_z"].rank(pct=True)
    out["xs_volume_abnormality"] = df["rel_volume_z"] - by_ts["rel_volume_z"].transform("median")

    mkt_ret = by_ts["ret_1"].transform("median")          # median ⇒ robust market proxy
    out["market_ret"] = mkt_ret
    out["market_relative_ret"] = df["ret_1"] - mkt_ret
    out["xs_breadth_abnormal"] = by_ts["rel_volume_z"].transform(
        lambda s: (s > p.abnormal_z).mean())
    out["xs_symbols_at_ts"] = by_ts["ret_1"].transform("size")
    return out


# Unbounded z-family columns. A handful of extreme rows must not dominate a
# downstream mean or model, so these are winsorised at ±z_clip. The clipping is
# NOT hidden: run_features.py reports how many values were clipped per feature,
# and "clipped at 20" is read as "at least 20", never as "exactly 20".
Z_FAMILY = ["rel_volume_z", "rel_turnover_z", "range_z", "range_compression",
            "amihud_z", "xs_volume_abnormality", "volume_price_divergence",
            "vol_regime_ratio"]

FEATURE_COLUMNS = [
    "ret_1", "ret_k", "range_pct", "close_location", "gap_open",
    "rel_volume_z", "rel_turnover_z", "range_z", "range_compression",
    "volume_persistence", "activity_concentration",
    "vol_regime_ratio", "realized_vol",
    "amihud_z", "hl_spread_proxy", "illiquidity_persistence",
    "volume_price_divergence", "accumulation_proxy", "ret_autocorr_1",
    "abnormal_persistence", "bars_since_abnormal", "baseline_active_days",
    "xs_rank_rel_volume", "xs_rank_rel_turnover", "xs_volume_abnormality",
    "market_ret", "market_relative_ret", "xs_breadth_abnormal", "xs_symbols_at_ts",
]


def build(df: pd.DataFrame, cfg: C.Config = C.DEFAULT) -> pd.DataFrame:
    """Feature matrix for a QA-annotated frame. Excluded rows are dropped FIRST so
    untrusted observations never enter any baseline."""
    p = cfg.features
    d = df[~df.get("qa_exclude", pd.Series(False, index=df.index))].copy()
    d = d.sort_values(["symbol", "ts"], kind="mergesort")

    parts = []
    for _, g in d.groupby("symbol", sort=False):
        parts.append(per_symbol_features(g, p))
    feats = pd.concat(parts).sort_index()
    d = pd.concat([d, feats], axis=1)
    d = pd.concat([d, cross_sectional_features(d, p)], axis=1)

    d.attrs["clipped"] = {}
    for c in Z_FAMILY:
        if c in d.columns:
            n = int((d[c].abs() > p.z_clip).sum())
            if n:
                d.attrs["clipped"][c] = n
            d[c] = d[c].clip(-p.z_clip, p.z_clip)

    leaked = [c for c in FEATURE_COLUMNS if c.startswith("fwd_")]
    assert not leaked, f"outcome labels leaked into FEATURE_COLUMNS: {leaked}"
    return d
