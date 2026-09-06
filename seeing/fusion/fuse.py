"""Fuse every source into ONE timestamp-aligned market state per symbol.

Frame clock
    The LankaBD depth snapshot receipt time is the primary frame clock (it is
    the most frequent book sensor). When only the dsebd depth snapshot exists
    inside a coalescing window the frame is built from it and flagged
    ``book_primary = "dsebd_depth"``.

Alignment rules (all recorded on the frame)
    * dsebd depth: nearest snapshot within ``coalesce_s`` → ``book_agree``
      (identical levels), ``book_diff`` (number of differing levels), ``xcheck_dt_s``.
    * watch (all-symbol, exchange-stamped): as-of backward join on receipt
      time → ``watch_age_s`` (receipt age) and ``watch_src_age_s`` (frame time
      minus the exchange stamp).
    * tape (exchange-stamped cumulative rows): all rows with
      t_prev_frame < t_source ≤ t_frame are summed into the interval → the
      per-frame INFERRED tape; ``tape_rows`` counts them.
    * snapshot tape: Δ day totals between consecutive primary frames.
    * side: quote rule on the interval VWAP vs the previous frame's book.
    * market stats: as-of backward; circuit reference: per symbol (latest poll
      at or before the frame, else the first of the day, flagged); block prints:
      per symbol per day (count, quantity).
    * shares_to_door: Σ ask qty at prices ≤ upper_limit; ``door_visible`` says
      whether the limit lies within the displayed ask levels.

Nothing is interpolated. A missing join leaves NaN and a recorded age/flag.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..reconstruct.book import reconstruct_books
from ..reconstruct.events import per_frame_event_summary, queue_events
from ..reconstruct.tape import classify_side, interval_tape
from .state import BOOK_COLS, FRAME_TRUTH


def _levels_equal(a, b) -> bool:
    return [tuple(x) for x in (a or [])] == [tuple(x) for x in (b or [])]


def _n_diff(a, b) -> int:
    sa = {tuple(x) for x in (a or [])}
    sb = {tuple(x) for x in (b or [])}
    return len(sa ^ sb)


def fuse(tables: Dict[str, Any], coalesce_s: float = 6.0, primary: str = "lankabd_depth",
         secondary: str = "dsebd_depth") -> pd.DataFrame:
    books = tables.get("books")
    if books is None or not len(books):
        return pd.DataFrame()
    circuit = tables.get("circuit")
    tick_by_symbol: Dict[str, float] = {}
    if circuit is not None and len(circuit):
        tick_by_symbol = circuit.groupby("symbol")["tick_size"].last().dropna().to_dict()
    recon = reconstruct_books(books, tick_by_symbol)
    prim = recon[recon["source"] == primary].copy()
    sec = recon[recon["source"] == secondary].copy()

    frames: List[pd.DataFrame] = []
    for sym, gp in prim.groupby("symbol", sort=False):
        gp = gp.sort_values("t_recv").copy()
        gp["book_primary"] = primary
        gs = sec[sec["symbol"] == sym].sort_values("t_recv")
        # secondary frames with no primary within the window become frames themselves
        if len(gs):
            used = np.zeros(len(gs), dtype=bool)
            agree, ndiff, dt = [], [], []
            s_times = gs["t_recv"].values
            for _, r in gp.iterrows():
                j = np.searchsorted(s_times, r["t_recv"].to_datetime64())
                cands = [k for k in (j - 1, j) if 0 <= k < len(gs)]
                best = None
                for k in cands:
                    d = abs((gs.iloc[k]["t_recv"] - r["t_recv"]).total_seconds())
                    if d <= coalesce_s and (best is None or d < best[1]):
                        best = (k, d)
                if best is None:
                    agree.append(np.nan); ndiff.append(np.nan); dt.append(np.nan)
                else:
                    k, d = best
                    used[k] = True
                    rs = gs.iloc[k]
                    agree.append(float(_levels_equal(r["bid_levels"], rs["bid_levels"]) and
                                       _levels_equal(r["ask_levels"], rs["ask_levels"])))
                    ndiff.append(_n_diff(r["bid_levels"], rs["bid_levels"]) + _n_diff(r["ask_levels"], rs["ask_levels"]))
                    dt.append(d)
            gp["book_agree"], gp["book_diff"], gp["xcheck_dt_s"] = agree, ndiff, dt
            orphan = gs[~used].copy()
            if len(orphan):
                orphan["book_primary"] = secondary
                orphan["book_agree"] = np.nan; orphan["book_diff"] = np.nan; orphan["xcheck_dt_s"] = np.nan
                gp = pd.concat([gp, orphan]).sort_values("t_recv")
        else:
            gp["book_agree"] = np.nan; gp["book_diff"] = np.nan; gp["xcheck_dt_s"] = np.nan
        frames.append(gp)
    if not frames:
        return pd.DataFrame()
    f = pd.concat(frames).sort_values(["symbol", "t_recv", "seq"], kind="mergesort").reset_index(drop=True)
    f = f.rename(columns={"t_recv": "t_frame"})
    f["frame_no"] = f.groupby("symbol").cumcount()
    f["t_prev_frame"] = f.groupby("symbol")["t_frame"].shift()
    f["frame_dt_s"] = (f["t_frame"] - f["t_prev_frame"]).dt.total_seconds()

    # ---- watch (as-of backward per symbol)
    watch = tables.get("watch")
    if watch is not None and len(watch):
        w = watch.sort_values("t_recv")[["symbol", "t_recv", "t_source", "ltp", "day_trades", "day_volume",
                                         "day_value_mn", "market_category", "high", "low", "open", "yclose"]]
        w = w.rename(columns={c: f"w_{c}" for c in w.columns if c not in ("symbol", "t_recv")})
        f = pd.merge_asof(f.sort_values("t_frame"), w.sort_values("t_recv"), left_on="t_frame", right_on="t_recv",
                          by="symbol", direction="backward").sort_values(["symbol", "t_frame"]).reset_index(drop=True)
        f["watch_age_s"] = (f["t_frame"] - f["t_recv"]).dt.total_seconds()
        f["watch_src_age_s"] = (f["t_frame"] - f["w_t_source"]).dt.total_seconds()
        f = f.drop(columns=["t_recv"])
        # market breadth from the same watch poll
        br = watch.groupby("t_recv").apply(lambda d: pd.Series({
            "mkt_up": int((d["ltp"] > d["yclose"]).sum()), "mkt_down": int((d["ltp"] < d["yclose"]).sum()),
            "mkt_n": int(len(d))})).reset_index()
        f = pd.merge_asof(f.sort_values("t_frame"), br.sort_values("t_recv"), left_on="t_frame", right_on="t_recv",
                          direction="backward").sort_values(["symbol", "t_frame"]).reset_index(drop=True).drop(columns=["t_recv"])
    else:
        f["watch_age_s"] = np.nan

    # ---- exchange-stamped tape summed into frame intervals
    it = interval_tape(tables.get("tape"))
    f["tape_d_trades"] = np.nan; f["tape_d_volume"] = np.nan; f["tape_d_value_mn"] = np.nan
    f["tape_vwap"] = np.nan; f["tape_rows"] = 0; f["tape_last_price"] = np.nan; f["tape_monotone_break"] = False
    if len(it):
        for sym, gi in it.groupby("symbol"):
            m = f["symbol"] == sym
            idx = f.index[m]
            tf = f.loc[m, "t_frame"].values
            tp = f.loc[m, "t_prev_frame"].values
            ts = gi["t_source"].values
            for i, (a, b) in zip(idx, zip(tp, tf)):
                if pd.isna(a):
                    continue
                sel = gi[(ts > a) & (ts <= b)]
                if len(sel):
                    dv = sel["d_volume"].sum(); dval = sel["d_value_mn"].sum()
                    f.at[i, "tape_d_trades"] = sel["d_trades"].sum()
                    f.at[i, "tape_d_volume"] = dv
                    f.at[i, "tape_d_value_mn"] = dval
                    f.at[i, "tape_vwap"] = (dval * 1e6 / dv) if dv > 0 else np.nan
                    f.at[i, "tape_rows"] = len(sel)
                    f.at[i, "tape_last_price"] = sel["price"].iloc[-1]
                    f.at[i, "tape_monotone_break"] = bool(sel["monotone_break"].any())

    # ---- snapshot tape from the frames' own day totals
    g = f.groupby("symbol")
    f["snap_d_trades"] = g["day_trades"].diff()
    f["snap_d_volume"] = g["day_volume"].diff()
    f["snap_d_value_mn"] = g["day_value_mn"].diff()
    with np.errstate(divide="ignore", invalid="ignore"):
        f["snap_vwap"] = (f["snap_d_value_mn"] * 1e6 / f["snap_d_volume"]).where(f["snap_d_volume"] > 0)
    f["snap_monotone_break"] = (f["snap_d_trades"] < 0) | (f["snap_d_volume"] < 0)
    # unified interval volume: exchange-stamped tape when present, else snapshot Δ
    f["int_d_volume"] = f["tape_d_volume"].where(f["tape_rows"] > 0, f["snap_d_volume"])
    f["int_d_trades"] = f["tape_d_trades"].where(f["tape_rows"] > 0, f["snap_d_trades"])
    f["int_vwap"] = f["tape_vwap"].where(f["tape_rows"] > 0, f["snap_vwap"])
    f["int_source"] = np.where(f["tape_rows"] > 0, "exchange_stamped_tape", np.where(f["snap_d_volume"].notna(), "snapshot_day_totals", "none"))

    # ---- side (quote rule vs previous frame's book)
    prev_bid = g["best_bid"].shift(); prev_ask = g["best_ask"].shift()
    touch_moved = (f["best_bid"] != prev_bid) | (f["best_ask"] != prev_ask)
    sides = [classify_side(v if pd.notna(v) else None, b if pd.notna(b) else None, a if pd.notna(a) else None, bool(tm))
             for v, b, a, tm in zip(f["int_vwap"], prev_bid, prev_ask, touch_moved)]
    f["side_score"] = [s["side_score"] for s in sides]
    f["side_truth"] = [s["side_truth"] for s in sides]
    f["side_conf"] = [s["side_conf"] for s in sides]
    f["signed_int_volume"] = f["side_score"] * f["int_d_volume"]

    # ---- queue / event reconstruction per frame (split bounded by interval volume)
    ev = queue_events(f.assign(t_recv=f["t_frame"]), interval_volume=f["int_d_volume"])
    evs = per_frame_event_summary(ev)
    if len(evs):
        f = f.join(evs, how="left")
        for c in evs.columns:
            f[c] = f[c].fillna(0)
    f.attrs["events"] = ev

    # ---- market stats (as-of)
    mk = tables.get("market")
    if mk is not None and len(mk):
        m = mk.sort_values("t_recv")[["t_recv", "market_trades", "market_volume", "market_value_mn", "up", "down", "flat"]]
        m = m.rename(columns={"up": "mkt_stat_up", "down": "mkt_stat_down", "flat": "mkt_stat_flat"})
        f = pd.merge_asof(f.sort_values("t_frame"), m, left_on="t_frame", right_on="t_recv", direction="backward") \
            .sort_values(["symbol", "t_frame"]).reset_index(drop=True)
        f["market_age_s"] = (f["t_frame"] - f["t_recv"]).dt.total_seconds()
        f = f.drop(columns=["t_recv"])

    # ---- circuit reference (latest per symbol at/before frame; else first; flagged)
    f["upper_limit"] = np.nan; f["lower_limit"] = np.nan; f["tick_size"] = np.nan; f["ref_from_future"] = False
    if circuit is not None and len(circuit):
        c = circuit.sort_values("t_recv")
        for sym, gc in c.groupby("symbol"):
            m = f["symbol"] == sym
            if not m.any():
                continue
            times = gc["t_recv"].values
            for i in f.index[m]:
                j = np.searchsorted(times, f.at[i, "t_frame"].to_datetime64(), side="right") - 1
                row = gc.iloc[j] if j >= 0 else gc.iloc[0]
                f.at[i, "upper_limit"] = row["upper_limit"]; f.at[i, "lower_limit"] = row["lower_limit"]
                f.at[i, "tick_size"] = row["tick_size"]; f.at[i, "ref_from_future"] = bool(j < 0)
    # shares to the door (Q13) and limit state
    std, vis, at_up, at_lo = [], [], [], []
    for asks, bids, ul, ll, bb, ba in zip(f["ask_levels"], f["bid_levels"], f["upper_limit"], f["lower_limit"],
                                          f["best_bid"], f["best_ask"]):
        if pd.isna(ul) or not asks:
            std.append(np.nan); vis.append(False)
        else:
            q = sum(qty for p, qty in asks if p <= ul + 1e-9)
            std.append(q); vis.append(bool(asks[-1][0] >= ul - 1e-9))
        at_up.append(bool(pd.notna(ul) and pd.notna(bb) and abs(bb - ul) < 1e-9))
        at_lo.append(bool(pd.notna(ll) and pd.notna(ba) and abs(ba - ll) < 1e-9))
    f["shares_to_door"] = std; f["door_visible"] = vis; f["bid_at_upper_limit"] = at_up; f["ask_at_lower_limit"] = at_lo

    # ---- block prints per symbol per day
    bl = tables.get("block")
    if bl is not None and len(bl):
        agg = bl.groupby(["symbol", "block_date"]).agg(block_trades=("block_trades", "max"),
                                                       block_quantity=("block_quantity", "max")).reset_index()
        latest = agg.sort_values("block_date").groupby("symbol").last()
        f["block_trades_today"] = f["symbol"].map(latest["block_trades"]).fillna(0)
        f["block_quantity_today"] = f["symbol"].map(latest["block_quantity"]).fillna(0)
    else:
        f["block_trades_today"] = 0; f["block_quantity_today"] = 0

    f.attrs["truth"] = dict(FRAME_TRUTH)
    return f


def frames_for_storage(f: pd.DataFrame) -> pd.DataFrame:
    """List columns as JSON strings so the table round-trips through parquet/CSV."""
    g = f.copy()
    for c in ("bid_levels", "ask_levels", "level_events", "raw_keys", "zero_fields", "bid_orders_per_level",
              "ask_orders_per_level", "holidays", "sessions"):
        if c in g.columns:
            g[c] = g[c].apply(lambda x: json.dumps(x, default=float) if isinstance(x, (list, dict)) else x)
    # attrs may hold DataFrames (events, transitions) — parquet metadata must be JSON
    truth = {k: (v.value if hasattr(v, "value") else str(v)) for k, v in (f.attrs.get("truth") or {}).items()}
    g.attrs = {"truth": truth}
    return g
