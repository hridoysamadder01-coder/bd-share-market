"""BD Market Intelligence OS — market-wide API endpoints for the shell UI.

Reads engine artefacts on disk. Every field a source does not carry is served
as ``null`` (NOT_OBSERVABLE), never as 0. Endpoints:

    GET /api/market/summary        one-shot: DSEX, breadth, turnover, phase, date
    GET /api/market/universe       one row per instrument (latest observed)
    GET /api/market/sectors        sector-aggregated: value, breadth, change
    GET /api/market/sources        source registry with status
    GET /api/features/latest       latest feature vector per symbol
    GET /api/stock/{sym}           full symbol detail bag

Attach via :func:`attach_market_api(app)` from :mod:`tower.ui.server`.
"""
from __future__ import annotations

import csv
import glob
import json
import math
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ────────────────────────────────────────────────────────────── file discovery
def _newest(paths: List[str]) -> Optional[str]:
    """Return the newest existing path by mtime, or None."""
    got = [p for p in paths if os.path.exists(p)]
    if not got:
        return None
    return max(got, key=os.path.getmtime)


def _newest_glob(pattern: str) -> Optional[str]:
    got = glob.glob(pattern)
    return max(got, key=os.path.getmtime) if got else None


def _find_latest_csv() -> Optional[str]:
    """Newest evidence/public_engine/*/extract/latest.csv."""
    return _newest_glob(os.path.join(REPO_ROOT, "evidence/public_engine/*/extract/latest.csv"))


def _find_instruments_csv() -> Optional[str]:
    return _newest_glob(os.path.join(REPO_ROOT, "evidence/public_engine/*/extract/instruments.csv"))


def _find_circuit_csv() -> Optional[str]:
    return _newest_glob(os.path.join(REPO_ROOT, "evidence/public_engine/*/extract/circuit.csv"))


def _find_fundamentals_csv() -> Optional[str]:
    return _newest_glob(os.path.join(REPO_ROOT, "evidence/public_engine/*/extract/fundamentals.csv"))


def _find_ownership_csv() -> Optional[str]:
    return _newest_glob(os.path.join(REPO_ROOT, "evidence/public_engine/*/extract/ownership.csv"))


def _find_source_status() -> Optional[str]:
    return _newest_glob(os.path.join(REPO_ROOT, "evidence/public_engine/*/SOURCE_STATUS.json"))


def _find_market_totals_parquet() -> Optional[str]:
    return _newest_glob(os.path.join(REPO_ROOT, "evidence/public/*/normalized/dse_market_statistics_totals.parquet"))


def _find_breadth_parquet() -> Optional[str]:
    return _newest_glob(os.path.join(REPO_ROOT, "evidence/public/*/normalized/dse_market_statistics_breadth.parquet"))


def _find_features_parquet() -> Optional[str]:
    return _newest(
        [
            os.path.join(REPO_ROOT, "results/dse_eod_features.parquet"),
        ]
    )


def _find_bars_annotated_parquet() -> Optional[str]:
    """The QA-annotated daily spine. Carries the flags that say when a printed
    price was not a freely traded price (floor era, locked bar)."""
    return _newest(
        [
            os.path.join(REPO_ROOT, "results/dse_eod_bars_annotated.parquet"),
        ]
    )


def _find_eod_raw_parquet() -> Optional[str]:
    """The raw extended daily file. Runs later than the annotated spine, but
    carries no QA flags, so anything only it knows is served with flags null."""
    return _newest(
        [
            os.path.join(REPO_ROOT, "data/raw/dse_eod_extended.parquet"),
        ]
    )


def _find_state_events_parquet() -> Optional[str]:
    return _newest(
        [
            os.path.join(REPO_ROOT, "results/STATE_EVENT_LOG.parquet"),
        ]
    )


# ────────────────────────────────────────────────────────────── file loaders
def _read_csv(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _f(v: Any) -> Optional[float]:
    """Parse a CSV cell to float or None. Blank / 'nan' / 'None' → None."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return None if math.isnan(x) else x


def _i(v: Any) -> Optional[int]:
    x = _f(v)
    return int(x) if x is not None else None


def _b(v: Any) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    return None


def _commit_sha() -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=2
        )
        return r.stdout.strip() or None
    except Exception:
        return None


# ────────────────────────────────────────────────────────────── cache
_CACHE: Dict[str, Tuple[float, Any]] = {}
_TTL = 10.0  # seconds


def _cached(key: str, build):
    import time

    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    v = build()
    _CACHE[key] = (now, v)
    return v


# ────────────────────────────────────────────────────────────── NaN-safe JSON
def _scrub(x):
    """Convert NaN / +/-inf to None recursively so FastAPI's JSON encoder is happy."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _scrub(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_scrub(v) for v in x]
    return x


# ────────────────────────────────────────────────────────────── universe builder
def _build_universe() -> Dict[str, Any]:
    inst = _read_csv(_find_instruments_csv())
    circ = _read_csv(_find_circuit_csv())
    latest = _read_csv(_find_latest_csv())
    circ_by_sym: Dict[str, Dict[str, Any]] = {r["symbol"]: r for r in circ if r.get("symbol")}
    latest_by_sym: Dict[str, Dict[str, Any]] = {r["symbol"]: r for r in latest if r.get("symbol")}

    rows: List[Dict[str, Any]] = []
    for r in inst:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        c = circ_by_sym.get(sym) or {}
        L = latest_by_sym.get(sym) or {}
        row = {
            "symbol": sym,
            "name": r.get("company_name") or None,
            "sector": r.get("sector_id") or None,
            "category": r.get("market_category") or None,
            "open": _f(r.get("open")),
            "high": _f(r.get("high")),
            "low": _f(r.get("low")),
            "close_published": _f(r.get("close_published")),
            "ycp": _f(r.get("yclose")),
            "trades": _i(r.get("day_trades")),
            "volume": _f(r.get("day_volume")),
            "value": (
                (_f(r.get("day_value_mn")) or 0.0) * 1_000_000 if _f(r.get("day_value_mn")) is not None else None
            ),
            "year_high": _f(r.get("yearly_high")),
            "year_low": _f(r.get("yearly_low")),
            "ref_7d": _f(r.get("ref_7d")),
            "ref_15d": _f(r.get("ref_15d")),
            "ref_30d": _f(r.get("ref_30d")),
            "ref_90d": _f(r.get("ref_90d")),
            "ref_180d": _f(r.get("ref_180d")),
            "ref_365d": _f(r.get("ref_365d")),
            "floor_flag": _b(r.get("floor_flag")),
            "sme_flag": _b(r.get("sme_flag")),
            "spot_flag": _b(r.get("spot_flag")),
            "t_source": r.get("t_source_str") or None,
            # LTP + circuit
            "ltp": _f(L.get("ltp")) if L else _f(r.get("close_published")),
            "upper_limit": _f(c.get("upper_limit")),
            "lower_limit": _f(c.get("lower_limit")),
            "tick_size": _f(c.get("tick_size")),
            "breaker_pct": _f(c.get("breaker_pct")),
        }
        # derive change_abs / change_pct if possible
        ltp, ycp = row["ltp"], row["ycp"]
        if ltp is not None and ycp not in (None, 0):
            row["change_abs"] = round(ltp - ycp, 4)
            row["change_pct"] = round((ltp - ycp) / ycp * 100.0, 4)
        else:
            row["change_abs"] = None
            row["change_pct"] = None
        rows.append(row)

    at = None
    lc = _find_latest_csv()
    if lc:
        try:
            import datetime as _dt

            at = _dt.datetime.utcfromtimestamp(os.path.getmtime(lc)).isoformat() + "Z"
        except Exception:
            pass
    return _scrub({"rows": rows, "at": at, "count": len(rows)})


# ────────────────────────────────────────────────────────────── summary
def _build_summary() -> Dict[str, Any]:
    ss_path = _find_source_status()
    ss: Dict[str, Any] = {}
    if ss_path:
        try:
            ss = json.load(open(ss_path))
        except Exception:
            ss = {}

    totals_row: Dict[str, Any] = {}
    breadth_row: Dict[str, Any] = {}
    try:
        import pandas as pd

        p = _find_market_totals_parquet()
        if p:
            df = pd.read_parquet(p).sort_values("report_date", ascending=False)
            if len(df):
                totals_row = df.iloc[0].to_dict()
        b = _find_breadth_parquet()
        if b:
            df = pd.read_parquet(b).sort_values("report_date", ascending=False)
            all_cat = df[df["category"] == "All Category"] if "category" in df.columns else df
            if len(all_cat):
                breadth_row = all_cat.iloc[0].to_dict()
    except Exception:
        pass

    # dsex — from latest.csv "0000" or "DSEX" pseudo row if present, else null
    dsex: Optional[float] = None
    dsex_change_pct: Optional[float] = None
    dsex_at: Optional[str] = None
    latest_rows = _read_csv(_find_latest_csv())
    for r in latest_rows:
        sym = (r.get("symbol") or "").upper()
        if sym == "DSEX" or sym == "00DSEX" or sym == "0000":
            dsex = _f(r.get("ltp"))
            y = _f(r.get("yclose"))
            if dsex is not None and y not in (None, 0):
                dsex_change_pct = round((dsex - y) / y * 100.0, 4)
            dsex_at = r.get("t_recv")
            break

    return {
        "trading_date": ss.get("trading_date_dhaka") or (
            str(totals_row.get("report_date")) if totals_row.get("report_date") is not None else None
        ),
        "session_phase": ss.get("session_phase") or "CLOSED",
        "dsex": dsex,
        "dsex_change_pct": dsex_change_pct,
        "dsex_at": dsex_at,
        "market_trades": _i(totals_row.get("day_trades")) if totals_row else None,
        "market_volume": _f(totals_row.get("day_volume")) if totals_row else None,
        "market_value": _f(totals_row.get("day_value_tk")) if totals_row else None,
        "market_at": str(totals_row.get("receipt_utc")) if totals_row.get("receipt_utc") is not None else None,
        "advancing": _i(breadth_row.get("advanced")),
        "declining": _i(breadth_row.get("declined")),
        "unchanged": _i(breadth_row.get("unchanged")),
        "total_traded": _i(breadth_row.get("total_traded")),
        "commit": _commit_sha(),
    }


# ────────────────────────────────────────────────────────────── sectors
def _build_sectors() -> Dict[str, Any]:
    u = _build_universe()["rows"]
    agg: Dict[str, Dict[str, Any]] = {}
    for r in u:
        sec = r.get("sector")
        if not sec:
            continue
        d = agg.setdefault(sec, {"name": sec, "value": 0.0, "trades": 0, "volume": 0.0, "advancing": 0, "declining": 0, "unchanged": 0, "n": 0, "sum_chg": 0.0, "n_chg": 0})
        if r.get("value") is not None:
            d["value"] += r["value"]
        if r.get("trades") is not None:
            d["trades"] += r["trades"]
        if r.get("volume") is not None:
            d["volume"] += r["volume"]
        cp = r.get("change_pct")
        if cp is not None:
            d["sum_chg"] += cp
            d["n_chg"] += 1
            if cp > 0:
                d["advancing"] += 1
            elif cp < 0:
                d["declining"] += 1
            else:
                d["unchanged"] += 1
        d["n"] += 1
    rows = []
    for name, d in agg.items():
        change_pct = round(d["sum_chg"] / d["n_chg"], 4) if d["n_chg"] else None
        rows.append(
            {
                "name": name,
                "value": d["value"] if d["value"] else None,
                "trades": d["trades"] or None,
                "volume": d["volume"] if d["volume"] else None,
                "advancing": d["advancing"],
                "declining": d["declining"],
                "unchanged": d["unchanged"],
                "n": d["n"],
                "change_pct": change_pct,
            }
        )
    return _scrub({"rows": rows, "at": _build_universe().get("at")})


# ────────────────────────────────────────────────────────────── sources
def _build_sources() -> List[Dict[str, Any]]:
    p = _find_source_status()
    if not p:
        return []
    try:
        d = json.load(open(p))
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for src in d.get("sources") or []:
        # normalise to the shell.js shape
        status = (src.get("status") or "").upper() or "UNKNOWN"
        out.append(
            {
                "name": src.get("name") or src.get("id") or src.get("source") or "?",
                "status": status,
                "delivers": src.get("delivers") or src.get("kind") or None,
                "cadence": (
                    f"{src['cadence_s']}s" if src.get("cadence_s") is not None else src.get("cadence")
                ),
                "delay": src.get("delay") or None,
                "truth": (src.get("truth") or "OBSERVED").upper(),
                "notes": src.get("notes"),
                "last_success": src.get("last_success") or src.get("t_source"),
                "records": src.get("records"),
                "runs_in": src.get("runs_in"),
            }
        )
    return out


# ────────────────────────────────────────────────────────────── features
def _build_features_latest() -> Dict[str, Any]:
    path = _find_features_parquet()
    if not path:
        return {"rows": [], "at": None, "__ok": False, "reason": "dse_eod_features.parquet not present"}
    try:
        import pandas as pd

        df = pd.read_parquet(path)
    except Exception as e:
        return {"rows": [], "at": None, "__ok": False, "reason": str(e)}
    if not len(df):
        return {"rows": [], "at": None, "__ok": True}
    ts_col = None
    for c in ("ts", "date", "trading_date", "t"):
        if c in df.columns:
            ts_col = c
            break
    if ts_col is None:
        latest = df
    else:
        df = df.sort_values(ts_col)
        latest = df.groupby("symbol").tail(1)
    # keep only known feature columns
    known = {
        "symbol",
        "ret_1", "ret_5", "range_pct", "close_location", "gap_open",
        "rel_volume_z", "rel_turnover_z", "range_z", "range_compression",
        "volume_persistence", "activity_concentration",
        "realized_vol", "vol_regime_ratio",
        "amihud_z", "hl_spread_proxy", "illiquidity_persistence",
        "volume_price_divergence", "accumulation_proxy", "ret_autocorr_1",
        "xs_rank_rel_volume", "xs_rank_rel_turnover", "xs_volume_abnormality",
        "market_ret", "market_relative_ret", "xs_breadth_abnormal", "xs_symbols_at_ts",
        "abnormal_persistence", "bars_since_abnormal", "baseline_active_days",
        "novelty", "novelty_pct", "state", "state_age", "elevated_run", "top_component",
        "xs_novelty_rank", "day_volume", "volume", "ret_1d",
    }
    keep = [c for c in latest.columns if c in known]
    latest = latest[keep].copy()
    latest = latest.where(latest.notnull(), None)
    at = None
    try:
        import datetime as _dt
        at = _dt.datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat() + "Z"
    except Exception:
        pass
    return _scrub({"rows": latest.to_dict("records"), "at": at, "__ok": True})


# ────────────────────────────────────────────────────────────── stock detail
def _build_stock(sym: str) -> Dict[str, Any]:
    inst = _read_csv(_find_instruments_csv())
    circ = _read_csv(_find_circuit_csv())
    lat = _read_csv(_find_latest_csv())
    fund = _read_csv(_find_fundamentals_csv())
    own = _read_csv(_find_ownership_csv())

    idx = lambda rows, sym: next((r for r in rows if (r.get("symbol") or "").upper() == sym.upper()), None)

    ir, cr, Lr, fr = idx(inst, sym), idx(circ, sym), idx(lat, sym), idx(fund, sym)
    instrument: Optional[Dict[str, Any]] = None
    if ir or Lr or cr:
        base = ir or {}
        row = {
            "symbol": sym,
            "name": (base.get("company_name") or (fr or {}).get("company_name") or None),
            "sector": base.get("sector_id") or (fr or {}).get("sector") or None,
            "category": base.get("market_category") or (fr or {}).get("market_category") or None,
            "open": _f(base.get("open") or (fr or {}).get("open")),
            "high": _f(base.get("high") or (fr or {}).get("high")),
            "low": _f(base.get("low") or (fr or {}).get("low")),
            "close_published": _f(base.get("close_published")),
            "ycp": _f(base.get("yclose") or (fr or {}).get("yclose")),
            "trades": _i(base.get("day_trades") or (fr or {}).get("day_trades")),
            "volume": _f(base.get("day_volume") or (fr or {}).get("day_volume")),
            "value": (
                ((_f(base.get("day_value_mn")) or _f((fr or {}).get("day_value_mn")) or 0.0)) * 1_000_000
            ),
            "year_high": _f(base.get("yearly_high")),
            "year_low": _f(base.get("yearly_low")),
            "ref_7d": _f(base.get("ref_7d")),
            "ref_15d": _f(base.get("ref_15d")),
            "ref_30d": _f(base.get("ref_30d")),
            "ref_90d": _f(base.get("ref_90d")),
            "ref_180d": _f(base.get("ref_180d")),
            "ref_365d": _f(base.get("ref_365d")),
            "floor_flag": _b(base.get("floor_flag")),
            "sme_flag": _b(base.get("sme_flag")),
            "spot_flag": _b(base.get("spot_flag")),
            "t_source": base.get("t_source_str") or (fr or {}).get("t_source_str"),
            "ltp": _f((Lr or {}).get("ltp")) or _f((fr or {}).get("ltp")) or _f(base.get("close_published")),
            "upper_limit": _f((cr or {}).get("upper_limit")),
            "lower_limit": _f((cr or {}).get("lower_limit")),
            "tick_size": _f((cr or {}).get("tick_size")),
            "breaker_pct": _f((cr or {}).get("breaker_pct")),
            "source": (Lr or {}).get("source") or (base.get("source") or None),
        }
        if row["ltp"] is not None and row["ycp"] not in (None, 0):
            row["change_abs"] = round(row["ltp"] - row["ycp"], 4)
            row["change_pct"] = round((row["ltp"] - row["ycp"]) / row["ycp"] * 100.0, 4)
        instrument = row

    # fundamentals
    fundamentals = None
    if fr:
        fundamentals = {
            "annualized_eps": _f(fr.get("eps")),
            "audited_nav": _f(fr.get("nav")),
            "annualized_pe": _f(fr.get("pe")),
            "price_to_nav": _f(fr.get("price_to_nav")),
            "paid_up_capital": (_f(fr.get("paid_up_capital_mn")) or 0.0) * 1_000_000
            if _f(fr.get("paid_up_capital_mn")) is not None
            else None,
            "outstanding_shares": _f(fr.get("total_shares")),
            "year_end": fr.get("year_end") or None,
            "market_cap": None,
        }
        if fundamentals["outstanding_shares"] is not None and instrument and instrument["ltp"] is not None:
            fundamentals["market_cap"] = fundamentals["outstanding_shares"] * instrument["ltp"]

    # ownership
    own_rows = [r for r in own if (r.get("symbol") or "").upper() == sym.upper()]
    ownership = None
    if own_rows:
        ownership = {
            "rows": [
                {
                    "as_on": r.get("as_on"),
                    "sponsor_director": _f(r.get("sponsor_director_pct")),
                    "government": _f(r.get("government_pct")),
                    "institution": _f(r.get("institution_pct")),
                    "foreign": _f(r.get("foreign_pct")),
                    "public": _f(r.get("public_pct")),
                }
                for r in own_rows
            ]
        }

    # features from latest features parquet
    feat = None
    fl = _build_features_latest()
    if fl.get("__ok"):
        feat = next((r for r in fl["rows"] if (r.get("symbol") or "").upper() == sym.upper()), None)

    # state events from state event log
    state = None
    try:
        p = _find_state_events_parquet()
        if p:
            import pandas as pd
            df = pd.read_parquet(p, columns=["symbol", "ts", "state", "state_age", "novelty", "top_component"])
            df = df[df["symbol"].str.upper() == sym.upper()]
            df = df.sort_values("ts", ascending=False).head(60)
            state = {"events": df.where(df.notnull(), None).to_dict("records")}
    except Exception:
        state = None

    # depth: cached last-seen from evidence/lankabd depth files, best-effort — served as null on absence
    depth = None

    return _scrub({
        "instrument": instrument,
        "fundamentals": fundamentals,
        "ownership": ownership,
        "features": feat,
        "state": state,
        "depth": depth,
        "history": None,
        "events": None,
    })


# ─────────────────────────────────────────────── daily history (read-only)
#
# READ-ONLY ADDITION. Nothing here computes a research quantity, and the shape of
# `/api/stock/{sym}` is unchanged — this is a new endpoint beside it.
#
# Two files, deliberately not blended into one undifferentiated line:
#
#   results/dse_eod_bars_annotated.parquet  the QA-annotated spine. Carries
#       flag_floor_era / flag_locked_bar / flag_zero_volume / qa_exclude, which
#       are the difference between "the price did not move" and "the price was
#       not allowed to move". 13.9% of its rows are floor-era.
#   data/raw/dse_eod_extended.parquet       runs later than the spine. Carries no
#       flags. Rows only it knows are served with every flag null (UNKNOWN) and
#       annotated=false, never as false, so the UI can shade them as unchecked.
#
# Sessions the exchange did not hold are simply absent. They are never filled in,
# and the response reports the gaps so the caller can break the line instead of
# drawing through them.

_HIST_CACHE: Dict[str, Any] = {}

_ANN_COLS = [
    "symbol", "ts", "open", "high", "low", "close", "volume", "turnover",
    "flag_floor_era", "flag_locked_bar", "flag_zero_volume", "qa_exclude",
]
_RAW_COLS = ["symbol", "ts", "open", "high", "low", "close", "volume", "turnover"]


def _hist_frames():
    """Load both daily files once, column-pruned, and keep them for the process."""
    if "frames" in _HIST_CACHE:
        return _HIST_CACHE["frames"]
    import pandas as pd

    ann = raw = None
    ap = _find_bars_annotated_parquet()
    if ap:
        try:
            ann = pd.read_parquet(ap, columns=_ANN_COLS)
            ann["symbol"] = ann["symbol"].astype(str).str.upper()
        except Exception:
            ann = None
    rp = _find_eod_raw_parquet()
    if rp:
        try:
            raw = pd.read_parquet(rp, columns=_RAW_COLS)
            raw["symbol"] = raw["symbol"].astype(str).str.upper()
        except Exception:
            raw = None
    _HIST_CACHE["frames"] = (ann, raw, ap, rp)
    return _HIST_CACHE["frames"]


def _f(v):
    """A number, or None. Never a substituted zero."""
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _b(v):
    """A flag as a real boolean, or None when the source does not carry it."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and not math.isfinite(v):
            return None
    except Exception:
        pass
    return bool(v)


def _build_stock_history(sym: str) -> Dict[str, Any]:
    sym = (sym or "").upper()
    ann, raw, ap, rp = _hist_frames()
    if ann is None and raw is None:
        return {
            "symbol": sym, "rows": [], "coverage": None, "position": None,
            "truth": "UNKNOWN", "source": None,
            "reason": "no daily history file is present in this checkout",
        }

    import pandas as pd

    rows: List[Dict[str, Any]] = []
    ann_last = None

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

    # the tail the annotated spine has not reached yet — flags stay UNKNOWN
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

    # gaps: calendar distance between consecutive sessions. Weekends and holidays
    # are ordinary; a long run is a coverage break and the caller must not draw
    # a line through it.
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

    n = len(rows)
    coverage = {
        "first": rows[0]["t"],
        "last": rows[-1]["t"],
        "sessions": n,
        "annotated_last": ann_last,
        "unannotated_sessions": unannotated,
        "floor_era_sessions": sum(1 for r in rows if r["floor"] is True),
        "locked_sessions": sum(1 for r in rows if r["locked"] is True),
        "zero_volume_sessions": sum(1 for r in rows if r["zero_volume"] is True),
        "qa_excluded_sessions": sum(1 for r in rows if r["qa_exclude"] is True),
        "calendar_days": int((pd.Timestamp(rows[-1]["t"]) - pd.Timestamp(rows[0]["t"])).days) + 1,
        "gaps_over_10_days": len(gaps),
        "longest_gap": gaps[0] if gaps else None,
        "gaps": gaps[:8],
    }

    # where the latest session sits inside this stock's own history. A rank over
    # observed numbers, nothing more — no forward claim attaches to it. Rows the
    # QA marked excluded are left out of the comparison, and the count that the
    # rank was taken over is reported so it can be checked.
    def _rank(key):
        vals = [r[key] for r in rows if r.get("qa_exclude") is not True and r[key] is not None]
        cur = rows[-1][key]
        if cur is None or len(vals) < 2:
            return None, None
        below = sum(1 for v in vals if v < cur)
        return round(100.0 * below / len(vals), 1), len(vals)

    c_pct, c_n = _rank("c")
    v_pct, v_n = _rank("v")
    position = {
        "close": rows[-1]["c"], "close_pct_rank": c_pct, "close_ranked_over": c_n,
        "volume": rows[-1]["v"], "volume_pct_rank": v_pct, "volume_ranked_over": v_n,
        "as_of": rows[-1]["t"],
    }

    return _scrub({
        "symbol": sym,
        "rows": rows,
        "coverage": coverage,
        "position": position,
        "truth": "OBSERVED",
        "source": " + ".join([p for p in (ap, rp) if p]),
    })


# ────────────────────────────────────────────────────────────── attach
def attach_market_api(app) -> None:
    from fastapi import HTTPException, Query

    @app.get("/api/market/summary")
    def _summary():
        return _cached("summary", _build_summary)

    @app.get("/api/market/universe")
    def _universe():
        return _cached("universe", _build_universe)

    @app.get("/api/market/sectors")
    def _sectors():
        return _cached("sectors", _build_sectors)

    @app.get("/api/market/sources")
    def _sources():
        return _cached("sources", _build_sources)

    @app.get("/api/features/latest")
    def _features():
        return _cached("features", _build_features_latest)

    @app.get("/api/stock/{sym}/history")
    def _stock_history(sym: str):
        return _cached("hist:" + sym.upper(), lambda: _build_stock_history(sym))

    @app.get("/api/stock/{sym}")
    def _stock(sym: str):
        d = _build_stock(sym)
        if d["instrument"] is None and d["features"] is None and d["fundamentals"] is None:
            raise HTTPException(status_code=404, detail=f"symbol not found: {sym}")
        return d
