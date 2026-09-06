"""Deterministic symbol-universe selection from the all-symbol watch payload.

Equities only (market category ``*-EQ``). ``n_top`` names by the previous
session's traded value plus ``n_mid`` names drawn (seeded) from the 40th–70th
percentile of value, so leave-one-symbol-out and the liquidity split have both
ends of the distribution. The choice and its inputs are written to META.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Sequence


def select_universe(watch_frames: Sequence[Dict[str, Any]], n_top: int = 8, n_mid: int = 6,
                    seed: int = 7, exclude: Sequence[str] = ()) -> Dict[str, Any]:
    eq = [f for f in watch_frames
          if str(f.get("market_category") or "").endswith("-EQ")
          and f.get("symbol") and f["symbol"] not in exclude
          and (f.get("day_value_mn") or 0) > 0 and (f.get("ltp") or 0) > 0]
    eq.sort(key=lambda f: -(f.get("day_value_mn") or 0))
    top = [f["symbol"] for f in eq[:n_top]]
    n = len(eq)
    lo, hi = int(n * 0.30), int(n * 0.60)          # 40th–70th percentile from the top = ranks 30–60 %
    pool = [f["symbol"] for f in eq[lo:hi] if f["symbol"] not in top]
    rng = random.Random(seed)
    mid = sorted(rng.sample(pool, min(n_mid, len(pool)))) if pool else []
    return {"symbols": top + mid, "top": top, "mid": mid, "n_equities": n, "seed": seed,
            "rule": "top n_top by previous-session value + seeded sample of ranks 30–60 % (equities only)"}


def default_universe() -> List[str]:
    """Fallback if the watch cannot be fetched at start: the 2026-09-03 ranking."""
    return ["MALEKSPIN", "SHARPIND", "SAIHAMCOT", "SAIHAMTEX", "BXPHARMA", "PTL", "LOVELLO", "BRACBANK",
            "BEXIMCO", "GP", "SQURPHARMA", "IPDC", "POWERGRID", "ORIONPHARM"]
