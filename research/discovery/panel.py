"""The DEV panel for discovery, and the two things that must never enter it.

Discovery is allowed to be aggressive. It is not allowed to be contaminated, and
there are exactly two ways this dataset gets contaminated:

**Sealed data.** `HOLDOUT_WINDOW` 2019-01-01 … 2022-07-27 belongs to Phase 5 and
is dropped at load with an assertion, exactly as `experiments/phase45_footprints.py`
does. The other reserved slice is 2026-01-25 … 2026-09-07 — the months fetched on
2026-09-07 that no phase has ever seen, kept as the single unseen test in
`ROADMAP.md` §Stage 11. Neither is available here, and both are asserted absent
rather than merely filtered.

**Look-ahead features.** Two columns in the feature table are computed from
information that did not exist at the decision instant:

* `flag_locked_run` and `flag_stale_run` — `bdlib/qa.py:40` stamps the *whole*
  run's length onto every day of the run, so day 1 of a ten-day stale run is
  already flagged. They are input **gates**, never predictors, and are removed
  from the candidate input set here rather than left for a reviewer to remember.

Everything under `fwd_*` lives in the labels table and is used only as an
outcome, never as an input. `assert_pit()` enforces both rules on any frame
handed to a candidate.

Panels come from `bdlib/panels.py` unchanged: the 2024-02-22 coverage break
(381 → 88 symbols) means no cross-sectional statistic may span it, and
`assert_single_panel()` raises rather than warns.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from bdlib import config as C
from bdlib import panels

# Reserved from discovery, both asserted absent after load.
SEALED_HOLDOUT = (pd.Timestamp(C.HOLDOUT_WINDOW[0]), pd.Timestamp(C.HOLDOUT_WINDOW[1]))
VIRGIN_SLICE_START = pd.Timestamp("2026-01-25")

# Columns that look ahead by construction. Never candidate inputs.
LOOKAHEAD_COLUMNS = ("flag_locked_run", "flag_stale_run")

# Outcome columns. Never inputs.
LABEL_PREFIX = "fwd_"

HORIZONS = (5, 15, 30)


def _results_dir(explicit: Optional[str] = None) -> str:
    """`results/` is a generated, git-ignored tree; find it wherever it lives."""
    if explicit:
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "..", "results"),
                 "/home/user/bd-share-market/results"):
        if os.path.exists(os.path.join(cand, "dse_eod_features.parquet")):
            return os.path.abspath(cand)
    raise FileNotFoundError("dse_eod_features.parquet not found; pass results_dir")


def load_dev(results_dir: Optional[str] = None) -> pd.DataFrame:
    """Features joined to labels, with everything reserved removed and asserted."""
    r = _results_dir(results_dir)
    f = pd.read_parquet(os.path.join(r, "dse_eod_features.parquet"))
    lab = pd.read_parquet(os.path.join(r, "dse_eod_labels.parquet"))
    d = f.merge(lab, on=["symbol", "ts"], how="inner")

    before = len(d)
    d = d[~d["ts"].between(*SEALED_HOLDOUT)]
    d = d[d["ts"] < VIRGIN_SLICE_START]
    d = d.sort_values(["symbol", "ts"]).reset_index(drop=True)

    # Assert, do not trust the filter.
    assert not d["ts"].between(*SEALED_HOLDOUT).any(), "sealed holdout leaked into DEV"
    assert (d["ts"] < VIRGIN_SLICE_START).all(), "reserved 2026 slice leaked into DEV"
    d.attrs["rows_removed_as_reserved"] = before - len(d)
    d.attrs["panel_summary"] = panels.summary(d)
    return d


def input_columns(d: pd.DataFrame) -> List[str]:
    """Every column a candidate may read: no labels, no look-ahead flags, no keys."""
    drop = {"symbol", "ts", "panel"} | set(LOOKAHEAD_COLUMNS)
    return sorted(c for c in d.columns
                  if c not in drop and not c.startswith(LABEL_PREFIX))


def assert_pit(cols: Sequence[str]) -> None:
    """Raise if a candidate reads anything it could not have known at the close."""
    bad_label = sorted(c for c in cols if c.startswith(LABEL_PREFIX))
    bad_ahead = sorted(c for c in cols if c in LOOKAHEAD_COLUMNS)
    if bad_label:
        raise ValueError(f"look-ahead: {bad_label} are forward labels, usable only as outcomes")
    if bad_ahead:
        raise ValueError(
            f"look-ahead: {bad_ahead} are computed from the whole run they belong to "
            f"(bdlib/qa.py:40), so they encode the future. They are input gates, not predictors.")


# --------------------------------------------------------------------------- regimes
# The daily price-limit ladder measured from the exchange's own published table on
# 2026-09-06 (evidence/public/2026-09-06/normalized/circuit_limits.parquet, 379
# equity rows). Applying it to history is an APPROXIMATION and is labelled as one:
# RESEARCH_STATUS.md D-16 records dated limit changes (a 2 % episode for 66 names
# from 2021-04-07; 3 % on 2024-04-24 widening to 10 % on 2024-08-28) that this
# single ladder does not reproduce. Circuit-room features built on it are INFERRED,
# and any candidate that leans on them must say so.
BAND_LADDER = ((200.0, 10.00), (500.0, 8.75), (1000.0, 7.50), (float("inf"), 6.25))
BAND_LADDER_TRUTH = ("INFERRED — today's published ladder applied to history; dated limit "
                     "changes in RESEARCH_STATUS.md D-16 are not reproduced by it")


def band_pct(prev_close: pd.Series) -> pd.Series:
    out = pd.Series(np.nan, index=prev_close.index, dtype=float)
    lo = pd.Series(0.0, index=prev_close.index)
    for hi, pct in BAND_LADDER:
        m = (prev_close > lo) & (prev_close <= hi)
        out = out.mask(m, pct)
        lo = pd.Series(hi, index=prev_close.index)
    return out


def add_regime_and_room(d: pd.DataFrame, results_dir: Optional[str] = None) -> pd.DataFrame:
    """Attach panel, floor-era flag, and circuit room — all point-in-time.

    Circuit room is the one genuinely new input in this repository's history:
    `REJECTED_CANDIDATES.md` P45-1…P45-8 are all built from price and volume, and
    none of them carries distance-to-band. Every quantity here is a function of
    information available at the close of day t.
    """
    d = d.copy()
    d["panel"] = panels.label(d)
    fa, fb = (pd.Timestamp(x) for x in C.FLOOR_ERA)
    d["floor_era"] = d["ts"].between(fa, fb)

    r = _results_dir(results_dir)
    bars = pd.read_parquet(os.path.join(r, "dse_eod_bars_annotated.parquet"),
                           columns=["symbol", "ts", "close", "high", "low", "volume"])
    d = d.merge(bars, on=["symbol", "ts"], how="left")

    g = d.groupby("symbol", sort=False)
    d["prev_close"] = g["close"].shift(1)
    d["band_pct"] = band_pct(d["prev_close"])
    d["upper_band"] = d["prev_close"] * (1 + d["band_pct"] / 100.0)
    d["lower_band"] = d["prev_close"] * (1 - d["band_pct"] / 100.0)

    # Room LEFT at the close of day t, as a fraction of the band's own width.
    width = (d["upper_band"] - d["lower_band"]).replace(0, np.nan)
    d["room_up_frac"] = (d["upper_band"] - d["close"]) / width
    d["room_down_frac"] = (d["close"] - d["lower_band"]) / width
    # How far today's high pushed toward the ceiling — an approach, not a lock.
    d["approach_up"] = (d["high"] - d["prev_close"]) / (d["upper_band"] - d["prev_close"]).replace(0, np.nan)
    d["closed_at_band"] = d["approach_up"].notna() & (
        (d["close"] - d["prev_close"]) / (d["upper_band"] - d["prev_close"]).replace(0, np.nan) >= 0.965)
    return d


@dataclass
class DevPanel:
    """A single-panel, single-regime slice ready for candidate evaluation."""

    frame: pd.DataFrame
    panel: str
    note: str

    def __len__(self) -> int:
        return len(self.frame)


def dev_slices(d: pd.DataFrame, min_rows: int = 5000) -> List[DevPanel]:
    """PRIMARY split at the floor era, POSTBREAK on its own. Never pooled.

    The floor era (2022-07-28 … 2024-01-31, 13.9 % of bars) is a market where
    prices could not fall past a floor; pooling it with a free market mixes two
    different games, so it is its own slice.
    """
    out: List[DevPanel] = []
    for name in ("PRIMARY", "POSTBREAK"):
        sub = d[d["panel"] == name]
        if sub.empty:
            continue
        for era, flag in (("free", False), ("floor_era", True)):
            s = sub[sub["floor_era"] == flag]
            if len(s) >= min_rows:
                out.append(DevPanel(
                    s.reset_index(drop=True), name,
                    f"{name}/{era}: {len(s):,} rows, {s['symbol'].nunique()} symbols, "
                    f"{s['ts'].min().date()}→{s['ts'].max().date()}"))
    return out
