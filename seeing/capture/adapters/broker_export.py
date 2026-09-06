"""Authorized broker Level-II and Time & Sales exports (CSV / JSON files).

DSE-Mobile, DSE Investor / M-Invest and broker-branded FlexTP terminals show a
Market Depth screen and Time & Sales screens; some brokers provide exports or
APIs. This adapter ingests such files without assuming a fixed column layout:
column names are matched case-insensitively against synonyms, every column
that is present becomes OBSERVED, everything absent stays NOT_OBSERVABLE,
and unknown columns are kept under ``extra``.

Level-II layouts supported
  * wide:  one row per snapshot with bid_price_1, bid_qty_1, [bid_orders_1], ask_price_1, ...
  * long:  one row per level with side, price, qty, [orders], [level], timestamp, symbol

Time & Sales layout
  * one row per print: time, price, qty, [side/aggressor], [board], [trade_id], symbol

A file that carries a per-print tape makes ``trade_prints`` OBSERVED; a file
that carries order counts makes ``*_orders_per_level`` OBSERVED — the two
fields no public source in this repository delivers.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ...clock import parse_source_local
from .base import Parsed, capability_map

SYN = {
    "symbol": ("symbol", "instrument", "trading code", "tradingcode", "scrip", "code", "inst"),
    "time": ("time", "timestamp", "datetime", "date time", "trade time", "tradetime", "lm_date_time", "t"),
    "price": ("price", "trade price", "ltp", "px", "rate"),
    "qty": ("qty", "quantity", "volume", "vol", "size", "shares"),
    "side": ("side", "aggressor", "buy/sell", "b/s", "direction", "taker side"),
    "board": ("board", "segment", "market"),
    "trade_id": ("trade id", "tradeid", "trade_no", "match id", "exec id"),
    "level": ("level", "depth", "position", "rank"),
    "orders": ("orders", "no. of orders", "order count", "num orders", "count", "noorders", "number of orders"),
}
LEVEL_RE = re.compile(r"^(bid|buy|ask|sell|offer)[ _\-]*(price|px|qty|quantity|volume|vol|orders|count)[ _\-]*(\d+)$", re.I)


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", h.strip().lower().replace("_", " "))


def _find(headers: List[str], key: str) -> Optional[str]:
    for h in headers:
        if _norm(h) in SYN[key]:
            return h
    return None


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _rows(body: bytes) -> Tuple[List[Dict[str, Any]], str]:
    text = body.decode("utf-8-sig", "replace")
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        d = json.loads(stripped)
        rows = d if isinstance(d, list) else (d.get("data") or d.get("rows") or d.get("items") or [])
        return [r for r in rows if isinstance(r, dict)], "json"
    return list(csv.DictReader(io.StringIO(text))), "csv"


def _parse_time(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%H:%M:%S"):
        t = parse_source_local(s, fmt)
        if t:
            return t.isoformat()
    return None


@dataclass
class BrokerLevel2Adapter:
    name: str = "broker_l2_export"
    kind: str = "book"
    source_label: str = "authorized broker Level-II export"

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        rows, fmt = _rows(body)
        out = Parsed(self.name)
        if not rows:
            out.problems.append("no rows")
            out.truth = capability_map(())
            return out
        headers = list(rows[0].keys())
        wide = [h for h in headers if LEVEL_RE.match(_norm(h).replace(" ", "_")) or LEVEL_RE.match(h.strip())]
        observes = {"bid_levels", "ask_levels", "t_recv"}
        has_orders = False
        if wide:
            for r in rows:
                bids: Dict[int, List[Any]] = {}
                asks: Dict[int, List[Any]] = {}
                for h in wide:
                    m = LEVEL_RE.match(h.strip()) or LEVEL_RE.match(_norm(h).replace(" ", "_"))
                    side, what, n = m.group(1).lower(), m.group(2).lower(), int(m.group(3))
                    tgt = bids if side in ("bid", "buy") else asks
                    slot = tgt.setdefault(n, [None, None, None])
                    idx = 0 if what in ("price", "px") else (1 if what in ("qty", "quantity", "volume", "vol") else 2)
                    slot[idx] = _num(r[h])
                    if idx == 2 and slot[2] is not None:
                        has_orders = True
                b = [(p, q if q is not None else 0.0) for _, (p, q, _) in sorted(bids.items()) if p is not None]
                a = [(p, q if q is not None else 0.0) for _, (p, q, _) in sorted(asks.items()) if p is not None]
                fr = {"symbol": (r.get(_find(headers, "symbol") or "", "") or key or "").upper(),
                      "t_source_utc": _parse_time(r.get(_find(headers, "time") or "")),
                      "bid_levels": sorted(b, key=lambda x: -x[0]), "ask_levels": sorted(a, key=lambda x: x[0]),
                      "bid_orders_per_level": [o for _, (p, _, o) in sorted(bids.items()) if p is not None] if has_orders else None,
                      "ask_orders_per_level": [o for _, (p, _, o) in sorted(asks.items()) if p is not None] if has_orders else None,
                      "layout": "wide", "format": fmt}
                out.frames.append(fr)
        else:
            hs, hp, hq, ho, ht, hsym = (_find(headers, k) for k in ("side", "price", "qty", "orders", "time", "symbol"))
            if not (hs and hp and hq):
                out.problems.append(f"long layout needs side/price/qty columns; have {headers}")
                out.truth = capability_map(())
                return out
            groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for r in rows:
                sym = (r.get(hsym) if hsym else key) or key or ""
                tkey = str(r.get(ht)) if ht else ""
                g = groups.setdefault((sym.upper(), tkey), {"bid": [], "ask": [], "bo": [], "ao": []})
                side = str(r[hs]).strip().lower()
                lvl = (_num(r[hp]), _num(r[hq]) or 0.0)
                o = int(_num(r[ho])) if (ho and _num(r[ho]) is not None) else None
                has_orders |= o is not None
                if side.startswith(("b", "buy", "bid")):
                    g["bid"].append(lvl); g["bo"].append(o)
                else:
                    g["ask"].append(lvl); g["ao"].append(o)
            for (sym, tkey), g in groups.items():
                out.frames.append({"symbol": sym, "t_source_utc": _parse_time(tkey),
                                   "bid_levels": sorted(g["bid"], key=lambda x: -x[0]),
                                   "ask_levels": sorted(g["ask"], key=lambda x: x[0]),
                                   "bid_orders_per_level": g["bo"] if has_orders else None,
                                   "ask_orders_per_level": g["ao"] if has_orders else None,
                                   "layout": "long", "format": fmt})
        if has_orders:
            observes |= {"bid_orders_per_level", "ask_orders_per_level"}
        if any(f.get("t_source_utc") for f in out.frames):
            observes.add("t_source")
        out.truth = capability_map(observes)
        return out


@dataclass
class BrokerTimeAndSalesAdapter:
    name: str = "broker_tns_export"
    kind: str = "tape"
    source_label: str = "authorized broker Time & Sales export"

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        rows, fmt = _rows(body)
        out = Parsed(self.name)
        if not rows:
            out.problems.append("no rows")
            out.truth = capability_map(())
            return out
        headers = list(rows[0].keys())
        ht, hp, hq, hs, hb, hid, hsym = (_find(headers, k) for k in ("time", "price", "qty", "side", "board", "trade_id", "symbol"))
        if not (ht and hp and hq):
            out.problems.append(f"time/price/qty columns required; have {headers}")
            out.truth = capability_map(())
            return out
        observes = {"trade_prints", "t_source", "t_recv", "ltp"}
        if hs: observes.add("trade_side")
        for i, r in enumerate(rows):
            side_raw = str(r.get(hs, "")).strip().lower() if hs else ""
            side = ("B" if side_raw.startswith("b") else "S" if side_raw.startswith("s") else None) if hs else None
            out.frames.append({"symbol": ((r.get(hsym) if hsym else None) or key or "").upper(), "print_index": i,
                               "t_source_utc": _parse_time(r.get(ht)), "t_source_str": str(r.get(ht)),
                               "price": _num(r.get(hp)), "qty": _num(r.get(hq)), "side": side,
                               "board": r.get(hb) if hb else None, "trade_id": r.get(hid) if hid else None,
                               "format": fmt,
                               "extra": {h: r[h] for h in headers if h not in (ht, hp, hq, hs, hb, hid, hsym)}})
        out.truth = capability_map(observes)
        return out
