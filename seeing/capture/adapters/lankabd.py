"""LankaBD (LankaBangla Securities public portal) adapters.

Verified endpoints (2026-09-06, from this container; see evidence/SOURCE_ACCESS_LEDGER.md):

* POST /Home/MarketDepthData  {Symbol, Exchange}  + header RequestVerificationToken
      → JSON: buyPriceTable / sellPriceTable (HTML tables of price, volume), open,
        LTP, yclose, published close, day high/low, noOfTrade, totalVolume,
        totalValueMN, buy/sell percentage, totalBuy/SellVolume.           (BOOK)
* GET  /api/datafeed/IndexLiveData/LiveStockWatchData
      → JSON {timestamp, items[638]}: per instrument the exchange's own
        last-modification time to the second, open/LTP/high/low/close/yclose,
        total trades/volume/value, public vs spot split, market category.  (WATCH)
* GET  /api/Company/MkSecondDataSymbol?cid=<companyID>&tradeCounts=<n>
      → JSON {length, data[[epoch_ms, price, cum_trades, cum_volume,
        cum_value_mn, price]]}: exchange-stamped cumulative totals, one row per
        change (~1/min observed).                                         (TAPE)
* GET  /api/datafeed/IndexLiveData/LiveDSETradeStatistics → market totals   (MARKET)
* GET  /api/APIMarket/GetLatestBlockMarket → block-board prints per symbol  (BLOCK)
* GET  /Home/CircuitBreaker → HTML table: breaker %, tick, lower/upper limit (REFERENCE)
* GET  /api/APIMarket/GetDataGrid → JSON all-symbol grid (L1 + fundamentals)  (L1)

All JSON endpoints require the anti-forgery token + cookie obtained from one GET
of /Home/MarketDepth. The portal's own UI polls the live endpoints every 15 s;
this adapter never polls faster than the portal itself does.

What LankaBD does NOT provide (NOT_OBSERVABLE here): number of orders per
level, individual trade prints, trade side, order-by-order events, queue
position. Those stay NOT_OBSERVABLE until a richer source is attached.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ...clock import epoch_ms_to_utc, parse_source_local
from .base import Parsed, capability_map
from ..http_client import Fetched, PoliteClient

BASE = "https://www.lankabd.com"
TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
CELL_RE = re.compile(r"<td[^>]*>\s*<div[^>]*>\s*([^<]*?)\s*</div>\s*</td>", re.I | re.S)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")


def _num(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = NUM_RE.search(str(s).replace(",", ""))
    return float(m.group(0)) if m else None


@dataclass
class LankaBDSession:
    """Anti-forgery token + cookies. Refreshed on demand and every ``ttl_s``."""

    client: PoliteClient
    token: Optional[str] = None
    obtained_mono: float = 0.0
    ttl_s: float = 1500.0
    refreshes: int = 0
    last_fetch: Optional[Fetched] = None

    def ensure(self, force: bool = False) -> Optional[str]:
        if not force and self.token and (time.monotonic() - self.obtained_mono) < self.ttl_s:
            return self.token
        f = self.client.get(f"{BASE}/Home/MarketDepth",
                            headers={"Accept": "text/html,application/xhtml+xml"})
        self.last_fetch = f
        self.refreshes += 1
        if f.ok:
            m = TOKEN_RE.search(f.body.decode("utf-8", "replace"))
            if m:
                self.token = m.group(1)
                self.obtained_mono = time.monotonic()
                return self.token
        return None

    def headers(self, json_accept: bool = True) -> Dict[str, str]:
        h = {"RequestVerificationToken": self.token or "",
             "Referer": f"{BASE}/Home/MarketDepth",
             "X-Requested-With": "XMLHttpRequest"}
        h["Accept"] = "application/json, text/javascript, */*; q=0.01" if json_accept else "text/html"
        return h


def _tokened_get(sess: LankaBDSession, url: str, params: Optional[Dict[str, str]] = None) -> Fetched:
    sess.ensure()
    f = sess.client.get(url, headers=sess.headers(), params=params)
    if f.status in (400, 401, 403, 405):        # token / cookie went stale → refresh once
        sess.ensure(force=True)
        f = sess.client.get(url, headers=sess.headers(), params=params)
    return f


# ---------------------------------------------------------------------- BOOK
def parse_depth_table(html: str) -> Tuple[List[Tuple[float, float]], List[str]]:
    """The buy/sell HTML table → [(price, qty), ...] in source order.

    Rows are two centred numeric cells (price, volume). A row with a different
    number of numeric cells is NOT silently dropped: it is reported as a problem
    (so a layout change during a live session shows up on replay, not as an
    empty book) and, when it has ≥ 2 numeric cells, its first two are still
    read as (price, qty)."""
    levels: List[Tuple[float, float]] = []
    problems: List[str] = []
    for row in ROW_RE.findall(html or ""):
        cells = [c.strip() for c in CELL_RE.findall(row)]
        if not cells:
            continue
        nums = [c for c in cells if NUM_RE.fullmatch(c.replace(",", ""))]
        if len(nums) != len(cells):
            continue                      # header rows ("Buy Price", "Buy Volume")
        if len(nums) != 2:
            problems.append(f"level row with {len(nums)} numeric cells (layout change?): {cells}")
            if len(nums) < 2:
                continue
        p, q = _num(nums[0]), _num(nums[1])
        if p is None or q is None:
            problems.append(f"unparsed level {cells}")
            continue
        levels.append((p, q))
    return levels, problems


@dataclass
class LankaBDDepthAdapter:
    sess: LankaBDSession
    name: str = "lankabd_depth"
    kind: str = "book"
    exchange: str = "DSE"
    observes = ("bid_levels", "ask_levels", "best_bid", "best_ask", "book_depth_count", "ltp",
                "open", "high", "low", "close_published", "yclose", "day_trades", "day_volume",
                "day_value", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        self.sess.ensure()
        f = self.sess.client.post(f"{BASE}/Home/MarketDepthData", headers=self.sess.headers(),
                                  data={"Symbol": key or "", "Exchange": self.exchange})
        if f.status in (400, 401, 403, 405):
            self.sess.ensure(force=True)
            f = self.sess.client.post(f"{BASE}/Home/MarketDepthData", headers=self.sess.headers(),
                                      data={"Symbol": key or "", "Exchange": self.exchange})
        return f

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            out.problems.append(f"json: {e}")
            return out
        bids, p1 = parse_depth_table(d.get("buyPriceTable") or "")
        asks, p2 = parse_depth_table(d.get("sellPriceTable") or "")
        out.problems += p1 + p2
        bids_sorted = sorted(bids, key=lambda x: -x[0])
        asks_sorted = sorted(asks, key=lambda x: x[0])
        frame = {
            "symbol": (d.get("symbol") or key or "").upper(),
            "bid_levels": bids_sorted, "ask_levels": asks_sorted,
            "src_order_preserved": bids == bids_sorted and asks == asks_sorted,
            "n_bid_levels": len(bids), "n_ask_levels": len(asks),
            "ltp": _num(d.get("lastTradePrice")), "open": _num(d.get("openPrice")),
            "high": _num(d.get("daysHigh")), "low": _num(d.get("daysLow")),
            "close_published": _num(d.get("closePrice")), "yclose": _num(d.get("yesterdayClosePrice")),
            "day_trades": _num(d.get("noOfTrade")), "day_volume": _num(d.get("totalVolume")),
            "day_value_mn": _num(d.get("totalValueMN")),
            "buy_pct": _num(d.get("buyPercentage")), "sell_pct": _num(d.get("sellPercentage")),
            "total_buy_volume": _num(d.get("totalBuyVolume")), "total_sell_volume": _num(d.get("totalSellVolume")),
            "raw_keys": sorted(d.keys()),
        }
        # Zero means "not populated" on this endpoint after the close (y* fields
        # are all 0.0 in every observed payload); we keep the value and flag it.
        frame["zero_fields"] = [k for k in ("ltp", "open", "high", "low", "close_published", "yclose")
                                if frame[k] == 0.0]
        out.frames.append(frame)
        return out


# ---------------------------------------------------------------------- WATCH (all symbols)
WATCH_MAP = {
    "mkistaT_INSTRUMENT_CODE": "symbol", "mkistaT_LM_DATE_TIME": "t_source_str",
    "unixTimeStamp": "t_source_unix", "mkistaT_QUOTE_BASES": "market_category",
    "mkistaT_OPEN_PRICE": "open", "mkistaT_PUB_LAST_TRADED_PRICE": "ltp",
    "mkistaT_SPOT_LAST_TRADED_PRICE": "ltp_spot", "mkistaT_HIGH_PRICE": "high",
    "mkistaT_LOW_PRICE": "low", "mkistaT_CLOSE_PRICE": "close_published",
    "mkistaT_YDAY_CLOSE_PRICE": "yclose", "mkistaT_TOTAL_TRADES": "day_trades",
    "mkistaT_TOTAL_VOLUME": "day_volume", "mkistaT_TOTAL_VALUE": "day_value_mn",
    "mkistaT_PUBLIC_TOTAL_TRADES": "public_trades", "mkistaT_PUBLIC_TOTAL_VOLUME": "public_volume",
    "mkistaT_PUBLIC_TOTAL_VALUE": "public_value_mn", "mkistaT_SPOT_TOTAL_TRADES": "spot_trades",
    "mkistaT_SPOT_TOTAL_VOLUME": "spot_volume", "mkistaT_SPOT_TOTAL_VALUE": "spot_value_mn",
    "companyID": "company_id", "sectorID": "sector_id", "mkistaT_INSTRUMENT_NUMBER": "instrument_number",
}


@dataclass
class LankaBDWatchAdapter:
    sess: LankaBDSession
    name: str = "lankabd_watch"
    kind: str = "watch"
    observes = ("ltp", "open", "high", "low", "close_published", "yclose", "day_trades", "day_volume",
                "day_value", "market_category", "t_source", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return _tokened_get(self.sess, f"{BASE}/api/datafeed/IndexLiveData/LiveStockWatchData")

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            out.problems.append(f"json: {e}")
            return out
        feed_ts = d.get("timestamp")
        feed_utc = parse_source_local(feed_ts) if feed_ts else None
        for it in d.get("items") or []:
            fr = {WATCH_MAP[k]: v for k, v in it.items() if k in WATCH_MAP}
            fr["symbol"] = (fr.get("symbol") or "").upper()
            t = parse_source_local(fr.get("t_source_str") or "")
            fr["t_source_utc"] = t.isoformat() if t else None
            fr["feed_timestamp_str"] = feed_ts
            fr["feed_timestamp_utc"] = feed_utc.isoformat() if feed_utc else None
            out.frames.append(fr)
        if not out.frames:
            out.problems.append("no items")
        return out


# ---------------------------------------------------------------------- TAPE (per company)
@dataclass
class LankaBDTapeAdapter:
    sess: LankaBDSession
    cid_map: Dict[str, int]
    name: str = "lankabd_tape"
    kind: str = "tape"
    trade_counts: int = 5000
    observes = ("ltp", "day_trades", "day_volume", "day_value", "t_source", "t_recv")
    infers = ("interval_trades", "interval_volume", "interval_value", "interval_vwap")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        cid = self.cid_map.get((key or "").upper())
        if cid is None:
            return Fetched(False, None, b"", {"method": "GET", "url": None, "note": f"no cid for {key}"},
                           error=f"no company id for {key}")
        return _tokened_get(self.sess, f"{BASE}/api/Company/MkSecondDataSymbol",
                            params={"cid": str(cid), "tradeCounts": str(self.trade_counts)})

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes, self.infers))
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            out.problems.append(f"json: {e}")
            return out
        rows = d.get("data") or []
        for i, r in enumerate(rows):
            if not isinstance(r, list) or len(r) < 5:
                out.problems.append(f"row {i} malformed: {r!r}"[:200])
                continue
            # a non-numeric stamp flags this one row (kept, t_source_utc None) instead of losing the whole pull
            try:
                t_src = epoch_ms_to_utc(r[0]).isoformat()
                t_ms = int(r[0])
            except (TypeError, ValueError, OverflowError, OSError):
                out.problems.append(f"row {i} bad stamp: {r[0]!r}"[:200])
                t_src, t_ms = None, None
            out.frames.append({
                "symbol": (key or "").upper(), "row_index": i,
                "t_source_ms": r[0], "t_source_utc": t_src,
                "price": r[1], "cum_trades": r[2], "cum_volume": r[3], "cum_value_mn": r[4],
                "price2": r[5] if len(r) > 5 else None,
            })
        out.frames.sort(key=lambda f: ((f["t_source_utc"] is None), f["t_source_utc"] or "", f["row_index"]))
        out.problems += [] if d.get("length") in (None, len(rows)) else \
            [f"length {d.get('length')} != rows {len(rows)} (partial pull)"]
        return out


# ---------------------------------------------------------------------- MARKET stats
@dataclass
class LankaBDMarketStatsAdapter:
    sess: LankaBDSession
    name: str = "lankabd_market"
    kind: str = "market"
    observes = ("market_trades", "market_volume", "market_value", "t_source", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return _tokened_get(self.sess, f"{BASE}/api/datafeed/IndexLiveData/LiveDSETradeStatistics")

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            out.problems.append(f"json: {e}")
            return out
        t = parse_source_local(d.get("timestamp") or "")
        out.frames.append({
            "market_trades": _num(d.get("trades")), "market_volume": _num(d.get("volume")),
            "market_value_mn": _num(d.get("value")), "symbols_traded": _num(d.get("symbols")),
            "up": _num(d.get("priceupsymbols")), "down": _num(d.get("pricedownsymbols")),
            "flat": _num(d.get("priceflatsymbols")), "t_source_str": d.get("timestamp"),
            "t_source_utc": t.isoformat() if t else None,
        })
        return out


# ---------------------------------------------------------------------- BLOCK board
@dataclass
class LankaBDBlockAdapter:
    sess: LankaBDSession
    name: str = "lankabd_block"
    kind: str = "block"
    observes = ("block_prints", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return _tokened_get(self.sess, f"{BASE}/api/APIMarket/GetLatestBlockMarket")

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            out.problems.append(f"json: {e}")
            return out
        for it in d if isinstance(d, list) else []:
            out.frames.append({
                "symbol": (it.get("symbol") or "").upper(), "block_date": it.get("date"),
                "block_trades": _num(it.get("noOfTrades")), "block_quantity": _num(it.get("quantity")),
                "block_value_mn": _num(it.get("valueMn")), "block_max_price": _num(it.get("maxPrice")),
                "block_min_price": _num(it.get("minPrice")),
                "market_trades": _num(it.get("totalMarketTrades")),
                "market_volume": _num(it.get("totalMarketVolume")),
                "market_value_mn": _num(it.get("totalMarketTurnoverMN")),
            })
        return out


# ---------------------------------------------------------------------- REFERENCE (circuit limits)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _table_rows(html: str, table_id: str) -> List[List[str]]:
    """Rows of <td> texts for one table. Cells may carry nested anchors and tooltip
    spans whose attribute values contain '<' and '>', so a real HTML parser is used."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    t = soup.find(id=table_id)
    if t is None:
        return []
    rows = []
    for tr in t.find_all("tr"):
        cells = []
        for td in tr.find_all("td"):
            a = td.find("a")
            txt = a.get_text(" ", strip=True) if a is not None else td.get_text(" ", strip=True)
            cells.append(txt.split()[0] if (a is not None and txt) else txt)
        if cells:
            rows.append(cells)
    return rows


@dataclass
class LankaBDCircuitAdapter:
    sess: LankaBDSession
    name: str = "lankabd_circuit"
    kind: str = "reference"
    observes = ("upper_limit", "lower_limit", "tick_size", "breaker_pct", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.sess.client.get(f"{BASE}/Home/CircuitBreaker",
                                    headers={"Accept": "text/html,application/xhtml+xml"})

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        html = body.decode("utf-8", "replace")
        k = html.find("TableDataMatrixDSE")
        m = re.search(r"(\d{4}-\d{2}-\d{2})", html[max(0, k - 3000):k]) if k >= 0 else None
        ref_date = m.group(1) if m else None
        for cells in _table_rows(html, "TableDataMatrixDSE"):
            if len(cells) < 8 or not NUM_RE.fullmatch(cells[0]):
                continue
            out.frames.append({
                "symbol": cells[1].upper(), "sector": cells[2], "breaker_pct": _num(cells[3]),
                "tick_size": _num(cells[4]), "open_adj_price": _num(cells[5]),
                "lower_limit": _num(cells[6]), "upper_limit": _num(cells[7]), "reference_date": ref_date,
            })
        if not out.frames:
            out.problems.append("no DSE circuit rows")
        return out


# ---------------------------------------------------------------------- L1 grid (JSON)
@dataclass
class LankaBDGridAdapter:
    sess: LankaBDSession
    name: str = "lankabd_grid"
    kind: str = "l1"
    observes = ("ltp", "open", "high", "low", "close_published", "yclose", "day_volume", "day_value",
                "market_category", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return _tokened_get(self.sess, f"{BASE}/api/APIMarket/GetDataGrid")

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            out.problems.append(f"json: {e}")
            return out
        items = d if isinstance(d, list) else d.get("items") or d.get("data") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            fr = {k: v for k, v in it.items()}          # schema learned on replay; keep everything
            sym = it.get("symbol") or it.get("Symbol") or it.get("mkistaT_INSTRUMENT_CODE")
            fr["symbol"] = (sym or "").upper()
            out.frames.append(fr)
        return out


# ---------------------------------------------------------------------- company-id map
def parse_cid_map(minute_chart_html: str) -> Dict[str, int]:
    """``#ddl1`` <option value=cid>SYMBOL</option> on /Home/MinuteChartMatrix."""
    i = minute_chart_html.find('id="ddl1"')
    j = minute_chart_html.find("</select>", i)
    out: Dict[str, int] = {}
    for v, t in re.findall(r'<option value="(\d+)">\s*([^<]+?)\s*</option>', minute_chart_html[i:j]):
        out[t.strip().upper()] = int(v)
    return out


def fetch_cid_map(sess: LankaBDSession) -> Tuple[Dict[str, int], Fetched]:
    f = sess.client.get(f"{BASE}/Home/MinuteChartMatrix", headers={"Accept": "text/html"})
    return (parse_cid_map(f.body.decode("utf-8", "replace")) if f.ok else {}), f


def build_adapters(client: PoliteClient, cid_map: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    sess = LankaBDSession(client)
    return {
        "session": sess,
        "depth": LankaBDDepthAdapter(sess),
        "watch": LankaBDWatchAdapter(sess),
        "tape": LankaBDTapeAdapter(sess, cid_map or {}),
        "market": LankaBDMarketStatsAdapter(sess),
        "block": LankaBDBlockAdapter(sess),
        "circuit": LankaBDCircuitAdapter(sess),
        "grid": LankaBDGridAdapter(sess),
    }
