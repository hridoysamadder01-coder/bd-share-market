"""PHASE 1 — data foundation audit.

Rule: this module DETECTS and RECORDS. It never repairs an uncertain value and
never drops a row silently. Every exclusion lands in EXCLUSIONS.csv with a
reason code, and every count lands in DATA_QA_REPORT.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


# Reason codes are stable identifiers — downstream code filters on these.
CODES = {
    "DUP_BAR": "duplicate (symbol, ts)",
    "NAN_FIELD": "missing/non-numeric OHLCV field",
    "NONPOS_PRICE": "price <= 0",
    "NEG_VOLUME": "volume < 0",
    "OHLC_INCONSISTENT": "high < max(open,close) or low > min(open,close) or high < low",
    "TS_UNPARSED": "timestamp could not be parsed",
    "OUT_OF_SESSION": "timestamp outside the ASSUMED session window (unverified)",
    "NON_TRADING_WEEKDAY": "timestamp on an assumed non-trading weekday (unverified)",
}

# Flags are observations about market state, NOT data errors. They are kept in
# the dataset and carried as columns so research can condition on them.
FLAGS = {
    "ZERO_VOLUME": "bar with zero volume",
    "LOCKED_BAR": "open==high==low==close (locked / one-price bar)",
    "LOCKED_RUN": "part of a run of >= threshold locked bars (circuit/floor proxy)",
    "STALE_RUN": "part of a run of >= threshold identical closes",
    "LARGE_OVERNIGHT_GAP": "|log(open/prev_close)| above threshold — possible corporate action",
    "FLOOR_ERA": "inside the price-floor regime — separate, never pool with free-market periods",
    "SESSION_FIRST_BAR": "first bar of a trading day (gap semantics differ)",
}


def _run_lengths(mask: pd.Series) -> pd.Series:
    """Length of the consecutive True-run each element belongs to (0 where False)."""
    grp = (mask != mask.shift()).cumsum()
    sizes = mask.groupby(grp).transform("size")
    return sizes.where(mask, 0).astype(int)


def audit(df: pd.DataFrame, cfg: C.Config = C.DEFAULT) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (annotated_df, exclusions, summary).

    annotated_df keeps EVERY input row plus boolean flag columns and `qa_exclude`.
    exclusions is one row per (index, code) so nothing is collapsed or hidden.
    """
    d = df.copy()
    d["_row"] = np.arange(len(d))
    ex: list[pd.DataFrame] = []

    def mark(mask: pd.Series, code: str) -> None:
        if mask.any():
            ex.append(pd.DataFrame({
                "row": d.loc[mask, "_row"].values,
                "symbol": d.loc[mask, "symbol"].values,
                "ts": d.loc[mask, "ts"].values,
                "code": code,
                "detail": CODES[code],
            }))

    # ---- hard integrity: rows that cannot be trusted as observations ----
    mark(d["ts"].isna(), "TS_UNPARSED")
    ohlcv = ["open", "high", "low", "close", "volume"]
    mark(d[ohlcv].isna().any(axis=1), "NAN_FIELD")
    price_cols = ["open", "high", "low", "close"]
    mark((d[price_cols] <= 0).any(axis=1).fillna(False), "NONPOS_PRICE")
    mark((d["volume"] < 0).fillna(False), "NEG_VOLUME")
    hi_ok = d["high"] >= d[["open", "close"]].max(axis=1)
    lo_ok = d["low"] <= d[["open", "close"]].min(axis=1)
    hl_ok = d["high"] >= d["low"]
    mark(~(hi_ok & lo_ok & hl_ok).fillna(False), "OHLC_INCONSISTENT")
    mark(d.duplicated(subset=["symbol", "ts"], keep=False) & d["ts"].notna(), "DUP_BAR")

    # ---- calendar / session (UNVERIFIED assumptions — flagged, and excluded
    #      only so that a wrong assumption is visible rather than silent) ----
    have_ts = d["ts"].notna()
    if C.BAR_FREQUENCY == "MINUTE":
        # Intraday only: a daily bar carries no time-of-day, so this check would
        # reject the entire dataset on a technicality.
        tod = d["ts"].dt.strftime("%H:%M")
        in_session = (tod >= C.ASSUMED_SESSION_START) & (tod <= C.ASSUMED_SESSION_END)
        mark(have_ts & ~in_session, "OUT_OF_SESSION")
    wd = d["ts"].dt.weekday
    mark(have_ts & ~wd.isin(C.ASSUMED_TRADING_WEEKDAYS), "NON_TRADING_WEEKDAY")

    exclusions = (pd.concat(ex, ignore_index=True) if ex
                  else pd.DataFrame(columns=["row", "symbol", "ts", "code", "detail"]))
    d["qa_exclude"] = d["_row"].isin(exclusions["row"]) if len(exclusions) else False

    # ---- market-state flags: kept, never excluded ----
    d["flag_zero_volume"] = (d["volume"] == 0).fillna(False)
    locked = ((d["open"] == d["high"]) & (d["high"] == d["low"]) &
              (d["low"] == d["close"])).fillna(False)
    d["flag_locked_bar"] = locked
    d["flag_session_first_bar"] = d.groupby(
        [d["symbol"], d["ts"].dt.date], dropna=False)["ts"].transform("min").eq(d["ts"])
    # Price-floor regime (Round 2 dates). Kept as a column so research can
    # separate it — pooling a floored market with a free one is meaningless.
    fa, fb = (pd.Timestamp(x) for x in C.FLOOR_ERA)
    d["flag_floor_era"] = d["ts"].between(fa, fb).fillna(False)

    d["flag_locked_run"] = False
    d["flag_stale_run"] = False
    d["flag_large_overnight_gap"] = False
    d["overnight_gap"] = np.nan
    for sym, idx in d.groupby("symbol", sort=False).groups.items():
        g = d.loc[idx].sort_values("ts", kind="mergesort")
        lr = _run_lengths(g["flag_locked_bar"])
        d.loc[g.index, "flag_locked_run"] = (lr >= cfg.qa.locked_bar_run_flag).values
        sr = _run_lengths(g["close"].eq(g["close"].shift()).fillna(False))
        d.loc[g.index, "flag_stale_run"] = (sr >= cfg.qa.stale_price_run_flag).values
        # Guard the log: a non-positive price (already excluded above) would emit
        # -inf and a RuntimeWarning. Undefined, not infinite.
        prev_close = g["close"].shift().where(lambda s: s > 0)
        open_pos = g["open"].where(g["open"] > 0)
        gap = np.log(open_pos / prev_close)
        first = g["flag_session_first_bar"].values
        gap = gap.where(pd.Series(first, index=g.index))   # only across day boundaries
        d.loc[g.index, "overnight_gap"] = gap.values
        d.loc[g.index, "flag_large_overnight_gap"] = (
            gap.abs() > cfg.qa.max_abs_overnight_gap).fillna(False).values

    summary = _summarize(d, exclusions, cfg)
    return d.drop(columns=["_row"]), exclusions, summary


def _summarize(d: pd.DataFrame, exclusions: pd.DataFrame, cfg: C.Config) -> dict:
    have_ts = d["ts"].notna()
    days = d.loc[have_ts, "ts"].dt.date
    all_days = sorted(days.unique())
    per_sym_days = d.loc[have_ts].groupby("symbol")["ts"].apply(lambda s: s.dt.date.nunique())
    coverage = (per_sym_days / max(len(all_days), 1)).sort_values()

    bars_per_day = (d.loc[have_ts].groupby(["symbol", days.rename("day")])
                    .size().rename("bars"))
    expected = _expected_bars_per_day()
    thin_days = bars_per_day[bars_per_day < expected * cfg.qa.min_bars_per_day_ratio]

    # COVERAGE REGIME BREAKS. A dataset stitched from two sources changes what a
    # cross-sectional statistic even means on the day the universe jumps. Real
    # DSE data drops 381 -> 88 reporting symbols on 2024-02-22; nothing about
    # that day is a market event, but every xs_* feature silently changes basis.
    per_day_symbols = d.loc[have_ts].groupby(days)["symbol"].nunique().sort_index()
    delta = per_day_symbols.diff()
    prior = per_day_symbols.shift()
    ratio = (delta.abs() / prior.where(prior > 0))
    brk = ratio[ratio > cfg.qa.coverage_break_ratio]
    coverage_breaks = [
        {"date": str(dt), "from": int(prior.loc[dt]), "to": int(per_day_symbols.loc[dt]),
         "change": int(delta.loc[dt])}
        for dt in brk.index
    ]

    # Listing / delisting: symbols whose first (last) observation is after (before)
    # the global first (last) day.
    first_seen = d.loc[have_ts].groupby("symbol")["ts"].min()
    last_seen = d.loc[have_ts].groupby("symbol")["ts"].max()
    global_first, global_last = d.loc[have_ts, "ts"].min(), d.loc[have_ts, "ts"].max()

    return {
        "rows_total": int(len(d)),
        "symbols": int(d["symbol"].nunique()),
        "date_range": [str(global_first), str(global_last)],
        "trading_days_observed": len(all_days),
        "expected_bars_per_day_assumed": expected,
        "rows_excluded": int(d["qa_exclude"].sum()),
        "exclusions_by_code": exclusions["code"].value_counts().to_dict() if len(exclusions) else {},
        "flags": {
            "zero_volume": int(d["flag_zero_volume"].sum()),
            "locked_bar": int(d["flag_locked_bar"].sum()),
            "locked_run": int(d["flag_locked_run"].sum()),
            "stale_run": int(d["flag_stale_run"].sum()),
            "large_overnight_gap": int(d["flag_large_overnight_gap"].sum()),
            "floor_era": int(d["flag_floor_era"].sum()),
        },
        "symbols_per_day": {"min": int(per_day_symbols.min()),
                            "median": int(per_day_symbols.median()),
                            "max": int(per_day_symbols.max()),
                            "last": int(per_day_symbols.iloc[-1])},
        "coverage_breaks": coverage_breaks,
        "thin_days_count": int(len(thin_days)),
        "thin_days_examples": [[str(a), str(b), int(c)] for (a, b), c in thin_days.head(10).items()],
        "survivorship": {
            "symbols_below_coverage": coverage[coverage < cfg.qa.min_days_coverage_ratio]
                                      .round(3).to_dict(),
            "late_listings": {s: str(t) for s, t in first_seen[first_seen > global_first].items()},
            "early_endings": {s: str(t) for s, t in last_seen[last_seen < global_last].items()},
        },
        "turnover_derived": bool(d.attrs.get("turnover_derived", False)),
    }


def _expected_bars_per_day() -> int:
    if C.BAR_FREQUENCY == "DAILY":
        return 1
    h1, m1 = (int(x) for x in C.ASSUMED_SESSION_START.split(":"))
    h2, m2 = (int(x) for x in C.ASSUMED_SESSION_END.split(":"))
    return max(((h2 * 60 + m2) - (h1 * 60 + m1)) // C.ASSUMED_BAR_MINUTES, 1)


def report_markdown(summary: dict, source: str, source_sha: str) -> str:
    flags = C.unverified_flags()
    lines = [
        "# DATA_QA_REPORT",
        "",
        "> Generated by `qa/run_qa.py`. Detection only — no value in the",
        "> dataset was repaired, interpolated or back-filled by this run.",
        "",
        "## Source",
        "",
        f"- bar frequency: **{C.BAR_FREQUENCY}**",
        f"- file: `{source}`",
        f"- sha256: `{source_sha}`",
        f"- rows: {summary['rows_total']:,} · symbols: {summary['symbols']} ·"
        f" observed trading days: {summary['trading_days_observed']}",
        f"- date range: {summary['date_range'][0]} → {summary['date_range'][1]}",
        f"- turnover column derived (close×volume) rather than exchange-reported: "
        f"**{summary['turnover_derived']}**",
        "",
        "## ⚠ Unverified market conventions",
        "",
        "Any conclusion that depends on one of these is provisional until the flag is true.",
        "",
        "| Convention | Verified |",
        "|---|---|",
    ]
    lines += [f"| {k} | {'✅' if v else '❌ **NO**'} |" for k, v in flags.items()]
    lines += [
        "",
        (f"Assumed session {C.ASSUMED_SESSION_START}–{C.ASSUMED_SESSION_END}, "
         f"{C.ASSUMED_BAR_MINUTES}-minute bars ⇒ "
         f"{summary['expected_bars_per_day_assumed']} bars/day expected."
         if C.BAR_FREQUENCY == "MINUTE" else
         "Daily bars: one bar per symbol per trading session. Intraday session-window "
         "checks do not apply; the assumed trading weekdays (Sun–Thu) still do."),
        "",
        "## Exclusions (rows that cannot be trusted as observations)",
        "",
        f"Total excluded: **{summary['rows_excluded']:,}** of {summary['rows_total']:,}",
        "",
        "| Code | Rows | Meaning |",
        "|---|---|---|",
    ]
    for code, n in sorted(summary["exclusions_by_code"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{code}` | {n:,} | {CODES.get(code, '')} |")
    if not summary["exclusions_by_code"]:
        lines.append("| — | 0 | no integrity violations detected |")

    lines += [
        "",
        "## Market-state flags (kept in the dataset, never excluded)",
        "",
        "| Flag | Bars | Meaning |",
        "|---|---|---|",
    ]
    flagmap = {"zero_volume": "ZERO_VOLUME", "locked_bar": "LOCKED_BAR",
               "locked_run": "LOCKED_RUN", "stale_run": "STALE_RUN",
               "large_overnight_gap": "LARGE_OVERNIGHT_GAP",
               "floor_era": "FLOOR_ERA"}
    for k, n in summary["flags"].items():
        lines.append(f"| `{k}` | {n:,} | {FLAGS[flagmap[k]]} |")

    sv = summary["survivorship"]
    sp = summary["symbols_per_day"]
    lines += [
        "",
        "## ⚠ Coverage regime breaks",
        "",
        f"Reporting symbols per day — min {sp['min']} · median {sp['median']} · "
        f"max {sp['max']} · latest {sp['last']}.",
        "",
        "A day where the reporting universe jumps is not a market event: it is the",
        "dataset changing basis. Every cross-sectional feature (`xs_*`, `market_ret`)",
        "means something different on either side, and a period straddling a break",
        "cannot be compared with one that does not.",
        "",
    ]
    if summary["coverage_breaks"]:
        lines += ["| Date | Symbols before | after | change |", "|---|---|---|---|"]
        for b in summary["coverage_breaks"]:
            lines.append(f"| {b['date']} | {b['from']} | {b['to']} | {b['change']:+d} |")
    else:
        lines.append("None detected at the configured threshold.")
    lines += [
        "",
        "## Coverage, listing and survivorship",
        "",
        f"- days with fewer than half the expected bars: **{summary['thin_days_count']}**",
        f"- symbols below the coverage threshold: **{len(sv['symbols_below_coverage'])}** "
        f"{list(sv['symbols_below_coverage'].items())[:10]}",
        f"- late listings (first bar after the global start): {len(sv['late_listings'])} "
        f"{list(sv['late_listings'].items())[:5]}",
        f"- early endings (last bar before the global end): {len(sv['early_endings'])} "
        f"{list(sv['early_endings'].items())[:5]}",
        "",
        "> Survivorship note: any cross-sectional statistic computed over symbols that",
        "> exist for the whole range is biased. Research must either include partial-life",
        "> symbols or state the restriction explicitly in its manifest.",
        "",
        "## Corporate actions",
        "",
        f"- CORP_ACTIONS_AVAILABLE = **{C.CORP_ACTIONS_AVAILABLE}**. With no action table,",
        "  splits/bonuses are indistinguishable from real gaps. Bars flagged",
        "  `flag_large_overnight_gap` are suspects only — they were NOT adjusted.",
        "",
    ]
    return "\n".join(lines)
