"""Event / queue reconstruction.

What is OBSERVED at snapshot cadence: the quantity resting at each visible
price, and how it changed since the last snapshot. What is INFERRED: the split
of a reduction at the touch into *traded* (bounded by the volume that printed
in the interval) and *cancelled* (the remainder), the labelling of an ADD at a
price that was reduced or removed within the last ``k`` frames as a
REPLENISH, and a REMOVE/REDUCE of a level the best price moved through as a
SWEEP. What is NOT_OBSERVABLE: order counts per level, order identities,
queue position, adds and cancels that net out inside one interval.

The function is deliberately conservative: when the interval traded volume
is unknown (no tape row), the split is NOT_OBSERVABLE and the reduction is
recorded as ``REDUCE_UNSPLIT``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def queue_events(recon: pd.DataFrame, interval_volume: Optional[pd.Series] = None, replenish_k: int = 6) -> pd.DataFrame:
    """One row per level event with an inferred class.

    ``recon`` is the output of ``reconstruct_books``; ``interval_volume`` (same
    index) is the traded volume between the previous and this snapshot for
    the same (source, symbol), or None.
    """
    rows: List[Dict[str, Any]] = []
    recent_hits: Dict[tuple, List[tuple]] = {}      # (source, symbol, side, price) -> [frame_no,...]
    frame_no: Dict[tuple, int] = {}
    for idx, r in recon.iterrows():
        key = (r["source"], r["symbol"])
        n = frame_no.get(key, 0)
        frame_no[key] = n + 1
        vol = None if interval_volume is None else interval_volume.get(idx)
        vol_left = float(vol) if (vol is not None and np.isfinite(vol) and vol > 0) else None
        for e in r["level_events"]:
            cls = e["kind"]
            traded = cancelled = np.nan
            truth = "OBSERVED"
            lk = (r["source"], r["symbol"], e["side"], e["price"])
            if e["kind"] in ("REDUCE", "REMOVE"):
                if e["through"]:
                    cls = "SWEEP"
                    truth = "INFERRED"
                elif e["at_touch"]:
                    if vol_left is None:
                        cls = "REDUCE_UNSPLIT"
                        truth = "NOT_OBSERVABLE(split)"
                    else:
                        traded = min(-e["dq"], vol_left)
                        cancelled = -e["dq"] - traded
                        vol_left -= traded
                        cls = "TOUCH_CONSUMED" if cancelled <= 0 else "TOUCH_REDUCED_MIXED"
                        truth = "INFERRED"
                else:
                    cls = "CANCEL_AWAY"     # away from the touch nothing trades in a price-time book
                    truth = "INFERRED"
                recent_hits.setdefault(lk, []).append(n)
            elif e["kind"] == "ADD":
                hits = [f for f in recent_hits.get(lk, []) if n - f <= replenish_k]
                if hits and e["q_prev"] is None:
                    cls = "REPLENISH"
                    truth = "INFERRED"
                elif hits:
                    cls = "REFILL"
                    truth = "INFERRED"
                elif e["at_touch"]:
                    cls = "ADD_AT_TOUCH"
                else:
                    cls = "ADD_AWAY"
            rows.append({"source": r["source"], "symbol": r["symbol"], "t_recv": r["t_recv"], "seq": r["seq"],
                         "frame_idx": idx, **e, "event_class": cls, "traded_est": traded,
                         "cancelled_est": cancelled, "event_truth": truth})
    df = pd.DataFrame(rows)
    df.attrs["truth"] = {"dq": "OBSERVED", "event_class": "INFERRED", "traded_est": "INFERRED (bounded by interval volume)",
                         "orders_per_level": "NOT_OBSERVABLE", "queue_position": "NOT_OBSERVABLE"}
    return df


def per_frame_event_summary(events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate events back to one row per frame_idx — the fusion layer joins this."""
    if events is None or not len(events):
        return pd.DataFrame()
    g = events.groupby("frame_idx")
    def _cnt(mask_col: str, val: str):
        return g.apply(lambda d: int((d[mask_col] == val).sum()))
    out = pd.DataFrame({
        "ev_n": g.size(),
        "ev_bid_add_qty": g.apply(lambda d: float(d.loc[(d.side == "bid") & (d.dq > 0), "dq"].sum())),
        "ev_bid_reduce_qty": g.apply(lambda d: float(-d.loc[(d.side == "bid") & (d.dq < 0), "dq"].sum())),
        "ev_ask_add_qty": g.apply(lambda d: float(d.loc[(d.side == "ask") & (d.dq > 0), "dq"].sum())),
        "ev_ask_reduce_qty": g.apply(lambda d: float(-d.loc[(d.side == "ask") & (d.dq < 0), "dq"].sum())),
        "ev_bid_replenish": g.apply(lambda d: int(((d.side == "bid") & d.event_class.isin(["REPLENISH", "REFILL"])).sum())),
        "ev_ask_replenish": g.apply(lambda d: int(((d.side == "ask") & d.event_class.isin(["REPLENISH", "REFILL"])).sum())),
        "ev_ask_touch_consumed": g.apply(lambda d: float(d.loc[(d.side == "ask") & d.event_class.isin(["TOUCH_CONSUMED", "TOUCH_REDUCED_MIXED"]), "traded_est"].sum())),
        "ev_bid_touch_consumed": g.apply(lambda d: float(d.loc[(d.side == "bid") & d.event_class.isin(["TOUCH_CONSUMED", "TOUCH_REDUCED_MIXED"]), "traded_est"].sum())),
        "ev_ask_cancel_away": g.apply(lambda d: float(-d.loc[(d.side == "ask") & (d.event_class == "CANCEL_AWAY"), "dq"].sum())),
        "ev_bid_cancel_away": g.apply(lambda d: float(-d.loc[(d.side == "bid") & (d.event_class == "CANCEL_AWAY"), "dq"].sum())),
        "ev_sweeps": g.apply(lambda d: int((d.event_class == "SWEEP").sum())),
    })
    return out
