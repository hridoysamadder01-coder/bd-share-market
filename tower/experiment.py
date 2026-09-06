"""tower.experiment — reusable mechanism-evaluation tooling over a state store.

Input: a state store written by ``tower.replay`` / ``tower.live``
(``<store>/states/<SYMBOL>.jsonl``, one ``MarketState.to_dict()`` per line).

Pipeline (all deterministic, all causal — outcomes are read strictly *after*
the signal row, signals strictly at or before it):

    load_store            one DataFrame row per state; mechanisms flattened to
                          ``mech_<name>_score / _state / _direction``; ``circuit.*``
                          scalars to ``circuit_<key>``; the simple baselines
                          (imb_l1, imb_topk, imb_weighted, depth_ratio,
                          price_only_response, volume_only_response) copied out.
    add_forward_outcomes  mid change in ticks over each horizon h using the state
                          series' own time index: the LAST state at or before t+h
                          (with a non-None mid). The outcome is None — never 0 —
                          when no later state exists inside (t, t+h] or the series
                          ends before t+h (the window is not complete).
    add_exclusions        every row counts; per-row exclusion reasons (any and a
                          primary one in fixed priority order) and ``eligible``.
    denominator           the DENOMINATOR table: total rows, per reason (any /
                          primary-exclusive), eligible — the exclusive counts plus
                          eligible sum to the total.
    assign_splits         chronological dev/val/holdout per symbol (40/30/30 by
                          row order) and the matching keys (symbol × time-of-day
                          quintile × spread bucket).
    run_mechanism         per mechanism: the signal is state ∈ {active, confirmed}
                          oriented by the mechanism's dominant evidence direction
                          (+1 → P(up), −1 → P(down), undirected → P(|Δmid| > 0));
                          a graded variant (score ≥ 0.6); the mirror (opposite
                          direction rows vs the opposite outcome); lift vs matched
                          controls and vs the base rate; incremental lift vs every
                          simple baseline; then the falsification battery on the
                          holdout at the primary horizon: block-bootstrap CI of
                          the incremental lift over the best baseline (blocks of
                          20 rows per symbol), timestamp permutation p (circular
                          shift within symbol), side flip, anchor-shift placebo
                          (signal taken from t − h) and leak control (t + h),
                          largest-wall removal (imbalance baselines recomputed from
                          the stored bids/asks without the largest displayed
                          level), leave-one-symbol-out, liquidity-regime split
                          (symbol day_volume median).
    bh_fdr                Benjamini–Hochberg across mechanisms on the permutation
                          p-values (same algorithm as experiments/phase45_footprints.py).
    decide                KEEP / KILL / BLOCKED per mechanism (VERDICT_RULE below).

Outputs: ``MECHANISM_RESULTS.csv``, ``DENOMINATOR.json``, ``FALSIFICATION.csv``,
``VERDICTS.json``, ``MANIFEST.json``.  CLI:

    python3 -m tower.experiment --store DIR --out DIR [--horizon 180]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import zlib
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------- constants / config
EXCLUSION_REASONS: Tuple[str, ...] = ("no_book", "crossed_locked", "stale_book", "duplicate",
                                      "no_forward_outcome", "outside_continuous_session", "circuit_locked")
BASELINES: Tuple[str, ...] = ("imb_l1", "imb_topk", "imb_weighted", "depth_ratio",
                              "price_only_response", "volume_only_response")
IMBALANCE_BASELINES: Tuple[str, ...] = ("imb_l1", "imb_topk", "imb_weighted", "depth_ratio")
ACTIVE_STATES: Tuple[str, ...] = ("active", "confirmed")
CONTINUOUS_PHASE = "CONTINUOUS"
SPLITS: Tuple[str, ...] = ("dev", "val", "holdout")
OUTCOMES: Tuple[str, ...] = ("up", "down", "move")
OPPOSITE = {"up": "down", "down": "up", "move": "move"}
EPS = 1e-9

SCALAR_FIELDS: Tuple[str, ...] = (
    "best_bid", "best_ask", "bid_qty1", "ask_qty1", "spread", "spread_ticks", "mid", "microprice", "ltp",
    "tick_size", "book_age_s", "imb_l1", "imb_topk", "imb_weighted", "visible_bid_liq", "visible_ask_liq",
    "depth_ratio", "price_only_response", "volume_only_response", "trade_count", "trade_volume", "trade_value",
    "interval_volume", "signed_flow_window", "ofi_window", "pressure_direction", "pressure_strength",
    "book_pressure", "trade_pressure", "combined_pressure", "price_velocity", "tape_age_s")
BOOL_FIELDS: Tuple[str, ...] = ("crossed", "locked", "one_sided", "empty_book")

VERDICT_RULE = """
Per mechanism, on the HOLDOUT split of the ELIGIBLE rows at the primary horizon h:
BLOCKED  if the eligible table has fewer than n_min_rows rows, or the holdout has fewer than
         n_min_episodes distinct signal episodes (the denominator is too small to decide).
KEEP     if all of the following hold:
           (a) lift(mechanism) − lift(best simple baseline) > 0 with block-bootstrap 95 % CI lower bound > 0;
           (b) timestamp-permutation p < alpha AND the mechanism survives Benjamini–Hochberg FDR (q) across
               all mechanisms tested in the same run;
           (c) side flip: the mirrored signal (opposite evidence direction) predicts the opposite outcome
               (its lift > 0) — not applicable (skipped) for a mechanism that never carries a direction;
           (d) anchor-shift placebo (signal taken from t − h) shows |lift| < ½ of the real lift;
           (e) the sign of the incremental lift is preserved after largest-wall removal (imbalance baselines
               recomputed without the largest displayed level);
           (f) leave-one-symbol-out: the incremental lift stays > 0 for every left-out symbol;
           (g) the incremental lift is > 0 in both liquidity halves (symbol day_volume median).
KILL     otherwise. The hypothesis is not protected: failing (a) alone kills it; a check that cannot be
         evaluated (no rows) counts as failed, except the not-applicable case in (c).
"""


@dataclass(frozen=True)
class ExperimentConfig:
    horizons: Tuple[int, ...] = (60, 180, 600)   # seconds
    primary_h: int = 180
    dev_frac: float = 0.40
    val_frac: float = 0.30
    block_len: int = 20            # block bootstrap block (rows, per symbol)
    n_boot: int = 500
    n_perm: int = 300
    seed: int = 7
    n_min_episodes: int = 30       # distinct holdout episodes to decide at all
    n_min_rows: int = 500          # eligible rows to run the test at all
    alpha: float = 0.05
    fdr_q: float = 0.10
    score_threshold: float = 0.60  # graded-signal variant
    theta_imb: float = 0.20        # imbalance baselines count as a signal beyond ±theta
    n_tod_buckets: int = 5
    stale_age_s: float = 120.0     # book older than this counts as stale when the source status is absent
    topk: int = 5                  # displayed levels used by the wall-free recomputation

    def describe(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_CONFIG = ExperimentConfig()


# ---------------------------------------------------------------------------- helpers
def _num(x: Any) -> float:
    """Loose numeric coercion: None / non-numeric → NaN (never a silent 0)."""
    if x is None or isinstance(x, (dict, list, tuple)):
        return float("nan")
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, (int, float)):
        return float(x) if not (isinstance(x, float) and math.isnan(x)) else float("nan")
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def _levels(x: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for lv in x or []:
        try:
            p, q = float(lv[0]), float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isnan(p) or math.isnan(q):
            continue
        out.append((p, q))
    return out


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _finite(x: Any) -> bool:
    try:
        return bool(np.isfinite(float(x)))
    except (TypeError, ValueError):
        return False


def _f(x: Any) -> Optional[float]:
    return float(x) if _finite(x) else None


# ---------------------------------------------------------------------------- loading
def _flatten_state(d: Dict[str, Any], families: Dict[str, str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"symbol": d.get("symbol"), "t": d.get("t"), "seq": _num(d.get("seq")),
                           "session_phase": d.get("session_phase") or "CLOSED",
                           "book_source": d.get("book_source")}
    for k in SCALAR_FIELDS:
        row[k] = _num(d.get(k))
    for k in BOOL_FIELDS:
        row[k] = bool(d.get(k, False))
    row["bids"] = _levels(d.get("bids"))
    row["asks"] = _levels(d.get("asks"))
    for k, v in (d.get("circuit") or {}).items():
        if v is None or isinstance(v, (bool, int, float, str)):
            row[f"circuit_{k}"] = v
    quote = ((d.get("session_state") or {}).get("quote") or {})
    row["day_volume"] = _num(quote.get("day_volume"))
    row["day_value_mn"] = _num(quote.get("day_value_mn"))
    src = d.get("book_source")
    st = (d.get("sources") or {}).get(src) if src else None
    row["book_src_duplicate"] = (bool(st.get("duplicate")) if isinstance(st, dict) else None)
    row["book_src_stale"] = (bool(st.get("stale")) if isinstance(st, dict) else None)
    for name, m in (d.get("mechanisms") or {}).items():
        if not isinstance(m, dict):
            continue
        row[f"mech_{name}_score"] = _num(m.get("score"))
        row[f"mech_{name}_state"] = m.get("state") or "inactive"
        row[f"mech_{name}_direction"] = _num((m.get("evidence") or {}).get("direction"))
        families.setdefault(name, m.get("family") or "")
    content = [row["bids"], row["asks"], d.get("ltp"), d.get("trade_count"), d.get("trade_volume")]
    row["content_key"] = hashlib.sha1(json.dumps(content, default=str).encode()).hexdigest()
    return row


def load_store(store_dir: str, symbols: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """One row per state from ``<store>/states/<SYMBOL>.jsonl``, sorted by (symbol, t, seq).
    ``df.attrs['mech_families']`` maps mechanism → family; ``df.attrs['files']`` the paths read."""
    files = sorted(glob.glob(os.path.join(store_dir, "states", "*.jsonl")))
    want = {s.upper() for s in symbols} if symbols else None
    rows: List[Dict[str, Any]] = []
    families: Dict[str, str] = {}
    used: List[str] = []
    for path in files:
        sym = os.path.basename(path)[:-6]
        if want is not None and sym.upper() not in want:
            continue
        used.append(path)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if not d.get("symbol"):
                    d["symbol"] = sym
                rows.append(_flatten_state(d, families))
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame({"symbol": pd.Series(dtype=object), "t": pd.Series(dtype="datetime64[ns, UTC]")})
    else:
        df["t"] = pd.to_datetime(df["t"], utc=True)
        df = df.sort_values(["symbol", "t", "seq"], kind="mergesort").reset_index(drop=True)
        for k in SCALAR_FIELDS:
            if k not in df:
                df[k] = np.nan
        for k in BOOL_FIELDS:
            if k not in df:
                df[k] = False
    df.attrs["mech_families"] = families
    df.attrs["files"] = used
    return df


def mechanism_names(df: pd.DataFrame) -> List[str]:
    return sorted(c[len("mech_"):-len("_state")] for c in df.columns if c.startswith("mech_") and c.endswith("_state"))


# ---------------------------------------------------------------------------- forward outcomes
def _effective_tick(df: pd.DataFrame) -> pd.Series:
    """tick_size, else circuit.tick, else the symbol's modal tick — NaN when nothing is known."""
    tick = df["tick_size"].astype(float).copy()
    if "circuit_tick" in df:
        tick = tick.fillna(pd.to_numeric(df["circuit_tick"], errors="coerce"))

    def _fill(s: pd.Series) -> pd.Series:
        if s.notna().any():
            return s.fillna(float(s.mode().iloc[0]))
        return s
    return tick.groupby(df["symbol"]).transform(_fill)


def add_forward_outcomes(df: pd.DataFrame, horizons: Iterable[int]) -> pd.DataFrame:
    """Rule: for row i at time t and horizon h, the forward mid is the mid of the LAST state of
    the same symbol at or before t + h that carries a non-None mid; the outcome exists only when
    that state is strictly later than row i and the symbol's series reaches t + h (complete window).
    ``fwd_mid_ticks_h{h}`` = (fwd_mid − mid) / tick; ``fwd_up`` (> 0), ``fwd_down`` (< 0),
    ``fwd_move`` (≠ 0) and ``fwd_valid``."""
    out = df.copy()
    horizons = list(horizons)
    n_total = len(out)
    cols: Dict[str, np.ndarray] = {}
    for h in horizons:
        cols[f"fwd_mid_ticks_h{h}"] = np.full(n_total, np.nan)
        cols[f"fwd_valid_h{h}"] = np.zeros(n_total, dtype=bool)
    if n_total:
        tick_all = _effective_tick(out).values.astype(float)
        out["tick_eff"] = tick_all
        for _, g in out.groupby("symbol", sort=False):
            pos = g.index.values
            n = len(pos)
            ts = g["t"].values.astype("datetime64[ns]").astype(np.int64)
            mid = g["mid"].values.astype(float)
            tk = tick_all[pos]
            has = ~np.isnan(mid)
            lv = np.maximum.accumulate(np.where(has, np.arange(n), -1))
            i_arr = np.arange(n)
            for h in horizons:
                target = ts + int(h) * 1_000_000_000
                j = np.searchsorted(ts, target, side="right") - 1
                jj = np.where(j >= 0, lv[np.clip(j, 0, n - 1)], -1)
                complete = target <= ts[-1]
                valid = (jj > i_arr) & complete & has & ~np.isnan(tk) & (tk > 0)
                fwd_mid = mid[np.clip(jj, 0, n - 1)]
                ticks = np.where(valid, (fwd_mid - mid) / np.where(tk > 0, tk, np.nan), np.nan)
                cols[f"fwd_mid_ticks_h{h}"][pos] = np.round(ticks, 6)
                cols[f"fwd_valid_h{h}"][pos] = valid
    else:
        out["tick_eff"] = np.nan
    for h in horizons:
        t_ = cols[f"fwd_mid_ticks_h{h}"]
        v = cols[f"fwd_valid_h{h}"]
        out[f"fwd_mid_ticks_h{h}"] = t_
        out[f"fwd_valid_h{h}"] = v
        out[f"fwd_up_h{h}"] = v & (t_ > EPS)
        out[f"fwd_down_h{h}"] = v & (t_ < -EPS)
        out[f"fwd_move_h{h}"] = v & (np.abs(t_) > EPS)
    return out


# ---------------------------------------------------------------------------- exclusions / denominator
def add_exclusions(df: pd.DataFrame, cfg: ExperimentConfig = DEFAULT_CONFIG, h: Optional[int] = None) -> pd.DataFrame:
    """Per-row exclusion flags ``excl_<reason>``, ``exclusion_reasons`` ("a|b"), ``primary_exclusion``
    (first reason in EXCLUSION_REASONS order) and ``eligible``.
      no_book                    empty_book or mid None
      crossed_locked             crossed or locked book
      stale_book                 the book source's status says stale, or book_age_s > stale_age_s
      duplicate                  the book source's status says duplicate payload, or the row's observable
                                 content (bids, asks, ltp, day trades/volume) is identical to the previous row
      no_forward_outcome         no complete forward window at the primary horizon
      outside_continuous_session session_phase ≠ CONTINUOUS
      circuit_locked             circuit.locked_up or circuit.locked_down"""
    h = cfg.primary_h if h is None else h
    out = df.copy()
    n = len(out)
    if n == 0:
        for r in EXCLUSION_REASONS:
            out[f"excl_{r}"] = pd.Series(dtype=bool)
        out["exclusion_reasons"], out["primary_exclusion"], out["eligible"] = "", "", pd.Series(dtype=bool)
        return out
    flags: Dict[str, np.ndarray] = {}
    flags["no_book"] = (out["empty_book"].astype(bool) | out["mid"].isna()).values
    flags["crossed_locked"] = (out["crossed"].astype(bool) | out["locked"].astype(bool)).values
    src_stale = out["book_src_stale"].map(lambda v: bool(v) if v is not None and v == v else False) if "book_src_stale" in out else pd.Series(False, index=out.index)
    age = out["book_age_s"].astype(float)
    flags["stale_book"] = (src_stale.astype(bool) | (age > cfg.stale_age_s).fillna(False)).values
    src_dup = out["book_src_duplicate"].map(lambda v: bool(v) if v is not None and v == v else False) if "book_src_duplicate" in out else pd.Series(False, index=out.index)
    prev_key = out.groupby("symbol")["content_key"].shift(1) if "content_key" in out else pd.Series(None, index=out.index)
    same = (out["content_key"] == prev_key).fillna(False) if "content_key" in out else pd.Series(False, index=out.index)
    flags["duplicate"] = (src_dup.astype(bool) | same.astype(bool)).values
    fv = out[f"fwd_valid_h{h}"] if f"fwd_valid_h{h}" in out else pd.Series(False, index=out.index)
    flags["no_forward_outcome"] = (~fv.astype(bool)).values
    flags["outside_continuous_session"] = (out["session_phase"].astype(str) != CONTINUOUS_PHASE).values
    lu = out["circuit_locked_up"].map(lambda v: bool(v) if isinstance(v, (bool, int, float)) and v == v else False) if "circuit_locked_up" in out else pd.Series(False, index=out.index)
    ld = out["circuit_locked_down"].map(lambda v: bool(v) if isinstance(v, (bool, int, float)) and v == v else False) if "circuit_locked_down" in out else pd.Series(False, index=out.index)
    flags["circuit_locked"] = (lu.astype(bool) | ld.astype(bool)).values
    any_ = np.zeros(n, dtype=bool)
    primary = np.full(n, "", dtype=object)
    reasons = [[] for _ in range(n)]
    for r in EXCLUSION_REASONS:
        f = flags[r].astype(bool)
        out[f"excl_{r}"] = f
        primary = np.where((primary == "") & f, r, primary)
        any_ |= f
        for i in np.flatnonzero(f):
            reasons[i].append(r)
    out["exclusion_reasons"] = ["|".join(x) for x in reasons]
    out["primary_exclusion"] = primary
    out["eligible"] = ~any_
    return out


def denominator(df: pd.DataFrame, cfg: ExperimentConfig = DEFAULT_CONFIG, h: Optional[int] = None) -> Dict[str, Any]:
    """The full denominator: every row, every symbol, every reason. ``per_reason_primary`` is exclusive
    (a row counts once, under its first reason) so ``eligible + Σ per_reason_primary == total_rows``;
    ``per_reason_any`` counts a row under every reason that applies."""
    h = cfg.primary_h if h is None else h
    n = int(len(df))
    out: Dict[str, Any] = {"total_rows": n, "horizon_s": int(h), "n_symbols": int(df["symbol"].nunique()) if n else 0,
                           "rows_per_symbol": {str(k): int(v) for k, v in df.groupby("symbol").size().items()} if n else {},
                           "per_reason_any": {r: int(df[f"excl_{r}"].sum()) if f"excl_{r}" in df else 0 for r in EXCLUSION_REASONS},
                           "per_reason_primary": {r: int((df["primary_exclusion"] == r).sum()) if "primary_exclusion" in df else 0
                                                  for r in EXCLUSION_REASONS},
                           "eligible": int(df["eligible"].sum()) if "eligible" in df and n else 0}
    out["sum_check"] = bool(out["eligible"] + sum(out["per_reason_primary"].values()) == n)
    if n:
        elig = df[df["eligible"]] if "eligible" in df else df.iloc[0:0]
        out["eligible_per_symbol"] = {str(k): int(v) for k, v in elig.groupby("symbol").size().items()}
        if "split" in df:
            out["rows_per_split"] = {str(k): int(v) for k, v in df["split"].value_counts().items()}
            out["eligible_per_split"] = {str(k): int(v) for k, v in elig["split"].value_counts().items()}
        out["phase_rows"] = {str(k): int(v) for k, v in df["session_phase"].value_counts().items()}
        out["t_range"] = [str(df["t"].min()), str(df["t"].max())]
        out["valid_outcomes_per_horizon"] = {}
        out["base_rates_eligible"] = {}
        for c in [c for c in df.columns if c.startswith("fwd_valid_h")]:
            hh = c[len("fwd_valid_h"):]
            out["valid_outcomes_per_horizon"][hh] = int(df[c].sum())
            ev = elig[elig[c]] if len(elig) else elig
            out["base_rates_eligible"][hh] = {
                "n": int(len(ev)),
                "p_up": _f(ev[f"fwd_up_h{hh}"].mean()) if len(ev) else None,
                "p_down": _f(ev[f"fwd_down_h{hh}"].mean()) if len(ev) else None,
                "p_move": _f(ev[f"fwd_move_h{hh}"].mean()) if len(ev) else None,
                "mean_ticks": _f(ev[f"fwd_mid_ticks_h{hh}"].mean()) if len(ev) else None}
        out["mechanism_active_rows"] = {m: int(df[f"mech_{m}_state"].isin(ACTIVE_STATES).sum()) for m in mechanism_names(df)}
        out["mechanism_active_rows_eligible"] = {m: int(elig[f"mech_{m}_state"].isin(ACTIVE_STATES).sum()) for m in mechanism_names(df)} if len(elig) else {}
    return out


# ---------------------------------------------------------------------------- splits / matching keys
def assign_splits(df: pd.DataFrame, cfg: ExperimentConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Chronological per-symbol dev/val/holdout by row order (dev_frac / val_frac / rest) over ALL rows
    (so the split is a time partition), plus the matched-control keys: ``tod_bucket`` = quintile of the
    row's time-of-day rank within the symbol, ``spread_bucket`` = spread in ticks clipped to 3 (−1 unknown)."""
    out = df.copy()
    n = len(out)
    out["split"] = "holdout"
    if n == 0:
        out["tod_bucket"], out["spread_bucket"] = pd.Series(dtype=int), pd.Series(dtype=int)
        return out
    for _, g in out.groupby("symbol", sort=False):
        order = g.sort_values(["t", "seq"], kind="mergesort").index
        m = len(order)
        k_dev = int(math.floor(cfg.dev_frac * m))
        k_val = int(math.floor((cfg.dev_frac + cfg.val_frac) * m))
        out.loc[order[:k_dev], "split"] = "dev"
        out.loc[order[k_dev:k_val], "split"] = "val"
        out.loc[order[k_val:], "split"] = "holdout"
    tod = (out["t"].dt.hour * 3600 + out["t"].dt.minute * 60 + out["t"].dt.second).astype(float)
    rank = tod.groupby(out["symbol"]).rank(pct=True, method="first")
    out["tod_bucket"] = rank.mul(cfg.n_tod_buckets).clip(upper=cfg.n_tod_buckets - 1e-9).astype(int)
    out["spread_bucket"] = out["spread_ticks"].astype(float).clip(upper=3).fillna(-1).round().astype(int)
    return out


def prepare(df: pd.DataFrame, cfg: ExperimentConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """load_store output → outcomes + exclusions + splits (the analysis table)."""
    return assign_splits(add_exclusions(add_forward_outcomes(df, cfg.horizons), cfg, cfg.primary_h), cfg)


# ---------------------------------------------------------------------------- baselines
def wall_free_geometry(df: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """Recompute imb_l1 / imb_topk / imb_weighted / depth_ratio from the stored displayed levels after
    removing the single largest level (by qty) from whichever side holds it — the largest-wall removal
    falsification (same rule as seeing.features.micro.largest_wall_removed_imbalances). Weighted
    imbalance uses weights 1/(1 + distance from the (wall-free) touch in ticks); NaN when a side is empty."""
    ticks = df["tick_eff"].values.astype(float) if "tick_eff" in df else np.full(len(df), np.nan)
    rows = []
    for bids, asks, tk in zip(df["bids"], df["asks"], ticks):
        b = list(bids or [])[:k]
        a = list(asks or [])[:k]
        allv = [("b", i, q) for i, (_, q) in enumerate(b)] + [("a", i, q) for i, (_, q) in enumerate(a)]
        if allv:
            side, i, _ = max(allv, key=lambda x: x[2])
            if side == "b":
                b = b[:i] + b[i + 1:]
            else:
                a = a[:i] + a[i + 1:]
        bd, ad = sum(q for _, q in b), sum(q for _, q in a)
        if not b or not a or (bd + ad) <= 0:
            rows.append((np.nan, np.nan, np.nan, np.nan))
            continue
        bq1, aq1 = b[0][1], a[0][1]
        l1 = (bq1 - aq1) / (bq1 + aq1) if (bq1 + aq1) > 0 else np.nan
        topk = (bd - ad) / (bd + ad)
        if _finite(tk) and tk > 0:
            wb = sum(q / (1.0 + abs(p - b[0][0]) / tk) for p, q in b)
            wa = sum(q / (1.0 + abs(p - a[0][0]) / tk) for p, q in a)
        else:
            wb = sum(q / (1.0 + i) for i, (_, q) in enumerate(b))
            wa = sum(q / (1.0 + i) for i, (_, q) in enumerate(a))
        wimb = (wb - wa) / (wb + wa) if (wb + wa) > 0 else np.nan
        rows.append((l1, topk, wimb, bd / (bd + ad)))
    return pd.DataFrame(rows, index=df.index, columns=["imb_l1_wf", "imb_topk_wf", "imb_weighted_wf", "depth_ratio_wf"])


def baseline_signal(df: pd.DataFrame, name: str, outcome: str, cfg: ExperimentConfig = DEFAULT_CONFIG,
                    col: Optional[str] = None, scope: Optional[pd.Series] = None) -> pd.Series:
    """Boolean simple-baseline signal oriented to the outcome. Rules:
      imb_l1 / imb_topk / imb_weighted:  up: x ≥ θ; down: x ≤ −θ; move: |x| ≥ θ
      depth_ratio (bid share):           centred as 2·x − 1, then the same θ rule
      price_only_response (ticks):       up: x > 0; down: x < 0; move: x ≠ 0   (momentum)
      volume_only_response:              x ≥ the symbol's median over ``scope`` rows (activity; undirected)
    NaN → False (a baseline that cannot be computed never fires)."""
    x = pd.to_numeric(df[col or name], errors="coerce").astype(float)
    th = cfg.theta_imb
    if name in ("imb_l1", "imb_topk", "imb_weighted", "depth_ratio"):
        if name == "depth_ratio":
            x = 2.0 * x - 1.0
        if outcome == "up":
            s = x >= th
        elif outcome == "down":
            s = x <= -th
        else:
            s = x.abs() >= th
    elif name == "price_only_response":
        if outcome == "up":
            s = x > EPS
        elif outcome == "down":
            s = x < -EPS
        else:
            s = x.abs() > EPS
    elif name == "volume_only_response":
        base = x[scope.astype(bool)] if scope is not None else x
        med = base.groupby(df.loc[base.index, "symbol"]).median()
        s = x >= df["symbol"].map(med).astype(float)
    else:
        raise ValueError(f"unknown baseline {name}")
    return s.fillna(False).astype(bool)


def baseline_signals(df: pd.DataFrame, outcome: str, cfg: ExperimentConfig = DEFAULT_CONFIG,
                     wall_free: bool = False) -> Dict[str, pd.Series]:
    scope = df["eligible"] if "eligible" in df else None
    wf = wall_free_geometry(df, cfg.topk) if wall_free else None
    out: Dict[str, pd.Series] = {}
    for b in BASELINES:
        if wall_free and b in IMBALANCE_BASELINES:
            tmp = df[["symbol"]].copy()
            tmp[b] = wf[f"{b}_wf"].values
            out[b] = baseline_signal(tmp, b, outcome, cfg)
        else:
            out[b] = baseline_signal(df, b, outcome, cfg, scope=scope)
    return out


# ---------------------------------------------------------------------------- mechanism signals
def mechanism_signals(df: pd.DataFrame, name: str, cfg: ExperimentConfig = DEFAULT_CONFIG) -> Dict[str, Any]:
    """Orientation rule: ``active`` = state ∈ {active, confirmed}. The dominant direction is the sign
    (+1 / −1) with more active rows; the primary signal is active ∧ direction == dominant and its outcome
    is P(up) for +1, P(down) for −1; the mirror is active ∧ direction == −dominant against the opposite
    outcome. A mechanism whose active rows never carry a ±1 direction is undirected: signal = active,
    outcome = P(|Δmid| > 0), mirror not applicable. ``score`` = score ≥ score_threshold with the same
    orientation."""
    state = df[f"mech_{name}_state"].astype(str)
    score = pd.to_numeric(df[f"mech_{name}_score"], errors="coerce").astype(float)
    direction = pd.to_numeric(df[f"mech_{name}_direction"], errors="coerce").astype(float) if f"mech_{name}_direction" in df else pd.Series(np.nan, index=df.index)
    active = state.isin(ACTIVE_STATES)
    graded = score >= cfg.score_threshold
    n_pos = int((active & (direction == 1)).sum())
    n_neg = int((active & (direction == -1)).sum())
    if n_pos == 0 and n_neg == 0:
        return {"primary_direction": 0, "outcome": "move", "state": active.astype(bool), "score": graded.fillna(False).astype(bool),
                "mirror": None, "mirror_outcome": None, "n_dir_pos": n_pos, "n_dir_neg": n_neg}
    dom = 1 if n_pos >= n_neg else -1
    outcome = "up" if dom == 1 else "down"
    return {"primary_direction": dom, "outcome": outcome,
            "state": (active & (direction == dom)).astype(bool),
            "score": (graded & (direction == dom)).fillna(False).astype(bool),
            "mirror": (active & (direction == -dom)).astype(bool), "mirror_outcome": OPPOSITE[outcome],
            "n_dir_pos": n_pos, "n_dir_neg": n_neg}


# ---------------------------------------------------------------------------- statistics
def _lift_arr(sig: np.ndarray, out: np.ndarray, valid: np.ndarray) -> float:
    s = sig & valid
    if not s.any() or not valid.any():
        return float("nan")
    return float(out[s].mean() - out[valid].mean())


def episodes(sig: pd.Series, symbol: pd.Series) -> int:
    """Distinct episodes = signal starts (True preceded by False / a symbol boundary)."""
    s = sig.fillna(False).astype(bool)
    prev = s.groupby(symbol).shift(1).fillna(False).astype(bool)
    return int((s & ~prev).sum())


def matched_controls(fs: pd.DataFrame, sig: pd.Series, out_col: str, valid_col: str, ticks_col: str,
                     cfg: ExperimentConfig, rng: np.random.Generator) -> Tuple[float, float, int]:
    """For each signal row draw (with replacement) one non-signal row with the same
    (symbol, tod_bucket, spread_bucket). Returns (P(outcome | control), mean ticks | control, n)."""
    valid = fs[valid_col].astype(bool)
    pool = fs[(~sig) & valid]
    if not len(pool):
        return np.nan, np.nan, 0
    keys = ["symbol", "tod_bucket", "spread_bucket"]
    groups = {k: v.index.values for k, v in pool.groupby(keys)}
    picks: List[np.ndarray] = []
    for key, g in fs[sig & valid].groupby(keys):
        cand = groups.get(key)
        if cand is None or not len(cand):
            continue
        picks.append(rng.choice(cand, size=len(g), replace=True))
    if not picks:
        return np.nan, np.nan, 0
    c = fs.loc[np.concatenate(picks)]
    return float(c[out_col].mean()), float(c[ticks_col].mean()), int(len(c))


def evaluate_signal(fs: pd.DataFrame, sig: pd.Series, outcome: str, h: int, cfg: ExperimentConfig,
                    rng: np.random.Generator, baselines: Dict[str, pd.Series]) -> Dict[str, Any]:
    """One results row: counts, episodes, P(outcome), matched-control and base-rate lifts, and the
    incremental lift over every simple baseline (P(outcome | mechanism) − P(outcome | baseline))."""
    out_col, valid_col, ticks_col = f"fwd_{outcome}_h{h}", f"fwd_valid_h{h}", f"fwd_mid_ticks_h{h}"
    valid = fs[valid_col].astype(bool)
    s = sig.reindex(fs.index).fillna(False).astype(bool)
    n_all, n_sig, n_sig_valid = int(len(fs)), int(s.sum()), int((s & valid).sum())
    base_p = float(fs.loc[valid, out_col].mean()) if valid.any() else np.nan
    base_ticks = float(fs.loc[valid, ticks_col].mean()) if valid.any() else np.nan
    if n_sig_valid:
        sv = s & valid
        p_out = float(fs.loc[sv, out_col].mean())
        p_up = float(fs.loc[sv, f"fwd_up_h{h}"].mean())
        p_down = float(fs.loc[sv, f"fwd_down_h{h}"].mean())
        mean_ticks = float(fs.loc[sv, ticks_col].mean())
    else:
        p_out = p_up = p_down = mean_ticks = np.nan
    c_p, c_ticks, n_c = matched_controls(fs, s, out_col, valid_col, ticks_col, cfg, rng)
    row: Dict[str, Any] = {
        "h": h, "outcome": outcome, "n_rows": n_all, "n_signal": n_sig, "n_signal_valid": n_sig_valid,
        "episodes": episodes(s, fs["symbol"]), "share_of_rows": (n_sig / n_all) if n_all else np.nan,
        "p_outcome": p_out, "p_up": p_up, "p_down": p_down, "mean_fwd_ticks": mean_ticks,
        "base_p_outcome": base_p, "base_mean_ticks": base_ticks,
        "ctrl_p_outcome": c_p, "ctrl_mean_ticks": c_ticks, "n_matched": n_c,
        "lift_vs_matched": (p_out - c_p) if (n_c and n_sig_valid) else np.nan,
        "lift_vs_base": (p_out - base_p) if n_sig_valid else np.nan,
        "ticks_vs_base": (mean_ticks - base_ticks) if n_sig_valid else np.nan}
    best_name, best_lift = None, -np.inf
    for b, bsig in baselines.items():
        bs = bsig.reindex(fs.index).fillna(False).astype(bool) & valid
        n_b = int(bs.sum())
        p_b = float(fs.loc[bs, out_col].mean()) if n_b else np.nan
        row[f"n_{b}"] = n_b
        row[f"p_{b}"] = p_b
        row[f"lift_{b}_vs_base"] = (p_b - base_p) if n_b else np.nan
        row[f"inc_vs_{b}"] = (p_out - p_b) if (n_b and n_sig_valid) else np.nan
        if n_b and np.isfinite(p_b) and (p_b - base_p) > best_lift:
            best_name, best_lift = b, p_b - base_p
    row["best_baseline"] = best_name
    row["inc_vs_best"] = row[f"inc_vs_{best_name}"] if best_name else np.nan
    return row


def _sorted_positions(fs: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    order = np.lexsort((fs["t"].values.astype("datetime64[ns]").astype(np.int64), fs["symbol"].values.astype(str)))
    return order, fs["symbol"].values.astype(str)[order]


def block_bootstrap_ci(fs: pd.DataFrame, sig_a: pd.Series, sig_b: Optional[pd.Series], out_col: str, valid_col: str,
                       cfg: ExperimentConfig, rng: np.random.Generator) -> Dict[str, Any]:
    """95 % block-bootstrap CI of lift(sig_a) − lift(sig_b) (sig_b None → lift(sig_a) alone). Rows are
    ordered by time within symbol and cut into consecutive blocks of ``block_len``; each resample draws
    blocks with replacement per symbol (as many as the symbol has), keeping the within-block run structure."""
    order, sym_sorted = _sorted_positions(fs)
    a = sig_a.reindex(fs.index).fillna(False).astype(bool).values[order]
    b = sig_b.reindex(fs.index).fillna(False).astype(bool).values[order] if sig_b is not None else None
    o = fs[out_col].astype(float).values[order]
    v = fs[valid_col].astype(bool).values[order]
    bounds = np.flatnonzero(np.r_[True, sym_sorted[1:] != sym_sorted[:-1], True])
    blocks: List[List[np.ndarray]] = []
    for s0, s1 in zip(bounds[:-1], bounds[1:]):
        idx = np.arange(s0, s1)
        blocks.append([idx[i:i + cfg.block_len] for i in range(0, len(idx), cfg.block_len)])
    point = _lift_arr(a, o, v) - (_lift_arr(b, o, v) if b is not None else 0.0)
    stats: List[float] = []
    for _ in range(cfg.n_boot):
        pick: List[np.ndarray] = []
        for bl in blocks:
            if bl:
                kk = rng.integers(0, len(bl), size=len(bl))
                pick.extend(bl[i] for i in kk)
        if not pick:
            break
        p = np.concatenate(pick)
        la = _lift_arr(a[p], o[p], v[p])
        lb = _lift_arr(b[p], o[p], v[p]) if b is not None else 0.0
        stats.append(la - lb)
    arr = np.array([x for x in stats if np.isfinite(x)])
    if not len(arr):
        return {"ci_lo": np.nan, "ci_hi": np.nan, "n_boot_valid": 0, "point": point, "block_len": cfg.block_len}
    return {"ci_lo": float(np.percentile(arr, 2.5)), "ci_hi": float(np.percentile(arr, 97.5)),
            "n_boot_valid": int(len(arr)), "point": float(point), "block_len": cfg.block_len}


def permutation_p(fs: pd.DataFrame, sig: pd.Series, out_col: str, valid_col: str, cfg: ExperimentConfig,
                  rng: np.random.Generator) -> Dict[str, Any]:
    """Timestamp permutation: circularly shift the signal series within each symbol by a random offset
    (the signal keeps its run structure, loses its alignment with outcomes). p = (#null ≥ observed + 1)/(n + 1)."""
    order, sym_sorted = _sorted_positions(fs)
    s_all = sig.reindex(fs.index).fillna(False).astype(bool).values[order]
    o = fs[out_col].astype(float).values[order]
    v = fs[valid_col].astype(bool).values[order]
    obs = _lift_arr(s_all, o, v)
    if not np.isfinite(obs):
        return {"observed": np.nan, "p_value": np.nan, "n_perm": 0, "null_mean": np.nan, "null_p95": np.nan}
    base = o[v].mean()
    bounds = np.flatnonzero(np.r_[True, sym_sorted[1:] != sym_sorted[:-1], True])
    null: List[float] = []
    for _ in range(cfg.n_perm):
        s_perm = s_all.copy()
        for s0, s1 in zip(bounds[:-1], bounds[1:]):
            if s1 - s0 > 1:
                s_perm[s0:s1] = np.roll(s_all[s0:s1], int(rng.integers(1, s1 - s0)))
        m = s_perm & v
        null.append(o[m].mean() - base if m.any() else np.nan)
    arr = np.array([x for x in null if np.isfinite(x)])
    p = float((np.sum(arr >= obs) + 1) / (len(arr) + 1)) if len(arr) else np.nan
    return {"observed": float(obs), "p_value": p, "n_perm": int(len(arr)),
            "null_mean": float(arr.mean()) if len(arr) else np.nan,
            "null_p95": float(np.percentile(arr, 95)) if len(arr) else np.nan}


def bh_fdr(p: np.ndarray, q: float) -> Tuple[np.ndarray, float]:
    """Benjamini–Hochberg: sort finite p ascending, threshold_k = q·k/m, keep every hypothesis up to the
    largest k with p_(k) ≤ threshold_k. Returns (keep mask aligned to p, cutoff threshold or NaN)."""
    p = np.asarray(p, dtype=float)
    ok = np.isfinite(p)
    idx = np.where(ok)[0]
    keep = np.zeros_like(p, dtype=bool)
    if len(idx) == 0:
        return keep, float("nan")
    order = idx[np.argsort(p[idx], kind="mergesort")]
    m = len(order)
    thr = q * (np.arange(1, m + 1) / m)
    passed = p[order] <= thr
    cutoff = float("nan")
    if passed.any():
        last = int(np.max(np.where(passed)[0]))
        keep[order[:last + 1]] = True
        cutoff = float(thr[last])
    return keep, cutoff


def bh_adjusted(p: np.ndarray) -> np.ndarray:
    """BH-adjusted q-values: q_(k) = min_{j ≥ k} p_(j)·m/j, clipped to 1 (monotone in p); NaN stays NaN."""
    p = np.asarray(p, dtype=float)
    out = np.full_like(p, np.nan)
    idx = np.where(np.isfinite(p))[0]
    if not len(idx):
        return out
    order = idx[np.argsort(p[idx], kind="mergesort")]
    m = len(order)
    raw = p[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(raw[::-1])[::-1]
    out[order] = np.clip(adj, 0.0, 1.0)
    return out


# ---------------------------------------------------------------------------- shifted signals
def time_shift_signal(df: pd.DataFrame, sig: pd.Series, seconds: float) -> pd.Series:
    """signal'(t) = signal at the last row of the same symbol at or before t + seconds (False when none).
    seconds < 0 → the anchor-shift placebo (signal from the past, outcome window disjoint from the real
    alignment); seconds > 0 → the leak control (signal from the future)."""
    out = np.zeros(len(df), dtype=bool)
    s = sig.reindex(df.index).fillna(False).astype(bool).values
    for _, g in df.groupby("symbol", sort=False):
        pos = g.index.values
        loc = df.index.get_indexer(pos)
        ts = g["t"].values.astype("datetime64[ns]").astype(np.int64)
        target = ts + int(round(seconds * 1e9))
        j = np.searchsorted(ts, target, side="right") - 1
        vals = np.where(j >= 0, s[loc][np.clip(j, 0, len(loc) - 1)], False)
        out[loc] = vals
    return pd.Series(out, index=df.index)


# ---------------------------------------------------------------------------- per-mechanism run
def _rng(cfg: ExperimentConfig, name: str, k: int) -> np.random.Generator:
    return np.random.default_rng([cfg.seed, zlib.crc32(name.encode("utf-8")), k])


def _fals_row(mechanism: str, test: str, variant: str, fs: pd.DataFrame, sig: Optional[pd.Series], outcome: str, h: int,
              best_sig: Optional[pd.Series], passed: Optional[bool], note: str = "", split: str = "holdout") -> Dict[str, Any]:
    out_col, valid_col = f"fwd_{outcome}_h{h}", f"fwd_valid_h{h}"
    v = fs[valid_col].astype(bool).values
    o = fs[out_col].astype(float).values
    s = sig.reindex(fs.index).fillna(False).astype(bool) if sig is not None else pd.Series(False, index=fs.index)
    lift = _lift_arr(s.values, o, v)
    inc = np.nan
    if best_sig is not None:
        lb = _lift_arr(best_sig.reindex(fs.index).fillna(False).astype(bool).values, o, v)
        inc = lift - lb if (np.isfinite(lift) and np.isfinite(lb)) else np.nan
    return {"mechanism": mechanism, "test": test, "variant": variant, "split": split, "h": h, "outcome": outcome,
            "n_rows": int(len(fs)), "n_signal": int((s.values & v).sum()), "episodes": episodes(s, fs["symbol"]) if len(fs) else 0,
            "lift_vs_base": lift, "incremental_vs_best_baseline": inc, "passed": passed, "note": note}


def liquidity_groups(df: pd.DataFrame) -> Tuple[Dict[str, str], str]:
    """Symbol → 'top' | 'mid' by the median across symbols of the symbol's day_volume (max over rows);
    falls back to trade_volume, then mean visible liquidity. Returns ({}, 'none') when nothing is known."""
    for col, how in (("day_volume", "max"), ("trade_volume", "max"), ("visible_liq", "mean")):
        if col == "visible_liq":
            x = (df["visible_bid_liq"].astype(float).fillna(0) + df["visible_ask_liq"].astype(float).fillna(0)).where(
                df["visible_bid_liq"].notna() | df["visible_ask_liq"].notna())
        else:
            x = pd.to_numeric(df[col], errors="coerce") if col in df else pd.Series(np.nan, index=df.index)
        per = x.groupby(df["symbol"]).agg(how).dropna()
        if len(per):
            med = float(per.median())
            return {str(s): ("top" if v >= med else "mid") for s, v in per.items()}, col
    return {}, "none"


def run_mechanism(df: pd.DataFrame, name: str, cfg: ExperimentConfig = DEFAULT_CONFIG,
                  family: str = "") -> Dict[str, Any]:
    """Evaluate one mechanism on the prepared table. Returns {"results": [...], "falsification": [...],
    "verdict": {...}} — the verdict is provisional until ``finalize_verdicts`` applies BH-FDR."""
    h = cfg.primary_h
    sigs = mechanism_signals(df, name, cfg)
    outcome, mirror_outcome = sigs["outcome"], sigs["mirror_outcome"]
    elig = df[df["eligible"].astype(bool)]
    base_by_outcome = {oc: baseline_signals(df, oc, cfg) for oc in {outcome, mirror_outcome} if oc}
    results: List[Dict[str, Any]] = []
    rng_ctrl = _rng(cfg, name, 1)
    variants = [("state", sigs["state"], outcome), ("score_ge_0.6", sigs["score"], outcome)]
    if sigs["mirror"] is not None:
        variants.append(("mirror", sigs["mirror"], mirror_outcome))
    for split in SPLITS:
        fs = elig[elig["split"] == split]
        if not len(fs):
            continue
        for hh in cfg.horizons:
            for variant, sig, oc in variants:
                r = evaluate_signal(fs, sig, oc, hh, cfg, rng_ctrl, base_by_outcome[oc])
                results.append({"mechanism": name, "family": family, "variant": variant, "split": split,
                                "primary_direction": sigs["primary_direction"], **r})

    # ---- falsification battery on the holdout at the primary horizon
    hold = elig[elig["split"] == "holdout"]
    sig = sigs["state"]
    bases = base_by_outcome[outcome]
    out_col, valid_col = f"fwd_{outcome}_h{h}", f"fwd_valid_h{h}"
    rows: List[Dict[str, Any]] = []
    hold_rows = [r for r in results if r["split"] == "holdout" and r["h"] == h and r["variant"] == "state"]
    best_b = hold_rows[0]["best_baseline"] if hold_rows else None
    best_sig = bases[best_b] if best_b else None
    real = _fals_row(name, "real", "holdout", hold, sig, outcome, h, best_sig, None,
                     f"best simple baseline = {best_b}; outcome = {outcome}; direction = {sigs['primary_direction']}")
    rows.append(real)
    real_lift, real_inc = real["lift_vs_base"], real["incremental_vs_best_baseline"]
    n_hold_sig = real["n_signal"]
    hold_episodes = real["episodes"]
    n_elig = int(len(elig))
    blocked = n_elig < cfg.n_min_rows or hold_episodes < cfg.n_min_episodes

    # (a) baseline comparison — block bootstrap CI
    if best_sig is not None and n_hold_sig and len(hold):
        ci = block_bootstrap_ci(hold, sig, best_sig, out_col, valid_col, cfg, _rng(cfg, name, 2))
    else:
        ci = {"ci_lo": np.nan, "ci_hi": np.nan, "point": real_inc, "n_boot_valid": 0, "block_len": cfg.block_len}
    rows.append({**real, "test": "baseline_comparison", "variant": f"state - {best_b}",
                 "incremental_vs_best_baseline": ci["point"],
                 "passed": bool(np.isfinite(ci["ci_lo"]) and ci["ci_lo"] > 0),
                 "note": (f"block bootstrap 95% CI [{ci['ci_lo']:.4f}, {ci['ci_hi']:.4f}] (blocks of {cfg.block_len} rows, "
                          f"{ci['n_boot_valid']} resamples)") if np.isfinite(ci["ci_lo"]) else "no valid bootstrap resamples"})
    for b in BASELINES:
        rows.append(_fals_row(name, "baseline", b, hold, bases[b], outcome, h, None, None))
    rows.append(_fals_row(name, "graded_score", f"score_ge_{cfg.score_threshold:g}", hold, sigs["score"], outcome, h, best_sig, None))

    # (b) timestamp permutation
    if n_hold_sig and len(hold):
        perm = permutation_p(hold, sig, out_col, valid_col, cfg, _rng(cfg, name, 3))
    else:
        perm = {"observed": np.nan, "p_value": np.nan, "n_perm": 0, "null_mean": np.nan, "null_p95": np.nan}
    rows.append({**real, "test": "timestamp_permutation", "variant": "circular shift within symbol",
                 "lift_vs_base": perm["observed"], "incremental_vs_best_baseline": np.nan,
                 "passed": bool(np.isfinite(perm["p_value"]) and perm["p_value"] < cfg.alpha),
                 "note": (f"p = {perm['p_value']:.4f} over {perm['n_perm']} permutations; null mean {perm['null_mean']:.4f}, "
                          f"null p95 {perm['null_p95']:.4f}") if np.isfinite(perm["p_value"]) else "no signal rows"})

    # (c) side flip
    if sigs["mirror"] is None:
        rows.append(_fals_row(name, "side_flip", "not_applicable", hold, None, outcome, h, None, None,
                              "mechanism carries no direction: side flip not applicable"))
        side_ok: Optional[bool] = None
        side_na = True
    else:
        r = _fals_row(name, "side_flip", f"mirror → P({mirror_outcome})", hold, sigs["mirror"], mirror_outcome, h, None, None,
                      f"lift of the mirrored signal on P({mirror_outcome}) (must be > 0 for a symmetric mechanism)")
        r["passed"] = (bool(r["lift_vs_base"] > 0) if np.isfinite(r["lift_vs_base"]) else None)
        rows.append(r)
        side_ok, side_na = r["passed"], False

    # (d) anchor shift: placebo (−h) and leak control (+h)
    placebo_ok: Optional[bool] = None
    for secs, vname in ((-h, "placebo_shift_-h"), (h, "leak_control_shift_+h")):
        shifted = time_shift_signal(df, sig, secs)
        r = _fals_row(name, "anchor_shift", vname, hold, shifted, outcome, h, best_sig, None,
                      "placebo must show |lift| < ½ real lift" if secs < 0 else "future-shifted signal: expected to inflate (sensitivity)")
        if secs < 0:
            r["passed"] = (bool(abs(r["lift_vs_base"]) < 0.5 * abs(real_lift))
                           if (np.isfinite(r["lift_vs_base"]) and np.isfinite(real_lift)) else None)
            placebo_ok = r["passed"]
        rows.append(r)

    # (e) largest-wall removal
    wf = baseline_signals(df, outcome, cfg, wall_free=True)
    best_wf = wf[best_b] if best_b else None
    r = _fals_row(name, "removal", "largest_wall_removed", hold, sig, outcome, h, best_wf, None,
                  f"imbalance baselines recomputed without the largest displayed level; best baseline {best_b} re-derived")
    r["passed"] = (bool(np.sign(r["incremental_vs_best_baseline"]) == np.sign(real_inc) and real_inc != 0)
                   if (np.isfinite(r["incremental_vs_best_baseline"]) and np.isfinite(real_inc)) else None)
    wall_ok = r["passed"]
    rows.append(r)

    # (f) leave-one-symbol-out
    syms = sorted(hold["symbol"].unique().tolist())
    loso_vals: List[Optional[bool]] = []
    for s in syms:
        fs = hold[hold["symbol"] != s]
        r = _fals_row(name, "leave_one_symbol_out", f"without {s}", fs, sig, outcome, h, best_sig, None)
        r["passed"] = (bool(r["incremental_vs_best_baseline"] > 0) if np.isfinite(r["incremental_vs_best_baseline"]) else None)
        loso_vals.append(r["passed"])
        rows.append(r)
    loso_ok: Optional[bool] = (None if (len(syms) < 2 or any(v is None for v in loso_vals)) else all(bool(v) for v in loso_vals))

    # (g) liquidity split
    groups, liq_basis = liquidity_groups(df)
    liq_vals: List[Optional[bool]] = []
    for grp in ("top", "mid"):
        gs = [s for s, g in groups.items() if g == grp]
        fs = hold[hold["symbol"].isin(gs)]
        r = _fals_row(name, "liquidity_split", grp, fs, sig, outcome, h, best_sig, None,
                      f"symbols: {', '.join(gs) if gs else '(none)'}; basis: {liq_basis}")
        r["passed"] = (bool(r["incremental_vs_best_baseline"] > 0) if np.isfinite(r["incremental_vs_best_baseline"]) else None)
        liq_vals.append(r["passed"])
        rows.append(r)
    liq_ok: Optional[bool] = (None if any(v is None for v in liq_vals) else all(bool(v) for v in liq_vals))

    verdict = {"mechanism": name, "family": family, "primary_direction": sigs["primary_direction"], "outcome": outcome,
               "n_dir_pos_active": sigs["n_dir_pos"], "n_dir_neg_active": sigs["n_dir_neg"],
               "eligible_rows": n_elig, "holdout_rows": int(len(hold)), "holdout_signal_rows": n_hold_sig,
               "holdout_episodes": hold_episodes, "best_baseline": best_b,
               "lift_vs_base": _f(real_lift), "incremental_vs_best_baseline": _f(real_inc),
               "bootstrap": {k: _f(v) if k != "block_len" and k != "n_boot_valid" else v for k, v in ci.items()},
               "permutation": {k: (_f(v) if k != "n_perm" else v) for k, v in perm.items()},
               "checks": {"a_beats_best_baseline_ci": bool(np.isfinite(ci["ci_lo"]) and ci["ci_lo"] > 0),
                          "b_permutation": bool(np.isfinite(perm["p_value"]) and perm["p_value"] < cfg.alpha),
                          "c_side_flip": side_ok, "d_placebo": placebo_ok, "e_wall_removal": wall_ok,
                          "f_loso": loso_ok, "g_liquidity": liq_ok},
               "side_flip_not_applicable": side_na, "blocked": blocked,
               "blocked_reasons": ([f"only {n_elig} eligible rows < n_min_rows {cfg.n_min_rows}"] if n_elig < cfg.n_min_rows else []) +
                                  ([f"only {hold_episodes} distinct holdout episodes < n_min_episodes {cfg.n_min_episodes}"]
                                   if hold_episodes < cfg.n_min_episodes else [])}
    return {"results": results, "falsification": rows, "verdict": verdict}


def decide(v: Dict[str, Any], fdr_pass: Optional[bool], fdr_q: Optional[float]) -> Dict[str, Any]:
    """Apply VERDICT_RULE to one mechanism's provisional verdict once the FDR pass is known."""
    out = dict(v)
    out["fdr_pass"] = fdr_pass
    out["fdr_q_value"] = fdr_q
    if v["blocked"]:
        out["verdict"] = "BLOCKED"
        out["reasons"] = list(v["blocked_reasons"])
        return out
    checks = dict(v["checks"])
    checks["b_permutation"] = bool(checks["b_permutation"] and fdr_pass is True)
    reasons: List[str] = []
    ok = True
    for k, val in checks.items():
        if k == "c_side_flip" and v.get("side_flip_not_applicable"):
            continue
        if val is not True:
            ok = False
            reasons.append(f"{k}: {'FAILED' if val is False else 'not evaluable'}")
    out["checks"] = checks
    out["verdict"] = "KEEP" if ok else "KILL"
    out["reasons"] = reasons or ["all checks passed"]
    return out


def finalize_verdicts(provisional: Dict[str, Dict[str, Any]], cfg: ExperimentConfig) -> Dict[str, Any]:
    """BH-FDR across the mechanisms' permutation p-values, then the verdict per mechanism."""
    names = sorted(provisional)
    p = np.array([provisional[n]["permutation"]["p_value"] if provisional[n]["permutation"]["p_value"] is not None else np.nan
                  for n in names], dtype=float)
    keep, cutoff = bh_fdr(p, cfg.fdr_q)
    qv = bh_adjusted(p)
    verdicts = {}
    for i, n in enumerate(names):
        fp = bool(keep[i]) if np.isfinite(p[i]) else None
        verdicts[n] = decide(provisional[n], fp, _f(qv[i]))
    return {"mechanisms": verdicts,
            "fdr": {"q": cfg.fdr_q, "cutoff": _f(cutoff), "n_tested": int(np.isfinite(p).sum()),
                    "n_pass": int(keep.sum()), "p_values": {n: _f(p[i]) for i, n in enumerate(names)},
                    "q_values": {n: _f(qv[i]) for i, n in enumerate(names)}},
            "counts": {k: sum(1 for v in verdicts.values() if v["verdict"] == k) for k in ("KEEP", "KILL", "BLOCKED")},
            "rule": VERDICT_RULE, "config": cfg.describe()}


# ---------------------------------------------------------------------------- outputs
RESULT_COLUMNS: List[str] = (
    ["mechanism", "family", "variant", "split", "h", "outcome", "primary_direction", "n_rows", "n_signal",
     "n_signal_valid", "episodes", "share_of_rows", "p_outcome", "p_up", "p_down", "mean_fwd_ticks",
     "base_p_outcome", "base_mean_ticks", "ctrl_p_outcome", "ctrl_mean_ticks", "n_matched", "lift_vs_matched",
     "lift_vs_base", "ticks_vs_base"]
    + [c for b in BASELINES for c in (f"n_{b}", f"p_{b}", f"lift_{b}_vs_base", f"inc_vs_{b}")]
    + ["best_baseline", "inc_vs_best"])
FALS_COLUMNS: List[str] = ["mechanism", "test", "variant", "split", "h", "outcome", "n_rows", "n_signal", "episodes",
                           "lift_vs_base", "incremental_vs_best_baseline", "passed", "note"]


def _write_csv(rows: List[Dict[str, Any]], columns: List[str], path: str, sort_by: List[str]) -> None:
    df = pd.DataFrame(rows, columns=columns)
    if len(df):
        df = df.sort_values(sort_by, kind="mergesort").reset_index(drop=True)
    df.to_csv(path, index=False, float_format="%.10g", lineterminator="\n")


def run_experiment(store_dir: str, out_dir: str, cfg: ExperimentConfig = DEFAULT_CONFIG,
                   symbols: Optional[Sequence[str]] = None, mechanisms: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Whole pipeline over a state store; writes the five output files and returns a summary."""
    os.makedirs(out_dir, exist_ok=True)
    raw = load_store(store_dir, symbols)
    families = dict(raw.attrs.get("mech_families", {}))
    files = list(raw.attrs.get("files", []))
    df = prepare(raw, cfg) if len(raw) else raw
    den = denominator(df, cfg, cfg.primary_h) if len(raw) else {
        "total_rows": 0, "horizon_s": cfg.primary_h, "n_symbols": 0, "rows_per_symbol": {},
        "per_reason_any": {r: 0 for r in EXCLUSION_REASONS}, "per_reason_primary": {r: 0 for r in EXCLUSION_REASONS},
        "eligible": 0, "sum_check": True}
    names = mechanism_names(df) if len(raw) else []
    if mechanisms:
        want = set(mechanisms)
        names = [n for n in names if n in want]
    results: List[Dict[str, Any]] = []
    fals: List[Dict[str, Any]] = []
    provisional: Dict[str, Dict[str, Any]] = {}
    for name in names:
        r = run_mechanism(df, name, cfg, families.get(name, ""))
        results.extend(r["results"])
        fals.extend(r["falsification"])
        provisional[name] = r["verdict"]
    verdicts = finalize_verdicts(provisional, cfg)
    # stamp the FDR result into the permutation rows of the falsification table
    for row in fals:
        if row["test"] == "timestamp_permutation":
            v = verdicts["mechanisms"].get(row["mechanism"], {})
            row["note"] += f"; BH-FDR q={v.get('fdr_q_value')} pass={v.get('fdr_pass')}"
    p_res = os.path.join(out_dir, "MECHANISM_RESULTS.csv")
    p_fal = os.path.join(out_dir, "FALSIFICATION.csv")
    p_den = os.path.join(out_dir, "DENOMINATOR.json")
    p_ver = os.path.join(out_dir, "VERDICTS.json")
    p_man = os.path.join(out_dir, "MANIFEST.json")
    _write_csv(results, RESULT_COLUMNS, p_res, ["mechanism", "variant", "split", "h"])
    _write_csv(fals, FALS_COLUMNS, p_fal, ["mechanism"])   # stable (mergesort) keeps battery order within mechanism
    with open(p_den, "w", encoding="utf-8") as fh:
        json.dump(den, fh, indent=1, sort_keys=True, default=str)
    with open(p_ver, "w", encoding="utf-8") as fh:
        json.dump(verdicts, fh, indent=1, sort_keys=True, default=str)
    manifest = {"store": os.path.abspath(store_dir), "out": os.path.abspath(out_dir),
                "inputs": {os.path.relpath(p, store_dir): _sha256_file(p) for p in files},
                "config": cfg.describe(), "symbols_requested": list(symbols) if symbols else None,
                "mechanisms": names, "n_rows": int(len(df)), "eligible_rows": den["eligible"],
                "t_range": den.get("t_range"), "verdict_counts": verdicts["counts"],
                "outputs": {os.path.basename(p): _sha256_file(p) for p in (p_res, p_fal, p_den, p_ver)}}
    with open(p_man, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True, default=str)
    return {"denominator": den, "verdicts": verdicts, "n_results": len(results), "n_falsification": len(fals),
            "manifest": manifest, "table": df}


# ---------------------------------------------------------------------------- CLI
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mechanism evaluation over a tower state store")
    ap.add_argument("--store", required=True, help="directory containing states/<SYMBOL>.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--horizon", type=int, default=DEFAULT_CONFIG.primary_h, help="primary horizon (s)")
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_CONFIG.horizons))
    ap.add_argument("--symbols", default="", help="comma-separated subset")
    ap.add_argument("--mechanisms", default="", help="comma-separated subset")
    ap.add_argument("--n-boot", type=int, default=DEFAULT_CONFIG.n_boot)
    ap.add_argument("--n-perm", type=int, default=DEFAULT_CONFIG.n_perm)
    ap.add_argument("--seed", type=int, default=DEFAULT_CONFIG.seed)
    ap.add_argument("--n-min-rows", type=int, default=DEFAULT_CONFIG.n_min_rows)
    ap.add_argument("--n-min-episodes", type=int, default=DEFAULT_CONFIG.n_min_episodes)
    a = ap.parse_args(argv)
    hs = tuple(sorted({int(x) for x in a.horizons.split(",") if x.strip()} | {a.horizon}))
    cfg = ExperimentConfig(horizons=hs, primary_h=a.horizon, n_boot=a.n_boot, n_perm=a.n_perm, seed=a.seed,
                           n_min_rows=a.n_min_rows, n_min_episodes=a.n_min_episodes)
    res = run_experiment(a.store, a.out, cfg,
                         symbols=[s for s in a.symbols.split(",") if s.strip()] or None,
                         mechanisms=[m for m in a.mechanisms.split(",") if m.strip()] or None)
    den = res["denominator"]
    print(json.dumps({"rows": den["total_rows"], "eligible": den["eligible"], "per_reason_primary": den["per_reason_primary"],
                      "verdicts": res["verdicts"]["counts"], "out": os.path.abspath(a.out)}, default=str))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
