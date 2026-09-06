"""Rolling temporal windows and small numerics shared by every engine.

All windows are causal: they only ever see values at or before "now".
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque, Iterable, List, Optional, Sequence, Tuple

import math


@dataclass
class TimedValue:
    t: datetime
    v: float


class RollingSeries:
    """Time-bounded ring of (t, value). ``window_s`` seconds are retained
    (plus ``min_keep`` most recent points regardless of age)."""

    def __init__(self, window_s: float = 600.0, min_keep: int = 8, max_len: int = 5000) -> None:
        self.window_s = float(window_s)
        self.min_keep = min_keep
        self.buf: Deque[TimedValue] = deque(maxlen=max_len)

    def push(self, t: datetime, v: Optional[float]) -> None:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return
        self.buf.append(TimedValue(t, float(v)))
        self._trim(t)

    def _trim(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_s)
        while len(self.buf) > self.min_keep and self.buf[0].t < cutoff:
            self.buf.popleft()

    def __len__(self) -> int:
        return len(self.buf)

    def last(self) -> Optional[float]:
        return self.buf[-1].v if self.buf else None

    def values(self, seconds: Optional[float] = None, n: Optional[int] = None) -> List[float]:
        pts = self.points(seconds, n)
        return [p.v for p in pts]

    def points(self, seconds: Optional[float] = None, n: Optional[int] = None) -> List[TimedValue]:
        if not self.buf:
            return []
        pts = list(self.buf)
        if seconds is not None:
            cutoff = pts[-1].t - timedelta(seconds=seconds)
            pts = [p for p in pts if p.t >= cutoff]
        if n is not None:
            pts = pts[-n:]
        return pts

    def value_at_or_before(self, t: datetime) -> Optional[float]:
        for p in reversed(self.buf):
            if p.t <= t:
                return p.v
        return None

    def change(self, seconds: float) -> Optional[float]:
        """value(now) − value(now − seconds) using the last point at or before that instant."""
        if not self.buf:
            return None
        now = self.buf[-1]
        prev = self.value_at_or_before(now.t - timedelta(seconds=seconds))
        return None if prev is None else now.v - prev

    def slope_per_s(self, seconds: float) -> Optional[float]:
        """Least-squares slope over the last ``seconds`` (units per second)."""
        pts = self.points(seconds)
        if len(pts) < 3:
            return None
        t0 = pts[0].t
        xs = [(p.t - t0).total_seconds() for p in pts]
        ys = [p.v for p in pts]
        return slope(xs, ys)

    def mean(self, seconds: float) -> Optional[float]:
        v = self.values(seconds)
        return sum(v) / len(v) if v else None

    def std(self, seconds: float) -> Optional[float]:
        v = self.values(seconds)
        if len(v) < 2:
            return None
        m = sum(v) / len(v)
        return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))

    def zscore(self, seconds: float, x: Optional[float] = None) -> Optional[float]:
        """z of the latest value (or x) against the trailing window EXCLUDING the latest value."""
        pts = self.points(seconds)
        if len(pts) < 4:
            return None
        cur = pts[-1].v if x is None else x
        base = [p.v for p in pts[:-1]]
        m = sum(base) / len(base)
        s = math.sqrt(sum((b - m) ** 2 for b in base) / (len(base) - 1))
        if s <= 1e-12:
            return None                      # degenerate baseline is "unknown", never "normal"
        return (cur - m) / s

    def max(self, seconds: float) -> Optional[float]:
        v = self.values(seconds)
        return max(v) if v else None

    def min(self, seconds: float) -> Optional[float]:
        v = self.values(seconds)
        return min(v) if v else None

    def run_length(self, pred) -> int:
        """Number of consecutive most-recent points satisfying pred."""
        n = 0
        for p in reversed(self.buf):
            if pred(p.v):
                n += 1
            else:
                break
        return n


def slope(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-18:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def curvature(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Second-order coefficient of a least-squares quadratic fit (2·a)."""
    n = len(xs)
    if n < 3:
        return None
    # normal equations for y = a x² + b x + c
    sx = sum(xs); sx2 = sum(x * x for x in xs); sx3 = sum(x ** 3 for x in xs); sx4 = sum(x ** 4 for x in xs)
    sy = sum(ys); sxy = sum(x * y for x, y in zip(xs, ys)); sx2y = sum(x * x * y for x, y in zip(xs, ys))
    A = [[sx4, sx3, sx2], [sx3, sx2, sx], [sx2, sx, n]]
    B = [sx2y, sxy, sy]
    try:
        a = _solve3(A, B)[0]
    except ZeroDivisionError:
        return None
    return 2.0 * a


def _solve3(A, B):
    # Gaussian elimination, 3×3
    M = [row[:] + [b] for row, b in zip(A, B)]
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(M[r][i]))
        if abs(M[piv][i]) < 1e-18:
            raise ZeroDivisionError
        M[i], M[piv] = M[piv], M[i]
        for r in range(3):
            if r != i:
                f = M[r][i] / M[i][i]
                M[r] = [a - f * b for a, b in zip(M[r], M[i])]
    return [M[i][3] / M[i][i] for i in range(3)]


def ewma(prev: Optional[float], x: Optional[float], alpha: float) -> Optional[float]:
    if x is None:
        return prev
    return x if prev is None else prev + alpha * (x - prev)


def clamp01(x: Optional[float]) -> float:
    if x is None or math.isnan(x):
        return 0.0
    return max(0.0, min(1.0, float(x)))


def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def sign(x: Optional[float]) -> int:
    if x is None or x == 0:
        return 0
    return 1 if x > 0 else -1
