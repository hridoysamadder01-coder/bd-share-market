"""DSE website (dsebd.org) adapters — parsers reused from DSE-AI-TRADER.

The table parsers below are vendored from hridoysamadder01-coder/DSE-AI-TRADER
(``app/collectors/dse.py``, ``dse_history.py``, ``index_history.py``), stripped
of their ORM/DB layer so they can run on raw bytes at replay time. Their column
mapping (incl. the YCP-vs-CLOSEP correction) is preserved.

Reachability: dsebd.org resets TLS connections from this container (recorded
as GAP records when the runner tries). The adapters are complete and tested on
fixture HTML; they run unchanged from a host that can reach the exchange site.

``mkt_depth_3.php`` ("Market Price" depth page) loads its content with
``POST ajax/load-instrument.php {inst}`` (verified 2026-09-06: the response is
the Buy / Sell two-column tables plus a Price Statistics table, the same table
shape LankaBD mirrors). ``DSEBDDepthAdapter`` uses that endpoint. The generic
``parse_depth_page`` reader is kept for the static page and for any other
buy/sell table markup.

``hts.php`` (Holidays and Trading Sessions) is the exchange's own calendar and
session table — the reference source for the session windows in ``seeing.clock``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from .base import Parsed, capability_map
from ..http_client import Fetched, PoliteClient

BASE = "https://www.dsebd.org"
_NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
_ROW_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*,\s*(-?\d+(?:\.\d+)?)")


def _to_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    text = text.strip().replace(",", "")
    if not text or text in {"-", "--", "N/A"}:
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _to_int(text: Optional[str]) -> Optional[int]:
    f = _to_float(text)
    return int(f) if f is not None else None


# ---------------------------------------------------------------- latest share price (vendored)
def parse_latest_share_price(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="table") or soup.find("table")
    if table is None:
        return []
    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    col_idx = {h: i for i, h in enumerate(headers)}

    def col(name: str) -> Optional[int]:
        for key in (name, name.upper(), name.lower()):
            if key in col_idx:
                return col_idx[key]
        for h, i in col_idx.items():
            if name in h:
                return i
        return None

    idx_symbol = col("trading code") or col("symbol") or 1
    idx_ltp, idx_high, idx_low = col("ltp"), col("high"), col("low")
    idx_ycp = col("ycp") or col("close")   # YCP, never CLOSEP (0 intraday)
    idx_trades, idx_volume, idx_value = col("trade"), col("volume"), col("value")
    out = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4 or idx_symbol >= len(tds):
            continue
        symbol = tds[idx_symbol].get_text(strip=True)
        if not symbol or symbol.lower() in {"trading code", "symbol"}:
            continue

        def cell(i: Optional[int]) -> Optional[str]:
            return tds[i].get_text(strip=True) if (i is not None and i < len(tds)) else None

        value_mn = _to_float(cell(idx_value))
        out.append({
            "symbol": symbol.upper(), "ltp": _to_float(cell(idx_ltp)), "high": _to_float(cell(idx_high)),
            "low": _to_float(cell(idx_low)), "yclose": _to_float(cell(idx_ycp)),
            "day_trades": _to_int(cell(idx_trades)), "day_volume": _to_int(cell(idx_volume)),
            "day_value_mn": value_mn,
        })
    return out


# ---------------------------------------------------------------- day-end archive (vendored)
def parse_archive(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    target = None
    for t in soup.find_all("table"):
        headers = " ".join(th.get_text(strip=True).lower() for th in t.find_all("th"))
        if "date" in headers and "closep" in headers:
            target = t
            break
    if target is None:
        return []
    headers = [th.get_text(strip=True).lower() for th in target.find_all("th")]
    idx = {h: i for i, h in enumerate(headers)}

    def col(*names) -> Optional[int]:
        for n in names:
            if n in idx:
                return idx[n]
            for h, i in idx.items():
                if n in h:
                    return i
        return None

    i_date, i_ltp, i_high, i_low = col("date"), col("ltp"), col("high"), col("low")
    i_open, i_close, i_ycp = col("openp", "open"), col("closep", "close"), col("ycp")
    i_trade, i_value, i_volume = col("trade"), col("value (mn)", "value"), col("volume")
    out = []
    for tr in target.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 6 or i_date is None:
            continue
        try:
            d = datetime.strptime(tds[i_date].get_text(strip=True), "%Y-%m-%d").date()
        except ValueError:
            continue

        def cell(i: Optional[int]) -> Optional[str]:
            return tds[i].get_text(strip=True) if (i is not None and i < len(tds)) else None

        out.append({"trade_date": d.isoformat(), "ltp": _to_float(cell(i_ltp)), "high": _to_float(cell(i_high)),
                    "low": _to_float(cell(i_low)), "open": _to_float(cell(i_open)), "close": _to_float(cell(i_close)),
                    "yclose": _to_float(cell(i_ycp)), "day_trades": _to_int(cell(i_trade)),
                    "day_value_mn": _to_float(cell(i_value)), "day_volume": _to_int(cell(i_volume))})
    return out


def parse_index_series(html: str) -> List[Tuple[str, float]]:
    by_date: Dict[str, float] = {}
    for m in _ROW_RE.finditer(html):
        try:
            v = float(m.group(2))
        except ValueError:
            continue
        if v > 0:
            by_date[m.group(1)] = v
    return sorted(by_date.items())


# ---------------------------------------------------------------- depth page (UNVERIFIED markup)
def parse_depth_page(html: str) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[str]]:
    """Generic reader: any table whose header mentions buy/bid → bids, sell/ask/offer → asks;
    rows with exactly two numeric cells are (price, qty). UNVERIFIED against the live page."""
    soup = BeautifulSoup(html, "lxml")
    bids: List[Tuple[float, float]] = []
    asks: List[Tuple[float, float]] = []
    problems: List[str] = []
    found = False
    for t in soup.find_all("table"):
        head = " ".join(x.get_text(" ", strip=True).lower() for x in t.find_all(["th", "caption"]))
        head = head or " ".join(td.get_text(" ", strip=True).lower() for td in t.find_all("td")[:4])
        side = "bid" if ("buy" in head or "bid" in head) else ("ask" if ("sell" in head or "ask" in head or "offer" in head) else None)
        if side is None:
            continue
        found = True
        for tr in t.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            nums = [_to_float(c) for c in cells]
            if len(cells) >= 2 and all(n is not None for n in nums[:2]) and all(_NUM_RE.fullmatch(c.replace(",", "")) for c in cells[:2]):
                (bids if side == "bid" else asks).append((nums[0], nums[1]))
    if not found:
        problems.append("no buy/sell table found (markup unverified)")
    return sorted(bids, key=lambda x: -x[0]), sorted(asks, key=lambda x: x[0]), problems


@dataclass
class DSEBDLatestAdapter:
    client: PoliteClient
    name: str = "dsebd_latest"
    kind: str = "l1"
    observes = ("ltp", "high", "low", "yclose", "day_trades", "day_volume", "day_value", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(f"{BASE}/latest_share_price_scroll_l.php", allow_tls_fallback=True,
                               headers={"Accept": "text/html"})

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        out.frames = parse_latest_share_price(body.decode("utf-8", "replace"))
        if not out.frames:
            out.problems.append("no rows")
        return out


_CELL_RE = re.compile(r"<td[^>]*>\s*<div[^>]*>\s*([^<]*?)\s*</div>\s*</td>", re.I | re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_STAT_RE = re.compile(r"<td[^>]*>\s*([^<:]+?)\s*:?\s*(?:<div[^>]*>\s*)?</td>\s*<td[^>]*>\s*:?\s*([-\d.,]+)", re.I | re.S)


def _levels_from(html: str) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for row in _TR_RE.findall(html):
        cells = [c.strip() for c in _CELL_RE.findall(row)]
        if len(cells) == 2 and all(_NUM_RE.fullmatch(c.replace(",", "")) for c in cells):
            out.append((float(cells[0].replace(",", "")), float(cells[1].replace(",", ""))))
    return out


def parse_load_instrument(html: str) -> Dict[str, Any]:
    """POST ajax/load-instrument.php response → bids, asks, price statistics.

    The Buy table starts at the first '>Buy<' heading and ends at its </table>;
    the Sell table likewise. Level rows are two centred numeric cells."""
    res: Dict[str, Any] = {"bid_levels": [], "ask_levels": [], "problems": []}
    m_sym = re.search(r"Instrument\s*:\s*<strong>.*?>([^<]+)</a>", html, re.S)
    res["symbol"] = m_sym.group(1).strip().upper() if m_sym else None
    for side, label in (("bid_levels", ">Buy<"), ("ask_levels", ">Sell<")):
        i = html.find(label)
        if i < 0:
            res["problems"].append(f"no {label} table")
            continue
        j = html.find("</table>", i)
        levels = _levels_from(html[i:j])
        res[side] = sorted(levels, key=lambda x: (-x[0] if side == "bid_levels" else x[0]))
        res[side + "_src_order_preserved"] = levels == res[side]
    stats: Dict[str, Optional[float]] = {}
    k = html.find("Price Statistics")
    if k >= 0:
        j = html.rfind("<table", 0, k)
        block = html[j: html.find("</table>", k) + 8]
        soup = BeautifulSoup(block, "lxml")
        for tr in soup.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            label: Optional[str] = None
            for c in cells:
                c2 = c.strip()
                if not c2:
                    continue
                num = c2.lstrip(":").strip()
                if label is not None and _NUM_RE.fullmatch(num.replace(",", "")):
                    stats[label] = float(num.replace(",", ""))
                    label = None
                elif not _NUM_RE.fullmatch(num.replace(",", "")):
                    label = c2.rstrip(":").strip().lower()
    res["ltp"] = stats.get("last trade price")
    res["open"] = stats.get("open price")
    res["high"] = stats.get("day's high")
    res["low"] = stats.get("day's low")
    res["yclose"] = stats.get("yesterday close price")
    res["close_published"] = stats.get("close price")
    res["day_trades"] = stats.get("no. of trade")
    res["day_volume"] = stats.get("total volume")
    res["day_value_mn"] = stats.get("total value (mn)")
    res["stats_raw"] = stats
    return res


@dataclass
class DSEBDDepthAdapter:
    client: PoliteClient
    name: str = "dsebd_depth"
    kind: str = "book"
    observes = ("bid_levels", "ask_levels", "best_bid", "best_ask", "book_depth_count", "ltp", "open",
                "high", "low", "close_published", "yclose", "day_trades", "day_volume", "day_value", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.post(f"{BASE}/ajax/load-instrument.php", data={"inst": key or ""},
                               allow_tls_fallback=True,
                               headers={"Accept": "text/html, */*; q=0.01", "X-Requested-With": "XMLHttpRequest",
                                        "Referer": f"{BASE}/mkt_depth_3.php"})

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        r = parse_load_instrument(body.decode("utf-8", "replace"))
        out.problems += r.pop("problems")
        r["symbol"] = r.get("symbol") or (key or "").upper()
        r["n_bid_levels"] = len(r["bid_levels"])
        r["n_ask_levels"] = len(r["ask_levels"])
        if r["symbol"] and (key or "").upper() and r["symbol"] != (key or "").upper():
            out.problems.append(f"symbol mismatch: asked {key}, page says {r['symbol']}")
        out.frames.append(r)
        return out


# ---------------------------------------------------------------- holidays & sessions (hts.php)
def parse_hts(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    holidays: List[Dict[str, str]] = []
    sessions: List[Dict[str, str]] = []
    for t in soup.find_all("table"):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")]
        rows = [r for r in rows if r]
        if rows and rows[0][:2] == ["Name of Holidays", "Date"]:
            for r in rows[1:]:
                if len(r) >= 4:
                    holidays.append({"name": r[0], "date": r[1], "days": r[2], "n_days": r[3]})
        if rows and rows[0] and rows[0][0] == "Market" and len(rows) > 1:
            hdr = rows[0]
            for r in rows[1:]:
                if len(r) >= 2:
                    sessions.append(dict(zip(hdr, r)))
    return {"holidays": holidays, "sessions": sessions}


@dataclass
class DSEBDSessionsAdapter:
    client: PoliteClient
    name: str = "dsebd_hts"
    kind: str = "reference"
    observes = ("t_recv",)

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(f"{BASE}/hts.php", allow_tls_fallback=True, headers={"Accept": "text/html"})

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        r = parse_hts(body.decode("utf-8", "replace"))
        if not r["holidays"]:
            out.problems.append("no holiday table")
        out.frames.append(r)
        return out
