"""EcoSoft OST broker web terminal (``ost.ecosoftbd.com``) — the account holder's
own authenticated session, recorded by the account holder from their own browser.

Learned from a real recording (HAR, 2026-09-07 14:22–14:31 UTC, market closed;
80 entries, 0 WebSocket frames) — nothing below is assumed. Evidence:
``evidence/ecosoft_ost/SCHEMA_MAP.md`` and the sanitized payload file next to it.

Endpoints seen (both plain XHR/fetch JSON, polled by the page itself):

* ``GET /core/api/v1/Order/MarketDepth``   (~every 11 s while the depth panel is open)
      → ``{Success, StockExchange, IsBlock, TradingCode, Depth:{TradingCode, DateTime,
         Open, Ltp, Ycp, Close, High, Low, Trade, Volume, Value, Source, Change,
         ChangePercentage, BuySellDetails:[{BuyPrice, BuyVolume, SellPrice, SellVolume}…]}}``
      Ten rows; a row pairs the i-th bid with the i-th ask and drops the keys of the
      side that has no i-th level.                                        (BOOK)
* ``GET /core/api/v1/Analysis/IndexData``  (~every 60 s)
      → ``{Success, Data:{TradingCode:"DSEX", Ycp, Ltp, Close, CloseOrLtp, Open, High,
         Low, Volume, Trade, Value, Change, ChangePercentage, Gainer, Looser,
         Unchanged, Date, StockExchange, IsSector}}``                     (MARKET)

What this source does NOT deliver (NOT_OBSERVABLE, same ceiling as the public
sensors): number of orders per level, individual trade prints, trade side,
order-by-order events, queue position, a sequence / message id (payloads are
de-duplicated by body hash instead — INFERRED).

Two things the recording could not settle and this adapter therefore does not
decide (rule: never invent a field):

* ``Depth.DateTime`` carries a ``Z`` suffix, but its wall-clock value (14:13:45 on a
  day the market closed at 14:10 Dhaka) reads as Dhaka local time with a literal
  ``Z``. Both readings are exported as candidates; ``t_source_utc`` stays None until
  an open-market recording pins the convention (compare DateTime against t_recv).
* Units of ``Value``: per-symbol ``Value`` = 14.376 against Volume 112,750 × ~127.5
  ≈ 14.38 M BDT → million BDT (INFERRED). Index ``Value`` = 571.32 against Volume
  200,350,191 → only crore BDT (×10⁷) gives a sane ~28.5 BDT average price → crore
  BDT (INFERRED). The frames carry the raw number and the inferred unit label.

This adapter never fetches. The terminal is a credentialed broker session; the
account holder records it (HAR from DevTools, or the CDP probe kit) and the
sanitized bodies are parsed here. No login, cookie, token or account field ever
enters this module — ``seeing.capture.har_probe`` strips them before anything is
written, and ``parse`` re-applies the same strip defensively.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ...clock import DHAKA
from ..http_client import Fetched
from .base import Parsed, capability_map

BASE = "https://ost.ecosoftbd.com"
DEPTH_PATH = "/core/api/v1/Order/MarketDepth"
INDEX_PATH = "/core/api/v1/Analysis/IndexData"

# Convention of Depth.DateTime. None = unresolved (only a closed-market recording
# exists). Set to "utc" or "dhaka_with_z" ONLY after an open-market recording is
# compared against receipt time; record the recording in SCHEMA_MAP.md when doing so.
T_SOURCE_TZ_CONVENTION: Optional[str] = None

NO_FETCH_REASON = ("ecosoft_ost never fetches: it is the account holder's authenticated broker "
                   "session, recorded by the account holder (HAR / CDP probe) and parsed here")

# Keys that must never survive into a frame even if a recording slipped past the probe.
_SENSITIVE = re.compile(r"(password|passwd|token|authorization|cookie|session|apikey|api_key|secret|"
                        r"clientid|client_id|account|boid|balance|cash|portfolio|orderid|order_id|"
                        r"email|phone|mobile|nid|address|jwt|bearer|csrf|xsrf|otp|pin)", re.I)

_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$")


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_dotnet_iso(s: Any) -> Tuple[Optional[datetime], Optional[str]]:
    """``2026-09-07T14:13:45.1505564Z`` (.NET, 7 fractional digits) → (naive datetime
    truncated to microseconds, suffix). Returns (None, None) when it does not parse.
    The verbatim string is always kept by the caller."""
    if not isinstance(s, str):
        return None, None
    m = _ISO_RE.match(s.strip())
    if not m:
        return None, None
    base, frac, suffix = m.groups()
    us = int((frac or "0")[:6].ljust(6, "0"))
    try:
        naive = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(microsecond=us)
    except ValueError:
        return None, None
    return naive, suffix


def source_time_candidates(s: Any) -> Dict[str, Any]:
    """Both readings of a source stamp, plus the resolved one when the convention is known."""
    naive, suffix = parse_dotnet_iso(s)
    out: Dict[str, Any] = {"t_source_str": s, "t_source_suffix": suffix, "t_source_utc": None,
                           "t_source_tz_convention": T_SOURCE_TZ_CONVENTION or "UNRESOLVED",
                           "t_source_utc_candidates": {}}
    if naive is None:
        return out
    as_utc = naive.replace(tzinfo=timezone.utc)
    as_dhaka = naive.replace(tzinfo=DHAKA).astimezone(timezone.utc)
    out["t_source_utc_candidates"] = {"as_stamped_utc": as_utc.isoformat(),
                                      "as_dhaka_wall_clock": as_dhaka.isoformat()}
    if T_SOURCE_TZ_CONVENTION == "utc":
        out["t_source_utc"] = as_utc.isoformat()
    elif T_SOURCE_TZ_CONVENTION == "dhaka_with_z":
        out["t_source_utc"] = as_dhaka.isoformat()
    return out


def _strip(v: Any, removed: List[str], path: str = "") -> Any:
    """Defensive re-application of the probe's key strip. Records what it removed."""
    if isinstance(v, list):
        return [_strip(x, removed, f"{path}[]") for x in v]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if _SENSITIVE.search(str(k)):
                removed.append(f"{path}.{k}" if path else str(k))
                continue
            out[k] = _strip(x, removed, f"{path}.{k}" if path else str(k))
        return out
    return v


def _load(body: bytes, out: Parsed) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        out.problems.append(f"json: {e}")
        return None
    if not isinstance(d, dict):
        out.problems.append(f"top-level {type(d).__name__}, expected object")
        return None
    removed: List[str] = []
    d = _strip(d, removed)
    if removed:
        out.problems.append(f"sensitive keys stripped at parse time (should have been stripped by the "
                            f"probe): {removed}")
    if d.get("Success") is False:
        out.problems.append("Success=false")
    return d


def _no_fetch(name: str, key: Optional[str]) -> Fetched:
    return Fetched(False, None, b"", {"method": None, "url": None, "source": name, "key": key,
                                      "note": NO_FETCH_REASON}, error=NO_FETCH_REASON)


# ---------------------------------------------------------------------- BOOK
def parse_buy_sell_details(rows: Iterable[Any]) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[str]]:
    """``BuySellDetails`` rows → (bids, asks) in source order. A row may carry only
    one side's keys (observed: rows 8–10 had Buy* only). A row with a price but no
    volume keeps the price with ``None`` — never 0."""
    bids: List[Tuple[float, float]] = []
    asks: List[Tuple[float, float]] = []
    problems: List[str] = []
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict):
            problems.append(f"level row {i} is {type(r).__name__}, expected object")
            continue
        bp, av = _num(r.get("BuyPrice")), _num(r.get("SellPrice"))
        if bp is not None:
            bids.append((bp, _num(r.get("BuyVolume"))))
        if av is not None:
            asks.append((av, _num(r.get("SellVolume"))))
        extra = sorted(set(r) - {"BuyPrice", "BuyVolume", "SellPrice", "SellVolume"})
        if extra:
            problems.append(f"level row {i} has unexpected keys {extra} (schema change? kept raw)")
        if bp is None and av is None:
            problems.append(f"level row {i} has neither BuyPrice nor SellPrice: {r!r}"[:200])
    return bids, asks, problems


@dataclass
class EcoSoftOSTDepthAdapter:
    name: str = "ecosoft_ost_depth"
    kind: str = "book"
    observes = ("bid_levels", "ask_levels", "best_bid", "best_ask", "book_depth_count", "ltp",
                "open", "high", "low", "close_published", "yclose", "day_trades", "day_volume",
                "day_value", "t_source", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return _no_fetch(self.name, key)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        d = _load(body, out)
        if d is None:
            return out
        depth = d.get("Depth")
        if not isinstance(depth, dict):
            out.problems.append("no Depth object")
            return out
        bids, asks, p = parse_buy_sell_details(depth.get("BuySellDetails"))
        out.problems += p
        bids_sorted = sorted(bids, key=lambda x: -x[0])
        asks_sorted = sorted(asks, key=lambda x: x[0])
        rows = depth.get("BuySellDetails") or []
        vol, ltp, val = _num(depth.get("Volume")), _num(depth.get("Ltp")), _num(depth.get("Value"))
        frame: Dict[str, Any] = {
            "symbol": str(depth.get("TradingCode") or d.get("TradingCode") or key or "").upper(),
            "stock_exchange_code": d.get("StockExchange"),          # 1 observed with DSE symbols; CSE code not observed
            "is_block": d.get("IsBlock"),
            "bid_levels": bids_sorted, "ask_levels": asks_sorted,
            "src_order_preserved": bids == bids_sorted and asks == asks_sorted,
            "n_bid_levels": len(bids), "n_ask_levels": len(asks),
            "book_depth_count": len(rows) if isinstance(rows, list) else None,   # rows delivered (10 observed)
            "best_bid": bids_sorted[0][0] if bids_sorted else None,
            "best_ask": asks_sorted[0][0] if asks_sorted else None,
            "ltp": ltp, "open": _num(depth.get("Open")), "high": _num(depth.get("High")),
            "low": _num(depth.get("Low")), "close_published": _num(depth.get("Close")),
            "yclose": _num(depth.get("Ycp")), "day_trades": _num(depth.get("Trade")),
            "day_volume": vol, "day_value_raw": val,
            "day_value_mn": val,                     # unit INFERRED: million BDT (see module doc)
            "day_value_unit": "million_BDT (INFERRED: Volume×Ltp/1e6 ≈ Value on the 2026-09-07 recording)",
            "change": _num(depth.get("Change")), "change_pct": _num(depth.get("ChangePercentage")),
            "feed_source_tag": depth.get("Source"),  # "DWS" observed; meaning not interpretable from the recording
            "raw_keys": sorted(depth.keys()),
            # not delivered by this source — stay None, never filled
            "bid_orders_per_level": None, "ask_orders_per_level": None, "sequence_id": None,
        }
        frame.update(source_time_candidates(depth.get("DateTime")))
        if bids_sorted and asks_sorted and bids_sorted[0][0] >= asks_sorted[0][0]:
            out.problems.append(f"crossed/locked book: best_bid {bids_sorted[0][0]} >= best_ask {asks_sorted[0][0]}")
        if vol and ltp and val is not None and vol * ltp > 0:
            ratio = val / (vol * ltp / 1e6)
            frame["day_value_unit_check_ratio"] = ratio
            if not 0.5 <= ratio <= 2.0:
                out.problems.append(f"Value/(Volume×Ltp/1e6) = {ratio:.3g}: million-BDT unit inference does not hold here")
        frame["zero_fields"] = [k for k in ("ltp", "open", "high", "low", "close_published", "yclose") if frame[k] == 0.0]
        out.frames.append(frame)
        return out


# ---------------------------------------------------------------------- MARKET (index)
@dataclass
class EcoSoftOSTIndexAdapter:
    name: str = "ecosoft_ost_index"
    kind: str = "market"
    observes = ("market_trades", "market_volume", "market_value", "t_recv")

    def fetch(self, key: Optional[str] = None) -> Fetched:
        return _no_fetch(self.name, key)

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        d = _load(body, out)
        if d is None:
            return out
        x = d.get("Data")
        if not isinstance(x, dict):
            out.problems.append("no Data object")
            return out
        val = _num(x.get("Value"))
        frame: Dict[str, Any] = {
            "index_code": str(x.get("TradingCode") or "").upper(), "stock_exchange_code": x.get("StockExchange"),
            "is_sector": x.get("IsSector"),
            "index_ltp": _num(x.get("Ltp")), "index_open": _num(x.get("Open")), "index_high": _num(x.get("High")),
            "index_low": _num(x.get("Low")), "index_close": _num(x.get("Close")),
            "index_close_or_ltp": _num(x.get("CloseOrLtp")), "index_yclose": _num(x.get("Ycp")),
            "index_change": _num(x.get("Change")), "index_change_pct": _num(x.get("ChangePercentage")),
            "gainers": _num(x.get("Gainer")), "losers": _num(x.get("Looser")), "unchanged": _num(x.get("Unchanged")),
            "market_trades": _num(x.get("Trade")), "market_volume": _num(x.get("Volume")),
            "market_value_raw": val,
            "market_value_crore": val,               # unit INFERRED: crore BDT (see module doc)
            "market_value_mn": None if val is None else val * 10.0,
            "market_value_unit": "crore_BDT (INFERRED: Volume 200,350,191 vs Value 571.32 → avg price ≈ 28.5 BDT only under ×1e7)",
            "date_str": x.get("Date"),               # "2026-09-07T00:00:00": trading date only, no clock time
            "raw_keys": sorted(x.keys()),
        }
        out.frames.append(frame)
        return out


# ---------------------------------------------------------------------- probe file (kit / har_probe output)
ADAPTERS = {DEPTH_PATH: EcoSoftOSTDepthAdapter(), INDEX_PATH: EcoSoftOSTIndexAdapter()}


def route(url: str) -> Optional[Any]:
    """Adapter for a recorded URL (scheme+host+path, query already dropped by the probe)."""
    for path, ad in ADAPTERS.items():
        if url.split("?", 1)[0].endswith(path):
            return ad
    return None


def iter_probe_records(path: str) -> Iterator[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                yield {"_bad_line": ln, "_error": str(e)}


def frames_from_probe(path: str) -> Dict[str, Any]:
    """Parse every routed record of a ``rich_sensor_probe.ndjson`` file. Each frame
    carries ``t_recv_utc`` (the browser's startedDateTime), ``body_sha256`` (the only
    de-duplication key this source offers — INFERRED, no sequence id) and the source
    adapter name. Unrouted records are counted, not dropped silently."""
    frames: List[Dict[str, Any]] = []
    problems: List[str] = []
    unrouted: Dict[str, int] = {}
    n = 0
    for rec in iter_probe_records(path):
        n += 1
        if "_bad_line" in rec:
            problems.append(f"line {rec['_bad_line']}: {rec['_error']}")
            continue
        ad = route(str(rec.get("url") or ""))
        if ad is None:
            u = str(rec.get("url") or "")
            unrouted[u] = unrouted.get(u, 0) + 1
            continue
        parsed = ad.parse(json.dumps(rec.get("payload")).encode("utf-8"))
        for fr in parsed.frames:
            fr.update({"source": ad.name, "t_recv_utc": rec.get("t_recv_utc"), "body_sha256": rec.get("body_sha256"),
                       "transport": rec.get("transport"), "http_status": rec.get("status")})
            frames.append(fr)
        problems += [f"{ad.name} @ {rec.get('t_recv_utc')}: {p}" for p in parsed.problems]
    return {"records": n, "frames": frames, "problems": problems, "unrouted": unrouted}


def truth_map() -> Dict[str, Dict[str, str]]:
    """The declared capability of each adapter over the canonical vocabulary — what a
    report prints as OBSERVED / INFERRED / NOT_OBSERVABLE for this source."""
    return {ad.name: {k: v.value for k, v in capability_map(ad.observes).items()} for ad in ADAPTERS.values()}
