"""Forward-looking OUTCOME labels. These are answers, never inputs.

Every column here starts with `fwd_`. features.py asserts that no feature name
carries that prefix, and leakage_test.py asserts the two sets stay disjoint.
Labels exist so Phase 4 can ask "what happened after this state?" — including,
crucially, all the times nothing happened (the failed-footprint denominator).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


def build(df: pd.DataFrame, cfg: C.Config = C.DEFAULT) -> pd.DataFrame:
    p = cfg.labels
    out = pd.DataFrame(index=df.index)
    for _, g in df.groupby("symbol", sort=False):
        c = g["close"]
        for h in p.horizons:
            fwd = np.log(c.shift(-h) / c)
            out.loc[g.index, f"fwd_ret_{h}"] = fwd
            # Maximum favourable / adverse excursion inside the horizon.
            hi = g["high"].shift(-1).rolling(h, min_periods=h).max().shift(-(h - 1))
            lo = g["low"].shift(-1).rolling(h, min_periods=h).min().shift(-(h - 1))
            out.loc[g.index, f"fwd_mfe_{h}"] = np.log(hi / c)
            out.loc[g.index, f"fwd_mae_{h}"] = np.log(lo / c)
            out.loc[g.index, f"fwd_move_{h}"] = (fwd.abs() >= p.move_threshold).astype(float)
    return out


def label_columns(cfg: C.Config = C.DEFAULT) -> list[str]:
    cols = []
    for h in cfg.labels.horizons:
        cols += [f"fwd_ret_{h}", f"fwd_mfe_{h}", f"fwd_mae_{h}", f"fwd_move_{h}"]
    return cols
