"""Public Bangladesh market sources beyond DSE and LankaBD.

Every endpoint below was requested from this container on 2026-09-08 and the
response inspected; the row in `PUBLIC_SOURCE_MATRIX.md` records the status and
byte count that came back. Nothing here is a guessed route — where a surface was
looked for and not found, it is registered as a BLOCKED spec with the reason,
not quietly dropped, because a dropped source disappears from the gap report too.

What each source adds that DSE + LankaBD do not already give:

* **StockNow** `/api/v1/instruments` — 473 instruments in one JSON call, with
  multi-horizon reference prices (7/15/30/90/180/365-day), yearly high/low, a
  `floor` flag and a source-side `updated_at`. The horizon prices and the floor
  flag are not published by the other sensors.
* **CSE** `/market/current_price` — the *other* exchange. Same instruments,
  independently matched, so a DSE-only error cannot hide inside it. This is the
  only genuinely independent exchange-level cross-check available for free.
* **BullBD** `/detail/<TICKER>` — server-rendered fundamentals: EPS, NAV, PE,
  paid-up capital and **outstanding shares**, plus a snapshot carrying an ISO-8601
  UTC timestamp. Outstanding shares is what turns a holding percentage into a
  share count, and no other free source here publishes it.
* **Bangladesh Bank** — the macro regime (FX, policy and bill rates) that every
  cross-sectional study should condition on and none of the price sources carry.

Deliberately NOT here:

* **AmarStock.** `robots.txt` names `ClaudeBot`, `anthropic-ai` and `Claude-Web`
  under `Disallow: /`. It is registered blocked and never fetched.
* **BullBD depth / minute series.** The page ships `selectedShareMarketDepth`,
  `minuteDataVolumeSeries` and `selectedShareCircuitBreaker` as **empty** slots in
  `window.__INITIAL_STATE__`; they are filled client-side over a socket
  (`global.socketData`). A plain GET therefore observes no book here, and the
  spec says so rather than implying a book we cannot see.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .base import Parsed, capability_map
from ..http_client import Fetched, PoliteClient

STOCKNOW_API = "https://stocknow.com.bd/api/v1"
CSE_BASE = "https://www.cse.com.bd"
BULLBD_BASE = "https://bullbd.com"
BB_BASE = "https://www.bb.org.bd/en/index.php"

NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
_STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")


def _num(s: Any) -> Optional[float]:
    """A number, or None. Never 0.0 as a stand-in for 'missing'."""
    if s is None:
        return None
    if isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = NUM_RE.search(str(s).replace(",", ""))
    return float(m.group(0)) if m else None


def is_hollow(v: Any) -> bool:
    """True when a slot is a placeholder rather than data.

    BullBD ships socket-filled slots pre-created with zeros — the circuit-breaker
    slot arrives as ``{"ul": 0, "ll": 0, "fl": 0, "date": ""}`` on a page whose
    socket has not run. A plain truthiness test calls that dict populated, and a
    downstream reader would then take ``ul = 0`` for a real upper limit of zero.
    A container whose every leaf is falsy carries no observation.
    """
    if v is None:
        return True
    if isinstance(v, dict):
        return all(is_hollow(x) for x in v.values()) if v else True
    if isinstance(v, (list, tuple)):
        return all(is_hollow(x) for x in v) if v else True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, bool):
        return v is False
    if isinstance(v, (int, float)):
        return v == 0
    return False


def extract_initial_state(html: str) -> Optional[Dict[str, Any]]:
    """`window.__INITIAL_STATE__ = {...}` by brace matching.

    A regex to the end of the line fails here because the object contains
    newlines and quoted braces; counting depth outside string literals is the
    only correct way to find where the object ends.
    """
    m = _STATE_RE.search(html or "")
    if not m:
        return None
    s = html[m.end():]
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------- StockNow
# Verified 2026-09-08: HTTP 200, application/json, 345,202 bytes, 473 symbols.
STOCKNOW_FIELDS = ("code", "name", "sector_id", "category", "open", "high", "low", "close",
                   "ycp", "trades", "volume", "value", "yearly_high", "yearly_low", "floor",
                   "sme", "spot", "nv", "new_value", "updated_at",
                   "7d", "15d", "30d", "90d", "180d", "365d")


@dataclass
class StockNowInstrumentsAdapter:
    """All-symbol snapshot with reference prices the other sensors do not publish."""

    client: PoliteClient
    name: str = "stocknow_instruments"
    kind: str = "watch"
    exchange: str = "DSE"
    observes = ("symbol", "open", "high", "low", "close_published", "yclose", "day_trades",
                "day_volume", "day_value", "market_category", "sector", "t_source", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(f"{STOCKNOW_API}/instruments",
                               headers={"Accept": "application/json"})

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:                                       # noqa: BLE001
            out.problems.append(f"json: {e}")
            return out
        if not isinstance(d, dict):
            out.problems.append(f"expected an object keyed by symbol, got {type(d).__name__}")
            return out
        for sym, v in d.items():
            if not isinstance(v, dict):
                out.problems.append(f"{sym}: expected an object, got {type(v).__name__}")
                continue
            fr: Dict[str, Any] = {
                "symbol": str(v.get("code") or sym).upper(),
                "company_name": v.get("name"), "sector_id": v.get("sector_id"),
                "market_category": v.get("category"),
                "open": _num(v.get("open")), "high": _num(v.get("high")),
                "low": _num(v.get("low")), "close_published": _num(v.get("close")),
                "yclose": _num(v.get("ycp")), "day_trades": _num(v.get("trades")),
                "day_volume": _num(v.get("volume")), "day_value_mn": _num(v.get("value")),
                "yearly_high": _num(v.get("yearly_high")), "yearly_low": _num(v.get("yearly_low")),
                # the distinguishing part of this source
                "ref_7d": _num(v.get("7d")), "ref_15d": _num(v.get("15d")),
                "ref_30d": _num(v.get("30d")), "ref_90d": _num(v.get("90d")),
                "ref_180d": _num(v.get("180d")), "ref_365d": _num(v.get("365d")),
                "floor_flag": v.get("floor"), "sme_flag": v.get("sme"), "spot_flag": v.get("spot"),
                "t_source_str": v.get("updated_at"),
                "raw_keys": sorted(v.keys()),
            }
            # The page's own "not populated" sentinel is 0.0 on price fields; it is
            # kept in the frame and named, never silently treated as a traded price.
            fr["zero_fields"] = [k for k in ("open", "high", "low", "close_published", "yclose")
                                 if fr[k] == 0.0]
            out.frames.append(fr)
        if not out.frames:
            out.problems.append("no instruments in the payload")
        return out


# ---------------------------------------------------------------------- CSE
# Verified 2026-09-08: HTTP 200, 433,763 bytes, one table, 388 rows,
# columns SL. | STOCK CODE | LTP | OPEN | HIGH | LOW | YCP | TRADE | VALUE(MN) | VOLUME
@dataclass
class CSECurrentPriceAdapter:
    """The other exchange. An independent match of the same instruments."""

    client: PoliteClient
    name: str = "cse_current_price"
    kind: str = "watch"
    exchange: str = "CSE"
    observes = ("symbol", "ltp", "open", "high", "low", "yclose", "day_trades",
                "day_volume", "day_value", "exchange", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(f"{CSE_BASE}/market/current_price",
                               headers={"Accept": "text/html,application/xhtml+xml"},
                               allow_tls_fallback=True)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body.decode("utf-8", "replace"), "lxml")
        table = soup.find("table")
        if table is None:
            out.problems.append("no table on the page (layout change or an error page)")
            return out
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 10:
                continue
            # CSE renders the code one letter per node, so joining with a separator
            # yields "1 J A N A T A M F"; join with nothing and drop any residue.
            code = re.sub(r"\s+", "", tds[1].get_text("", strip=True)).upper()
            if not code:
                out.problems.append(f"row with an unreadable stock code: "
                                    f"{[td.get_text(' ', strip=True) for td in tds[:3]]}")
                continue
            fr = {
                "symbol": code, "exchange": "CSE",
                "ltp": _num(tds[2].get_text(" ", strip=True)),
                "open": _num(tds[3].get_text(" ", strip=True)),
                "high": _num(tds[4].get_text(" ", strip=True)),
                "low": _num(tds[5].get_text(" ", strip=True)),
                "yclose": _num(tds[6].get_text(" ", strip=True)),
                "day_trades": _num(tds[7].get_text(" ", strip=True)),
                "day_value_mn": _num(tds[8].get_text(" ", strip=True)),
                "day_volume": _num(tds[9].get_text(" ", strip=True)),
            }
            fr["zero_fields"] = [k for k in ("ltp", "open", "high", "low", "yclose")
                                 if fr[k] == 0.0]
            out.frames.append(fr)
        if not out.frames:
            out.problems.append("table found but no data rows parsed")
        return out


# ---------------------------------------------------------------------- BullBD
@dataclass
class BullBDDetailAdapter:
    """Server-rendered fundamentals and a stamped snapshot, per symbol.

    Only `shareDetail` is populated server-side. `intraday` (depth, minute series,
    circuit breaker) arrives over a socket and is empty here — the parser reports
    that rather than emitting an empty book that would read as "no orders".
    """

    client: PoliteClient
    name: str = "bullbd_detail"
    kind: str = "fundamentals"
    per_symbol: bool = True
    observes = ("symbol", "ltp", "open", "high", "low", "yclose", "day_volume", "day_trades",
                "day_value", "eps", "nav", "pe", "paid_up_capital", "total_shares", "sector",
                "market_category", "exchange", "t_source", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(f"{BULLBD_BASE}/detail/{(key or '').upper()}",
                               headers={"Accept": "text/html,application/xhtml+xml"})

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        st = extract_initial_state(body.decode("utf-8", "replace"))
        if st is None:
            out.problems.append("window.__INITIAL_STATE__ not found or not parseable")
            return out
        sd = st.get("shareDetail") or {}
        if sd.get("notFound"):
            out.problems.append(f"bullbd reports notFound for {key!r}")
            return out
        co, ms, fu = sd.get("company") or {}, sd.get("marketSnapshot") or {}, sd.get("fundamentals") or {}
        if not (co or ms or fu):
            out.problems.append("shareDetail present but company/marketSnapshot/fundamentals all empty")
            return out
        fr = {
            "symbol": str(sd.get("ticker") or co.get("ticker") or key or "").upper(),
            "company_name": co.get("name"), "exchange": co.get("exchange"), "mic": co.get("mic"),
            "sector": co.get("sector"), "market_category": co.get("category"),
            "year_end": co.get("yearEnd"),
            "ltp": _num(ms.get("ltp")), "open": _num(ms.get("open")), "high": _num(ms.get("high")),
            "low": _num(ms.get("low")), "yclose": _num(ms.get("ycp")),
            "day_volume": _num(ms.get("volume")), "day_trades": _num(ms.get("trades")),
            "day_value_crore": _num(ms.get("valueCrore")),
            "change_abs": _num(ms.get("changeAbs")), "change_pct": _num(ms.get("changePer")),
            "t_source_str": ms.get("timestamp"),
            # fundamentals — the reason this source is registered at all
            "eps": _num(fu.get("annualizedEps")), "nav": _num(fu.get("auditedNav")),
            "pe": _num(fu.get("annualizedPe")), "price_to_nav": _num(fu.get("priceToNav")),
            "paid_up_capital_mn": _num(fu.get("paidupCapitalMillion")),
            "total_shares": _num(fu.get("outstandingShares")),
            "corporate_event": sd.get("corporateEvent"),
        }
        # day_value in millions, derived from the crore figure the page publishes.
        fr["day_value_mn"] = round(fr["day_value_crore"] * 10.0, 6) \
            if fr["day_value_crore"] is not None else None
        intraday = st.get("intraday") or {}
        empty = [k for k in ("selectedShareMarketDepth", "minuteDataVolumeSeries",
                             "selectedShareCircuitBreaker")
                 if is_hollow(intraday.get(k))]
        if empty:
            # Not a parse failure: it is the documented shape of this source.
            fr["socket_only_fields"] = empty
        fr["zero_fields"] = [k for k in ("ltp", "open", "high", "low", "yclose")
                             if fr[k] == 0.0]
        out.frames.append(fr)
        return out


# ---------------------------------------------------------------------- Bangladesh Bank
@dataclass
class BangladeshBankPageAdapter:
    """One Bangladesh Bank statistics page, kept raw. Low cadence by nature.

    The parser deliberately extracts only the page's tables into rows of cells.
    BB's layouts differ per page and change between publications; committing to a
    column mapping here would break silently, so the mapping is left to replay,
    where a fixed parser can be re-run over every byte ever captured.
    """

    client: PoliteClient
    path: str
    name: str = "bb_page"
    kind: str = "macro"
    observes = ("t_recv",)

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(f"{BB_BASE}/{self.path}",
                               headers={"Accept": "text/html,application/xhtml+xml"},
                               allow_tls_fallback=True)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body.decode("utf-8", "replace"), "lxml")
        for ti, table in enumerate(soup.find_all("table")):
            rows = []
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True)
                         for c in tr.find_all(["td", "th"])]
                if any(c for c in cells):
                    rows.append(cells)
            if len(rows) >= 2:
                out.frames.append({"table_index": ti, "n_rows": len(rows),
                                   "header": rows[0], "rows": rows[1:], "page": self.path})
        if not out.frames:
            out.problems.append(f"no tables with data on {self.path}")
        return out


# ---------------------------------------------------------------------- HTML page keeper
@dataclass
class RawPageAdapter:
    """Fetch and keep a public page whose schema we have not committed to yet.

    Used for BSEC and CDBL: the bytes are worth capturing from today onwards
    (they are the only record of what the regulator published on a given day),
    but inventing a parser for a layout that has not been studied would be
    guessing. Raw-first makes that a legitimate position: capture now, parse on
    replay once the layout is understood.
    """

    client: PoliteClient
    url: str
    name: str
    kind: str = "reference"
    observes = ("t_recv",)

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(self.url, headers={"Accept": "text/html,application/xhtml+xml"},
                               allow_tls_fallback=True)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        text = body.decode("utf-8", "replace")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "lxml")
        links = [{"text": a.get_text(" ", strip=True)[:200], "href": a.get("href")}
                 for a in soup.find_all("a", href=True)]
        out.frames.append({
            "url": self.url, "bytes": len(body), "title": (soup.title.get_text(strip=True)
                                                           if soup.title else None),
            "n_links": len(links), "n_tables": len(soup.find_all("table")),
            "links": links[:400],
            "note": "raw page kept; no committed schema yet — parsed on replay",
        })
        return out


# ---------------------------------------------------------------------- registry
def build_specs(client: PoliteClient, symbols: Sequence[str]) -> List[Any]:
    """SourceSpec rows for everything in this module, blocked entries included."""
    from ..engine import ALL_PHASES, SourceSpec, TRADING_PHASES

    return [
        SourceSpec("stocknow_instruments", "watch", StockNowInstrumentsAdapter(client), 120.0,
                   phases=TRADING_PHASES, closed_cadence_s=3600.0,
                   access_note="473 instruments; 7/15/30/90/180/365-day reference prices, "
                               "yearly high/low, floor flag, source updated_at"),
        SourceSpec("cse_current_price", "watch", CSECurrentPriceAdapter(client), 120.0,
                   phases=TRADING_PHASES, closed_cadence_s=3600.0,
                   access_note="the other exchange — independent cross-check, 388 rows"),
        SourceSpec("bullbd_detail", "fundamentals", BullBDDetailAdapter(client), 21600.0,
                   per_symbol=True,
                   access_note="EPS / NAV / PE / paid-up capital / outstanding shares "
                               "+ ISO-stamped snapshot"),
        SourceSpec("bb_exchange_rate", "macro",
                   BangladeshBankPageAdapter(client, "econdata/exchangerate",
                                             name="bb_exchange_rate"), 86400.0,
                   access_note="USD/BDT reference rate"),
        SourceSpec("bb_bill_rate", "macro",
                   BangladeshBankPageAdapter(client, "monetaryactivity/bbbill",
                                             name="bb_bill_rate"), 86400.0,
                   access_note="Bangladesh Bank bill / policy-adjacent rates"),
        SourceSpec("bsec_site", "regulatory",
                   RawPageAdapter(client, "https://sec.gov.bd/", name="bsec_site"), 86400.0,
                   access_note="regulator publications index; raw-kept, parsed on replay"),
        SourceSpec("cdbl_site", "macro",
                   RawPageAdapter(client, "https://www.cdbl.com.bd/", name="cdbl_site"), 86400.0,
                   access_note="CDS / BO participation statistics; raw-kept, parsed on replay"),

        # ---- registered and deliberately not fetched -------------------------
        SourceSpec("amarstock", "book", None, 0.0, enabled=False,
                   blocked_reason="robots.txt Disallow: / for ClaudeBot, anthropic-ai and "
                                  "Claude-Web. Not fetched, by policy, at any cadence.",
                   delayed=True, delay_note="public quotes are documented as delayed",
                   access_note="depth monitor, 1-minute VPA, VPA archive"),
        SourceSpec("bullbd_depth", "book", None, 0.0, enabled=False,
                   blocked_reason="selectedShareMarketDepth / minuteDataVolumeSeries / "
                                  "selectedShareCircuitBreaker ship EMPTY in the server-rendered "
                                  "__INITIAL_STATE__ and are filled over a socket (global.socketData). "
                                  "A plain GET observes no book here.",
                   access_note="would add a third independent book sensor if the socket were public"),
    ]
