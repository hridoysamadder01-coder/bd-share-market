#!/usr/bin/env python3
"""All-symbol public DSE acquisition: raw-first, rerunnable, provenance-complete.

    python3 -m collector.dse_public_collector --out data --all-depth --all-tape \
        --company --history-start 2015-01-01

Public/open endpoints only. Normal browser flows are reproduced (anti-forgery token,
referer, XHR headers); nothing authenticated, paywalled or CAPTCHA-protected is touched.

What it collects, per source, with a truth class on every field group
(OBSERVED = the source delivered it · INFERRED = derived here from observations ·
NOT_AVAILABLE = no obtained public source carries it):

  dsebd.org (official)
    latest_share_price_scroll_l.php   all-symbol L1 + day totals            OBSERVED
    ajax/load-instrument.php          per-symbol displayed depth (L2)       OBSERVED
    displayCompany.php?name=SYM       fundamentals: sector, category, market cap,
                                      free-float market cap, outstanding securities,
                                      paid-up/authorized capital, face value, market lot,
                                      P/E (basic/diluted/trailing), EPS & dividend history,
                                      shareholding % (sponsor/govt/institute/foreign/public),
                                      52-week range, day range                OBSERVED
    day_end_archive.php               historical OHLCV + trades + value      OBSERVED
    hts.php                           trading sessions and holidays          OBSERVED

  lankabd.com
    Home/MarketDepthData              per-symbol displayed depth (L2)         OBSERVED
    api/datafeed/IndexLiveData/LiveStockWatchData
                                      all-symbol L1 with per-instrument exchange stamp
                                                                             OBSERVED
    api/Company/MkSecondDataSymbol    exchange-stamped CUMULATIVE intraday tape
                                      (~1 row/min; NOT individual prints)     OBSERVED
    api/datafeed/IndexLiveData/LiveDSETradeStatistics   market totals/breadth OBSERVED
    api/APIMarket/GetLatestBlockMarket block-board prints                     OBSERVED
    Home/CircuitBreaker               per-symbol circuit limits + tick size   OBSERVED
    api/APIMarket/GetDataGrid         all-symbol grid                         OBSERVED
    Home/MinuteChartMatrix            symbol → company-id map                 OBSERVED

  derived here
    interval trades/volume/value      differences of consecutive cumulative rows  INFERRED

  never available from these public sources (never fabricated)
    individual trade prints · number of orders per level · order ids ·
    queue position · intra-interval add/cancel netting                    NOT_AVAILABLE

Every response is written byte-exact under ``data/raw/<source>/`` (gzip above 64 KB,
verified by round-trip) and every normalized row carries source, endpoint, symbol,
exchange, exchange timestamp when the source has one, receipt timestamp, the raw file it
came from, that file's sha256, the HTTP status and its truth class. Nothing that failed
is dropped: failures land in ``data/metadata/failures.json`` and in the request manifest.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from bs4 import BeautifulSoup

from seeing.capture.adapters import dsebd, lankabd
from seeing.capture.http_client import Fetched, PoliteClient

DSE = "https://www.dsebd.org"
LB = "https://www.lankabd.com"
GZIP_ABOVE = 64 * 1024
NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def num(x: Any) -> Optional[float]:
    """A number the source actually printed, else None. '-', '--', '' are NOT_AVAILABLE."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if not s or s in {"-", "--", "N/A", "n/a", "null", "None", "*"}:
        return None
    m = NUM_RE.search(s)
    return float(m.group(0)) if m else None


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s)).strip("_")[:80]


# ------------------------------------------------------------------ company page (dsebd.org)
def _cells(tr) -> List[str]:
    return [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]


def _pairs(table) -> Dict[str, str]:
    """label → value for DSE's 2-cell and 4-cell (label|value|label|value) rows.

    Empty cells are kept while pairing (a row may end with an empty value, e.g. an
    unlisted "Debut Trading Date") and only then dropped, so the labels after it still
    line up with their own values."""
    out: Dict[str, str] = {}
    for tr in table.find_all("tr"):
        c = _cells(tr)
        if len(c) < 2 or len(c) > 8:
            continue
        for i in range(0, len(c) - 1, 2):
            k = c[i].rstrip("*: ").strip()
            v = c[i + 1].strip()
            if k and k not in out and v != "":
                out[k] = v
    return out


def _pe_basis(table) -> str:
    """'audited' / 'unaudited' from the heading that introduces a P/E block, else 'unknown'."""
    node = table
    for _ in range(14):
        node = node.find_previous(string=True)
        if node is None:
            break
        t = str(node).strip().lower()
        if "price earnings" in t and "ratio" in t:
            if "un-audited" in t or "unaudited" in t:
                return "unaudited"
            if "audited" in t:
                return "audited"
    return "unknown"


def parse_company_page(html: str, symbol: str) -> Dict[str, Any]:
    """Fundamentals for one instrument from displayCompany.php.

    Returns ``{"info": {...}, "financials": [...], "pe": [...], "holdings": [...],
    "problems": [...]}``. Every value is what the page printed; a field the page does
    not carry (or prints as '-') is absent/None, never zero.
    """
    soup = BeautifulSoup(html, "lxml")
    info: Dict[str, Any] = {"symbol": symbol.upper()}
    financials: List[Dict[str, Any]] = []
    pe_rows: List[Dict[str, Any]] = []
    holdings: List[Dict[str, Any]] = []
    problems: List[str] = []

    quote_keys = {
        "Last Trading Price": "last_trading_price", "Closing Price": "close",
        "Yesterday's Closing Price": "yclose", "Opening Price": "open",
        "Adjusted Opening Price": "adjusted_open", "Day's Range": "days_range",
        "Day's Value (mn)": "day_value_mn", "Day's Volume (Nos.)": "day_volume",
        "Day's Trade (Nos.)": "day_trades", "Market Capitalization (mn)": "market_cap_mn",
        "Free Float Market Cap. (mn)": "free_float_market_cap_mn",
        "52 Weeks' Moving Range": "week52_range", "Last Update": "last_update_local",
        "Change": "change_raw",
    }
    basic_keys = {
        "Authorized Capital (mn)": "authorized_capital_mn", "Paid-up Capital (mn)": "paid_up_capital_mn",
        "Face/par Value": "face_value", "Total No. of Outstanding Securities": "outstanding_securities",
        "Sector": "sector", "Market Lot": "market_lot", "Type of Instrument": "instrument_type",
        "Debut Trading Date": "debut_trading_date", "Listing Year": "listing_year",
        "Market Category": "market_category", "Electronic Share": "electronic_share",
        "Remarks": "remarks",
    }
    text_fields = {"days_range", "week52_range", "last_update_local", "change_raw", "sector",
                   "instrument_type", "debut_trading_date", "market_category", "electronic_share",
                   "remarks"}

    for table in soup.find_all("table"):
        txt = table.get_text(" ", strip=True)
        if len(txt) > 8000:
            continue
        if "Last Trading Price" in txt or "Authorized Capital" in txt or "Listing Year" in txt:
            for k, v in _pairs(table).items():
                for keys in (quote_keys, basic_keys):
                    if k in keys:
                        field = keys[k]
                        info[field] = v if field in text_fields else num(v)
        # P/E table: Particulars | date | date | ... ; keep every dated column
        if "Particulars" in txt and "P/E" in txt:
            rows = [_cells(tr) for tr in table.find_all("tr")]
            head = next((r for r in rows if r and r[0].strip().lower() == "particulars"), None)
            if head:
                basis = _pe_basis(table)                     # audited / unaudited block
                dates = [d.strip() for d in head[1:]]
                for r in rows:
                    if not r or r[0].strip().lower() == "particulars":
                        continue
                    label = r[0].strip().rstrip("*").strip()
                    for i, d in enumerate(dates, start=1):
                        if i < len(r):
                            pe_rows.append({"symbol": symbol.upper(), "as_of": d, "basis": basis,
                                            "measure": label, "value": num(r[i])})
        # yearly EPS / dividend history
        if txt.startswith("Year ") and "Dividend" in txt and "Earnings per share" in txt:
            for r in (_cells(tr) for tr in table.find_all("tr")):
                if not r or not re.fullmatch(r"\d{4}", r[0].strip()):
                    continue
                vals = [num(x) for x in r[1:]]
                financials.append({
                    "symbol": symbol.upper(), "year": int(r[0]),
                    "pe_year_end_basic_original": vals[0] if len(vals) > 0 else None,
                    "pe_year_end_basic_restated": vals[1] if len(vals) > 1 else None,
                    "pe_year_end_diluted_original": vals[2] if len(vals) > 2 else None,
                    "eps_basic": vals[3] if len(vals) > 3 else None,
                    "eps_continuing_basic": vals[4] if len(vals) > 4 else None,
                    "eps_continuing_diluted": vals[5] if len(vals) > 5 else None,
                    "dividend_raw": r[-2] if len(r) >= 2 else None,
                    "dividend_yield_pct": num(r[-1]) if len(r) >= 1 else None,
                })
        # shareholding percentages, one row per as-on date
        if "Share Holding Percentage" in txt:
            for tr in table.find_all("tr"):
                c = _cells(tr)
                if not c or "Share Holding Percentage" not in c[0]:
                    continue
                m = re.search(r"\[as on ([^\]]+)\]", c[0])
                blob = " ".join(c[1:])
                pct = dict(re.findall(r"(Sponsor/Director|Govt|Institute|Foreign|Public)\s*:\s*([\d.]+)", blob))
                if pct:
                    holdings.append({
                        "symbol": symbol.upper(), "as_on": (m.group(1).strip() if m else None),
                        "sponsor_director_pct": num(pct.get("Sponsor/Director")),
                        "govt_pct": num(pct.get("Govt")), "institute_pct": num(pct.get("Institute")),
                        "foreign_pct": num(pct.get("Foreign")), "public_pct": num(pct.get("Public")),
                    })
    if len(info) <= 1:
        problems.append("no fundamentals tables recognised on the page")
    # latest P/E as flat fields, kept separate per basis: the page publishes an audited and an
    # un-audited block for the same dates and they disagree; collapsing them would invent a number
    if pe_rows:
        last_date = max(pe_rows, key=lambda r: pe_rows.index(r))["as_of"]
        info["pe_as_of"] = last_date
        for r in pe_rows:
            if r["as_of"] != last_date:
                continue
            lab = r["measure"].lower()
            kind = "trailing" if "trailing" in lab else "diluted" if "diluted" in lab else \
                   "basic" if "basic" in lab else None
            if kind:
                info[f"pe_{kind}_{r['basis']}"] = r["value"]
    if holdings:
        h = holdings[-1]
        info.update({"holding_as_on": h["as_on"], "sponsor_director_pct": h["sponsor_director_pct"],
                     "govt_pct": h["govt_pct"], "institute_pct": h["institute_pct"],
                     "foreign_pct": h["foreign_pct"], "public_pct": h["public_pct"]})
    if info.get("market_cap_mn") and info.get("free_float_market_cap_mn"):
        info["free_float_pct_of_mcap"] = round(
            100.0 * info["free_float_market_cap_mn"] / info["market_cap_mn"], 4)   # INFERRED
    return {"info": info, "financials": financials, "pe": pe_rows, "holdings": holdings,
            "problems": problems}


# ------------------------------------------------------------------ generic day-end tables
def parse_page_table(html: str) -> Tuple[List[str], List[List[str]]]:
    """The main data table of a DSE day-end page, with the page's own column names.

    Nothing about the schema is assumed: the table with the most cells whose rows agree on a
    column count (≥ 3) wins, its first row (or its ``th`` row) supplies the headers, and the body
    rows are returned as printed. The site's scrolling ticker (one cell per row) never qualifies.
    """
    soup = BeautifulSoup(html, "lxml")
    best: Tuple[int, List[str], List[List[str]]] = (0, [], [])
    for t in soup.find_all("table"):
        rows = [_cells(tr) for tr in t.find_all("tr")]
        rows = [r for r in rows if any(x.strip() for x in r)]
        if len(rows) < 3:
            continue
        widths = Counter(len(r) for r in rows)
        width, n = widths.most_common(1)[0]
        if width < 3 or n < 3:
            continue
        body = [r for r in rows if len(r) == width]
        head_cells = [th.get_text(" ", strip=True) for th in t.find_all("th")]
        head = head_cells if len(head_cells) == width else body[0]
        stripped = [h.strip() for h in head]
        data = [r for r in body if [x.strip() for x in r] != stripped]     # the header row is not data
        score = len(data) * width
        if score > best[0]:
            best = (score, [h.strip() for h in head], data)
    return best[1], best[2]


def parse_market_statistics(html: str) -> Dict[str, Any]:
    """The official day-end statistics report (market-statistics.php), which is plain text,
    not a table: breadth per market category, day totals, market capitalisation by instrument
    class, and the block-transactions board. Anything the report does not print stays absent."""
    text = BeautifulSoup(html, "lxml").get_text("\n")
    out: Dict[str, Any] = {"breadth": [], "block": []}
    m = re.search(r"TODAY'S SHARE MARKET\s*:\s*(\d{4}-\d{2}-\d{2})", text)
    out["report_date"] = m.group(1) if m else None
    for m in re.finditer(r"([A-Za-z][A-Za-z .()&-]*?)\s*\n\s*ISSUES ADVANCED\s*:\s*(\d+)\s*\n\s*"
                         r"ISSUES DECLINED\s*:\s*(\d+)\s*\n\s*ISSUES UNCHANGED\s*:\s*(\d+)\s*\n\s*"
                         r"TOTAL ISSUES TRADED\s*:\s*(\d+)", text):
        out["breadth"].append({"category": m.group(1).strip(), "advanced": int(m.group(2)),
                               "declined": int(m.group(3)), "unchanged": int(m.group(4)),
                               "total_traded": int(m.group(5))})
    for key, pat in (("day_trades", r"NO\. OF TRADES\s*:\s*([\d,.]+)"),
                     ("day_volume", r"VOLUME\(Nos\.\)\s*:\s*([\d,.]+)"),
                     ("day_value_tk", r"VALUE\(Tk\)\s*:\s*([\d,.]+)"),
                     ("mcap_equity_tk", r"1\.\s*EQUITY\s*:?\s*([\d,.]+)"),
                     ("mcap_mutual_fund_tk", r"2\.\s*MUTUAL FUND\s*:?\s*([\d,.]+)"),
                     ("mcap_debt_tk", r"3\.\s*DEBT SECURITIES\s*:?\s*([\d,.]+)"),
                     ("mcap_total_tk", r"TOTAL\s*:?\s*([\d,.]+)\s*\n")):
        mm = re.search(pat, text)
        out[key] = num(mm.group(1)) if mm else None
    m = re.search(r"PRICES IN BLOCK TRANSACTIONS\s*:\s*(\d{4}-\d{2}-\d{2})", text)
    out["block_date"] = m.group(1) if m else None
    tail = text[m.end():] if m else ""
    for line in tail.splitlines():
        mm = re.match(r"\s*([A-Z0-9][A-Z0-9.()&-]{1,20})\s+([\d,.]+)\s+([\d,.]+)\s+(\d+)\s+([\d,]+)\s+([\d,.]+)\s*$",
                      line)
        if mm:
            out["block"].append({"symbol": mm.group(1), "max_price": num(mm.group(2)),
                                 "min_price": num(mm.group(3)), "trades": int(mm.group(4)),
                                 "quantity": num(mm.group(5)), "value_mn": num(mm.group(6)),
                                 "block_date": out["block_date"]})
    return out


EXTRA_PAGES = {
    "circuit_breaker_official": "/cbul.php",
    "pe_at_a_glance": "/latest_PE.php",
    "sector_wise_company_list": "/by_industrylisting.php",
    "top_ten_gainer": "/top_ten_gainer.php",
    "top_ten_loser": "/top_ten_loser.php",
    "marginable_securities": "/marginable-securities.php",
    "close_price": "/dse_close_price.php",
    "recent_market_information": "/recent_market_information.php",
}


# ------------------------------------------------------------------ collector
class Collector:
    def __init__(self, out: Path, min_gap: float = 0.4, timeout: float = 45.0):
        self.out = out
        self.raw = out / "raw"
        self.norm = out / "normalized"
        self.meta = out / "metadata"
        for p in (self.raw, self.norm, self.meta):
            p.mkdir(parents=True, exist_ok=True)
        self.client = PoliteClient(min_gap_s=min_gap, timeout_s=timeout)
        self.lb = lankabd.build_adapters(self.client)
        self.manifest: List[Dict[str, Any]] = []
        self.failures: List[Dict[str, Any]] = []
        self.stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"ok": 0, "failed": 0, "bytes": 0})
        self.sha_seen: Dict[str, List[str]] = defaultdict(list)
        self.t0 = utcnow()

    # ---------------------------------------------------------- raw store
    def save_raw(self, source: str, name: str, body: bytes, ext: str, receipt: str) -> Tuple[str, str]:
        ts = receipt.replace(":", "").replace("-", "").replace("+0000", "Z")[:17]
        rel = Path("raw") / source / f"{ts}_{slug(name)}.{ext}"
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256(body).hexdigest()
        if len(body) > GZIP_ABOVE:
            rel = rel.with_suffix(rel.suffix + ".gz")
            path = self.out / rel
            with gzip.open(path, "wb") as fh:
                fh.write(body)
            with gzip.open(path, "rb") as fh:                       # round-trip verified
                if hashlib.sha256(fh.read()).hexdigest() != sha:
                    self.failures.append({"source": source, "name": name, "error": "gzip round-trip mismatch"})
        else:
            path.write_bytes(body)
        self.sha_seen[sha].append(str(rel))
        return str(rel), sha

    def record(self, source: str, name: str, f: Fetched, ext: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        receipt = f.http.get("t_last_byte_utc") or f.http.get("t_send_utc") or utcnow()
        raw_path = sha = None
        if f.body:
            raw_path, sha = self.save_raw(source, name, f.body, ext, receipt)
        row = {"source": source, "name": name, "symbol": symbol, "method": f.http.get("method"),
               "url": f.http.get("url"), "params": f.http.get("params"), "form": f.http.get("form"),
               "status": f.status, "ok": bool(f.ok), "bytes": len(f.body or b""),
               "t_send_utc": f.http.get("t_send_utc"), "receipt_utc": receipt,
               "elapsed_s": f.http.get("elapsed_s"), "tls_verify": f.http.get("tls_verify"),
               "raw_path": raw_path, "raw_sha256": sha, "error": f.error}
        self.manifest.append(row)
        st = self.stats[source]
        st["ok" if f.ok else "failed"] += 1
        st["bytes"] += len(f.body or b"")
        if not f.ok:
            self.failures.append(row)
        return row

    def prov(self, row: Dict[str, Any], truth: str = "OBSERVED") -> Dict[str, Any]:
        return {"source": row["source"], "endpoint": row["url"], "exchange": "DSE",
                "receipt_utc": row["receipt_utc"], "http_status": row["status"],
                "raw_path": row["raw_path"], "raw_sha256": row["raw_sha256"], "truth": truth}

    # ---------------------------------------------------------- fetch helpers
    def dse_get(self, name: str, path: str, params: Optional[Dict[str, str]] = None,
                symbol: Optional[str] = None) -> Tuple[Fetched, Dict[str, Any]]:
        f = self.client.get(f"{DSE}{path}", params=params, allow_tls_fallback=True,
                            headers={"Accept": "text/html"})
        return f, self.record("dse" if not symbol else name.split("_")[0], name, f, "html", symbol)

    empty_month_stop: int = 12          # consecutive served-empty months that end the archive walk

    def archive_range(self, a: date, b: date, out: List[Dict[str, Any]], depth: int = 0) -> int:
        """One archive range for every instrument. Two different outcomes are told apart and
        never confused: a 200 that carries no data table (the archive does not serve that range)
        is accepted as an empty answer, while a transfer that dies mid-body is retried and then
        split in half down to a single day before it is given up on and recorded as a failure."""
        f = self.get_retry(f"{DSE}/day_end_archive.php",
                           {"startDate": a.isoformat(), "endDate": b.isoformat(),
                            "inst": "All Instrument", "archive": "data"},
                           headers={"Referer": f"{DSE}/data_archive.php"})
        row = self.record("dse_history", f"archive_{a}_{b}", f, "html")
        if not f.ok or not f.body:
            if a < b and depth < 5:                       # split: half a range at a time
                mid = a + (b - a) / 2
                n = self.archive_range(a, mid, out, depth + 1)
                return n + self.archive_range(mid + timedelta(days=1), b, out, depth + 1)
            self.failures.append({**row, "error": f"archive transfer failed for {a}..{b}: {f.error}"})
            return 0
        try:
            rows = dsebd.parse_archive(f.body.decode("utf-8", "replace"))
        except Exception as e:                            # noqa: BLE001
            self.failures.append({**row, "error": f"archive parse: {type(e).__name__}: {e}"})
            return 0
        p = self.prov(row)
        out += [{**r, "symbol": (r.get("symbol") or "").upper(), **p} for r in rows]
        print(f"[{utcnow()[11:19]}] archive {a}..{b}: {len(rows)} rows", file=sys.stderr, flush=True)
        return len(rows)

    def get_retry(self, url: str, params: Dict[str, str], attempts: int = 4,
                  headers: Optional[Dict[str, str]] = None) -> Fetched:
        """A multi-megabyte page (the all-instrument archive) can be cut mid-transfer; retry the
        whole request, never a partial body. Every attempt, including the failures, is recorded."""
        f = None
        for i in range(attempts):
            f = self.client.get(url, params=params, allow_tls_fallback=True,
                                headers={"Accept": "text/html", **(headers or {})})
            if f.ok and f.body:
                return f
            time.sleep(min(20.0, 3.0 * (i + 1)))
        return f                                                     # type: ignore[return-value]

    def adapter_pull(self, source: str, adapter: Any, key: Optional[str], ext: str) -> Tuple[Any, Dict[str, Any]]:
        f = adapter.fetch(key)
        row = self.record(source, f"{source}_{key or 'all'}", f, ext, symbol=key)
        if not f.ok:
            return None, row
        try:
            parsed = adapter.parse(f.body, key)
        except Exception as e:                                       # noqa: BLE001
            self.failures.append({**row, "error": f"parse: {type(e).__name__}: {e}"})
            return None, row
        if parsed.problems:
            self.failures.append({**row, "error": "parse problems: " + "; ".join(parsed.problems[:5])})
        return parsed, row

    # ---------------------------------------------------------- acquisition
    def extras(self) -> Dict[str, List[Dict[str, Any]]]:
        """Official DSE day-end pages, each stored raw and normalized with the page's own columns."""
        out: Dict[str, List[Dict[str, Any]]] = {}
        f = self.get_retry(f"{DSE}/market-statistics.php", {})
        row = self.record("dse_extras", "market_statistics", f, "html")
        if f.ok:
            ms = parse_market_statistics(f.body.decode("utf-8", "replace"))
            p = self.prov(row)
            if ms["breadth"]:
                out["market_statistics_breadth"] = [{**r, "report_date": ms["report_date"], **p}
                                                    for r in ms["breadth"]]
            if ms["block"]:
                out["market_statistics_block"] = [{**r, **p} for r in ms["block"]]
            totals = {k: v for k, v in ms.items() if k not in ("breadth", "block")}
            if any(v is not None for v in totals.values()):
                out["market_statistics_totals"] = [{**totals, **p}]
            print(f"[{utcnow()[11:19]}] extras market_statistics: {len(ms['breadth'])} breadth rows, "
                  f"{len(ms['block'])} block rows", file=sys.stderr, flush=True)
        for name, path in EXTRA_PAGES.items():
            f = self.get_retry(f"{DSE}{path}", {})
            row = self.record("dse_extras", name, f, "html")
            if not f.ok:
                continue
            head, data = parse_page_table(f.body.decode("utf-8", "replace"))
            if not head or not data:
                self.failures.append({**row, "error": f"{name}: no data table recognised"})
                continue
            p = self.prov(row)
            cols = [slug(h).lower() or f"col{i}" for i, h in enumerate(head)]
            rows = [{**{c: v for c, v in zip(cols, r)}, "page": name, "column_names": head, **p} for r in data]
            out[name] = rows
            print(f"[{utcnow()[11:19]}] extras {name}: {len(rows)} rows x {len(cols)} cols",
                  file=sys.stderr, flush=True)
        return out

    def run(self, all_depth: bool, all_tape: bool, company: bool,
            history_start: Optional[str], history_end: Optional[str],
            max_symbols: Optional[int], extras: bool = False) -> Dict[str, Any]:
        watch: List[Dict[str, Any]] = []
        grid: List[Dict[str, Any]] = []
        circuit: List[Dict[str, Any]] = []
        market: List[Dict[str, Any]] = []
        block: List[Dict[str, Any]] = []
        dse_latest: List[Dict[str, Any]] = []
        depth: List[Dict[str, Any]] = []
        depth_sum: List[Dict[str, Any]] = []
        tape: List[Dict[str, Any]] = []
        comp_info: List[Dict[str, Any]] = []
        comp_fin: List[Dict[str, Any]] = []
        comp_pe: List[Dict[str, Any]] = []
        comp_hold: List[Dict[str, Any]] = []
        history: List[Dict[str, Any]] = []

        # ---- market-wide surfaces
        parsed, row = self.adapter_pull("lankabd_watch", self.lb["watch"], None, "json")
        if parsed:
            for fr in parsed.frames:
                watch.append({**fr, "symbol": (fr.get("symbol") or "").upper(), **self.prov(row)})
        parsed, row = self.adapter_pull("lankabd_grid", self.lb["grid"], None, "json")
        if parsed:
            for fr in parsed.frames:
                grid.append({**fr, "symbol": (fr.get("symbol") or "").upper(), **self.prov(row)})
        parsed, row = self.adapter_pull("lankabd_circuit", self.lb["circuit"], None, "html")
        if parsed:
            for fr in parsed.frames:
                circuit.append({**fr, **self.prov(row)})
        parsed, row = self.adapter_pull("lankabd_market", self.lb["market"], None, "json")
        if parsed:
            for fr in parsed.frames:
                market.append({**fr, **self.prov(row)})
        parsed, row = self.adapter_pull("lankabd_block", self.lb["block"], None, "json")
        if parsed:
            for fr in parsed.frames:
                block.append({**fr, **self.prov(row)})

        cid_map, f_cid = lankabd.fetch_cid_map(self.lb["session"])
        self.record("lankabd_cidmap", "minute_chart_matrix", f_cid, "html")
        self.lb["tape"].cid_map = cid_map

        f, row = self.dse_get("latest_share_price", "/latest_share_price_scroll_l.php")
        if f.ok:
            for fr in dsebd.parse_latest_share_price(f.body.decode("utf-8", "replace")):
                dse_latest.append({**fr, "symbol": (fr.get("symbol") or "").upper(), **self.prov(row)})
        f, row = self.dse_get("hts_sessions", "/hts.php")
        sessions = dsebd.parse_hts(f.body.decode("utf-8", "replace")) if f.ok else {}
        if sessions:
            (self.meta / "sessions.json").write_text(json.dumps({**sessions, **self.prov(row)},
                                                                indent=2, default=str), encoding="utf-8")

        # ---- the universe: union of every independent public surface
        universe: Dict[str, Dict[str, Any]] = {}
        for name, rows in (("watch", watch), ("grid", grid), ("circuit", circuit), ("dse_latest", dse_latest)):
            for r in rows:
                s = (r.get("symbol") or "").upper()
                if not s:
                    continue
                u = universe.setdefault(s, {"symbol": s})
                u[f"in_{name}"] = True
        for s in cid_map:
            universe.setdefault(s.upper(), {"symbol": s.upper()})["has_company_id"] = True
        for s, u in universe.items():
            for k in ("in_watch", "in_grid", "in_circuit", "in_dse_latest", "has_company_id"):
                u.setdefault(k, False)
            u["company_id"] = cid_map.get(s)
        for r in circuit:                                   # sector/tick/limits onto the universe row
            u = universe.get((r.get("symbol") or "").upper())
            if u:
                u.update({"sector_circuit_table": r.get("sector"), "tick_size": r.get("tick_size"),
                          "breaker_pct": r.get("breaker_pct"), "upper_limit": r.get("upper_limit"),
                          "lower_limit": r.get("lower_limit")})
        symbols = sorted(universe)
        if max_symbols:
            symbols = symbols[:max_symbols]

        # ---- per-symbol pulls
        for n, s in enumerate(symbols, 1):
            if all_depth:
                for src, adapter in (("lankabd_depth", self.lb["depth"]),
                                     ("dsebd_depth", dsebd.DSEBDDepthAdapter(self.client))):
                    parsed, row = self.adapter_pull(src, adapter, s, "json" if "lanka" in src else "html")
                    if not parsed or not parsed.frames:
                        continue
                    fr = parsed.frames[0]
                    p = self.prov(row)
                    bids = fr.get("bid_levels") or []
                    asks = fr.get("ask_levels") or []
                    depth_sum.append({
                        "symbol": s, "ltp": fr.get("ltp"), "open": fr.get("open"), "high": fr.get("high"),
                        "low": fr.get("low"), "close_published": fr.get("close_published"),
                        "yclose": fr.get("yclose"), "day_trades": fr.get("day_trades"),
                        "day_volume": fr.get("day_volume"), "day_value_mn": fr.get("day_value_mn"),
                        "best_bid": bids[0][0] if bids else None, "best_ask": asks[0][0] if asks else None,
                        "bid_qty1": bids[0][1] if bids else None, "ask_qty1": asks[0][1] if asks else None,
                        "n_bid_levels": len(bids), "n_ask_levels": len(asks),
                        "total_buy_volume": fr.get("total_buy_volume"),
                        "total_sell_volume": fr.get("total_sell_volume"), **p})
                    for side, arr in (("bid", bids), ("ask", asks)):
                        for lvl, (px, qty) in enumerate(arr, 1):
                            depth.append({"symbol": s, "side": side, "level": lvl, "price": px,
                                          "volume": qty, "t_exchange": None, **p})
            if all_tape:
                parsed, row = self.adapter_pull("lankabd_tape", self.lb["tape"], s, "json")
                if parsed:
                    p = self.prov(row, "OBSERVED_CUMULATIVE")
                    for fr in parsed.frames:
                        tape.append({"symbol": s, "row_index": fr.get("row_index"),
                                     "t_exchange": fr.get("t_source_utc"), "t_source_ms": fr.get("t_source_ms"),
                                     "price": fr.get("price"), "cum_trades": fr.get("cum_trades"),
                                     "cum_volume": fr.get("cum_volume"), "cum_value_mn": fr.get("cum_value_mn"),
                                     **p})
            if company:
                f = self.client.get(f"{DSE}/displayCompany.php", params={"name": s},
                                    allow_tls_fallback=True, headers={"Accept": "text/html"})
                row = self.record("dse_company", f"company_{s}", f, "html", symbol=s)
                if f.ok:
                    try:
                        c = parse_company_page(f.body.decode("utf-8", "replace"), s)
                        p = self.prov(row)
                        if c["problems"]:
                            self.failures.append({**row, "error": "; ".join(c["problems"])})
                        if len(c["info"]) > 1:
                            comp_info.append({**c["info"], **p})
                        comp_fin += [{**r, **p} for r in c["financials"]]
                        comp_pe += [{**r, **p} for r in c["pe"]]
                        comp_hold += [{**r, **p} for r in c["holdings"]]
                    except Exception as e:                            # noqa: BLE001
                        self.failures.append({**row, "error": f"company parse: {type(e).__name__}: {e}"})
            if n % 25 == 0:
                print(f"[{utcnow()[11:19]}] symbols {n}/{len(symbols)} "
                      f"depth={len(depth)} tape={len(tape)} company={len(comp_info)}",
                      file=sys.stderr, flush=True)

        # ---- interval flow inferred from consecutive cumulative rows (never called prints)
        inferred: List[Dict[str, Any]] = []
        by_sym: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in tape:
            by_sym[r["symbol"]].append(r)
        for s, rows in by_sym.items():
            rows.sort(key=lambda r: (r.get("t_source_ms") is None, r.get("t_source_ms") or 0,
                                     r.get("row_index") or 0))
            prev = None
            for r in rows:
                if prev is not None:
                    d_tr, d_vol, d_val = (num(r.get("cum_trades")), num(r.get("cum_volume")),
                                          num(r.get("cum_value_mn")))
                    p_tr, p_vol, p_val = (num(prev.get("cum_trades")), num(prev.get("cum_volume")),
                                          num(prev.get("cum_value_mn")))
                    it = None if (d_tr is None or p_tr is None) else d_tr - p_tr
                    iv = None if (d_vol is None or p_vol is None) else d_vol - p_vol
                    ivl = None if (d_val is None or p_val is None) else d_val - p_val
                    inferred.append({
                        "symbol": s, "t_exchange": r.get("t_exchange"), "price": r.get("price"),
                        "interval_trades": it, "interval_volume": iv, "interval_value_mn": ivl,
                        "interval_vwap": (ivl * 1e6 / iv) if (iv and ivl is not None and iv > 0) else None,
                        "monotone_break": bool((it is not None and it < 0) or (iv is not None and iv < 0)),
                        "source": "lankabd_tape_diff", "endpoint": r.get("endpoint"), "exchange": "DSE",
                        "receipt_utc": r.get("receipt_utc"), "raw_path": r.get("raw_path"),
                        "raw_sha256": r.get("raw_sha256"), "http_status": r.get("http_status"),
                        "truth": "INFERRED_FROM_CUMULATIVE"})
                prev = r

        # ---- official historical archive, all instruments, newest month first
        if history_start:
            start = date.fromisoformat(history_start)
            end = date.fromisoformat(history_end) if history_end else date.today()
            months: List[Tuple[date, date]] = []
            cur = start.replace(day=1)
            while cur <= end:
                nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
                months.append((cur, min(end, nxt - timedelta(days=1))))
                cur = nxt
            empty_streak = 0
            for a, b in reversed(months):                 # newest first: the served window is recent
                rows = self.archive_range(a, b, history)
                empty_streak = 0 if rows else empty_streak + 1
                if empty_streak >= self.empty_month_stop:
                    print(f"[{utcnow()[11:19]}] archive: {empty_streak} consecutive months served no data "
                          f"(back to {a}); the public archive window ends here", file=sys.stderr, flush=True)
                    break

        extra_tables: Dict[str, List[Dict[str, Any]]] = self.extras() if extras else {}
        tables = {
            "symbols.csv": list(universe.values()),
            "market_watch.parquet": watch,
            "market_grid.parquet": grid,
            "market_depth.parquet": depth,
            "market_depth_summary.parquet": depth_sum,
            "trades_or_tape.parquet": tape,
            "tape_interval_flow.parquet": inferred,
            "market_stats.parquet": market,
            "circuit_limits.parquet": circuit,
            "block_market.parquet": block,
            "dse_latest.parquet": dse_latest,
            "company_fundamentals.parquet": comp_info,
            "company_financials.parquet": comp_fin,
            "company_pe_history.parquet": comp_pe,
            "company_shareholding.parquet": comp_hold,
            "historical_prices.parquet": history,
        }
        tables.update({f"dse_{k}.parquet": v for k, v in extra_tables.items()})
        for name, rows in tables.items():
            write_table(self.norm / name, rows)
        return self.finish(tables, symbols, universe, depth, depth_sum)

    # ---------------------------------------------------------- metadata / validation
    def finish(self, tables: Dict[str, List[Dict[str, Any]]], symbols: List[str],
               universe: Dict[str, Dict[str, Any]], depth: List[Dict[str, Any]],
               depth_sum: List[Dict[str, Any]]) -> Dict[str, Any]:
        # depth levels per symbol per sensor, and a cross-source best-quote check
        lvl = defaultdict(lambda: {"lankabd_depth": 0, "dsebd_depth": 0})
        for r in depth:
            lvl[r["symbol"]][r["source"]] = max(lvl[r["symbol"]][r["source"]], r["level"])
        best: Dict[Tuple[str, str, str], Any] = {}
        for r in depth:
            if r["level"] == 1:
                best[(r["source"], r["symbol"], r["side"])] = r["price"]
        cross = []
        for s in symbols:
            lb_b, ds_b = best.get(("lankabd_depth", s, "bid")), best.get(("dsebd_depth", s, "bid"))
            lb_a, ds_a = best.get(("lankabd_depth", s, "ask")), best.get(("dsebd_depth", s, "ask"))
            if any(v is not None for v in (lb_b, ds_b, lb_a, ds_a)):
                cross.append({"symbol": s, "lankabd_best_bid": lb_b, "dsebd_best_bid": ds_b,
                              "lankabd_best_ask": lb_a, "dsebd_best_ask": ds_a,
                              "bid_agree": (lb_b == ds_b) if (lb_b is not None and ds_b is not None) else None,
                              "ask_agree": (lb_a == ds_a) if (lb_a is not None and ds_a is not None) else None,
                              "levels_lankabd": lvl[s]["lankabd_depth"], "levels_dsebd": lvl[s]["dsebd_depth"]})
        write_table(self.meta / "depth_crosscheck.csv", cross)

        coverage = {}
        for name, rows in tables.items():
            df = pd.DataFrame(rows)
            coverage[name] = ({c: round(float(df[c].notna().mean()), 4) for c in df.columns} if len(df) else {})
        dup = {k: v for k, v in self.sha_seen.items() if len(v) > 1}
        agree = [c for c in cross if c["bid_agree"] is not None]
        validation = {
            "started_utc": self.t0, "completed_utc": utcnow(),
            "symbols_discovered": len(universe), "symbols_pulled": len(symbols),
            "rows": {k: len(v) for k, v in tables.items()},
            "requests_by_source": {k: dict(v) for k, v in self.stats.items()},
            "client_stats": dict(self.client.stats),
            "failures": len(self.failures),
            "duplicate_raw_payload_groups": len(dup),
            "depth_symbols_with_levels": sum(1 for s in lvl if max(lvl[s].values()) > 0),
            "depth_max_levels_seen": max([max(v.values()) for v in lvl.values()], default=0),
            "cross_source_best_bid_checked": len(agree),
            "cross_source_best_bid_agree": sum(1 for c in agree if c["bid_agree"]),
            "depth_summary_rows": len(depth_sum),
        }
        json_dump(self.meta / "validation.json", validation)
        json_dump(self.meta / "field_coverage.json", coverage)
        json_dump(self.meta / "failures.json", self.failures)
        json_dump(self.meta / "manifest.json", self.manifest)
        json_dump(self.meta / "duplicate_raw_payloads.json", dup)
        json_dump(self.meta / "sources.json", SOURCES)
        json_dump(self.meta / "observability.json", OBSERVABILITY)
        json_dump(self.meta / "schema.json", {k: sorted(pd.DataFrame(v).columns) if v else []
                                              for k, v in tables.items()})
        samples = {k: v[:2] for k, v in tables.items() if v}
        json_dump(self.meta / "sample_records.json", samples)
        print(json.dumps(validation, indent=2, default=str))
        return validation


SOURCES = {
    "dsebd.org": {
        "latest_share_price_scroll_l.php": {"method": "GET", "gives": "all-symbol L1 + day totals",
                                            "truth": "OBSERVED"},
        "ajax/load-instrument.php": {"method": "POST {inst}", "gives": "displayed depth (price, volume per level)",
                                     "truth": "OBSERVED", "note": "snapshot L2; no order counts, no order ids"},
        "displayCompany.php": {"method": "GET {name}", "truth": "OBSERVED",
                               "gives": "sector, market category, market cap, free-float market cap, outstanding "
                                        "securities, capital, face value, market lot, P/E, EPS and dividend history, "
                                        "shareholding %, 52-week range"},
        "day_end_archive.php": {"method": "GET {startDate,endDate,inst=All Instrument,archive=data}",
                                "gives": "historical OHLCV, trades, value", "truth": "OBSERVED"},
        "hts.php": {"method": "GET", "gives": "trading sessions and holidays", "truth": "OBSERVED"},
    },
    "lankabd.com": {
        "Home/MarketDepthData": {"method": "POST {Symbol,Exchange} + anti-forgery token",
                                 "gives": "displayed depth", "truth": "OBSERVED"},
        "api/datafeed/IndexLiveData/LiveStockWatchData": {"method": "GET",
                                                          "gives": "all-symbol L1 with per-instrument exchange stamp",
                                                          "truth": "OBSERVED"},
        "api/Company/MkSecondDataSymbol": {"method": "GET {cid,tradeCounts}",
                                           "gives": "exchange-stamped CUMULATIVE intraday rows (~1/min)",
                                           "truth": "OBSERVED_CUMULATIVE",
                                           "note": "cumulative totals, NOT individual prints"},
        "api/datafeed/IndexLiveData/LiveDSETradeStatistics": {"method": "GET", "gives": "market totals and breadth",
                                                              "truth": "OBSERVED"},
        "api/APIMarket/GetLatestBlockMarket": {"method": "GET", "gives": "block-board prints", "truth": "OBSERVED"},
        "Home/CircuitBreaker": {"method": "GET", "gives": "per-symbol circuit limits, tick size, breaker %",
                                "truth": "OBSERVED"},
        "api/APIMarket/GetDataGrid": {"method": "GET", "gives": "all-symbol grid", "truth": "OBSERVED"},
        "Home/MinuteChartMatrix": {"method": "GET", "gives": "symbol → company-id map", "truth": "OBSERVED"},
    },
    "excluded_on_policy": {"amarstock.com": "robots.txt disallows this agent"},
}

OBSERVABILITY = {
    "displayed_depth_price_volume_per_level": "OBSERVED (snapshot, both sensors)",
    "all_symbol_l1_and_day_totals": "OBSERVED",
    "per_instrument_exchange_timestamp": "OBSERVED (LankaBD watch, tape)",
    "book_exchange_timestamp": "NOT_AVAILABLE (depth pages carry no exchange stamp; receipt time is recorded)",
    "cumulative_intraday_trades_volume_value": "OBSERVED (~1 row/min, exchange-stamped)",
    "interval_trades_volume_value_vwap": "INFERRED (difference of consecutive cumulative rows)",
    "individual_trade_prints": "NOT_AVAILABLE",
    "trade_side_aggressor": "NOT_AVAILABLE (inferable only by a quote rule on interval VWAP)",
    "number_of_orders_per_level": "NOT_AVAILABLE",
    "order_ids": "NOT_AVAILABLE",
    "queue_position": "NOT_AVAILABLE",
    "intra_interval_add_cancel_netting": "NOT_AVAILABLE",
    "circuit_limits_tick_size_breaker_pct": "OBSERVED",
    "block_board_prints": "OBSERVED (daily board)",
    "historical_ohlcv_trades_value": "OBSERVED (official day-end archive)",
    "sector_category_market_cap_free_float_outstanding": "OBSERVED (company page)",
    "pe_eps_dividend_shareholding": "OBSERVED (company page)",
    "nav": "NOT_AVAILABLE on the DSE company page for equities (mutual funds publish NAV separately)",
}


def json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_table(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """Write a normalized table. An empty result never overwrites an existing file: a run that
    collects only part of the surface (``--extras``, ``--history-start`` alone) leaves the tables
    it did not touch as they were, so the output directory can be filled incrementally."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and (path.exists() or path.with_suffix(".csv").exists()):
        return
    df = pd.DataFrame(list(rows))
    for c in df.columns:
        if df[c].map(lambda x: isinstance(x, (dict, list, tuple))).any():
            df[c] = df[c].map(lambda x: json.dumps(x, ensure_ascii=False, default=str)
                              if isinstance(x, (dict, list, tuple)) else x)
    if path.suffix == ".csv":
        df.to_csv(path, index=False)
        return
    try:
        df.to_parquet(path, index=False)
    except Exception:                                                # noqa: BLE001
        df.to_csv(path.with_suffix(".csv"), index=False)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data")
    ap.add_argument("--all-depth", action="store_true", help="displayed depth for every symbol, both sensors")
    ap.add_argument("--all-tape", action="store_true", help="cumulative intraday tape for every symbol with a cid")
    ap.add_argument("--company", action="store_true", help="dsebd company page fundamentals for every symbol")
    ap.add_argument("--extras", action="store_true",
                    help="official DSE day-end pages (circuit breaker, P/E at a glance, company and sector "
                         "listings, market statistics, top gainers/losers, marginable securities, close price)")
    ap.add_argument("--history-start", default=None, help="YYYY-MM-DD, official archive (all instruments)")
    ap.add_argument("--history-end", default=None)
    ap.add_argument("--min-gap", type=float, default=0.4)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--max-symbols", type=int, default=None, help="debug only; omit for every symbol")
    a = ap.parse_args(argv)
    c = Collector(Path(a.out), min_gap=a.min_gap, timeout=a.timeout)
    c.run(a.all_depth, a.all_tape, a.company, a.history_start, a.history_end, a.max_symbols, a.extras)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
