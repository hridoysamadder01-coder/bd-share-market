"""Pressure layer: book pressure, trade pressure, combined pressure, persistence,
reversal and book-vs-trade divergence. Pure functions over MarketState + history.

Rules
  book_pressure     = 0.5·imb_weighted + 0.3·imb_topk + 0.2·imb_l1 (whichever exist, re-weighted)
  trade_pressure    = signed_flow_window / (|volume over the same window| + floor)   ∈ [−1, 1]
  combined          = mean of the available pressures
  direction         = sign(combined) when |combined| ≥ 0.20 else 0
  strength          = min(1, |combined|)
  persistence_s     = seconds the direction has been unchanged and non-zero (from history)
  reversal          = direction flipped within the last 120 s from a state with strength ≥ 0.4
  divergence        = book_pressure − trade_pressure when both exist (positive: book bid-heavy, trades sell-heavy)
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from .mechanics.base import StateHistory
from .state import MarketState

DIRECTION_THRESHOLD = 0.20
REVERSAL_LOOKBACK_S = 120.0
REVERSAL_MIN_STRENGTH = 0.40


def _blend(parts) -> Optional[float]:
    parts = [(w, v) for w, v in parts if v is not None]
    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / wsum


def fill_pressure(ms: MarketState, hist: StateHistory) -> None:
    ms.book_pressure = _blend([(0.5, ms.imb_weighted), (0.3, ms.imb_topk), (0.2, ms.imb_l1)])
    tp = None
    if ms.signed_flow_window is not None:
        vol = ms.volume_only_response
        # the signed window and the volume window are both rolling totals from the tape engine;
        # normalise by the larger of the two so |tp| ≤ 1 even when the windows differ in length
        denom = max(abs(ms.signed_flow_window), vol or 0.0)
        tp = (ms.signed_flow_window / denom) if denom > 0 else None
    ms.trade_pressure = tp
    ms.combined_pressure = _blend([(1.0, ms.book_pressure), (1.0, ms.trade_pressure)])
    c = ms.combined_pressure
    if c is None:
        ms.pressure_direction = None
        ms.pressure_strength = None
        ms.pressure_persistence_s = None
        ms.pressure_reversal = None
    else:
        ms.pressure_direction = 1 if c >= DIRECTION_THRESHOLD else (-1 if c <= -DIRECTION_THRESHOLD else 0)
        ms.pressure_strength = min(1.0, abs(c))
        # persistence: walk history backwards while direction unchanged
        persist_from = ms.t
        for s in reversed(list(hist.buf)):
            if s.pressure_direction == ms.pressure_direction and ms.pressure_direction != 0:
                persist_from = s.t
            else:
                break
        ms.pressure_persistence_s = (ms.t - persist_from).total_seconds() if ms.pressure_direction else 0.0
        rev = False
        if ms.pressure_direction != 0:
            cutoff = ms.t - timedelta(seconds=REVERSAL_LOOKBACK_S)
            for s in reversed(list(hist.buf)):
                if s.t < cutoff:
                    break
                if s.pressure_direction not in (None, 0) and s.pressure_direction != ms.pressure_direction \
                        and (s.pressure_strength or 0) >= REVERSAL_MIN_STRENGTH:
                    rev = True
                    break
        ms.pressure_reversal = rev
    ms.pressure_divergence = (ms.book_pressure - ms.trade_pressure) \
        if (ms.book_pressure is not None and ms.trade_pressure is not None) else None
