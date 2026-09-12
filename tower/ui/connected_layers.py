"""Read-only bridge from already-captured public/live artefacts into the market UI.

No research logic, thresholds, strategy, or engine state is computed here.  This
module only exposes data that already exists on disk, preserving its source and
truth class.  Missing data stays missing.
"""
from __future__ import annotations

import ast
import csv
import glob
import json
import math
import os
from typing import Any, Dict, List, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _latest(pattern: str) -> Optional[str]:
    paths = glob.glob(os.path.join(ROOT, pattern))
    if not paths:
        return None
    # Captures are date-named.  Lexicographic path order is deterministic on a
    # fresh checkout, unlike filesystem mtimes which may all be identical.
    return sorted(paths)[-1]


def _csv(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(str(v).strip())
    except (ValueError, TypeError):
        return None
    return x if math.isfinite(x) else None


def _levels(v: Any) -> List[Dict[str, float]]:
    if v in (None, ""):
        return []
    try:
        raw = ast.literal_eval(str(v))
    except (ValueError, SyntaxError):
        return []
    out: List[Dict[str, float]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        p, q = _f(item[0]), _f(item[1])
        if p is not None and q is not None:
            out.append({"price": p, "volume": q})
    return out


def _jlist(v: Any) -> List[Any]:
    if v in (None, ""):
        return []
    try:
        x = json.loads(str(v))
    except (ValueError, TypeError, json.JSONDecodeError):
        return []
    return x if isinstance(x, list) else []


def _sym_rows(path: Optional[str], sym: str) -> List[Dict[str, Any]]:
    u = sym.upper()
    return [r for r in _csv(path) if str(r.get("symbol") or "").upper() == u]


def _latest_sym(path: Optional[str], sym: str) -> Optional[Dict[str, Any]]:
    rows = _sym_rows(path, sym)
    if not rows:
        return None
    return max(rows, key=lambda r: str(r.get("t_recv") or r.get("t_source_utc") or r.get("t_source") or ""))


def _parquet_rows(path: Optional[str], sym: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {"available": False, "rows": [], "reason": "source artefact not present"}
    try:
        import pandas as pd
        df = pd.read_parquet(path)
    except Exception as exc:
        return {"available": False, "rows": [], "reason": f"parquet decoder unavailable: {type(exc).__name__}"}
    if "symbol" not in df.columns:
        return {"available": False, "rows": [], "reason": "source has no symbol column"}
    d = df[df["symbol"].astype(str).str.upper() == sym.upper()].copy()
    if not len(d):
        return {"available": True, "rows": [], "reason": None}
    date_col = next((c for c in ("trade_date", "ts", "date", "trading_date") if c in d.columns), None)
    if date_col:
        d = d.sort_values(date_col)
    d = d.where(d.notnull(), None)
    rows = d.to_dict("records")
    for row in rows:
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif isinstance(v, float) and not math.isfinite(v):
                row[k] = None
    return {
        "available": True,
        "rows": rows,
        "count": len(rows),
        "first": rows[0].get(date_col) if date_col and rows else None,
        "last": rows[-1].get(date_col) if date_col and rows else None,
        "reason": None,
    }


def _history(sym: str) -> Dict[str, Any]:
    candidates = [
        os.path.join(ROOT, "data/raw/dse_eod_extended.parquet"),
        os.path.join(ROOT, "results/dse_eod_bars_annotated.parquet"),
        os.path.join(ROOT, "data/raw/dse_eod.parquet"),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    if path is None:
        path = _latest("evidence/public/*/normalized/historical_prices.parquet")
    result = _parquet_rows(path, sym)
    result.update({"truth": "OBSERVED", "source": os.path.relpath(path, ROOT) if path else None})
    return result


def _depth(sym: str) -> Optional[Dict[str, Any]]:
    path = _latest("evidence/market_day/*/extract/books.csv")
    row = _latest_sym(path, sym)
    if not row:
        return None
    bids, asks = _levels(row.get("bid_levels")), _levels(row.get("ask_levels"))
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    return {
        "truth": "OBSERVED",
        "source": row.get("source"),
        "at": row.get("t_recv"),
        "bid_levels": bids,
        "ask_levels": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": (best_ask - best_bid) if best_bid is not None and best_ask is not None else None,
        "buy_pct": _f(row.get("buy_pct")),
        "sell_pct": _f(row.get("sell_pct")),
        "total_buy_volume": _f(row.get("total_buy_volume")),
        "total_sell_volume": _f(row.get("total_sell_volume")),
        "n_bid_levels": len(bids),
        "n_ask_levels": len(asks),
    }


def _market() -> Optional[Dict[str, Any]]:
    path = _latest("evidence/market_day/*/extract/market.csv")
    rows = _csv(path)
    if not rows:
        return None
    row = max(rows, key=lambda r: str(r.get("t_recv") or r.get("t_source_utc") or ""))
    status_path = _latest("evidence/market_day/*/SOURCE_STATUS.json")
    status: Dict[str, Any] = {}
    if status_path:
        try:
            with open(status_path, encoding="utf-8") as fh:
                status = json.load(fh)
        except (OSError, ValueError, json.JSONDecodeError):
            status = {}
    return {
        "truth": "OBSERVED",
        "source": row.get("source"),
        "at": row.get("t_source_utc") or row.get("t_recv"),
        "trading_date": status.get("trading_date_dhaka"),
        "session_phase": status.get("session_phase"),
        "trades": _f(row.get("market_trades")),
        "volume": _f(row.get("market_volume")),
        "value": (_f(row.get("market_value_mn")) * 1_000_000) if _f(row.get("market_value_mn")) is not None else None,
        "advancing": _f(row.get("up")),
        "declining": _f(row.get("down")),
        "unchanged": _f(row.get("flat")),
        "symbols_traded": _f(row.get("symbols_traded")),
    }


def _profile(sym: str) -> Optional[Dict[str, Any]]:
    row = _latest_sym(_latest("evidence/public_engine/*/extract/company_profile.csv"), sym)
    if not row:
        return None
    return {
        "truth": "OBSERVED",
        "source": row.get("source"),
        "at": row.get("t_recv"),
        "listing_year": _f(row.get("listing_year")),
        "market_category": row.get("market_category") or None,
        "year_end": row.get("year_end") or None,
        "operational_status": row.get("operational_status") or None,
        "right_issue": row.get("right_issue") or None,
        "reserve_surplus_mn": _f(row.get("reserve_surplus_mn")),
        "short_term_loan_mn": _f(row.get("short_term_loan_mn")),
        "long_term_loan_mn": _f(row.get("long_term_loan_mn")),
        "financials_url": row.get("financials_url") or None,
        "price_sensitive_url": row.get("price_sensitive_url") or None,
    }


def _events(sym: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    prof = _latest_sym(_latest("evidence/public_engine/*/extract/company_profile.csv"), sym)
    if prof:
        for x in _jlist(prof.get("cash_dividend_history")):
            if isinstance(x, dict):
                rows.append({"type": "cash_dividend", "date": str(x.get("year") or ""), "value": _f(x.get("percent")), "truth": "OBSERVED"})
        for x in _jlist(prof.get("bonus_dividend_history")):
            if isinstance(x, dict):
                rows.append({"type": "bonus_dividend", "date": str(x.get("year") or ""), "value": _f(x.get("percent")), "truth": "OBSERVED"})
    block = _latest_sym(_latest("evidence/market_day/*/extract/block.csv"), sym)
    if block:
        rows.append({
            "type": "block_market", "date": block.get("block_date"),
            "quantity": _f(block.get("block_quantity")), "value_mn": _f(block.get("block_value_mn")),
            "trades": _f(block.get("block_trades")), "truth": "OBSERVED",
        })
    return {"rows": rows, "count": len(rows), "truth": "OBSERVED"}


def _ownership(sym: str) -> List[Dict[str, Any]]:
    rows = _sym_rows(_latest("evidence/public_engine/*/extract/ownership.csv"), sym)
    out = []
    for r in rows:
        out.append({
            "as_on": r.get("as_on"), "sponsor_director": _f(r.get("sponsor_director_pct")),
            "government": _f(r.get("government_pct")), "institution": _f(r.get("institution_pct")),
            "foreign": _f(r.get("foreign_pct")), "public": _f(r.get("public_pct")), "truth": "OBSERVED",
        })
    return out


def build_connected_stock(sym: str) -> Dict[str, Any]:
    return {
        "symbol": sym.upper(),
        "depth": _depth(sym),
        "history": _history(sym),
        "events": _events(sym),
        "company_profile": _profile(sym),
        "ownership_history": _ownership(sym),
        "market_context": _market(),
    }


def attach_connected_layers(app) -> None:
    @app.get("/api/connected/market")
    def _connected_market():
        return _market() or {"truth": "NOT_OBSERVABLE"}

    @app.get("/api/connected/stock/{sym}")
    def _connected_stock(sym: str):
        return build_connected_stock(sym)
