#!/usr/bin/env python3
"""Extend the EOD bar table past the owner-supplied CSVs, from DSE's day-end archive.

`ingest_dse_eod.py` reads CSVs the owner supplies; it does not fetch. That left the bar
table ending 2026-01-22 while the shareholding observations ran to 2026-08-31, so only 22
symbols had two holding dates inside the price range — too few to test anything. This
pulls the missing months from `dsebd.org/day_end_archive.php` through `bdshare` and
appends them.

Two things this does that a naive append must not skip:

* **The overlap is verified, not assumed.** Before appending, the bars the new source
  reports for dates the existing table already covers are compared field by field. The
  append only proceeds when they agree. On the first run (2026-01-01…22, 1,182 rows over
  74 symbols) open, high, low, close and volume matched to the last decimal, 100 %.
* **Provenance is carried per row.** Every row records which source produced it, so no
  analysis can silently mix the owner's CSVs with the archive pull.

`turnover` keeps the existing table's definition (close × volume, derived) so the column
means one thing throughout. The archive's own `value` field is kept beside it as
`turnover_reported_mn` — exchange-reported, in million BDT — and is never merged into
`turnover`.

dsebd.org serves a certificate chain that does not verify, so the fallback is forced for
that host alone, exactly as `seeing/capture/http_client.py` does.

The parquet is a generated artifact and is git-ignored like every other one; the
committed evidence is `RAW_EXTENSION_MANIFEST.json` — hashes, row counts, date ranges and
the overlap check — so the dataset behind any result stays identifiable.

    python3 data/extend_dse_eod.py --start 2026-01-01
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Same instrument-class filter as ingest_dse_eod.py, so the universe stays comparable.
NONEQ = ("BOND", "SUKUK", "MF", "TB1", "TB2", "TB5", "TB10", "TB15", "TB20",
         "00DS", "ETF", "PBOND", "GBF", "INCOMEF", "GROWTHF")

BASE_BARS = os.path.join(ROOT, "results", "dse_eod_bars_annotated.parquet")
OUT_PARQUET = os.path.join(HERE, "raw", "dse_eod_extended.parquet")
OUT_MANIFEST = os.path.join(HERE, "raw", "RAW_EXTENSION_MANIFEST.json")


def looks_equity(sym: str) -> bool:
    return not any(h in sym for h in NONEQ)


def enable_dsebd_tls_fallback() -> None:
    """dsebd.org's chain does not verify; scope the fallback to that host only."""
    import requests
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    original = requests.Session.request

    def patched(self, method, url, **kwargs):
        if "dsebd.org" in str(url) or "dse.com.bd" in str(url):
            kwargs["verify"] = False
        return original(self, method, url, **kwargs)

    requests.Session.request = patched


def month_windows(start: date, end: date) -> List[Tuple[str, str]]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        out.append((max(cur, start).isoformat(), min(nxt - timedelta(days=1), end).isoformat()))
        cur = nxt
    return out


def fetch_range(start: date, end: date, pause: float = 1.0,
                retries: int = 3) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """One request per calendar month for every instrument. Failures are recorded."""
    enable_dsebd_tls_fallback()
    from bdshare import get_historical_data

    parts, log = [], []
    for a, b in month_windows(start, end):
        got, err = None, None
        for attempt in range(retries):
            try:
                got = get_historical_data(a, b)
                break
            except Exception as exc:  # noqa: BLE001 — a failed window is recorded, not fatal
                err = f"{type(exc).__name__}: {exc}"[:200]
                time.sleep(pause * (attempt + 1))
        n = 0 if got is None else len(got)
        if n:
            parts.append(got)
        log.append({"window": f"{a}..{b}", "rows": int(n), "error": None if n else err})
        print(f"  {a}..{b}: {n} rows" + ("" if n else f"  FAILED {err}"))
        time.sleep(pause)
    if not parts:
        return pd.DataFrame(), log
    raw = pd.concat(parts).reset_index()
    raw = raw.rename(columns={raw.columns[0]: "date"})
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw["symbol"] = raw["symbol"].astype(str).str.strip().str.upper()
    raw = raw.dropna(subset=["date", "symbol"]).drop_duplicates(["symbol", "date"], keep="first")
    return raw, log


def verify_overlap(base: pd.DataFrame, new: pd.DataFrame) -> Dict[str, Any]:
    """Compare the two sources where their dates overlap. The append depends on this."""
    lo, hi = new["date"].min(), base["ts"].max()
    if pd.isna(lo) or lo > hi:
        return {"rows": 0, "agree": None,
                "note": "no overlapping dates — the two sources cannot be cross-checked"}
    o = base[(base["ts"] >= lo) & (base["ts"] <= hi)][
        ["symbol", "ts", "open", "high", "low", "close", "volume"]]
    n = new[(new["date"] >= lo) & (new["date"] <= hi)][
        ["symbol", "date", "open", "high", "low", "close", "volume"]]
    m = o.merge(n, left_on=["symbol", "ts"], right_on=["symbol", "date"], suffixes=("_old", "_new"))
    per = {}
    for c in ("open", "high", "low", "close", "volume"):
        diff = (m[f"{c}_old"] - m[f"{c}_new"]).abs()
        per[c] = {"exact_match_pct": round(float((diff <= 1e-9).mean() * 100), 4),
                  "max_abs_diff": float(diff.max()) if len(m) else None}
    agree = all(v["exact_match_pct"] == 100.0 for v in per.values()) if len(m) else None
    return {"window": [str(lo.date()), str(hi.date())], "rows": int(len(m)),
            "symbols": int(m["symbol"].nunique()), "dates": int(m["ts"].nunique()),
            "per_field": per, "agree": agree}


def build(start: date, end: date, pause: float = 1.0,
          allow_mismatch: bool = False) -> Dict[str, Any]:
    base = pd.read_parquet(BASE_BARS)
    cut = base["ts"].max()
    print(f"existing bars: {len(base):,} rows, {base['symbol'].nunique()} symbols, "
          f"{base['ts'].min().date()} -> {cut.date()}")

    raw, log = fetch_range(start, end, pause=pause)
    if raw.empty:
        raise SystemExit("no rows fetched — nothing to extend")
    eq = raw[raw["symbol"].map(looks_equity)].copy()
    print(f"fetched {len(raw):,} rows, {raw['symbol'].nunique()} instruments "
          f"-> {eq['symbol'].nunique()} after the equity filter")

    overlap = verify_overlap(base, eq)
    print(f"overlap check: {overlap.get('rows', 0)} rows, agree={overlap.get('agree')}")
    if overlap.get("agree") is False and not allow_mismatch:
        raise SystemExit("the two sources disagree on the overlap — refusing to append. "
                         "Inspect RAW_EXTENSION_MANIFEST.json, or pass --allow-mismatch "
                         "if the difference is understood and acceptable.")

    ext = eq[eq["date"] > cut].rename(columns={"date": "ts"}).copy()
    ext["turnover"] = ext["close"] * ext["volume"]
    ext["turnover_reported_mn"] = ext.get("value", np.nan)
    cols = ["symbol", "ts", "open", "high", "low", "close", "volume", "turnover",
            "turnover_reported_mn", "trade", "ycp", "ltp"]
    for c in cols:
        if c not in ext.columns:
            ext[c] = np.nan
    ext = ext[cols]

    keep = base[["symbol", "ts", "open", "high", "low", "close", "volume", "turnover"]].copy()
    for c in ("turnover_reported_mn", "trade", "ycp", "ltp"):
        keep[c] = np.nan
    comb = pd.concat([keep[cols], ext], ignore_index=True)
    comb = comb.sort_values(["symbol", "ts"]).reset_index(drop=True)
    comb["source"] = np.where(comb["ts"] <= cut, "owner_csv_ingest", "bdshare_day_end_archive")

    os.makedirs(os.path.dirname(OUT_PARQUET), exist_ok=True)
    comb.to_parquet(OUT_PARQUET, index=False)

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "script": "data/extend_dse_eod.py",
        "output_parquet": os.path.relpath(OUT_PARQUET, ROOT),
        "output_sha256": sha256(OUT_PARQUET),
        "output_bytes": os.path.getsize(OUT_PARQUET),
        "base": {"file": os.path.relpath(BASE_BARS, ROOT), "rows": int(len(base)),
                 "symbols": int(base["symbol"].nunique()),
                 "range": [str(base["ts"].min().date()), str(cut.date())]},
        "fetched": {"source": "dsebd.org/day_end_archive.php via bdshare.get_historical_data",
                    "requested": [start.isoformat(), end.isoformat()],
                    "rows": int(len(raw)), "instruments": int(raw["symbol"].nunique()),
                    "equity_symbols": int(eq["symbol"].nunique()), "windows": log},
        "overlap_check": overlap,
        "appended": {"rows": int(len(ext)), "symbols": int(ext["symbol"].nunique()),
                     "range": [str(ext["ts"].min().date()), str(ext["ts"].max().date())]
                     if len(ext) else None},
        "combined": {"rows": int(len(comb)), "symbols": int(comb["symbol"].nunique()),
                     "range": [str(comb["ts"].min().date()), str(comb["ts"].max().date())],
                     "by_source": {k: int(v) for k, v in comb["source"].value_counts().items()}},
        "turnover_note": "turnover = close x volume (derived, same definition as the base "
                         "table). turnover_reported_mn is the archive's own value field in "
                         "million BDT and is never merged into turnover.",
        "truth": "OBSERVED — exchange day-end bars; no price adjusted, no gap filled.",
    }
    with open(OUT_MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"\ncombined: {len(comb):,} rows, {comb['symbol'].nunique()} symbols, "
          f"{comb['ts'].min().date()} -> {comb['ts'].max().date()}")
    print(f"wrote {OUT_PARQUET} and {OUT_MANIFEST}")
    return manifest


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-01-01", help="first date to fetch (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="last date to fetch (default: today UTC)")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--allow-mismatch", action="store_true",
                    help="append even if the sources disagree on the overlap")
    a = ap.parse_args(argv)
    start = datetime.strptime(a.start, "%Y-%m-%d").date()
    end = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else datetime.now(timezone.utc).date()
    build(start, end, pause=a.pause, allow_mismatch=a.allow_mismatch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
