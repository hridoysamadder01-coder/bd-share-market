"""Strict point-in-time cross-sectional state.

The bug this replaces
---------------------
`research/edge_discovery/families.py` and `run.py` both did::

    f["_tbin"] = f["t_frame"].dt.floor("60s")
    f["market_pressure"] = f.groupby("_tbin")["P"].transform("mean")
    f["xs_rank"] = f.groupby("_tbin")["P"].rank(pct=True) - 0.5

A bucket is not an instant. Every row in the 10:00:00–10:00:59 bucket saw every
other row in it, so a decision at 10:00:05 was computed from an observation at
10:00:55. That is future information, and `CTX_xs_rank_top` — reported at
+21.75 pp — was measured with it. Nothing built on that number is trustworthy
until it is rebuilt here.

What replaces it
----------------
For a decision row (entity i, time t), the market state uses, for every other
entity j, **only j's latest observation with t_j <= t**. Rows sharing a timestamp
are processed as one batch: all of them enter the state, then all of them read it.
That is the only ordering-independent choice, and it keeps genuine same-instant
cross-sections (all symbols closing on the same date) usable, which is not
leakage — the decision is taken after that instant, not during it.

`strict_before=True` tightens it to `t_j < t`, excluding the same-instant batch
entirely. It is the adversarial setting: if a result only survives with the
same-instant batch included, that is worth knowing, so both are reported.

Staleness is never hidden
-------------------------
An entity that stopped printing does not vanish from the state; its last value
persists and its **age** is exposed. `max_age` drops entities staler than a
cutoff. No cutoff is tuned here — callers pass a predeclared grid and the
response curve is reported, because a freshness threshold picked to maximise a
result is a fitted parameter wearing a hygiene costume.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd

EPS = 1e-12

# Columns produced. Named `_asof` so a leaked non-causal column cannot be mistaken
# for one of these in a downstream frame.
ASOF_COLUMNS = (
    "market_mean_asof", "market_median_asof", "market_mad_asof", "market_n_asof",
    "xs_rank_asof", "share_resid_asof", "share_z_asof",
    "age_self_s", "age_median_s", "age_max_s", "n_stale_dropped",
)


def _mad(x: np.ndarray, med: float) -> float:
    return float(np.median(np.abs(x - med))) if x.size else np.nan


@dataclass
class AsofResult:
    frame: pd.DataFrame
    n_rows: int
    n_entities: int
    n_instants: int
    strict_before: bool
    max_age_s: Optional[float]


def asof_cross_section(df: pd.DataFrame, *, value: str, entity: str = "symbol",
                       time: str = "ts", strict_before: bool = False,
                       max_age_s: Optional[float] = None,
                       min_others: int = 3) -> pd.DataFrame:
    """Cross-sectional market state computed with no future information.

    Returns `df` with `ASOF_COLUMNS` added. Every statistic is **leave-one-out**:
    entity i is excluded from its own market, so `share_resid_asof` cannot be
    diluted by the share it is measuring.

    Rows are never reordered in the output; the internal ordering is by time only,
    with ties handled as a batch, so the result does not depend on input order.
    """
    if value not in df.columns:
        raise KeyError(f"no value column {value!r}")
    out = df.copy()
    n = len(out)
    for c in ASOF_COLUMNS:
        out[c] = np.nan

    t = pd.to_datetime(out[time]).to_numpy(dtype="datetime64[ns]")
    ent = out[entity].astype(str).to_numpy()
    val = pd.to_numeric(out[value], errors="coerce").to_numpy(dtype=float)

    codes, uniq = pd.factorize(ent, sort=True)
    n_ent = len(uniq)
    last_val = np.full(n_ent, np.nan)
    last_t = np.full(n_ent, np.datetime64("NaT"), dtype="datetime64[ns]")

    order = np.argsort(t, kind="stable")           # time only; ties keep a batch
    t_sorted = t[order]
    # Boundaries of each distinct instant.
    starts = np.flatnonzero(np.r_[True, t_sorted[1:] != t_sorted[:-1]])
    ends = np.r_[starts[1:], len(t_sorted)]

    res = {c: np.full(n, np.nan) for c in ASOF_COLUMNS}

    for s, e in zip(starts, ends):
        idx = order[s:e]                            # every row at this instant
        now = t_sorted[s]

        if strict_before:
            # Read the state BEFORE this instant's rows are applied.
            snapshot_val, snapshot_t = last_val.copy(), last_t.copy()

        for i in idx:                               # apply the batch
            c = codes[i]
            if not np.isnan(val[i]):
                last_val[c] = val[i]
                last_t[c] = t[i]

        cur_val, cur_t = (snapshot_val, snapshot_t) if strict_before else (last_val, last_t)

        seen = ~np.isnan(cur_val)
        age_all = np.full(n_ent, np.nan)
        if seen.any():
            age_all[seen] = (now - cur_t[seen]) / np.timedelta64(1, "s")

        fresh = seen.copy()
        n_dropped = 0
        if max_age_s is not None and seen.any():
            too_old = seen & (age_all > max_age_s)
            n_dropped = int(too_old.sum())
            fresh = seen & ~too_old

        for i in idx:
            c = codes[i]
            mask = fresh.copy()
            if mask[c]:
                mask[c] = False                     # leave-one-out, always
            others = cur_val[mask]
            k = others.size
            res["market_n_asof"][i] = k
            res["n_stale_dropped"][i] = n_dropped
            res["age_self_s"][i] = age_all[c] if seen[c] else np.nan
            if k:
                ages = age_all[mask]
                res["age_median_s"][i] = float(np.nanmedian(ages))
                res["age_max_s"][i] = float(np.nanmax(ages))
            if k < min_others or np.isnan(val[i]):
                continue
            med = float(np.median(others))
            mad = _mad(others, med)
            res["market_mean_asof"][i] = float(np.mean(others))
            res["market_median_asof"][i] = med
            res["market_mad_asof"][i] = mad
            # Rank of this row's own value among the others, in [0, 1].
            res["xs_rank_asof"][i] = float((others < val[i]).sum() + 0.5 * (others == val[i]).sum()) / k
            res["share_resid_asof"][i] = val[i] - med
            res["share_z_asof"][i] = (val[i] - med) / (mad + EPS) if np.isfinite(mad) else np.nan

    for c in ASOF_COLUMNS:
        out[c] = res[c]
    return out


def audit(df: pd.DataFrame, *, value: str, entity: str = "symbol",
          time: str = "ts") -> Dict[str, Any]:
    """Numbers a causality audit needs, computed from the frame itself."""
    t = pd.to_datetime(df[time])
    return {
        "n_rows": int(len(df)),
        "n_entities": int(df[entity].nunique()),
        "n_instants": int(t.nunique()),
        "rows_per_instant_max": int(t.value_counts().max()) if len(df) else 0,
        "value_notna": int(pd.to_numeric(df[value], errors="coerce").notna().sum()),
    }
