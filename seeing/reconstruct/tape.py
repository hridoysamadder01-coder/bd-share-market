"""Tape reconstruction.

Two tapes can be reconstructed from the obtained sources, both INFERRED
aggregates over OBSERVED cumulative totals; individual prints are
NOT_OBSERVABLE until a Time & Sales source is attached (see
``seeing.capture.adapters.broker_export`` / ``fix_md``).

1. ``interval_tape`` from the exchange-stamped cumulative rows (LankaBD
   MkSecondData): for consecutive rows of one symbol
       d_trades = Δcum_trades, d_volume = Δcum_volume, d_value = Δcum_value,
       vwap = d_value / d_volume, last_price = price of the later row.
   A negative delta is a source-side reset or correction: kept and flagged
   (``monotone_break``), never repaired.

2. ``snapshot_tape`` from the day totals carried by every depth snapshot
   (noOfTrade / totalVolume / totalValueMN): the same deltas between
   consecutive snapshots of one (source, symbol). Coarser stamps (receipt
   time) but aligned with the book by construction.

Trade side is INFERRED by the quote rule on the interval VWAP against the last
book seen before the interval started (``classify_side``): +1 buyer-initiated
if vwap ≥ ask, −1 if vwap ≤ bid, otherwise the position inside the spread
scaled to (−1, 1); confidence is LOW if the touch moved inside the interval,
and the side is EXACT by construction when the book was locked at a limit.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def interval_tape(tape: pd.DataFrame) -> pd.DataFrame:
    if tape is None or not len(tape):
        return pd.DataFrame()
    t = tape.sort_values(["symbol", "t_source_ms", "row_index"], kind="mergesort").copy()
    g = t.groupby("symbol", sort=False)
    t["t_prev"] = g["t_source"].shift()
    # The first row of a symbol's day is the first change since the open; its
    # delta is the cumulative value itself (the day starts at zero). Flagged.
    t["first_row_of_day"] = g.cumcount() == 0
    t["d_trades"] = g["cum_trades"].diff().where(~t["first_row_of_day"], t["cum_trades"])
    t["d_volume"] = g["cum_volume"].diff().where(~t["first_row_of_day"], t["cum_volume"])
    t["d_value_mn"] = g["cum_value_mn"].diff().where(~t["first_row_of_day"], t["cum_value_mn"])
    t["dt_s"] = (t["t_source"] - t["t_prev"]).dt.total_seconds()
    t["monotone_break"] = (t["d_trades"] < 0) | (t["d_volume"] < 0) | (t["d_value_mn"] < 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        t["vwap"] = (t["d_value_mn"] * 1e6 / t["d_volume"]).where(t["d_volume"] > 0)
    t["last_price"] = t["price"]
    t["price_prev"] = g["price"].shift()
    t["tick_dir"] = np.sign(t["price"] - t["price_prev"]).fillna(0)
    t.attrs["truth"] = {"d_trades": "INFERRED (Δ cumulative, exchange-stamped)", "d_volume": "INFERRED",
                        "d_value_mn": "INFERRED", "vwap": "INFERRED", "trade_prints": "NOT_OBSERVABLE",
                        "t_source": "OBSERVED"}
    return t


def snapshot_tape(books: pd.DataFrame) -> pd.DataFrame:
    if books is None or not len(books):
        return pd.DataFrame()
    cols = ["source", "symbol", "t_recv", "seq", "day_trades", "day_volume", "day_value_mn", "ltp"]
    b = books[cols].sort_values(["source", "symbol", "t_recv", "seq"], kind="mergesort").copy()
    g = b.groupby(["source", "symbol"], sort=False)
    b["t_prev"] = g["t_recv"].shift()
    b["d_trades"] = g["day_trades"].diff()
    b["d_volume"] = g["day_volume"].diff()
    b["d_value_mn"] = g["day_value_mn"].diff()
    b["dt_s"] = (b["t_recv"] - b["t_prev"]).dt.total_seconds()
    b["monotone_break"] = (b["d_trades"] < 0) | (b["d_volume"] < 0) | (b["d_value_mn"] < 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        b["vwap"] = (b["d_value_mn"] * 1e6 / b["d_volume"]).where(b["d_volume"] > 0)
    b.attrs["truth"] = {"d_trades": "INFERRED (Δ day totals between snapshots)", "vwap": "INFERRED"}
    return b


def classify_side(vwap: Optional[float], bid: Optional[float], ask: Optional[float],
                  touch_moved: bool = False) -> Dict[str, Any]:
    """Quote rule on an interval VWAP. Returns side_score in [-1, 1], truth, confidence, rule."""
    if vwap is None or not np.isfinite(vwap):
        return {"side_score": np.nan, "side_truth": "NOT_OBSERVABLE", "side_conf": "none",
                "side_rule": "no traded volume in interval"}
    if bid is not None and ask is not None and bid == ask:
        # locked book (limit / floor): every print is against the resting side — exact by construction
        return {"side_score": 0.0, "side_truth": "OBSERVED", "side_conf": "exact",
                "side_rule": "locked book: side known by construction (resting side absorbs)"}
    if bid is None and ask is None:
        return {"side_score": np.nan, "side_truth": "NOT_OBSERVABLE", "side_conf": "none",
                "side_rule": "no pre-interval quote"}
    eps = 1e-9   # VWAP is value/volume in floating point; a print at the ask must classify as at the ask
    if ask is not None and vwap >= ask - eps:
        score = 1.0
    elif bid is not None and vwap <= bid + eps:
        score = -1.0
    elif bid is not None and ask is not None and ask > bid:
        score = 2.0 * (vwap - bid) / (ask - bid) - 1.0
    else:
        score = np.nan
    return {"side_score": float(score) if np.isfinite(score) else np.nan, "side_truth": "INFERRED",
            "side_conf": "low" if touch_moved else "medium",
            "side_rule": "quote rule on interval VWAP vs last pre-interval book" +
                         (" (touch moved inside interval)" if touch_moved else "")}
