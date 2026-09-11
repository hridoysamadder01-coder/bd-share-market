"""The daily panel for big-move discovery: features that look back, outcomes that look forward.

Target
------
Not scalping. The question is whether a measurable buildup exists BEFORE a large
multi-day move — 3, 5, 10, 20, 30 sessions out, at magnitudes from +5 % to +30 %.

Reservations
------------
Two windows never enter discovery, asserted absent rather than merely filtered:
the Phase-5 sealed holdout 2019-01-01 … 2022-07-27, and the reserved slice from
2026-01-25 onward. Dropping them leaves a **hole in every symbol's history**, and
a 20-day trailing mean computed across that hole would silently mix 2018 with
2022. Every trailing feature is therefore invalidated when its window spans a gap
longer than `MAX_GAP_DAYS` calendar days — the row becomes NOT_OBSERVABLE, which
is a different thing from zero.

Point-in-time
-------------
A decision is taken at the close of day t. Features may use everything up to and
including day t; outcomes are measured from day t forward. `rel_*` baselines
exclude day t itself, so "today's volume against its own history" is a ratio to
the past and not to a window containing the numerator.

Outcomes are paths, not endpoints
---------------------------------
Big-gain research cannot use close-to-close alone: a name that runs +20 % then
gives it all back is not the same as one that grinds up, and a signal that needs a
-15 % drawdown first is not tradeable at any horizon. So every horizon carries
forward return, maximum favourable and adverse excursion, time to each target
band, and whether the target was reached BEFORE an adverse threshold.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

EPS = 1e-12

EOD_PATH = "data/raw/dse_eod_extended.parquet"

# The EOD parquet is git-ignored (it is owner-supplied source data, not an
# artifact), so it lives in whichever clone actually holds it. Research code must
# not care which working tree it was started from.
_EOD_SEARCH = (
    EOD_PATH,
    "/home/user/bd-share-market/data/raw/dse_eod_extended.parquet",
    "/home/user/bd-discovery/data/raw/dse_eod_extended.parquet",
)


def resolve_eod(path: Optional[str] = None) -> str:
    for p in ([path] if path else []) + list(_EOD_SEARCH):
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError(f"no EOD panel found; looked in {_EOD_SEARCH}")

# Reserved. Both asserted absent after load.
SEALED_HOLDOUT = (pd.Timestamp("2019-01-01"), pd.Timestamp("2022-07-27"))
VIRGIN_SLICE_START = pd.Timestamp("2026-01-25")

# The 381 -> 88 symbol coverage break. No cross-sectional statistic may span it.
COVERAGE_BREAK = pd.Timestamp("2024-02-22")

# A trailing window whose calendar span exceeds this has jumped a reservation
# hole or a delisting gap; its features are NOT_OBSERVABLE rather than wrong.
MAX_GAP_DAYS = 30

HORIZONS = (3, 5, 10, 20, 30)
TARGET_BANDS = (0.05, 0.08, 0.10, 0.15, 0.20, 0.30)
ADVERSE_STOP = 0.08          # for "reached target before this drawdown"

BASE_WIN = 20                # trailing baseline for abnormality
LONG_WIN = 60


# ---------------------------------------------------------------------- load
def load_eod(path: Optional[str] = None) -> pd.DataFrame:
    d = pd.read_parquet(resolve_eod(path))
    d = d.rename(columns={"ts": "date"})
    d["date"] = pd.to_datetime(d["date"])
    d["symbol"] = d["symbol"].astype(str)
    d = d.sort_values(["symbol", "date"], kind="mergesort").reset_index(drop=True)
    return d


def apply_reservations(d: pd.DataFrame) -> pd.DataFrame:
    """Drop the sealed holdout and the reserved slice, then ASSERT both are gone."""
    lo, hi = SEALED_HOLDOUT
    out = d[~((d["date"] >= lo) & (d["date"] <= hi))]
    out = out[out["date"] < VIRGIN_SLICE_START]
    assert not ((out["date"] >= lo) & (out["date"] <= hi)).any(), "sealed holdout leaked in"
    assert (out["date"] < VIRGIN_SLICE_START).all(), "reserved slice leaked in"
    return out.reset_index(drop=True)


def panel_of(dates: pd.Series) -> pd.Series:
    return np.where(dates < COVERAGE_BREAK, "PRIMARY", "POSTBREAK")


# ---------------------------------------------------------------------- features
def _roll(g: pd.core.groupby.SeriesGroupBy, win: int, fn: str, shift: int = 1):
    """Trailing statistic that EXCLUDES the current row (shift first, then roll)."""
    return getattr(g.shift(shift).rolling(win, min_periods=max(5, win // 2)), fn)()


def add_features(d: pd.DataFrame) -> pd.DataFrame:
    """Everything a decision at the close of day t may legitimately use."""
    f = d.copy()
    g = f.groupby("symbol", sort=False)

    # --- gap guard: a trailing window that jumps a hole is not measurable ------
    f["prev_date"] = g["date"].shift(1)
    f["gap_days"] = (f["date"] - f["prev_date"]).dt.days
    gmax = f.groupby("symbol", sort=False)["gap_days"]
    f["max_gap_in_window"] = gmax.rolling(BASE_WIN, min_periods=1).max().reset_index(level=0, drop=True)
    f["window_clean"] = f["max_gap_in_window"].fillna(0) <= MAX_GAP_DAYS

    px = f["close"]
    vol = pd.to_numeric(f["volume"], errors="coerce")
    tno = pd.to_numeric(f["turnover"], errors="coerce")
    trd = pd.to_numeric(f.get("trade"), errors="coerce")

    gv, gt, gr, gp = (f.groupby("symbol", sort=False)[c] for c in ("volume", "turnover", "trade", "close"))

    # --- A. abnormal activity, against the share's OWN past --------------------
    base_v = _roll(gv, BASE_WIN, "median")
    base_t = _roll(gt, BASE_WIN, "median")
    base_r = _roll(gr, BASE_WIN, "median")
    f["rel_volume"] = vol / (base_v + EPS)
    f["rel_turnover"] = tno / (base_t + EPS)
    f["rel_trades"] = trd / (base_r + EPS)

    mv, sv = _roll(gv, LONG_WIN, "mean"), _roll(gv, LONG_WIN, "std")
    f["vol_z"] = (vol - mv) / (sv + EPS)
    mt, st = _roll(gt, LONG_WIN, "mean"), _roll(gt, LONG_WIN, "std")
    f["turnover_z"] = (tno - mt) / (st + EPS)

    f["vol_accel"] = f["rel_volume"] / (f.groupby("symbol", sort=False)["rel_volume"].shift(1) + EPS)
    abn = (f["rel_volume"] >= 2.0).astype(float)
    f["abn_days_5"] = f.assign(_a=abn).groupby("symbol", sort=False)["_a"] \
        .rolling(5, min_periods=1).sum().reset_index(level=0, drop=True)
    f["abn_days_prev5"] = f.groupby("symbol", sort=False)["abn_days_5"].shift(1)
    f["is_first_shock"] = (f["rel_volume"] >= 2.0) & (f["abn_days_prev5"].fillna(0) == 0)
    f["is_repeat_shock"] = (f["rel_volume"] >= 2.0) & (f["abn_days_prev5"].fillna(0) >= 1)

    # --- B. has price already moved? the PRE-MOVE vs POST-MOVE distinction -----
    for k in (1, 3, 5, 10):
        f[f"prior_{k}d_ret"] = px / (gp.shift(k) + EPS) - 1.0
    hi20 = f.groupby("symbol", sort=False)["high"].shift(1).rolling(20, min_periods=10).max() \
        .reset_index(level=0, drop=True)
    lo20 = f.groupby("symbol", sort=False)["low"].shift(1).rolling(20, min_periods=10).min() \
        .reset_index(level=0, drop=True)
    f["dist_from_20d_high"] = px / (hi20 + EPS) - 1.0
    f["dist_from_20d_low"] = px / (lo20 + EPS) - 1.0
    # A frozen share has hi20 == lo20, and dividing by EPS made the mean over
    # such rows come out as 5.7e8. Where the 20-day range is not a range, the
    # position within it is NOT_OBSERVABLE rather than enormous.
    span20 = hi20 - lo20
    f["range_pos_20d"] = np.where(span20 > 1e-6, (px - lo20) / span20, np.nan)

    # --- C. volume with a small body — the SAI shape, neutrally named ----------
    avg10 = _roll(gv, 10, "mean")
    body = (f["close"] - f["open"]).abs()
    span = (f["high"] - f["low"]).abs()
    f["body_ratio"] = body / (span + EPS)
    f["volume_compression_activity"] = (vol / (avg10 + EPS)) * (1.0 - f["body_ratio"])

    # --- D. supply-tightening proxies -----------------------------------------
    f["day_ret"] = px / (f["ycp"].replace(0, np.nan).fillna(gp.shift(1)) + EPS) - 1.0
    f["true_range_pct"] = span / (px + EPS)
    tr5 = f.groupby("symbol", sort=False)["true_range_pct"].shift(1).rolling(5, min_periods=3).mean() \
        .reset_index(level=0, drop=True)
    tr20 = f.groupby("symbol", sort=False)["true_range_pct"].shift(1).rolling(20, min_periods=10).mean() \
        .reset_index(level=0, drop=True)
    f["range_compression"] = tr5 / (tr20 + EPS)
    down = (f["day_ret"] < 0).astype(float)
    f["down_vol_share_5"] = f.assign(_dv=down * vol).groupby("symbol", sort=False)["_dv"] \
        .rolling(5, min_periods=3).sum().reset_index(level=0, drop=True) / \
        (f.assign(_v=vol).groupby("symbol", sort=False)["_v"]
         .rolling(5, min_periods=3).sum().reset_index(level=0, drop=True) + EPS)
    f["down_vol_share_5_prev"] = f.groupby("symbol", sort=False)["down_vol_share_5"].shift(1)
    f["sell_pressure_drop"] = f["down_vol_share_5_prev"] - f["down_vol_share_5"]
    lo5 = f.groupby("symbol", sort=False)["low"].shift(1).rolling(5, min_periods=3).min() \
        .reset_index(level=0, drop=True)
    f["downside_excursion_5"] = lo5 / (px + EPS) - 1.0

    # --- liquidity / price context, for bucketing and controls ----------------
    f["adv20_mn"] = _roll(gt, BASE_WIN, "mean") / 1e6
    f["price_bucket"] = pd.cut(px, [0, 20, 50, 100, 300, 1e9],
                               labels=["<20", "20-50", "50-100", "100-300", ">300"])
    f["vol20"] = f.groupby("symbol", sort=False)["day_ret"].shift(1).rolling(20, min_periods=10).std() \
        .reset_index(level=0, drop=True)

    # --- F. circuit room, from the date-specific band -------------------------
    f["circuit_pct"] = _circuit_pct(px)
    f["room_pct"] = f["circuit_pct"]
    f["at_limit_up"] = f["day_ret"] >= (f["circuit_pct"] - 0.0025)

    f["panel"] = panel_of(f["date"])
    f["year"] = f["date"].dt.year

    # Any feature built on a dirty window is unmeasurable, not wrong.
    dirty = ~f["window_clean"]
    for c in ("rel_volume", "rel_turnover", "rel_trades", "vol_z", "turnover_z", "vol_accel",
              "volume_compression_activity", "range_compression", "down_vol_share_5",
              "sell_pressure_drop", "downside_excursion_5", "dist_from_20d_high",
              "dist_from_20d_low", "range_pos_20d", "adv20_mn", "vol20"):
        f.loc[dirty, c] = np.nan
    return f


def _circuit_pct(px: pd.Series) -> pd.Series:
    """DSE's price-dependent circuit ladder, applied to the reference price.

    The bands are `bdlib.config.CIRCUIT_BANDS_UNVERIFIED` — flagged unverified in
    the config and treated the same way here. Room is an OPPORTUNITY CONSTRAINT,
    not a directional signal: a buildup with no room left cannot expand tomorrow
    whatever else is true about it.
    """
    from bdlib import config as C
    out = pd.Series(np.nan, index=px.index, dtype=float)
    prev_hi = 0.0
    for hi, pct in C.CIRCUIT_BANDS_UNVERIFIED:
        m = (px > prev_hi) & (px <= hi)
        out[m] = pct
        prev_hi = hi
    return out


# ---------------------------------------------------------------------- outcomes
def add_outcomes(d: pd.DataFrame, horizons: Sequence[int] = HORIZONS,
                 bands: Sequence[float] = TARGET_BANDS,
                 adverse: float = ADVERSE_STOP) -> pd.DataFrame:
    """Forward paths from the close of day t. Nothing here may become an input."""
    f = d.copy()
    out_cols: Dict[str, np.ndarray] = {}
    n = len(f)
    sym = f["symbol"].to_numpy()
    close = f["close"].to_numpy(dtype=float)
    high = f["high"].to_numpy(dtype=float)
    low = f["low"].to_numpy(dtype=float)

    # Row ranges per symbol (the frame is already sorted by symbol, date).
    starts = np.flatnonzero(np.r_[True, sym[1:] != sym[:-1]])
    ends = np.r_[starts[1:], n]

    hmax = max(horizons)
    for h in horizons:
        out_cols[f"fwd_ret_{h}d"] = np.full(n, np.nan)
        out_cols[f"mfe_{h}d"] = np.full(n, np.nan)
        out_cols[f"mae_{h}d"] = np.full(n, np.nan)
        out_cols[f"n_fwd_{h}d"] = np.full(n, np.nan)
    for b in bands:
        tag = f"{int(round(b * 100))}"
        out_cols[f"days_to_p{tag}"] = np.full(n, np.nan)
        for h in horizons:
            out_cols[f"hit_p{tag}_{h}d"] = np.full(n, np.nan)
            out_cols[f"clean_p{tag}_{h}d"] = np.full(n, np.nan)

    for s, e in zip(starts, ends):
        c = close[s:e]
        hi = high[s:e]
        lo = low[s:e]
        m = e - s
        for i in range(m):
            base = c[i]
            if not np.isfinite(base) or base <= 0:
                continue
            j_end = min(i + hmax, m - 1)
            if j_end <= i:
                continue
            fwd_hi = hi[i + 1:j_end + 1]
            fwd_lo = lo[i + 1:j_end + 1]
            fwd_c = c[i + 1:j_end + 1]
            run_max = np.maximum.accumulate(fwd_hi)
            run_min = np.minimum.accumulate(fwd_lo)
            for h in horizons:
                k = min(h, len(fwd_c))
                if k <= 0:
                    continue
                out_cols[f"n_fwd_{h}d"][s + i] = k
                if k == h:                       # a partial window is not an h-day outcome
                    out_cols[f"fwd_ret_{h}d"][s + i] = fwd_c[k - 1] / base - 1.0
                out_cols[f"mfe_{h}d"][s + i] = run_max[k - 1] / base - 1.0
                out_cols[f"mae_{h}d"][s + i] = run_min[k - 1] / base - 1.0
            for b in bands:
                tag = f"{int(round(b * 100))}"
                tgt = base * (1.0 + b)
                reach = np.flatnonzero(fwd_hi >= tgt)
                first = reach[0] if reach.size else None
                if first is not None:
                    out_cols[f"days_to_p{tag}"][s + i] = first + 1
                stop = base * (1.0 - adverse)
                broke = np.flatnonzero(fwd_lo <= stop)
                first_stop = broke[0] if broke.size else None
                for h in horizons:
                    k = min(h, len(fwd_c))
                    if k <= 0:
                        continue
                    hit = first is not None and first < k
                    out_cols[f"hit_p{tag}_{h}d"][s + i] = float(hit)
                    if hit:
                        clean = first_stop is None or first_stop > first
                    else:
                        clean = False
                    out_cols[f"clean_p{tag}_{h}d"][s + i] = float(clean)

    for k, v in out_cols.items():
        f[k] = v
    return f


# ---------------------------------------------------------------------- build
def build(path: Optional[str] = None, with_outcomes: bool = True) -> pd.DataFrame:
    d = apply_reservations(load_eod(path))
    f = add_features(d)
    if with_outcomes:
        f = add_outcomes(f)
    return f


def dataset_hash(d: pd.DataFrame, cols: Optional[Sequence[str]] = None) -> str:
    cols = list(cols or ["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
    have = [c for c in cols if c in d.columns]
    b = pd.util.hash_pandas_object(d[have], index=False).to_numpy().tobytes()
    return hashlib.sha256(b).hexdigest()
