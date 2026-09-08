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


# ---------------------------------------------------------------------- ownership
@dataclass
class DSEOwnershipAdapter:
    """Monthly shareholding declaration for one company, from dsebd.org.

    This is the slow clock that `PUBLIC_DATA_GAPS.md` §3 names as the gap that
    dominates everything else: DSE publishes only three as-on dates per company
    and keeps no archive, so 162 of 208 usable observations share a single month.
    The only remedy is time — one snapshot per symbol per month from now on, each
    run adding one as-on date. Nothing computed before that panel exists means
    anything, and this adapter does not pretend otherwise.

    Raw-first: the page bytes are stored and the percentages are parsed on replay
    by `collector/dse_public_collector.py`, which already owns that parser. Here
    the parse exists only to confirm the page is a company page and not an error.
    """

    client: PoliteClient
    name: str = "dse_ownership"
    kind: str = "ownership"
    observes = ("sponsor_director_pct", "government_pct", "institution_pct",
                "foreign_pct", "public_pct", "t_recv")

    # One block per as-on date. The page carries three, and a dict() over all
    # matches would silently keep only the last — throwing away two thirds of the
    # little ownership history DSE publishes, which is the scarce resource here.
    ROW_RE = re.compile(
        r"Sponsor/Director\s*:\s*([\d.]+).*?Govt\s*:\s*([\d.]+).*?Institute\s*:\s*([\d.]+)"
        r".*?Foreign\s*:\s*([\d.]+).*?Public\s*:\s*([\d.]+)", re.S)
    AS_ON_RE = re.compile(r"as on ([A-Za-z]{3,9}\s+\d{1,2},?\s*\d{4})", re.I)

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get("https://www.dsebd.org/displayCompany.php",
                               params={"name": (key or "").upper()},
                               headers={"Accept": "text/html,application/xhtml+xml"},
                               allow_tls_fallback=True)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body.decode("utf-8", "replace"), "lxml")
        text = soup.get_text(" ", strip=True)
        rows = self.ROW_RE.findall(text)
        if not rows:
            out.problems.append(f"no shareholding block on the company page for {key!r}")
            return out
        as_on = self.AS_ON_RE.findall(text)
        for i, (sp, gv, inst, fo, pub) in enumerate(rows):
            out.frames.append({
                "symbol": (key or "").upper(),
                "as_on": as_on[i] if i < len(as_on) else None,
                "row_index": i,
                "sponsor_director_pct": _num(sp), "government_pct": _num(gv),
                "institution_pct": _num(inst), "foreign_pct": _num(fo),
                "public_pct": _num(pub),
            })
        # DSE publishes at most three as-on dates and keeps no archive; if that
        # ever changes, the count changing is the signal, so it is recorded.
        out.frames[0]["as_on_dates_on_page"] = len(rows)
        if len(as_on) < len(rows):
            out.problems.append(f"{len(rows)} holding rows but only {len(as_on)} as-on dates read")
        return out


@dataclass
class DSEMarketHistoryAdapter:
    """A rolling ~30 session-day market history from dsebd.org, no account needed.

    Found by checking a broker terminal's header ribbon against what the public
    site already gives away. The ribbon read `DSEX: 5539.32 | Tr: 168,534 | Vol:
    191,268,628 | Val: 590.44 cr`; this page's top row for the same day reads
    168,534 trades, 191,268,628 shares and 5,904.379 mn — the same numbers, free.

    Two things here exist nowhere else in the collection:

    * **Total market capitalisation** (6,911,303.853 mn on 2026-09-08). No other
      wired source publishes it, and it is the denominator for turnover-to-cap —
      the only size-free way to compare one session's activity against another's.
    * **DSES and DS30 beside DSEX.** A move in the broad index that the shariah
      and blue-chip indices do not share is a different event from one they do,
      and that separation cannot be recovered from DSEX alone.

    The window is short and rolling — roughly a month — so this is a source that
    must be polled to accumulate rather than fetched once. Rows are keyed by their
    own date, so re-runs overlap harmlessly and the panel grows a row per session.

    `DGEN Index` is published as a literal "-": DGEN was retired, so the column is
    kept empty rather than zero, because a discontinued index is not an index that
    fell to nothing.
    """

    client: PoliteClient
    name: str = "dse_market_history"
    kind: str = "market"
    url: str = "https://www.dsebd.org/recent_market_information.php"
    observes = ("date", "total_trade", "total_volume", "total_value_mn",
                "market_cap_mn", "dsex", "dses", "ds30", "t_recv")

    # Matched loosely (case, whitespace, the "in Taka (mn)" qualifiers) so a
    # cosmetic rewording does not silently drop a column — and the header actually
    # seen is recorded on the frame either way, so a real change is visible.
    COLUMNS = (("date", r"^date$"),
               ("total_trade", r"total\s+trade"),
               ("total_volume", r"total\s+volume"),
               ("total_value_mn", r"total\s+value"),
               ("market_cap_mn", r"market\s+cap"),
               ("dsex", r"\bDSEX\b"),
               ("dses", r"\bDSES\b"),
               ("ds30", r"\bDS30\b"),
               ("dgen", r"\bDGEN\b"))

    DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(self.url, headers={"Accept": "text/html,application/xhtml+xml"},
                               allow_tls_fallback=True)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body.decode("utf-8", "replace"), "lxml")

        # The page opens with a 397-row scrolling ticker of every instrument — by
        # far the largest table, and carrying nothing this source is for. So the
        # history table is found by its HEADER, never by size or position.
        table, header, idx = None, None, {}
        for t in soup.find_all("table"):
            rows = t.find_all("tr")
            if len(rows) < 2:
                continue
            cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            # Identified by the header carrying BOTH Date and DSEX — not by column
            # count, which would break the day DSE trims a column, and not by size
            # or position, which the ticker wins. `^date$` is anchored so a ticker
            # cell like "1JANATAMF 3.40 0.00 0.00%" cannot match it.
            found: Dict[str, int] = {}
            for field, pat in self.COLUMNS:
                for i, c in enumerate(cells):
                    if re.search(pat, c, re.I):
                        found[field] = i
                        break
            if "date" in found and "dsex" in found:
                table, header, idx = t, cells, found
                break
        if table is None:
            out.problems.append("no market-history table (no header carrying both Date and DSEX)")
            return out

        for tr in table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if len(cells) <= idx["date"]:
                continue
            raw_date = cells[idx["date"]]
            if not self.DATE_RE.match(raw_date):
                continue                       # a totals row, a spacer, or a footer line
            d, m, y = raw_date.split("-")
            fr: Dict[str, Any] = {"date": f"{y}-{m}-{d}", "date_raw": raw_date}
            for field, i in idx.items():
                if field != "date":
                    fr[field] = _num(cells[i]) if i < len(cells) else None
            out.frames.append(fr)

        if not out.frames:
            out.problems.append(f"history table found but no dated rows parsed; header={header}")
            return out
        out.frames[0]["header_seen"] = header
        out.frames[0]["columns_missing"] = [f for f, _ in self.COLUMNS if f not in idx]
        out.frames[0]["sessions_on_page"] = len(out.frames)
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
        SourceSpec("bb_bill_rate", "macro", BangladeshBankBillAdapter(client), 86400.0,
                   access_note="BB Bill auction results: ISIN, tenor, bids received/accepted, "
                               "yield ranges — the risk-free reference"),
        SourceSpec("bsec_publications", "regulatory", BSECPublicationsAdapter(client), 86400.0,
                   access_note="directives, orders, notifications and press releases with dates "
                               "and PDF links; LABELS and CONTEXT, never a live signal"),
        SourceSpec("cdbl_stats", "macro", CDBLStatisticsAdapter(client), 86400.0,
                   access_note="BO accounts, depository participants, enlisted ISINs, "
                               "CDS market value, shares in CDS"),
        SourceSpec("dse_market_history", "market", DSEMarketHistoryAdapter(client), 3600.0,
                   access_note="~30 session-day rolling history: trades, volume, turnover, "
                               "TOTAL MARKET CAP, and DSEX/DSES/DS30 together. The market-cap "
                               "and the two non-DSEX indices are published by no other wired "
                               "source. The window rolls, so the panel only exists if polled"),
        SourceSpec("dse_ownership", "ownership", DSEOwnershipAdapter(client), 2592000.0,
                   per_symbol=True,
                   access_note="monthly shareholding snapshot — the slow clock that is the only "
                               "remedy for the ownership gap (PUBLIC_DATA_GAPS.md section 3). "
                               "DSE publishes 3 as-on dates and keeps no archive, so each run "
                               "adds one date and nothing before that panel exists means anything"),

        # ---- registered and deliberately not fetched -------------------------
        SourceSpec("bb_exchange_rate", "macro", None, 0.0, enabled=False,
                   blocked_reason="the page answers HTTP 200 with a CAPTCHA, not data "
                                  "(\"This question is for testing whether you are a human "
                                  "visitor\", support ID, image code) — reproduced on every "
                                  "attempt 2026-09-08. Solving a bot challenge is not something "
                                  "this system does, so the source is blocked rather than "
                                  "worked around. Detected as `bot_challenge`, not as WORKING.",
                   access_note="USD/BDT reference rate — would fill fx_rate"),
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


# ---------------------------------------------------------------------- Bangladesh Bank bills
@dataclass
class BangladeshBankBillAdapter:
    """BB Bill auction results — the risk-free reference the price sources do not carry.

    Verified 2026-09-08: HTTP 200, 44,460 B, one table. Two stacked header rows
    (a merged "Bids received" / "Bids accepted" band over per-column labels),
    then one row per auction: issue date, ISIN, tenor, then the received and
    accepted blocks. A row where nothing was taken reads "No bid accepted" and
    is SHORTER than an accepted row, so positional parsing must tolerate a
    variable width instead of assuming a fixed column count.
    """

    client: PoliteClient
    name: str = "bb_bill_rate"
    kind: str = "macro"
    path: str = "monetaryactivity/bbbill"
    observes = ("tbill_yield", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(f"{BB_BASE}/{self.path}",
                               headers={"Accept": "text/html,application/xhtml+xml"},
                               allow_tls_fallback=True)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body.decode("utf-8", "replace"), "lxml")
        table = None
        for t in soup.find_all("table"):
            if "isin" in t.get_text(" ", strip=True).lower():
                table = t
                break
        if table is None:
            out.problems.append("no auction table found (layout change or a challenge page)")
            return out
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 4:
                continue
            issue, isin = cells[0], cells[1]
            # a data row starts with a date and an ISIN; header rows do not
            if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", issue) or not re.fullmatch(r"[A-Z]{2}\w{10}", isin):
                continue
            accepted = not any("no bid accepted" in c.lower() for c in cells)
            fr = {
                "issue_date": issue, "isin": isin, "tenor": cells[2] if len(cells) > 2 else None,
                "bids_received_count": _num(cells[3]) if len(cells) > 3 else None,
                "bids_received_face_value_cr": _num(cells[4]) if len(cells) > 4 else None,
                "bids_received_yield_range": cells[5] if len(cells) > 5 else None,
                "any_bid_accepted": accepted,
                "cells": cells, "n_cells": len(cells),
            }
            if accepted and len(cells) >= 10:
                fr.update({
                    "bids_accepted_count": _num(cells[6]),
                    "bids_accepted_face_value_cr": _num(cells[7]),
                    "sale_value_cr": _num(cells[8]),
                    "accepted_yield_range": cells[9],
                    "weighted_average_price": _num(cells[10]) if len(cells) > 10 else None,
                })
            elif accepted:
                out.problems.append(f"accepted auction row with only {len(cells)} cells: {isin}")
            out.frames.append(fr)
        if not out.frames:
            out.problems.append("auction table found but no data rows parsed")
        return out


# ---------------------------------------------------------------------- CDBL
@dataclass
class CDBLStatisticsAdapter:
    """CDBL headline statistics — BO accounts and depository participation.

    Verified 2026-09-08: HTTP 200, 82,085 B. The figures are label/value pairs in
    a two-column table ("BO Accounts Operable in ..." | "1,661,613"), not a data
    grid. Labels are matched loosely because CDBL truncates them in the markup;
    the label text is kept verbatim beside the parsed value so a wording change
    is visible rather than silently unmatched.
    """

    client: PoliteClient
    name: str = "cdbl_stats"
    kind: str = "macro"
    url: str = "https://www.cdbl.com.bd/"
    observes = ("bo_accounts", "shares_in_cds", "t_recv")

    KEYS = (("bo_accounts", r"\bBO\b.*account"),
            ("depository_participants", r"depository\s+participant"),
            ("isin_enlisted", r"\bISIN\b"),
            ("cds_market_value", r"market\s+value"),
            ("shares_in_cds", r"(shares|securities).*(in\s+CDS|dematerial)"))

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(self.url, headers={"Accept": "text/html,application/xhtml+xml"},
                               allow_tls_fallback=True)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body.decode("utf-8", "replace"), "lxml")
        stats: Dict[str, Any] = {}
        seen: List[Dict[str, Any]] = []
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) != 2:
                continue
            label, value = cells[0], cells[1]
            n = _num(value)
            if not label or n is None:
                continue
            seen.append({"label": label, "value": n, "raw_value": value})
            for field_name, pat in self.KEYS:
                if field_name not in stats and re.search(pat, label, re.I):
                    stats[field_name] = n
        if not seen:
            out.problems.append("no label/value statistic pairs found (layout change?)")
            return out
        out.frames.append({"source_url": self.url, **stats,
                           "all_pairs": seen, "n_pairs": len(seen),
                           "unmatched_labels": [p["label"] for p in seen
                                                if not any(re.search(pat, p["label"], re.I)
                                                           for _, pat in self.KEYS)]})
        return out


# ---------------------------------------------------------------------- BSEC
@dataclass
class BSECPublicationsAdapter:
    """BSEC publications: directives, orders, notifications and press releases.

    Verified 2026-09-08: HTTP 200, 385,303 B, **zero tables** — the publications
    are dated links to PDFs under `/storage/laws/` and `/storage/press_releases/`.
    Each anchor reads "Sep 01, 2026 Directive Regarding ...", so the date and the
    document type are parsed out of the link text and the category from its path.

    These are LABELS and CONTEXT for research, never live signals, and the
    document body is a PDF this parser does not open: it records that the
    publication exists, when, of what type, and where. Extracting a penalty or a
    symbol from the PDF is a separate job and is not guessed here.
    """

    client: PoliteClient
    name: str = "bsec_publications"
    kind: str = "regulatory"
    url: str = "https://sec.gov.bd/"
    observes = ("announcement_date", "announcement_type", "announcement_text", "t_recv")

    DATE_RE = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*(.*)$", re.S)
    TYPE_RE = re.compile(r"^(Directive|Notification|Amendment|Order|Circular|Guideline|Rules?)\b",
                         re.I)
    CATEGORY = ((r"/storage/press_releases/", "press_release"),
                (r"/storage/laws/", "law_order_directive"),
                (r"/storage/draft-rules/", "draft_rule"),
                (r"/circular/", "circular"),
                (r"/downloads/", "download"))

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return self.client.get(self.url, headers={"Accept": "text/html,application/xhtml+xml"},
                               allow_tls_fallback=True)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body.decode("utf-8", "replace"), "lxml")
        seen = set()
        rows: List[Dict[str, Any]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(" ", strip=True)
            category = next((c for pat, c in self.CATEGORY if re.search(pat, href)), None)
            if category is None:
                continue
            m = self.DATE_RE.match(text)
            if not m:
                # a document link with no date in its text: kept, date left None
                date_str, rest = None, text
            else:
                date_str, rest = m.group(1), m.group(2).strip()
            tm = self.TYPE_RE.match(rest)
            doc_type = tm.group(1).title() if tm else None
            title = rest[tm.end():].strip() if tm else rest
            key_ = (href, date_str)
            if key_ in seen:
                continue
            seen.add(key_)
            rows.append({
                "announcement_date": date_str, "announcement_type": doc_type,
                "announcement_text": title or None, "category": category,
                "url": href, "is_pdf": href.lower().endswith(".pdf"),
                "link_text": text,
            })
        # The hero panel links the same PDFs again as bare "Discover More", with
        # no date. Those are the same publication, so the dated row wins and the
        # undated duplicate is folded away — an undated link to a URL that is not
        # listed elsewhere is still kept, because then it is the only record of it.
        dated_urls = {r["url"] for r in rows if r["announcement_date"]}
        out.frames = [r for r in rows
                      if r["announcement_date"] or r["url"] not in dated_urls]
        folded = len(rows) - len(out.frames)
        if folded:
            out.frames.sort(key=lambda r: (r["announcement_date"] is None, r["url"]))
        if not out.frames:
            out.problems.append("no publication links found (layout change?)")
        return out
