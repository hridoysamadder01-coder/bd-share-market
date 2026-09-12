"""Read-only daily stock history for the integrated UI.

This module carries the PR #17 history endpoint without changing the existing
market API response shape. Missing data stays missing; no interpolation or
prediction is introduced.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CACHE: Dict[str, Any] = {}

_ANN_COLS = [
    "symbol", "ts", "open", "high", "low", "close", "volume", "turnover",
    "flag_floor_era", "flag_locked_bar", "flag_zero_volume", "qa_exclude",
]
_RAW_COLS = ["symbol", "ts", "open", "high", "low", "close", "volume", "turnover"]


def _newest(paths: List[str]) -> Optional[str]:
    got = [p for p in paths if os.path.exists(p)]
    return max(got, key=os.path.getmtime) if got else None


def _find_bars_annotated_parquet() -> Optional[str]:
    return _newest([os.path.join(ROOT, "results/dse_eod_bars_annotated.parquet")])


def _find_eod_raw_parquet() -> Optional[str]:
    return _newest([os.path.join(ROOT, "data/raw/dse_eod_extended.parquet")])


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _b(v: Any) -> Optional[bool]:
    if v is None:
        return None
    try:
        if isinstance(v, float) and not math.isfinite(v):
            return None
    except Exception:
        pass
    return bool(v)


def _scrub(x: Any) -> Any:
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _scrub(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_scrub(v) for v in x]
    return x


def _frames() -> Tuple[Any, Any, Optional[str], Optional[str]]:
    if "frames" in _CACHE:
        return _CACHE["frames"]
    import pandas as pd

    ann = raw = None
    ap = _find_bars_annotated_parquet()
    rp = _find_eod_raw_parquet()
    if ap:
        try:
            ann = pd.read_parquet(ap, columns=_ANN_COLS)
            ann["symbol"] = ann["symbol"].astype(str).str.upper()
        except Exception:
            ann = None
    if rp:
        try:
            raw = pd.read_parquet(rp, columns=_RAW_COLS)
            raw["symbol"] = raw["symbol"].astype(str).str.upper()
        except Exception:
            raw = None
    _CACHE["frames"] = (ann, raw, ap, rp)
    return _CACHE["frames"]


def build_stock_history(sym: str) -> Dict[str, Any]:
    sym = (sym or "").upper()
    ann, raw, ap, rp = _frames()
    if ann is None and raw is None:
        return {
            "symbol": sym, "rows": [], "coverage": None, "position": None,
            "truth": "UNKNOWN", "source": None,
            "reason": "no daily history file is present in this checkout",
        }

    import pandas as pd

    rows: List[Dict[str, Any]] = []
    ann_last: Optional[str] = None

    if ann is not None:
        a = ann[ann["symbol"] == sym].sort_values("ts")
        for r in a.itertuples(index=False):
            rows.append({
                "t": pd.Timestamp(r.ts).strftime("%Y-%m-%d"),
                "o": _f(r.open), "h": _f(r.high), "l": _f(r.low), "c": _f(r.close),
                "v": _f(r.volume), "turnover": _f(r.turnover),
                "floor": _b(r.flag_floor_era), "locked": _b(r.flag_locked_bar),
                "zero_volume": _b(r.flag_zero_volume), "qa_exclude": _b(r.qa_exclude),
                "annotated": True,
            })
        if rows:
            ann_last = rows[-1]["t"]

    unannotated = 0
    if raw is not None:
        b = raw[raw["symbol"] == sym].sort_values("ts")
        for r in b.itertuples(index=False):
            t = pd.Timestamp(r.ts).strftime("%Y-%m-%d")
            if ann_last is not None and t <= ann_last:
                continue
            rows.append({
                "t": t,
                "o": _f(r.open), "h": _f(r.high), "l": _f(r.low), "c": _f(r.close),
                "v": _f(r.volume), "turnover": _f(r.turnover),
                "floor": None, "locked": None, "zero_volume": None, "qa_exclude": None,
                "annotated": False,
            })
            unannotated += 1

    if not rows:
        return {
            "symbol": sym, "rows": [], "coverage": None, "position": None,
            "truth": "OBSERVED", "source": ap or rp,
            "reason": "no daily rows for this symbol",
        }

    rows.sort(key=lambda r: r["t"])
    seen = set()
    unique = []
    for r in rows:
        if r["t"] in seen:
            continue
        seen.add(r["t"])
        unique.append(r)
    rows = unique

    gaps = []
    prev = None
    for r in rows:
        d = pd.Timestamp(r["t"])
        if prev is not None:
            days = int((d - prev).days)
            if days > 10:
                gaps.append({"from": prev.strftime("%Y-%m-%d"), "to": r["t"], "days": days})
        prev = d
    gaps.sort(key=lambda g: -g["days"])

    coverage = {
        "first": rows[0]["t"], "last": rows[-1]["t"], "sessions": len(rows),
        "annotated_last": ann_last, "unannotated_sessions": unannotated,
        "floor_era_sessions": sum(1 for r in rows if r["floor"] is True),
        "locked_sessions": sum(1 for r in rows if r["locked"] is True),
        "zero_volume_sessions": sum(1 for r in rows if r["zero_volume"] is True),
        "qa_excluded_sessions": sum(1 for r in rows if r["qa_exclude"] is True),
        "calendar_days": int((pd.Timestamp(rows[-1]["t"]) - pd.Timestamp(rows[0]["t"])).days) + 1,
        "gaps_over_10_days": len(gaps), "longest_gap": gaps[0] if gaps else None,
        "gaps": gaps[:8],
    }

    def rank(key: str):
        vals = [r[key] for r in rows if r.get("qa_exclude") is not True and r[key] is not None]
        cur = rows[-1][key]
        if cur is None or len(vals) < 2:
            return None, None
        below = sum(1 for v in vals if v < cur)
        return round(100.0 * below / len(vals), 1), len(vals)

    c_pct, c_n = rank("c")
    v_pct, v_n = rank("v")
    position = {
        "close": rows[-1]["c"], "close_pct_rank": c_pct, "close_ranked_over": c_n,
        "volume": rows[-1]["v"], "volume_pct_rank": v_pct, "volume_ranked_over": v_n,
        "as_of": rows[-1]["t"],
    }

    return _scrub({
        "symbol": sym, "rows": rows, "coverage": coverage, "position": position,
        "truth": "OBSERVED", "source": " + ".join(p for p in (ap, rp) if p),
    })


def attach_stock_history(app) -> None:
    @app.get("/api/stock/{sym}/history")
    def stock_history(sym: str):
        return build_stock_history(sym)
