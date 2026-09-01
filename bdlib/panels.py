"""Coverage panels — the hard partition of this dataset.

On 2024-02-22 the reporting universe collapses from 381 to 88 symbols. That is
the file changing basis, not the market changing behaviour. Every cross-sectional
statistic (`xs_*`, `market_ret`, any rank or breadth measure) therefore means
something different on either side, and a number pooled across the break is not
a market fact — it is an artefact of two stitched sources.

So panels are explicit, and pooling is refused rather than discouraged:
`assert_single_panel()` raises if a frame handed to an aggregate spans the break.

  PRIMARY   2012-10-01 … 2024-02-20   full universe (~380 symbols at the end)
  POSTBREAK 2024-02-22 … present      ~88 symbols
"""
from __future__ import annotations

import pandas as pd

from . import config as C

PANELS = {
    "PRIMARY": C.PANEL_PRIMARY,
    "POSTBREAK": C.PANEL_POSTBREAK,
}


def label(df: pd.DataFrame) -> pd.Series:
    """Panel name per row; NaN for rows in neither window (e.g. the break day)."""
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    for name, (a, b) in PANELS.items():
        m = df["ts"].between(pd.Timestamp(a), pd.Timestamp(b))
        out = out.mask(m, name)
    return out


def select(df: pd.DataFrame, panel: str) -> pd.DataFrame:
    if panel not in PANELS:
        raise ValueError(f"unknown panel {panel!r}; expected one of {sorted(PANELS)}")
    a, b = PANELS[panel]
    out = df[df["ts"].between(pd.Timestamp(a), pd.Timestamp(b))].copy()
    out.attrs["panel"] = panel
    return out


def assert_single_panel(df: pd.DataFrame, what: str = "this aggregate") -> str:
    """Refuse to compute anything cross-sectional over a frame that spans the break."""
    labs = set(label(df).dropna().unique())
    if len(labs) > 1:
        raise ValueError(
            f"{what} spans the coverage break {C.COVERAGE_BREAK_DATE} "
            f"({sorted(labs)}). Cross-sectional statistics are not comparable "
            f"across it — run each panel separately and report them separately.")
    return labs.pop() if labs else "EMPTY"


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """Rows/symbols/date-range per panel — printed by every experiment."""
    lab = label(df)
    rows = []
    for name in list(PANELS) + [None]:
        m = lab.isna() if name is None else (lab == name)
        if not m.any():
            continue
        d = df[m]
        rows.append({
            "panel": name or "(outside panels)",
            "rows": int(len(d)),
            "symbols": int(d["symbol"].nunique()),
            "first": str(d["ts"].min().date()),
            "last": str(d["ts"].max().date()),
            "median_symbols_per_day": int(
                d.groupby(d["ts"].dt.date)["symbol"].nunique().median()),
        })
    return pd.DataFrame(rows)
