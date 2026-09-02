#!/usr/bin/env python3
"""PHASE 1 DATA QA — DSE trade-minute dataset (Muntasib-creator/DSE_dataset). v2.

Detection only. Nothing is repaired, interpolated, forward-filled, deduplicated
or dropped by this script. Every finding is a count plus a capped sample; the
data on disk is never written to.

The data is treated as IRREGULAR TRADE-MINUTE rows — a row exists for a minute
in which something printed, not for every minute of the session.

v2 (after a three-lens adversarial verification of v1) adds the checks v1 could
not see: physical line count vs parsed rows; cross-symbol duplicate files;
SNAPSHOT rows (board snapshots stamped at scrape time, carrying a whole
session's volume); market-wide calendar reconciliation against the dataset's
own daily files; per-DATE session windows; closing-session prints; direction
splits of every cross-check; zero-price overlap of the return-based detectors;
strict JSON; hashes of every input that feeds a number.

  python3 qa/run_minute_qa.py --input /home/user/dse_minute_data \
      --eod data/raw/dse_eod.parquet
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from bdlib import config as C  # noqa: E402
from bdlib import io as bio  # noqa: E402

EXPECTED_HEADER = ["timestamp", "closing", "opening", "high", "low", "volume"]
DAY_HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]
NON_SYMBOL_FILES = {"summary.csv", "__summary__.csv"}
SYMBOL_RE = re.compile(r"^[A-Z0-9&.()\-]+\.csv$")
SAMPLE_CAP = 25
TOP_N = 40
SESSION_STRICT = (C.ASSUMED_SESSION_START, C.ASSUMED_SESSION_END)   # UNVERIFIED
SESSION_LOOSE = ("09:30", "15:00")
WEEKEND_WEEKDAYS = {4, 5}            # Fri, Sat — DSE weekend, UNVERIFIED
MINUTE_JUMP = 0.10
SPIKE, REVERT_TOL = 0.05, 0.01
CAL_MIN_SYMBOLS = 30
FLOOR = (pd.Timestamp(C.FLOOR_ERA[0]).normalize(), pd.Timestamp(C.FLOOR_ERA[1]).normalize())  # INCLUSIVE dates
TOL_XCHECK, TOL_VOL = 0.005, 0.05
PROVENANCE_SYMBOLS = 60
CLOSING_WINDOW_MIN = 10              # last N minutes of the day's prints, for closing-session detection
TICK = 0.10


def band_of(price: float) -> float:
    for cap, b in C.CIRCUIT_BANDS_UNVERIFIED:
        if price <= cap:
            return b
    return C.CIRCUIT_BANDS_UNVERIFIED[-1][1]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def physical_lines(path: str) -> int:
    n = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            n += chunk.count(b"\n")
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        if fh.tell() > 0:
            fh.seek(-1, 2)
            if fh.read(1) != b"\n":
                n += 1
    return n


def hhmm(ts: pd.Series) -> pd.Series:
    return ts.dt.hour * 60 + ts.dt.minute


def m2t_(m) -> str:
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}" if pd.notna(m) else "—"


def t2m(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def finite(x):
    """JSON-safe: non-finite floats → None."""
    if isinstance(x, float) and not np.isfinite(x):
        return None
    return x


def read_daily(dpath: str) -> tuple[pd.DataFrame | None, dict]:
    info = {"daily_file_present": False}
    if not os.path.exists(dpath):
        return None, info
    dd = pd.read_csv(dpath)
    info["daily_file_present"] = True
    info["daily_header_ok"] = list(dd.columns) == DAY_HEADER
    if not info["daily_header_ok"]:
        return None, info
    dt = pd.to_datetime(dd["Date"], format="%m/%d/%Y", errors="coerce")
    info["daily_rows"] = int(len(dd))
    info["daily_date_unparseable"] = int(dt.isna().sum())
    dd = dd.assign(Date=dt).dropna(subset=["Date"])
    info["daily_date_duplicates"] = int(dd["Date"].duplicated().sum())
    dd = dd.drop_duplicates("Date", keep="first").set_index("Date").sort_index()
    for c in ("Open", "High", "Low", "Close", "Volume"):
        dd[c] = pd.to_numeric(dd[c], errors="coerce")
    info["daily_first"], info["daily_last"] = str(dd.index.min().date()), str(dd.index.max().date())
    return dd, info


# --------------------------------------------------------------------------- #
def qa_one(path: str, sym: str, daily_dir: str) -> tuple[dict, dict, pd.DataFrame, pd.DataFrame]:
    S, X = {"symbol": sym, "file": os.path.basename(path)}, collections.defaultdict(list)
    S["file_bytes"] = os.path.getsize(path)
    S["lines_physical"] = physical_lines(path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        header = fh.readline().strip().split(",")
    S["header_ok"] = header == EXPECTED_HEADER
    if not S["header_ok"]:
        X["schema"].append({"symbol": sym, "header": header})
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, on_bad_lines="skip", engine="python")
    S["rows_raw"] = int(len(raw))
    S["rows_skipped_by_parser"] = int(max(S["lines_physical"] - 1, 0) - S["rows_raw"])
    if S["rows_skipped_by_parser"]:
        X["parser_skipped"].append({"symbol": sym, "n": S["rows_skipped_by_parser"]})
    dd, dinfo = read_daily(os.path.join(daily_dir, os.path.basename(path)), )
    S.update(dinfo)
    if len(raw) == 0:
        S["status"] = "EMPTY"
        if dd is not None:
            S["daily_rows_in_minute_era"] = int((dd.index >= "2015-10-15").sum())
        return S, X, pd.DataFrame(), pd.DataFrame()
    S["status"] = "OK"
    for c in EXPECTED_HEADER:
        if c not in raw.columns:
            raw[c] = ""
    ts = pd.to_datetime(raw["timestamp"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    S["ts_unparseable"] = int(ts.isna().sum())
    if S["ts_unparseable"]:
        X["ts_unparseable"].append({"symbol": sym, "n": S["ts_unparseable"],
                                    "examples": raw.loc[ts.isna(), "timestamp"].head(3).tolist()})
    num = {}
    for c in ("closing", "opening", "high", "low", "volume"):
        v = pd.to_numeric(raw[c], errors="coerce")
        num[c] = v
        S[f"nonnumeric_{c}"] = int(v.isna().sum())
        if S[f"nonnumeric_{c}"]:
            X["nonnumeric"].append({"symbol": sym, "column": c, "n": S[f"nonnumeric_{c}"],
                                    "examples": raw.loc[v.isna(), c].head(3).tolist()})
    S["seconds_nonzero"] = int((ts.dt.second.fillna(0) != 0).sum())
    d = pd.DataFrame({"ts": ts, **num})
    ok = d["ts"].notna() & d["closing"].notna()
    S["rows_unusable"] = int((~ok).sum())
    d = d[ok].copy()
    S["rows_usable"] = int(len(d))
    if len(d) == 0:
        S["status"] = "CORRUPT"
        return S, X, pd.DataFrame(), pd.DataFrame()

    # ---- order / duplicates in FILE order --------------------------------------
    S["non_monotonic_rows"] = int((d["ts"].diff() < pd.Timedelta(0)).sum())
    dup_mask = d["ts"].duplicated(keep=False)
    S["duplicate_ts_rows"] = int(dup_mask.sum())
    S["duplicate_ts_distinct"] = int(d.loc[dup_mask, "ts"].nunique())
    S["duplicate_ts_conflicting_prices"] = int((d[dup_mask].groupby("ts")["closing"].nunique() > 1).sum()) if dup_mask.any() else 0
    if S["duplicate_ts_rows"]:
        X["duplicates"].append({"symbol": sym, "rows": S["duplicate_ts_rows"], "distinct_ts": S["duplicate_ts_distinct"],
                                "with_conflicting_close": S["duplicate_ts_conflicting_prices"],
                                "examples": [str(t) for t in d.loc[dup_mask, "ts"].head(3)]})
    d = d.sort_values("ts", kind="mergesort").reset_index(drop=True)

    # ---- calendar / session -----------------------------------------------------
    d["date"] = d["ts"].dt.normalize()
    d["mins"] = hhmm(d["ts"])
    S["first_ts"], S["last_ts"] = str(d["ts"].iloc[0]), str(d["ts"].iloc[-1])
    S["days"] = int(d["date"].nunique())
    wd = d["ts"].dt.weekday
    weekend = wd.isin(WEEKEND_WEEKDAYS)
    S["rows_fri_sat"] = int(weekend.sum()); S["days_fri_sat"] = int(d.loc[weekend, "date"].nunique())
    lo_s, hi_s = t2m(SESSION_STRICT[0]), t2m(SESSION_STRICT[1])
    lo_l, hi_l = t2m(SESSION_LOOSE[0]), t2m(SESSION_LOOSE[1])
    oos_strict = (d["mins"] < lo_s) | (d["mins"] > hi_s)
    oos_loose = (d["mins"] < lo_l) | (d["mins"] > hi_l)
    S["rows_outside_strict_session"] = int(oos_strict.sum())
    S["rows_outside_loose_session"] = int(oos_loose.sum())
    if oos_loose.any():
        X["out_of_session"].append({"symbol": sym, "n": int(oos_loose.sum()), "examples": [str(t) for t in d.loc[oos_loose, "ts"].head(3)]})
        S["_oos_by_year"] = d.loc[oos_loose, "ts"].dt.year.value_counts().to_dict()
        S["_oos_by_hour"] = d.loc[oos_loose, "ts"].dt.hour.value_counts().to_dict()
        S["_oos_by_date"] = d.loc[oos_loose, "date"].dt.strftime("%Y-%m-%d").value_counts().to_dict()
    S["_strict_oos_by_hour"] = d.loc[oos_strict, "ts"].dt.hour.value_counts().to_dict()
    S["_weekend_by_year"] = d.loc[weekend, "ts"].dt.year.value_counts().to_dict()

    # ---- per-day structure --------------------------------------------------------
    by_day = d.groupby("date", sort=True)
    pday = pd.DataFrame({"rows": by_day.size(), "first_min": by_day["mins"].min(), "last_min": by_day["mins"].max(),
                         "first_close": by_day["closing"].first(), "last_close": by_day["closing"].last(),
                         "first_open": by_day["opening"].first(), "day_high": by_day["high"].max(),
                         "day_low": by_day["low"].min(), "volume": by_day["volume"].sum(),
                         "max_row_volume": by_day["volume"].max()})
    pday.insert(0, "symbol", sym)
    gaps = by_day["ts"].diff().dt.total_seconds().div(60)
    gaps = gaps[gaps.notna()]
    S["gap_1min_share"] = float((gaps == 1).mean()) if len(gaps) else np.nan
    S["gap_2min_share"] = float((gaps == 2).mean()) if len(gaps) else np.nan
    S["gap_gt5min_share"] = float((gaps > 5).mean()) if len(gaps) else np.nan
    r = pday["rows"]
    S["rows_per_day_p10"], S["rows_per_day_median"], S["rows_per_day_p90"], S["rows_per_day_max"] = \
        float(r.quantile(0.1)), float(r.median()), float(r.quantile(0.9)), int(r.max())
    S["days_with_1_row"] = int((r == 1).sum()); S["days_with_lt5_rows"] = int((r < 5).sum())

    # ---- price / volume anomalies ------------------------------------------------
    nonpos = (d[["closing", "opening", "high", "low"]] <= 0).any(axis=1)
    S["rows_nonpositive_price"] = int(nonpos.sum())
    S["rows_negative_volume"] = int((d["volume"] < 0).sum())
    S["rows_zero_volume"] = int((d["volume"] == 0).sum())
    S["rows_volume_1"] = int((d["volume"] == 1).sum())
    ohlc_eq = (d["opening"] == d["high"]) & (d["high"] == d["low"]) & (d["low"] == d["closing"])
    S["rows_ohlc_equal"] = int(ohlc_eq.sum())
    S["_zero_price_by_year"] = d.loc[nonpos, "ts"].dt.year.value_counts().to_dict()
    S["_volume1_by_year"] = d.loc[d["volume"] == 1, "ts"].dt.year.value_counts().to_dict()
    if nonpos.any():
        X["nonpositive_price"].append({"symbol": sym, "n": int(nonpos.sum()), "examples": [str(t) for t in d.loc[nonpos, "ts"].head(3)]})
    # tick-grid check (DSE tick Tk 0.10, UNVERIFIED): price*10 not integer
    offgrid = ((d["closing"] * 10).round(6) % 1 != 0) & (d["closing"] > 0)
    S["rows_off_tick_grid"] = int(offgrid.sum())
    # OHLC consistency
    hi_bad = d["high"] < d[["opening", "closing"]].max(axis=1)
    lo_bad = d["low"] > d[["opening", "closing"]].min(axis=1)
    hl_bad = d["high"] < d["low"]
    S["rows_ohlc_violation"] = int((hi_bad | lo_bad | hl_bad).sum()); S["rows_high_lt_low"] = int(hl_bad.sum())
    if S["rows_ohlc_violation"]:
        X["ohlc"].append({"symbol": sym, "n": S["rows_ohlc_violation"], "examples": [str(t) for t in d.loc[hi_bad | lo_bad | hl_bad, "ts"].head(3)]})

    # ---- intraday extreme returns / bad prints (zero-price overlap made explicit) ----
    same_day = d["date"].eq(d["date"].shift(1))
    pos_pair = (d["closing"] > 0) & (d["closing"].shift(1) > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.log(d["closing"] / d["closing"].shift(1)).where(same_day)
    jump_any = (lr.abs() >= MINUTE_JUMP) | (same_day & ~pos_pair & (d["closing"] != d["closing"].shift(1)))
    jump_finite = (lr.abs() >= MINUTE_JUMP) & pos_pair
    S["rows_minute_jump_ge10pct"] = int(jump_any.fillna(False).sum())
    S["rows_minute_jump_ge10pct_finite"] = int(jump_finite.fillna(False).sum())
    S["rows_minute_jump_through_zero"] = S["rows_minute_jump_ge10pct"] - S["rows_minute_jump_ge10pct_finite"]
    nxt = lr.shift(-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        back = np.log(d["closing"].shift(-1) / d["closing"].shift(1)).where(same_day & same_day.shift(-1))
    spike = ((lr.abs() >= SPIKE) & (nxt.abs() >= SPIKE) & (np.sign(lr) != np.sign(nxt)) & (back.abs() <= REVERT_TOL)
             & pos_pair & (d["closing"].shift(-1) > 0))
    S["rows_spike_and_revert"] = int(spike.fillna(False).sum())
    if S["rows_minute_jump_ge10pct"] or S["rows_spike_and_revert"]:
        idx = d.index[(jump_finite | spike).fillna(False)][:3]
        X["bad_print"].append({"symbol": sym, "jumps_ge10pct_finite": S["rows_minute_jump_ge10pct_finite"],
                               "jumps_through_zero": S["rows_minute_jump_through_zero"],
                               "spike_and_revert": S["rows_spike_and_revert"],
                               "examples": [{"ts": str(d.loc[i, "ts"]), "ret": finite(round(float(lr[i]), 4))} for i in idx]})

    # ---- day-over-day gaps: zero-endpoint / distance / daily-file agreement --------------
    prev_close = pday["last_close"].shift(1)
    prev_date = pday.index.to_series().shift(1)
    gap = pday["first_open"] / prev_close - 1.0
    zero_endpoint = (pday["first_open"] <= 0) | (prev_close <= 0)
    band = prev_close.map(band_of)
    beyond = (gap.abs() > (band + C.LIMIT_BAND_TOLERANCE)) & ~zero_endpoint & prev_close.notna()
    big = (gap.abs() > C.DEFAULT.qa.max_abs_overnight_gap) & ~zero_endpoint & prev_close.notna()
    pday["overnight_gap"] = gap.where(~zero_endpoint)
    pday["gap_zero_endpoint"] = zero_endpoint & prev_close.notna()
    pday["gap_beyond_band"] = beyond
    pday["prev_minute_day"] = prev_date
    S["days_gap_zero_endpoint"] = int(pday["gap_zero_endpoint"].sum())
    S["days_gap_beyond_band"] = int(beyond.sum()); S["days_gap_gt20pct"] = int(big.sum())
    pday["gap_beyond_band_in_daily_file"] = np.nan
    if dd is not None and beyond.any():
        j = pday.loc[beyond].join(dd[["Open", "Close"]], how="left")
        prev_daily_close = dd["Close"].shift(1).reindex(j.index)
        dgap = j["Open"] / prev_daily_close - 1.0
        agree = (dgap.abs() > (prev_daily_close.map(band_of) + C.LIMIT_BAND_TOLERANCE))
        pday.loc[beyond, "gap_beyond_band_in_daily_file"] = agree.astype(float).where(dgap.notna())
        S["days_gap_beyond_band_daily_agrees"] = int(agree.fillna(False).sum())
        S["days_gap_beyond_band_daily_disagrees"] = int((~agree.fillna(True)).sum() - int(dgap.isna().sum())) if len(agree) else 0
        S["days_gap_beyond_band_daily_untestable"] = int(dgap.isna().sum())
    if S["days_gap_beyond_band"]:
        X["beyond_band_gaps"].append({"symbol": sym, "beyond_band_days": S["days_gap_beyond_band"], "gt20pct_days": S["days_gap_gt20pct"],
                                      "zero_endpoint_days": S["days_gap_zero_endpoint"],
                                      "examples": [{"date": str(dt.date()), "gap": finite(round(float(g), 4))} for dt, g in gap[beyond].head(4).items()]})
    S["_beyond_band_by_date"] = pday.loc[beyond].index.strftime("%Y-%m-%d").value_counts().to_dict()

    # ---- floor era (INCLUSIVE dates) ----------------------------------------------------
    fl = d["date"].between(FLOOR[0], FLOOR[1])
    S["rows_floor_era"] = int(fl.sum()); S["days_floor_era"] = int(d.loc[fl, "date"].nunique())
    S["ohlc_equal_share_floor"] = float(ohlc_eq[fl].mean()) if fl.any() else np.nan
    S["ohlc_equal_share_nonfloor"] = float(ohlc_eq[~fl].mean()) if (~fl).any() else np.nan

    # ---- per-year coverage ------------------------------------------------------------------
    yr = d["ts"].dt.year
    pyear = pd.DataFrame({"rows": d.groupby(yr).size(), "days": d.groupby(yr)["date"].nunique()})
    pyear.index.name = "year"; pyear = pyear.reset_index(); pyear.insert(0, "symbol", sym)

    # ---- against the dataset's own DAILY file ------------------------------------------------
    pday["date_in_daily_file"] = np.nan
    pday["snapshot_prev_session"] = False
    pday["snapshot_same_day_multirow"] = False
    pday["closing_session_print_eq_close"] = np.nan
    if dd is not None:
        in_daily = pday.index.isin(dd.index)
        pday["date_in_daily_file"] = in_daily
        S["days_not_in_daily_file"] = int((~in_daily).sum())
        S["_rows_not_in_daily_by_year"] = d.loc[~d["date"].isin(dd.index), "ts"].dt.year.value_counts().to_dict()
        S["_dates_not_in_daily"] = pday.index[~in_daily].strftime("%Y-%m-%d").tolist()
        # SNAPSHOT rows: a row whose volume equals a whole session's daily Volume.
        #  (a) equals the PREVIOUS daily session's Volume and price equals its Close  → stale board snapshot
        #  (b) equals the SAME date's daily Volume on a day with >1 rows                → end-of-day snapshot appended
        dv = dd["Volume"]; dc = dd["Close"]
        prev_idx = pd.Series(dd.index, index=dd.index).shift(1)
        d["_dv_same"] = d["date"].map(dv); d["_dc_same"] = d["date"].map(dc)
        d["_prev_date"] = d["date"].map(prev_idx)
        d["_dv_prev"] = d["_prev_date"].map(dv); d["_dc_prev"] = d["_prev_date"].map(dc)
        # for dates absent from the daily file, "previous session" = last daily date before it
        absent = ~d["date"].isin(dd.index)
        if absent.any():
            pos = np.searchsorted(dd.index.values, d.loc[absent, "date"].values) - 1
            okp = pos >= 0
            prevd = pd.Series(pd.NaT, index=d.index[absent])
            prevd.iloc[np.where(okp)[0]] = dd.index.values[pos[okp]]
            d.loc[absent, "_prev_date"] = prevd
            d.loc[absent, "_dv_prev"] = d.loc[absent, "_prev_date"].map(dv)
            d.loc[absent, "_dc_prev"] = d.loc[absent, "_prev_date"].map(dc)
        snap_prev = (d["volume"] > 1) & (d["volume"] == d["_dv_prev"]) & (d["closing"] == d["_dc_prev"])
        rows_in_day = d.groupby("date")["ts"].transform("size")
        snap_same = (d["volume"] > 1) & (d["volume"] == d["_dv_same"]) & (rows_in_day > 1)
        S["rows_snapshot_prev_session"] = int(snap_prev.sum())
        S["rows_snapshot_same_day_multirow"] = int(snap_same.sum())
        S["_snap_prev_by_year"] = d.loc[snap_prev, "ts"].dt.year.value_counts().to_dict()
        S["_snap_same_by_year"] = d.loc[snap_same, "ts"].dt.year.value_counts().to_dict()
        S["_snap_same_by_date"] = d.loc[snap_same, "date"].dt.strftime("%Y-%m-%d").value_counts().to_dict()
        S["_snap_prev_by_date"] = d.loc[snap_prev, "date"].dt.strftime("%Y-%m-%d").value_counts().to_dict()
        pday["snapshot_prev_session"] = d.groupby("date")["ts"].size().index.isin(d.loc[snap_prev, "date"].unique())
        pday["snapshot_same_day_multirow"] = pday.index.isin(d.loc[snap_same, "date"].unique())
        if snap_prev.any() or snap_same.any():
            X["snapshot_rows"].append({"symbol": sym, "prev_session": S["rows_snapshot_prev_session"],
                                       "same_day_multirow": S["rows_snapshot_same_day_multirow"],
                                       "examples": [str(t) for t in d.loc[snap_prev | snap_same, "ts"].head(3)]})
        # CLOSING SESSION: prints in the last CLOSING_WINDOW_MIN minutes of the day at ONE price == daily Close
        lastmin = d.groupby("date")["mins"].transform("max")
        tail = d["mins"] >= (lastmin - CLOSING_WINDOW_MIN)
        tl = d[tail].groupby("date").agg(n=("closing", "size"), nprice=("closing", "nunique"), price=("closing", "last"))
        tl = tl.join(dc.rename("Close"), how="left")
        cs = (tl["n"] >= 2) & (tl["nprice"] == 1) & (tl["price"] == tl["Close"])
        pday["closing_session_print_eq_close"] = cs.reindex(pday.index).astype(float)
        S["_closing_session_days_by_year"] = pd.Series(cs[cs].index.year).value_counts().to_dict()
        S["_days_by_year_for_closing"] = pd.Series(tl.index.year).value_counts().to_dict()
        # cross-check aggregates
        j = pday.join(dd, how="inner")
        S["xcheck_days"] = int(len(j))
        if len(j):
            rel = lambda a, b: (a / b.replace(0, np.nan) - 1)  # noqa: E731
            close_mis = rel(j["last_close"], j["Close"]).abs() > TOL_XCHECK
            hi = rel(j["day_high"], j["High"]); lo = rel(j["day_low"], j["Low"]); vo = rel(j["volume"], j["Volume"])
            S["xcheck_close_mismatch"] = int(close_mis.sum())
            S["xcheck_close_within_day_range"] = int((close_mis & (j["Close"] >= j["day_low"]) & (j["Close"] <= j["day_high"])).sum())
            S["xcheck_high_mismatch"] = int((hi.abs() > TOL_XCHECK).sum()); S["xcheck_high_minute_above_daily"] = int((hi > TOL_XCHECK).sum())
            S["xcheck_low_mismatch"] = int((lo.abs() > TOL_XCHECK).sum()); S["xcheck_low_minute_below_daily"] = int((lo < -TOL_XCHECK).sum())
            S["xcheck_volume_untestable_daily_zero"] = int((j["Volume"] == 0).sum())
            S["xcheck_volume_mismatch_5pct"] = int((vo.abs() > TOL_VOL).sum())
            S["xcheck_volume_minute_gt_daily"] = int((vo > TOL_VOL).sum()); S["xcheck_volume_minute_lt_daily"] = int((vo < -TOL_VOL).sum())
            S["xcheck_volume_minute_gt_2x_daily"] = int((vo > 1.0).sum())
            S["_close_mis_by_year"] = pd.Series(j.index[close_mis].year).value_counts().to_dict()
            S["_xcheck_days_by_year"] = pd.Series(j.index.year).value_counts().to_dict()
            S["_vol_gt_by_year"] = pd.Series(j.index[vo > TOL_VOL].year).value_counts().to_dict()
            S["_vol_lt_by_year"] = pd.Series(j.index[vo < -TOL_VOL].year).value_counts().to_dict()
            span = dd.index[(dd.index >= pday.index.min()) & (dd.index <= pday.index.max())]
            S["xcheck_days_in_daily_not_minute"] = int(len(span.difference(pday.index)))
            S["xcheck_days_in_minute_not_daily"] = int(len(pday.index.difference(dd.index)))
            if int(close_mis.sum()):
                X["minute_vs_daily"].append({"symbol": sym, "close_mismatch_days": int(close_mis.sum()), "of_days": int(len(j)),
                                             "examples": [{"date": str(dt.date()), "minute_last_close": float(a), "daily_close": float(b)}
                                                          for dt, a, b in zip(j.index[close_mis][:3], j.loc[close_mis, "last_close"][:3], j.loc[close_mis, "Close"][:3])]})
        S["minute_start_vs_daily_start_days"] = int((pday.index.min() - dd.index.min()).days)
        S["daily_rows_in_minute_era"] = int((dd.index >= "2015-10-15").sum())
        d = d.drop(columns=[c for c in d.columns if c.startswith("_")])
    return S, X, pday.reset_index(), pyear


# --------------------------------------------------------------------------- #
def cross_symbol_duplicates(mdir: str, files: list[str]) -> list[dict]:
    """Files that are byte-for-byte identical, or where one file's entire
    content is a prefix of another's (a renamed symbol carried twice)."""
    heads = collections.defaultdict(list)
    for f in files:
        if os.path.getsize(os.path.join(mdir, f)) <= 64:        # header-only files are not duplicates of each other
            continue
        with open(os.path.join(mdir, f), "rb") as fh:
            fh.readline()
            heads[hashlib.sha256(fh.read(8192)).hexdigest()].append(f)
    out = []
    for group in heads.values():
        if len(group) < 2:
            continue
        frames = {f: pd.read_csv(os.path.join(mdir, f), dtype=str, keep_default_na=False) for f in group}
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                A, B = frames[a], frames[b]
                short, long_ = (a, b) if len(A) <= len(B) else (b, a)
                S_, L_ = frames[short], frames[long_]
                m = S_.merge(L_, on="timestamp", suffixes=("_s", "_l"))
                if len(S_) == 0 or len(m) / len(S_) < 0.95:
                    continue
                eq = np.all([(m[c + "_s"] == m[c + "_l"]).to_numpy() for c in EXPECTED_HEADER if c != "timestamp"], axis=0)
                out.append({"duplicate": short[:-4], "of": long_[:-4],
                            "relation": "identical" if (len(S_) == len(L_) and eq.all()) else "same series under two names (rename)",
                            "rows_short": int(len(S_)), "rows_long": int(len(L_)), "common_timestamps": int(len(m)),
                            "common_rows_all_fields_equal": int(eq.sum()),
                            "short_first": S_["timestamp"].iloc[0], "short_last": S_["timestamp"].iloc[-1],
                            "long_last": L_["timestamp"].iloc[-1]})
    return out


def daily_calendar(ddir: str) -> tuple[pd.DatetimeIndex, pd.Series, dict]:
    counts = collections.Counter(); info = {"daily_files": 0, "daily_index_like": [], "daily_header_bad": []}
    first = None
    for f in sorted(os.listdir(ddir)):
        if not f.endswith(".csv"):
            continue
        info["daily_files"] += 1
        try:
            dd = pd.read_csv(os.path.join(ddir, f), usecols=["Date", "Volume"])
        except Exception:  # noqa: BLE001
            info["daily_header_bad"].append(f); continue
        dt = pd.to_datetime(dd["Date"], format="%m/%d/%Y", errors="coerce").dropna()
        if len(dt):
            first = dt.min() if first is None else min(first, dt.min())
        for x in dt.dt.normalize().unique():
            counts[x] += 1
    s = pd.Series(counts).sort_index()
    cal = s[s >= CAL_MIN_SYMBOLS].index
    info["daily_first_date"] = str(first.date()) if first is not None else None
    return pd.DatetimeIndex(cal), s, info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--eod", default=None)
    ap.add_argument("--limit", type=int, default=None, help="SMOKE TEST: first N files only")
    a = ap.parse_args()
    t0 = time.time()
    paths = bio.paths()
    mdir, ddir = os.path.join(a.input, "minute_price_unadjusted"), os.path.join(a.input, "day_price_unadjusted")
    try:
        dataset_commit = subprocess.check_output(["git", "-C", a.input, "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        dataset_commit = "unknown"
    files = sorted(os.listdir(mdir))
    classified = {"symbol_files": [], "non_symbol_files": [], "unexpected_names": []}
    for f in files:
        (classified["non_symbol_files"] if f in NON_SYMBOL_FILES else
         classified["symbol_files"] if SYMBOL_RE.match(f) else classified["unexpected_names"]).append(f)
    sym_files = classified["symbol_files"][: a.limit] if a.limit else classified["symbol_files"]
    print(f"files: {len(files)} · symbol files: {len(classified['symbol_files'])} · non-symbol: {classified['non_symbol_files']}")

    dups = cross_symbol_duplicates(mdir, sym_files)
    dcal, dcal_counts, dinfo = daily_calendar(ddir)
    print(f"cross-symbol duplicate files: {dups}")
    print(f"daily files: {dinfo['daily_files']} · daily calendar days (≥{CAL_MIN_SYMBOLS} symbols): {len(dcal)} · first daily date {dinfo['daily_first_date']}")

    summaries, samples, pdays, pyears, hashes = [], collections.defaultdict(list), [], [], {}
    for i, f in enumerate(sym_files, 1):
        p = os.path.join(mdir, f); sym = f[:-4]
        hashes["minute/" + f] = sha256(p)
        S, X, pday, pyear = qa_one(p, sym, ddir)
        summaries.append(S)
        for k, v in X.items():
            samples[k].extend(v)
        if len(pday): pdays.append(pday)
        if len(pyear): pyears.append(pyear)
        if i % 50 == 0 or i == len(sym_files):
            print(f"  {i}/{len(sym_files)} · {time.time() - t0:.0f}s")
    for f in sorted(os.listdir(ddir)):
        if f.endswith(".csv"):
            hashes["daily/" + f] = sha256(os.path.join(ddir, f))

    def sum_dicts(key):
        out = collections.Counter()
        for s in summaries:
            for k, v in (s.get(key) or {}).items():
                out[str(k)] += int(v)
        return dict(sorted(out.items()))
    def top_dates(key, n=TOP_N):
        return dict(collections.Counter(sum_dicts(key)).most_common(n))
    breakdowns = {
        "out_of_session_rows_by_year": sum_dicts("_oos_by_year"), "out_of_session_rows_by_hour": sum_dicts("_oos_by_hour"),
        "out_of_session_rows_by_date_top": top_dates("_oos_by_date"),
        "outside_strict_session_rows_by_hour": sum_dicts("_strict_oos_by_hour"),
        "weekend_rows_by_year": sum_dicts("_weekend_by_year"), "zero_price_rows_by_year": sum_dicts("_zero_price_by_year"),
        "volume_1_rows_by_year": sum_dicts("_volume1_by_year"),
        "rows_on_dates_absent_from_daily_by_year": sum_dicts("_rows_not_in_daily_by_year"),
        "snapshot_prev_session_rows_by_year": sum_dicts("_snap_prev_by_year"),
        "snapshot_same_day_rows_by_year": sum_dicts("_snap_same_by_year"),
        "snapshot_same_day_rows_by_date_top": top_dates("_snap_same_by_date"),
        "snapshot_prev_session_rows_by_date_top": top_dates("_snap_prev_by_date"),
        "beyond_band_gap_days_by_date_top": top_dates("_beyond_band_by_date"),
        "close_mismatch_days_by_year": sum_dicts("_close_mis_by_year"), "xcheck_days_by_year": sum_dicts("_xcheck_days_by_year"),
        "volume_minute_gt_daily_days_by_year": sum_dicts("_vol_gt_by_year"), "volume_minute_lt_daily_days_by_year": sum_dicts("_vol_lt_by_year"),
        "closing_session_days_by_year": sum_dicts("_closing_session_days_by_year"),
        "symbol_days_with_daily_close_by_year": sum_dicts("_days_by_year_for_closing"),
    }
    dates_not_in_daily = collections.Counter()
    for s in summaries:
        for dte in s.get("_dates_not_in_daily") or []:
            dates_not_in_daily[dte] += 1
        for k in [k for k in s if k.startswith("_")]:
            s.pop(k)
    sym = pd.DataFrame(summaries)
    pday = pd.concat(pdays, ignore_index=True) if pdays else pd.DataFrame()
    pyear = pd.concat(pyears, ignore_index=True) if pyears else pd.DataFrame()

    # ---- market-wide calendars --------------------------------------------------------
    pday["date"] = pd.to_datetime(pday["date"])
    mcounts = pday.groupby("date")["symbol"].nunique()
    mcal = mcounts[mcounts >= CAL_MIN_SYMBOLS].index
    odd_days = mcounts[mcounts < CAL_MIN_SYMBOLS]
    mspan = dcal[(dcal >= mcal.min()) & (dcal <= mcal.max())]
    daily_not_minute = mspan.difference(mcal)          # whole-market capture gaps
    minute_not_daily = mcal.difference(dcal)           # snapshot dates
    cal_year = pd.Series(mcal.year).value_counts().sort_index()
    breakdowns["minute_calendar_days_by_year"] = {int(k): int(v) for k, v in cal_year.items()}
    breakdowns["daily_calendar_days_by_year_in_minute_span"] = {int(k): int(v) for k, v in pd.Series(mspan.year).value_counts().sort_index().items()}
    breakdowns["whole_market_days_in_daily_not_minute"] = [str(x.date()) for x in daily_not_minute]
    breakdowns["minute_dates_not_in_daily_calendar"] = [str(x.date()) for x in minute_not_daily]
    breakdowns["minute_dates_not_in_daily_calendar_weekday"] = {str(x.date()): int(x.weekday()) for x in minute_not_daily}
    breakdowns["calendar_fri_sat_dates"] = [str(x.date()) for x in mcal[mcal.weekday.isin(list(WEEKEND_WEEKDAYS))]]
    breakdowns["calendar_fri_sat_dates_in_daily_calendar"] = [str(x.date()) for x in mcal[mcal.weekday.isin(list(WEEKEND_WEEKDAYS))] if x in dcal]
    breakdowns["odd_days_below_calendar_threshold"] = {str(k.date()): int(v) for k, v in odd_days.items()}
    breakdowns["symbol_dates_absent_from_daily_top"] = dict(dates_not_in_daily.most_common(TOP_N))
    covid = pd.date_range("2020-03-26", "2020-05-30")
    breakdowns["covid_window_2020-03-26_05-30"] = {"minute_rows": int(pday.loc[pday["date"].isin(covid), "rows"].sum()),
                                                   "daily_calendar_days": int(len(dcal.intersection(covid)))}
    # missing days per symbol against the MINUTE calendar (as v1) — whole-market gaps are reported above separately
    missing_rows = []
    for s, g in pday.groupby("symbol"):
        have = pd.DatetimeIndex(g["date"])
        span = mcal[(mcal >= have.min()) & (mcal <= have.max())]
        miss = span.difference(have)
        longest = 0
        if len(miss):
            pos = np.searchsorted(span, miss)
            runs = np.split(pos, np.where(np.diff(pos) != 1)[0] + 1)
            longest = max(len(r) for r in runs)
        missing_rows.append({"symbol": s, "calendar_days_in_span": int(len(span)), "days_present": int(len(have)),
                             "days_missing": int(len(miss)), "missing_share": float(len(miss) / len(span)) if len(span) else np.nan,
                             "longest_missing_run": int(longest)})
    sym = sym.merge(pd.DataFrame(missing_rows), on="symbol", how="left")

    # ---- per-DATE session window (market-wide) + batch-stamp + in-session holes -----------------
    sess = pday.groupby("date").agg(first_p05=("first_min", lambda s: s.quantile(0.05)), last_p95=("last_min", lambda s: s.quantile(0.95)),
                                    symbols=("symbol", "nunique"), rows=("rows", "sum"))
    sess["first_p05_t"] = sess["first_p05"].map(m2t_); sess["last_p95_t"] = sess["last_p95"].map(m2t_)
    sess = sess[sess["symbols"] >= CAL_MIN_SYMBOLS]
    # regime listing: dates where the window moves ≥ 10 min against the trailing 5-day median
    roll_f = sess["first_p05"].shift(1).rolling(5, min_periods=3).median(); roll_l = sess["last_p95"].shift(1).rolling(5, min_periods=3).median()
    shift = ((sess["first_p05"] - roll_f).abs() >= 10) | ((sess["last_p95"] - roll_l).abs() >= 10)
    sess["window_shift_vs_prior_5d"] = shift
    sess_year = pday.assign(year=pday["date"].dt.year).groupby("year").agg(
        first_p01=("first_min", lambda s: s.quantile(0.01)), first_med=("first_min", "median"),
        last_med=("last_min", "median"), last_p99=("last_min", lambda s: s.quantile(0.99)), symbol_days=("rows", "size"))
    sess_tbl = pd.DataFrame({"year": sess_year.index, "first_row_p01": sess_year["first_p01"].map(m2t_), "first_row_median": sess_year["first_med"].map(m2t_),
                             "last_row_median": sess_year["last_med"].map(m2t_), "last_row_p99": sess_year["last_p99"].map(m2t_),
                             "symbol_days": sess_year["symbol_days"].astype(int)})

    # ---- overlap with our EOD universe + daily↔EOD provenance ----------------------------------------
    overlap = {}
    if a.eod and os.path.exists(a.eod):
        hashes["eod/" + os.path.basename(a.eod)] = sha256(a.eod)
        eod = pd.read_parquet(a.eod, columns=["symbol", "ts", "close"])
        eod_syms, min_syms = set(eod["symbol"].unique()), set(sym["symbol"])
        overlap = {"eod_symbols": len(eod_syms), "minute_symbols": len(min_syms), "in_both": len(eod_syms & min_syms),
                   "minute_only": sorted(min_syms - eod_syms), "eod_only": sorted(eod_syms - min_syms)}
        mism, tested, nsym, sample = 0, 0, 0, []
        for s in sorted(eod_syms & min_syms)[:PROVENANCE_SYMBOLS]:
            dd, _ = read_daily(os.path.join(ddir, f"{s}.csv"))
            if dd is None:
                continue
            e = eod[eod["symbol"] == s].set_index("ts")["close"]
            j = dd["Close"].to_frame().join(e.rename("eod_close"), how="inner")
            if len(j):
                nsym += 1
                bad = (j["Close"] / j["eod_close"] - 1).abs() > TOL_XCHECK
                mism += int(bad.sum()); tested += int(len(j))
                if bad.any() and len(sample) < 5:
                    sample.append({"symbol": s, "mismatch_days": int(bad.sum()), "of": int(len(j))})
        overlap.update({"daily_vs_eod_symbols_tested": nsym, "daily_vs_eod_close_days_tested": tested,
                        "daily_vs_eod_close_mismatch": mism, "daily_vs_eod_examples": sample})

    # ---- aggregates ---------------------------------------------------------------------------------
    ok = sym[sym["status"] == "OK"]
    def tot(c): return int(ok[c].fillna(0).sum()) if c in ok else 0
    dup_names = {d_["duplicate"] for d_ in dups}
    net = ok[~ok["symbol"].isin(dup_names)]
    empties = sym[sym["status"] == "EMPTY"]
    agg = {
        "files_total": len(files), "symbol_files": len(classified["symbol_files"]), "non_symbol_files": classified["non_symbol_files"],
        "unexpected_names": classified["unexpected_names"], "daily_files": dinfo["daily_files"], "daily_first_date": dinfo["daily_first_date"],
        "symbols_ok": int(len(ok)), "symbols_empty": int(len(empties)), "symbols_corrupt": int((sym["status"] == "CORRUPT").sum()),
        "empty_symbols": empties["symbol"].tolist(),
        "empty_symbols_daily_rows_in_minute_era": {r["symbol"]: (int(r["daily_rows_in_minute_era"]) if pd.notna(r.get("daily_rows_in_minute_era")) else None)
                                                   for _, r in empties.iterrows()},
        "cross_symbol_duplicate_files": dups, "symbols_ok_net_of_duplicates": int(len(net)),
        "rows_raw": int(sym["rows_raw"].sum()), "rows_raw_net_of_duplicates": int(net["rows_raw"].sum()),
        "lines_physical_minus_headers": int((sym["lines_physical"] - 1).clip(lower=0).sum()), "rows_skipped_by_parser": int(sym["rows_skipped_by_parser"].sum()),
        "rows_usable": tot("rows_usable"), "rows_unusable": tot("rows_unusable"), "ts_unparseable": tot("ts_unparseable"),
        "header_mismatch_files": int((~sym["header_ok"]).sum()), "seconds_nonzero": tot("seconds_nonzero"),
        "duplicate_ts_rows_within_symbol": tot("duplicate_ts_rows"), "duplicate_ts_conflicting_prices": tot("duplicate_ts_conflicting_prices"),
        "non_monotonic_rows": tot("non_monotonic_rows"),
        "rows_fri_sat": tot("rows_fri_sat"), "days_fri_sat": tot("days_fri_sat"),
        "rows_outside_strict_session": tot("rows_outside_strict_session"), "rows_outside_loose_session": tot("rows_outside_loose_session"),
        "rows_nonpositive_price": tot("rows_nonpositive_price"), "rows_negative_volume": tot("rows_negative_volume"),
        "rows_zero_volume": tot("rows_zero_volume"), "rows_volume_1": tot("rows_volume_1"), "rows_off_tick_grid": tot("rows_off_tick_grid"),
        "rows_ohlc_equal": tot("rows_ohlc_equal"), "rows_ohlc_violation": tot("rows_ohlc_violation"), "rows_high_lt_low": tot("rows_high_lt_low"),
        "rows_minute_jump_ge10pct": tot("rows_minute_jump_ge10pct"), "rows_minute_jump_ge10pct_finite": tot("rows_minute_jump_ge10pct_finite"),
        "rows_minute_jump_through_zero": tot("rows_minute_jump_through_zero"), "rows_spike_and_revert": tot("rows_spike_and_revert"),
        "days_gap_zero_endpoint": tot("days_gap_zero_endpoint"), "days_gap_beyond_band": tot("days_gap_beyond_band"), "days_gap_gt20pct": tot("days_gap_gt20pct"),
        "days_gap_beyond_band_daily_agrees": tot("days_gap_beyond_band_daily_agrees"),
        "days_gap_beyond_band_daily_disagrees": tot("days_gap_beyond_band_daily_disagrees"),
        "days_gap_beyond_band_daily_untestable": tot("days_gap_beyond_band_daily_untestable"),
        "days_gap_beyond_band_prev_minute_day_not_adjacent": int(0),
        "rows_snapshot_prev_session": tot("rows_snapshot_prev_session"), "rows_snapshot_same_day_multirow": tot("rows_snapshot_same_day_multirow"),
        "days_not_in_daily_file": tot("days_not_in_daily_file"),
        "symbol_days": int(len(pday)), "symbol_days_net_of_duplicates": int(len(pday[~pday["symbol"].isin(dup_names)])),
        "minute_calendar_days": int(len(mcal)), "daily_calendar_days_in_minute_span": int(len(mspan)),
        "whole_market_days_in_daily_not_minute": int(len(daily_not_minute)), "minute_dates_not_in_daily_calendar": int(len(minute_not_daily)),
        "odd_days_below_calendar_threshold": int(len(odd_days)),
        "first_ts": str(pd.to_datetime(ok["first_ts"]).min()), "last_ts": str(pd.to_datetime(ok["last_ts"]).max()),
        "symbols_starting_by_year": {int(k): int(v) for k, v in pd.to_datetime(ok["first_ts"]).dt.year.value_counts().sort_index().items()},
        "symbols_starting_2015_by_month": {int(k): int(v) for k, v in pd.to_datetime(ok["first_ts"])[pd.to_datetime(ok["first_ts"]).dt.year == 2015].dt.month.value_counts().sort_index().items()},
        "symbols_ending_by_year": {int(k): int(v) for k, v in pd.to_datetime(ok["last_ts"]).dt.year.value_counts().sort_index().items()},
        "symbols_ending_before_last_month": ok.loc[pd.to_datetime(ok["last_ts"]) < "2024-01-01", "symbol"].tolist(),
        "symbols_daily_history_predates_minute_by_gt_30_days": ok.loc[ok["minute_start_vs_daily_start_days"].fillna(0) > 30 + (pd.Timestamp("2015-10-15") - pd.Timestamp("2012-10-01")).days, "symbol"].tolist(),
        "rows_floor_era": tot("rows_floor_era"), "days_floor_era_symbol_days": tot("days_floor_era"), "symbols_with_floor_era_rows": int((ok["rows_floor_era"] > 0).sum()),
        "ohlc_equal_share_floor_median": float(ok["ohlc_equal_share_floor"].median()), "ohlc_equal_share_nonfloor_median": float(ok["ohlc_equal_share_nonfloor"].median()),
        "gap_1min_share_median": float(ok["gap_1min_share"].median()), "gap_2min_share_median": float(ok["gap_2min_share"].median()), "gap_gt5min_share_median": float(ok["gap_gt5min_share"].median()),
        "rows_per_day_median_of_medians": float(ok["rows_per_day_median"].median()), "rows_per_day_p10_median": float(ok["rows_per_day_p10"].median()),
        "rows_per_day_p90_median": float(ok["rows_per_day_p90"].median()), "days_with_1_row": tot("days_with_1_row"), "days_with_lt5_rows": tot("days_with_lt5_rows"),
        "days_missing_total": tot("days_missing"), "missing_share_median": float(ok["missing_share"].median()),
        "symbols_missing_share_gt20pct": int((ok["missing_share"] > 0.2).sum()), "longest_missing_run_max": int(ok["longest_missing_run"].max()),
        "xcheck_symbols": int(ok["daily_file_present"].sum()), "xcheck_days": tot("xcheck_days"),
        "xcheck_close_mismatch": tot("xcheck_close_mismatch"), "xcheck_close_within_day_range": tot("xcheck_close_within_day_range"),
        "xcheck_high_mismatch": tot("xcheck_high_mismatch"), "xcheck_high_minute_above_daily": tot("xcheck_high_minute_above_daily"),
        "xcheck_low_mismatch": tot("xcheck_low_mismatch"), "xcheck_low_minute_below_daily": tot("xcheck_low_minute_below_daily"),
        "xcheck_volume_mismatch_5pct": tot("xcheck_volume_mismatch_5pct"), "xcheck_volume_minute_gt_daily": tot("xcheck_volume_minute_gt_daily"),
        "xcheck_volume_minute_lt_daily": tot("xcheck_volume_minute_lt_daily"), "xcheck_volume_minute_gt_2x_daily": tot("xcheck_volume_minute_gt_2x_daily"),
        "xcheck_volume_untestable_daily_zero": tot("xcheck_volume_untestable_daily_zero"),
        "xcheck_days_in_daily_not_minute": tot("xcheck_days_in_daily_not_minute"), "xcheck_days_in_minute_not_daily": tot("xcheck_days_in_minute_not_daily"),
        "daily_date_unparseable": tot("daily_date_unparseable"), "daily_date_duplicates": tot("daily_date_duplicates"),
        "overlap": overlap, "session_hours_by_year": sess_tbl.to_dict(orient="records"),
        "session_window_shift_dates": [str(x.date()) for x in sess.index[sess["window_shift_vs_prior_5d"]]],
        "elapsed_s": round(time.time() - t0, 1),
    }
    # distance to the previous minute-day for beyond-band gaps (not adjacent ⇒ multi-session move)
    if len(pday):
        bb = pday[pday["gap_beyond_band"] == True]  # noqa: E712
        pos_now = np.searchsorted(mcal.values, pd.to_datetime(bb["date"]).values)
        pos_prev = np.searchsorted(mcal.values, pd.to_datetime(bb["prev_minute_day"]).values)
        agg["days_gap_beyond_band_prev_minute_day_not_adjacent"] = int(((pos_now - pos_prev) > 1).sum())
    thresholds = {"SAMPLE_CAP": SAMPLE_CAP, "TOP_N": TOP_N, "SESSION_STRICT": SESSION_STRICT, "SESSION_LOOSE": SESSION_LOOSE,
                  "WEEKEND_WEEKDAYS": sorted(WEEKEND_WEEKDAYS), "MINUTE_JUMP": MINUTE_JUMP, "SPIKE": SPIKE, "REVERT_TOL": REVERT_TOL,
                  "CAL_MIN_SYMBOLS": CAL_MIN_SYMBOLS, "TOL_XCHECK": TOL_XCHECK, "TOL_VOL": TOL_VOL, "PROVENANCE_SYMBOLS": PROVENANCE_SYMBOLS,
                  "CLOSING_WINDOW_MIN": CLOSING_WINDOW_MIN, "TICK": TICK, "max_abs_overnight_gap": C.DEFAULT.qa.max_abs_overnight_gap,
                  "circuit_bands_unverified": [[finite(cap) if cap != float("inf") else None, b] for cap, b in C.CIRCUIT_BANDS_UNVERIFIED],
                  "limit_band_tolerance": C.LIMIT_BAND_TOLERANCE, "floor_era_inclusive": list(C.FLOOR_ERA)}

    # ---- outputs ----------------------------------------------------------------------------------------
    os.makedirs(paths["qa"], exist_ok=True)
    sym.to_csv(os.path.join(paths["qa"], "MINUTE_QA_SYMBOLS.csv"), index=False)
    pyear.to_csv(os.path.join(paths["qa"], "MINUTE_QA_COVERAGE_YEAR.csv"), index=False)
    pday.to_csv(os.path.join(paths["qa"], "MINUTE_QA_SYMBOL_DAYS.csv.gz"), index=False, compression="gzip")
    sess.reset_index().to_csv(os.path.join(paths["qa"], "MINUTE_QA_SESSION_BY_DATE.csv"), index=False)
    def clean(o):
        if isinstance(o, dict): return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)): return [clean(v) for v in o]
        if isinstance(o, (np.integer,)): return int(o)
        if isinstance(o, (np.floating, float)): return finite(float(o))
        if isinstance(o, (np.bool_,)): return bool(o)
        return o
    issues = clean({"dataset": {"repo": "Muntasib-creator/DSE_dataset", "git_commit": dataset_commit, "path": a.input, "minute_dir": mdir, "daily_dir": ddir},
                    "policy": "detection only — nothing interpolated, forward-filled, deduplicated, repaired or dropped",
                    "thresholds": thresholds, "unverified_flags": C.unverified_flags(), "aggregate": agg, "breakdowns": breakdowns,
                    "samples": {k: v[:SAMPLE_CAP] for k, v in samples.items()}, "sample_counts": {k: len(v) for k, v in samples.items()},
                    "per_symbol_csv": "qa/MINUTE_QA_SYMBOLS.csv", "per_symbol_year_csv": "qa/MINUTE_QA_COVERAGE_YEAR.csv",
                    "per_symbol_day_csv": "qa/MINUTE_QA_SYMBOL_DAYS.csv.gz", "session_by_date_csv": "qa/MINUTE_QA_SESSION_BY_DATE.csv"})
    with open(os.path.join(paths["qa"], "MINUTE_QA_ISSUES.json"), "w") as fh:
        json.dump(issues, fh, indent=2, allow_nan=False)
    bio.write_manifest("minute_qa_manifest.json", {
        "phase": "1_data_qa_minute_v2", "input": a.input, "dataset_git_commit": dataset_commit, "files_sha256": hashes,
        "outputs": ["qa/MINUTE_QA_ISSUES.json", "qa/MINUTE_QA_SYMBOLS.csv", "qa/MINUTE_QA_COVERAGE_YEAR.csv",
                    "qa/MINUTE_QA_SYMBOL_DAYS.csv.gz", "qa/MINUTE_QA_SESSION_BY_DATE.csv", "reports/MINUTE_DATA_QA_REPORT.md"]})
    print(json.dumps({k: v for k, v in agg.items() if k not in ("session_hours_by_year", "overlap", "session_window_shift_dates")}, indent=1, default=str))
    print("session hours by year:\n" + sess_tbl.to_string(index=False))
    print("overlap:", json.dumps({k: v for k, v in overlap.items() if not k.endswith("_only")}, default=str))
    print("breakdowns:", json.dumps(clean(breakdowns), default=str)[:6000])
    print("wrote qa/MINUTE_QA_ISSUES.json and CSVs. Nothing was repaired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
