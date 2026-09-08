"""Focused adversarial validation of `D_shallow_pullback`.

The claim under test
--------------------
In the 2026-09-08 run, `D_shallow_pullback` — the worst low of the last five days
within 3 % of today's close, with the close within 5 % of the 20-day high — hit
+10 % in 20 days at 14.67 % against a matched control of 27.35 %: **lift 0.536,
NW t -11.60**, and 0.477 at +20 %/30d. That is the largest effect in the whole
run, and it points the opposite way to the narrative that motivated it.

This module exists to try to destroy it, treating it as a possible VETO state
rather than a long entry. Twelve checks, in one consistent implementation, using
one definition and one control machine throughout — because an adversarial
validation whose steps disagree about the definition proves nothing.

The prior going in
------------------
Two mechanical explanations were visible before any test:

* **Volatility.** Signal rows have median `vol20` 0.59 % against 2.29 % for the
  rest. A share that cannot move 3 % in five days is mechanically unlikely to
  move 10 % in twenty. The original strata carried **no volatility term**.
* **Regime.** 51.9 % of signal rows fall inside the 2022-07-28 … 2024-01-31 hard
  floor era against 14.1 % of the rest, and 2023 alone supplies 38 % of them.
  A share pinned at a regulatory floor does not reach +10 % by construction.

If either explains the effect, it is an artifact of the control set and not a
tradeable veto. Both are tested rather than assumed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.bigmove import evaluate as EV

EPS = 1e-12

# The definition, verbatim from `research/bigmove/candidates.py`.
DEX_THRESHOLD = -0.03          # downside_excursion_5 >= this
DH_THRESHOLD = -0.05           # dist_from_20d_high  >= this

# The floor era: a hard regulatory price floor, during which a share could not
# fall and very often could not move at all.
FLOOR_ERA = (pd.Timestamp("2022-07-28"), pd.Timestamp("2024-01-31"))

# Matching sets, weakest first. The comparison between them IS the experiment.
STRATA_ORIGINAL = ("year", "liq_bucket", "price_bucket", "prior5_bucket")
STRATA_PLUS_VOL = STRATA_ORIGINAL + ("vol_bucket",)
STRATA_PLUS_DIST = STRATA_PLUS_VOL + ("dist20_bucket",)
STRATA_FULL = STRATA_PLUS_DIST + ("prior3_bucket", "turnover_bucket")

# An overnight gap this large is almost certainly a corporate action, not a
# market move. `bdlib.config` uses the same 0.2 for its QA flag, and
# CORP_ACTIONS_AVAILABLE is False, so detection is all that is available.
CORP_ACTION_GAP = 0.20
CORP_ACTION_WINDOW = 5         # sessions either side to exclude


def signal_mask(d: pd.DataFrame, dex_thr: float = DEX_THRESHOLD,
                dh_thr: float = DH_THRESHOLD) -> pd.Series:
    """The definition under test. One place, used by every check below."""
    dex = pd.to_numeric(d["downside_excursion_5"], errors="coerce")
    dh = pd.to_numeric(d["dist_from_20d_high"], errors="coerce")
    return ((dex >= dex_thr) & (dh >= dh_thr)).fillna(False)


# --------------------------------------------------------------- 2. de-overlap
def episode_id(d: pd.DataFrame, mask: pd.Series, min_gap: int = 20) -> pd.Series:
    """Label independent episodes; -1 for rows that are not the episode's start.

    A signal that persists for k consecutive sessions produces k overlapping
    forward windows sharing almost all of their data. Treating them as k
    observations inflates every count and every t-statistic. An episode is a run
    of signal days, and only its FIRST day is kept; a later run for the same
    symbol starts a new episode only once `min_gap` sessions have passed, so two
    "independent" episodes cannot share a forward window either.
    """
    out = pd.Series(-1, index=d.index, dtype=np.int64)
    sym = d["symbol"].to_numpy()
    m = mask.to_numpy()
    pos = np.arange(len(d))
    eid = 0
    last_kept = {}
    prev_sym, prev_sig = None, False
    for i in range(len(d)):
        s = sym[i]
        if s != prev_sym:
            prev_sig = False
        if m[i]:
            new_run = not prev_sig
            far_enough = (s not in last_kept) or (pos[i] - last_kept[s] >= min_gap)
            if new_run and far_enough:
                out.iloc[i] = eid
                last_kept[s] = pos[i]
                eid += 1
        prev_sym, prev_sig = s, bool(m[i])
    return out


# ------------------------------------------------------- 6. corporate actions
def corp_action_flag(d: pd.DataFrame, gap: float = CORP_ACTION_GAP,
                     window: int = CORP_ACTION_WINDOW) -> pd.Series:
    """Rows within `window` sessions of a suspected ex-date.

    `CORP_ACTIONS_AVAILABLE` is False, so no dividend or bonus calendar exists to
    join. What can be done is detect the footprint: an overnight gap beyond 20 %
    with no matching intraday range is what an unadjusted bonus or split looks
    like. This over-excludes real gaps, which is the safe direction for a test
    whose whole question is whether an effect is mechanical.
    """
    prev_close = d.groupby("symbol", sort=False)["close"].shift(1)
    overnight = d["open"] / (prev_close + EPS) - 1.0
    suspect = (overnight.abs() >= gap).fillna(False).astype(float)
    # A centred rolling max marks every row within `window` sessions of a suspect
    # one, and never crosses a symbol boundary.
    flag = suspect.groupby(d["symbol"], sort=False).transform(
        lambda s: s.rolling(2 * window + 1, center=True, min_periods=1).max())
    return flag.fillna(0).astype(bool)


def regime_of(dates: pd.Series) -> pd.Series:
    lo, hi = FLOOR_ERA
    return pd.Series(np.where(dates < lo, "PRE_FLOOR",
                              np.where(dates <= hi, "FLOOR", "POST_FLOOR")), index=dates.index)


# ------------------------------------------------------------ 11. FDR control
def benjamini_hochberg(p: Sequence[float], q: float = 0.10) -> Tuple[np.ndarray, float]:
    """BH step-up. Returns the reject mask and the largest p that survives."""
    p = np.asarray(p, dtype=float)
    ok = np.isfinite(p)
    m = int(ok.sum())
    reject = np.zeros_like(p, dtype=bool)
    if m == 0:
        return reject, np.nan
    idx = np.flatnonzero(ok)
    order = idx[np.argsort(p[idx])]
    thresh = q * (np.arange(1, m + 1) / m)
    passed = p[order] <= thresh
    if not passed.any():
        return reject, 0.0
    kmax = int(np.flatnonzero(passed).max())
    reject[order[:kmax + 1]] = True
    return reject, float(p[order[kmax]])


def two_sided_p(t: float) -> float:
    """Normal-approximation two-sided p from a Newey-West t."""
    if not np.isfinite(t):
        return np.nan
    from math import erfc, sqrt
    return float(erfc(abs(t) / sqrt(2.0)))
