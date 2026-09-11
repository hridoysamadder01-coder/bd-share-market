"""Forward outcomes, reproduced from the frozen prereg rather than redefined.

`micro/MICRO_PREREG.json` (sha256 169935bd…) fixes the outcome and forbids
redefining it. It is transcribed here verbatim in behaviour:

    fwd_mid_ticks_H = (mid[t_fwd] - mid[t]) / tick_size

    t_fwd = the FIRST frame of the SAME symbol in the SAME session with
            t_fwd >= t + H seconds
    valid = t_fwd exists in-session
            AND (t_fwd - t) <= 2H          (no stretching across a capture gap)
            AND mid is present at both ends

    up_H   = fwd_mid_ticks_H > 0
    down_H = fwd_mid_ticks_H < 0
    flat_H = fwd_mid_ticks_H == 0
    horizons = 60, 180, 600 s;  primary = 180 s

Two details in that text do the work and are easy to lose:

* **Horizons are seconds, anchored by timestamp.** Median cadence was 43.7 s on
  the calibration session, so "3 frames ahead" is not 180 seconds — it is
  whatever 3 frames happened to take. `searchsorted` on elapsed seconds is what
  the prereg specifies and what is implemented.
* **`(t_fwd - t) <= 2H` is a validity gate, not a convenience.** Without it a
  180-second label silently becomes a 40-minute label across a capture gap, and
  the gap is exactly where the market moved.

These are labels. Nothing in `seeing/features/` may read them, and
`assert_no_labels` is here so that rule is checked rather than remembered.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

HORIZONS_S: tuple = (60, 180, 600)
PRIMARY_HORIZON_S = 180
LABEL_PREFIX = "fwd_"


def add_labels(states: pd.DataFrame, horizons: Sequence[int] = HORIZONS_S,
               tick_fallback: Optional[float] = None) -> pd.DataFrame:
    """Attach `fwd_mid_ticks_<H>`, `fwd_valid_<H>` and `fwd_dt_<H>` per horizon.

    `tick_fallback` is None on purpose: where Stage 2 left the tick unobserved,
    the outcome is unobservable too, and inventing 0.10 to keep a row would put a
    guessed denominator under a measured result.
    """
    d = states.sort_values(["symbol", "t_frame"], kind="mergesort").reset_index(drop=True).copy()
    tick = d["tick_size"].astype(float)
    if tick_fallback is not None:
        tick = tick.fillna(tick_fallback)

    for H in horizons:
        ticks = np.full(len(d), np.nan)
        dts = np.full(len(d), np.nan)
        for _, gi in d.groupby("symbol", sort=False).indices.items():
            idx = np.asarray(gi)
            t = d["t_frame"].values[idx].astype("datetime64[ns]").astype("int64") / 1e9
            mid = d["mid"].values[idx].astype(float)
            tk = tick.values[idx]
            # first frame at or after t + H, by timestamp — never by frame count
            j = np.searchsorted(t, t + H, side="left")
            ok = j < len(idx)
            jj = np.where(ok, np.minimum(j, len(idx) - 1), 0)
            dt = np.where(ok, t[jj] - t, np.nan)
            valid = ok & (dt <= 2 * H) & np.isfinite(mid) & np.isfinite(mid[jj]) & (tk > 0)
            ticks[idx] = np.where(valid, (mid[jj] - mid) / np.where(tk > 0, tk, np.nan), np.nan)
            dts[idx] = dt
        d[f"fwd_mid_ticks_{H}"] = ticks
        d[f"fwd_dt_{H}"] = dts
        d[f"fwd_valid_{H}"] = np.isfinite(ticks)
        d[f"fwd_up_{H}"] = np.where(np.isfinite(ticks), ticks > 0, np.nan)
        d[f"fwd_down_{H}"] = np.where(np.isfinite(ticks), ticks < 0, np.nan)
        d[f"fwd_flat_{H}"] = np.where(np.isfinite(ticks), ticks == 0, np.nan)
    return d


def label_columns(d: pd.DataFrame) -> List[str]:
    return sorted(c for c in d.columns if c.startswith(LABEL_PREFIX))


def assert_no_labels(columns: Iterable[str]) -> None:
    """Raise if a candidate reads an outcome. The PIT rule, enforced."""
    bad = sorted(c for c in columns if str(c).startswith(LABEL_PREFIX))
    if bad:
        raise ValueError(f"look-ahead: {bad} are forward outcomes, usable only as labels")


def label_coverage(d: pd.DataFrame, horizons: Sequence[int] = HORIZONS_S) -> Dict[str, dict]:
    """How many states actually earn a label, and why the rest do not.

    The `invalid_*` counts are the honest denominator: a horizon that only labels
    a third of the session is a horizon most of the session cannot speak about.
    """
    out: Dict[str, dict] = {}
    n = len(d)
    for H in horizons:
        tk = d[f"fwd_mid_ticks_{H}"]
        dt = d[f"fwd_dt_{H}"]
        valid = d[f"fwd_valid_{H}"]
        out[str(H)] = {
            "states": int(n),
            "labelled": int(valid.sum()),
            "labelled_pct": round(100.0 * valid.sum() / n, 2) if n else None,
            "no_forward_frame": int(dt.isna().sum()),
            "gap_over_2H": int(((dt > 2 * H) & dt.notna()).sum()),
            "up": int((tk > 0).sum()), "down": int((tk < 0).sum()),
            "flat": int((tk == 0).sum()),
            "flat_pct_of_labelled": round(100.0 * (tk == 0).sum() / valid.sum(), 2)
            if valid.sum() else None,
            "median_dt_s": round(float(dt.median()), 2) if dt.notna().any() else None,
        }
    return out
