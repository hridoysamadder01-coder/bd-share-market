"""Queue emulation layer — poll the visible book and infer pending pressure.

Fetches market depth for a symbol on a fixed interval and applies `infer_pressure()`:
index-weighted pressure across levels, a spoofing penalty on single-level volume
cliffs, and a volume-dispersion damper. Results are appended to
``evidence/queue_sim_<SYMBOL>.json``.

Truth class: the ladder is OBSERVED; every ``inferred_*`` and ``breakout_probability``
value is INFERRED from ladder shape alone. Orders per level and queue position remain
NOT_OBSERVABLE — nothing here recovers them. ``breakout_probability`` is a bounded
buy-share score, not a calibrated probability; it is not fitted to any outcome.

Two things this module fixes relative to a naive implementation, because without them it
cannot run against real bdshare output:

* bdshare returns a NaN-padded frame — the bid and ask columns have different lengths and
  the shorter one is filled with NaN. Zipping the raw columns puts NaN into the volume
  sums and makes every downstream number NaN. Rows are cleaned per side here.
* dsebd.org serves a broken certificate chain, so bdshare raises
  ``SSLCertVerificationError`` out of the box and returns nothing. It builds its own
  ``requests.Session``, so ``set_session()`` does not help; the fallback is forced at the
  ``Session.request`` level for that host only, which is what
  ``seeing/capture/http_client.py`` already does for the same host.

    python3 -m seeing.queue_emulator BEXIMCO --duration 60 --interval 2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = ["infer_pressure", "fetch_and_save", "fetch_book", "enable_dsebd_tls_fallback"]

EVIDENCE_DIR = "evidence"
_TLS_PATCHED = False


# --------------------------------------------------------------------------- transport
def enable_dsebd_tls_fallback() -> None:
    """Let bdshare reach dsebd.org, whose certificate chain does not verify.

    Scoped to that one host and applied once. Every other host keeps full verification.
    """
    global _TLS_PATCHED
    if _TLS_PATCHED:
        return
    import requests
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    original = requests.Session.request

    def patched(self, method, url, **kwargs):
        if "dsebd.org" in str(url):
            kwargs["verify"] = False
        return original(self, method, url, **kwargs)

    requests.Session.request = patched
    _TLS_PATCHED = True


def fetch_book(symbol: str) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """One depth snapshot as (bids, asks), each best-price-first, NaN rows dropped."""
    enable_dsebd_tls_fallback()
    from bdshare import get_market_depth_data

    raw = get_market_depth_data(symbol)
    if raw is None or getattr(raw, "empty", True):
        return [], []
    bids = _clean_side(raw, "buy_price", "buy_volume", descending=True)
    asks = _clean_side(raw, "sell_price", "sell_volume", descending=False)
    return bids, asks


def _clean_side(frame, price_col: str, volume_col: str,
                descending: bool) -> List[Tuple[float, float]]:
    import pandas as pd

    if price_col not in frame.columns or volume_col not in frame.columns:
        return []
    price = pd.to_numeric(frame[price_col], errors="coerce")
    volume = pd.to_numeric(frame[volume_col], errors="coerce")
    keep = price.notna() & (price > 0)
    rows = [(float(p), float(v if np.isfinite(v) else 0.0))
            for p, v in zip(price[keep], volume[keep].fillna(0.0))]
    return sorted(rows, key=lambda r: -r[0] if descending else r[0])


# --------------------------------------------------------------------------- the model
def infer_pressure(bids: Sequence[Sequence[float]],
                   asks: Sequence[Sequence[float]]) -> Dict[str, Any]:
    """Infer resting pressure from the shape of one visible ladder.

    ``bids`` and ``asks`` are (price, volume) pairs ordered best price first — the index
    weighting below is only meaningful in that order.

    * pressure index: volume discounted by distance from the touch, ``sum(v_i / (i+1))``
    * spoofing penalty: how far the fattest level's share exceeds 40 % of the side
    * dispersion damper: a jagged ladder is damped by its own volume dispersion
    """
    bids = [(float(p), float(v)) for p, v in bids if np.isfinite(p) and np.isfinite(v)]
    asks = [(float(p), float(v)) for p, v in asks if np.isfinite(p) and np.isfinite(v)]

    buy_p = sum(vol / (i + 1) for i, (_, vol) in enumerate(bids))
    sell_p = sum(vol / (i + 1) for i, (_, vol) in enumerate(asks))

    total_b = sum(vol for _, vol in bids) or 1.0
    total_s = sum(vol for _, vol in asks) or 1.0
    max_b = max((vol for _, vol in bids), default=0.0)
    max_s = max((vol for _, vol in asks), default=0.0)

    pen_b = max(0.0, (max_b / total_b - 0.4)) * 2
    pen_s = max(0.0, (max_s / total_s - 0.4)) * 2

    b_std = float(np.std([vol for _, vol in bids])) if bids else 0.0
    s_std = float(np.std([vol for _, vol in asks])) if asks else 0.0

    buy_q = buy_p * (1 / (1 + b_std / 1000))
    sell_q = sell_p * (1 / (1 + s_std / 1000))

    total_p = buy_p + sell_p
    if total_p == 0:
        prob = 0.5
    else:
        prob = (buy_p / total_p) * (1 - (pen_b + pen_s) / 2)
        prob = max(0.0, min(1.0, prob))

    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    mid = (best_bid + best_ask) / 2 if (best_bid is not None and best_ask is not None) else None

    return {
        "inferred_buy_queue": round(buy_q, 2),
        "inferred_sell_queue": round(sell_q, 2),
        "breakout_probability": round(prob, 4),
        "buy_pressure_index": round(buy_p, 2),
        "sell_pressure_index": round(sell_p, 2),
        "spoof_penalty_bid": round(pen_b, 4),
        "spoof_penalty_ask": round(pen_s, 4),
        "n_bid_levels": len(bids),
        "n_ask_levels": len(asks),
        "total_bid_volume": round(total_b if bids else 0.0, 2),
        "total_ask_volume": round(total_s if asks else 0.0, 2),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": round(mid, 4) if mid is not None else None,
    }


# --------------------------------------------------------------------------- collection
def fetch_and_save(symbol: str, duration_seconds: int = 60, interval: int = 2,
                   out_dir: str = EVIDENCE_DIR,
                   verbose: bool = True) -> List[Dict[str, Any]]:
    """Poll `symbol` for `duration_seconds` and write the series as JSON.

    Every poll is recorded. A failed fetch is stored as a gap row rather than skipped, so
    a missing snapshot can never be mistaken for an empty book on replay.
    """
    symbol = symbol.upper()
    history: List[Dict[str, Any]] = []
    n_polls = max(1, duration_seconds // max(interval, 1))
    if verbose:
        print(f"[START] {symbol}: {n_polls} polls at {interval}s")

    for i in range(n_polls):
        stamp = datetime.now(timezone.utc).isoformat()
        try:
            bids, asks = fetch_book(symbol)
            row = infer_pressure(bids, asks)
            row.update(timestamp=stamp, symbol=symbol, gap=False,
                       bids=[list(b) for b in bids], asks=[list(a) for a in asks])
            if verbose:
                print(f"[{stamp}] BuyQ: {row['inferred_buy_queue']}, "
                      f"SellQ: {row['inferred_sell_queue']}, "
                      f"Breakout: {row['breakout_probability']}, "
                      f"levels {row['n_bid_levels']}x{row['n_ask_levels']}")
        except Exception as exc:  # noqa: BLE001 — a gap is data, not a reason to stop
            row = {"timestamp": stamp, "symbol": symbol, "gap": True,
                   "error": f"{type(exc).__name__}: {exc}"[:300]}
            if verbose:
                print(f"[{stamp}] GAP {row['error']}")
        history.append(row)
        if i < n_polls - 1:
            time.sleep(interval)

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"queue_sim_{symbol}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=2)
    if verbose:
        good = sum(1 for r in history if not r.get("gap"))
        print(f"[DONE] {good}/{len(history)} snapshots -> {path}")
    return history


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Poll a DSE book and infer pending pressure.")
    ap.add_argument("symbol", nargs="?", help="trading code, e.g. BEXIMCO")
    ap.add_argument("--duration", type=int, default=60, help="seconds to collect (default 60)")
    ap.add_argument("--interval", type=int, default=2, help="seconds between polls (default 2)")
    ap.add_argument("--out", default=EVIDENCE_DIR)
    a = ap.parse_args(argv)
    symbol = a.symbol or input("Symbol (e.g., GP): ")
    if not symbol.strip():
        print("no symbol given")
        return 2
    hist = fetch_and_save(symbol.strip().upper(), a.duration, a.interval, a.out)
    return 0 if any(not r.get("gap") for r in hist) else 1


if __name__ == "__main__":
    sys.exit(main())
