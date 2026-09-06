"""Book reconstruction from top-N snapshots.

Input: the replayed ``books`` table (one row per snapshot per source with
``bid_levels`` / ``ask_levels`` as lists of (price, qty) — best first).

Output: the same rows with per-frame book quantities (OBSERVED, derived
arithmetically from observed levels) and per-frame level-diff events against
the previous snapshot of the same (source, symbol) (INFERRED: a snapshot diff
cannot see events shorter than the snapshot interval, and cannot separate
several adds/cancels at one level without order counts).

Nothing is dropped: crossed, locked, empty-side and duplicate-payload frames
are flagged and kept — the experiment's falsification stage removes them
explicitly, and reports what removing them did.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

Level = Tuple[float, float]
DEFAULT_TICK = 0.10


@dataclass(frozen=True)
class BookStats:
    best_bid: Optional[float]
    best_ask: Optional[float]
    bid_qty1: float
    ask_qty1: float
    bid_depth_top3: float
    ask_depth_top3: float
    bid_depth_top5: float
    ask_depth_top5: float
    bid_depth_all: float
    ask_depth_all: float
    n_bid: int
    n_ask: int
    spread: Optional[float]
    spread_ticks: Optional[float]
    mid: Optional[float]
    microprice: Optional[float]
    crossed: bool
    locked: bool
    one_sided: bool
    empty: bool
    largest_wall_side: Optional[str]
    largest_wall_price: Optional[float]
    largest_wall_qty: float
    largest_wall_share: float          # qty / (bid_depth_all + ask_depth_all)
    bid_weighted_depth: float          # Σ qty / (1 + distance_ticks) — weighted imbalance input
    ask_weighted_depth: float


def _depth(levels: Sequence[Level], k: Optional[int]) -> float:
    ls = levels if k is None else levels[:k]
    return float(sum(q for _, q in ls))


def book_stats(bids: Sequence[Level], asks: Sequence[Level], tick: float = DEFAULT_TICK) -> BookStats:
    bids = [tuple(x) for x in (bids or [])]
    asks = [tuple(x) for x in (asks or [])]
    bb = bids[0][0] if bids else None
    ba = asks[0][0] if asks else None
    bq1 = bids[0][1] if bids else 0.0
    aq1 = asks[0][1] if asks else 0.0
    spread = (ba - bb) if (bb is not None and ba is not None) else None
    mid = (ba + bb) / 2.0 if spread is not None else None
    micro = None
    if spread is not None and (bq1 + aq1) > 0:
        micro = (ba * bq1 + bb * aq1) / (bq1 + aq1)     # size-weighted mid
    ref = mid if mid is not None else (bb if bb is not None else ba)
    wb = sum(q / (1.0 + abs((ref - p) / tick)) for p, q in bids) if ref is not None else 0.0
    wa = sum(q / (1.0 + abs((p - ref) / tick)) for p, q in asks) if ref is not None else 0.0
    walls = [("bid", p, q) for p, q in bids] + [("ask", p, q) for p, q in asks]
    total = _depth(bids, None) + _depth(asks, None)
    if walls:
        side, p, q = max(walls, key=lambda x: x[2])
    else:
        side, p, q = None, None, 0.0
    return BookStats(
        best_bid=bb, best_ask=ba, bid_qty1=float(bq1), ask_qty1=float(aq1),
        bid_depth_top3=_depth(bids, 3), ask_depth_top3=_depth(asks, 3),
        bid_depth_top5=_depth(bids, 5), ask_depth_top5=_depth(asks, 5),
        bid_depth_all=_depth(bids, None), ask_depth_all=_depth(asks, None),
        n_bid=len(bids), n_ask=len(asks), spread=spread,
        spread_ticks=(round(spread / tick, 3) if spread is not None else None), mid=mid, microprice=micro,
        crossed=bool(spread is not None and spread < 0), locked=bool(spread is not None and spread == 0),
        one_sided=bool((bids and not asks) or (asks and not bids)), empty=bool(not bids and not asks),
        largest_wall_side=side, largest_wall_price=p, largest_wall_qty=float(q),
        largest_wall_share=(float(q) / total if total > 0 else 0.0),
        bid_weighted_depth=float(wb), ask_weighted_depth=float(wa),
    )


def diff_levels(prev: Sequence[Level], cur: Sequence[Level], side: str, touch_prev: Optional[float],
                touch_cur: Optional[float]) -> List[Dict[str, Any]]:
    """Per-price quantity changes between two snapshots of one side.

    kind: ADD (new level or qty up), REDUCE (qty down), REMOVE (level gone).
    at_touch: the level was the best price in the previous snapshot.
    through: the level lies strictly inside the range the best price moved
             through (a sweep consumed it) — INFERRED reading.
    """
    p_map = {p: q for p, q in (prev or [])}
    c_map = {p: q for p, q in (cur or [])}
    out: List[Dict[str, Any]] = []
    for p in sorted(set(p_map) | set(c_map), reverse=(side == "bid")):
        q0, q1 = p_map.get(p), c_map.get(p)
        if q0 is None and q1 is not None:
            kind, dq = "ADD", q1
        elif q1 is None and q0 is not None:
            kind, dq = "REMOVE", -q0
        elif q0 is not None and q1 is not None and q1 != q0:
            kind, dq = ("ADD" if q1 > q0 else "REDUCE"), q1 - q0
        else:
            continue
        through = False
        if touch_prev is not None and touch_cur is not None and touch_prev != touch_cur:
            lo, hi = sorted((touch_prev, touch_cur))
            through = lo <= p < hi if side == "ask" else lo < p <= hi
        out.append({"side": side, "price": p, "kind": kind, "dq": float(dq),
                    "q_prev": q0, "q_cur": q1, "at_touch": (p == touch_prev), "through": through})
    return out


def reconstruct_books(books: pd.DataFrame, tick_by_symbol: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """Add BookStats columns and level-diff events to every snapshot row."""
    if books is None or not len(books):
        return books
    b = books.sort_values(["source", "symbol", "t_recv", "seq"], kind="mergesort").reset_index(drop=True)
    stats_rows: List[Dict[str, Any]] = []
    events_col: List[List[Dict[str, Any]]] = []
    unchanged_run: List[int] = []
    for (src, sym), g in b.groupby(["source", "symbol"], sort=False):
        tick = (tick_by_symbol or {}).get(sym, DEFAULT_TICK) or DEFAULT_TICK
        prev_b: Optional[List[Level]] = None
        prev_a: Optional[List[Level]] = None
        prev_hash: Optional[str] = None
        run = 0
        for _, r in g.iterrows():
            bids = [tuple(x) for x in (r["bid_levels"] or [])]
            asks = [tuple(x) for x in (r["ask_levels"] or [])]
            st = book_stats(bids, asks, tick)
            d = st.__dict__.copy()
            d["_idx"] = r.name
            stats_rows.append(d)
            ev: List[Dict[str, Any]] = []
            if prev_b is not None:
                tb0 = prev_b[0][0] if prev_b else None
                tb1 = bids[0][0] if bids else None
                ta0 = prev_a[0][0] if prev_a else None
                ta1 = asks[0][0] if asks else None
                ev = diff_levels(prev_b, bids, "bid", tb0, tb1) + diff_levels(prev_a, asks, "ask", ta0, ta1)
            events_col.append(ev)
            run = run + 1 if (prev_hash is not None and r["body_sha256"] == prev_hash) else 0
            unchanged_run.append(run)
            prev_b, prev_a, prev_hash = bids, asks, r["body_sha256"]
    st_df = pd.DataFrame(stats_rows).set_index("_idx").sort_index()
    out = pd.concat([b, st_df], axis=1)
    out["level_events"] = events_col
    out["n_level_events"] = [len(e) for e in events_col]
    out["unchanged_run"] = unchanged_run
    # imbalances (OBSERVED arithmetic on observed quantities)
    def _imb(x: pd.Series, y: pd.Series) -> pd.Series:
        s = x + y
        return ((x - y) / s).where(s > 0)
    out["imb_l1"] = _imb(out["bid_qty1"], out["ask_qty1"])
    out["imb_top3"] = _imb(out["bid_depth_top3"], out["ask_depth_top3"])
    out["imb_top5"] = _imb(out["bid_depth_top5"], out["ask_depth_top5"])
    out["imb_all"] = _imb(out["bid_depth_all"], out["ask_depth_all"])
    out["imb_weighted"] = _imb(out["bid_weighted_depth"], out["ask_weighted_depth"])
    out.attrs["truth"] = dict(getattr(books, "attrs", {}).get("truth", {}))
    out.attrs["truth"].update({"book_stats": "OBSERVED (arithmetic on observed levels)",
                               "level_events": "INFERRED (snapshot diff; sub-interval events invisible)"})
    return out


def explode_events(recon: pd.DataFrame) -> pd.DataFrame:
    """One row per level event with the frame's source/symbol/time."""
    rows = []
    for _, r in recon.iterrows():
        for e in r["level_events"]:
            rows.append({"source": r["source"], "symbol": r["symbol"], "t_recv": r["t_recv"], "seq": r["seq"], **e})
    return pd.DataFrame(rows)
