"""Mechanism contract, registry and the per-symbol StateHistory ring."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Type

from ..state import MarketState, MechanismState


@dataclass
class MechanismReading:
    name: str
    family: str
    score: float                                   # 0..1
    state: str                                     # inactive | building | active | confirmed | failed | resolved
    evidence: Dict[str, Any] = field(default_factory=dict)
    baseline: Dict[str, Any] = field(default_factory=dict)
    note: str = ""


class StateHistory:
    """Ring of past MarketState snapshots for one symbol (causal: only states
    at or before the current update). ``series(field, seconds)`` returns
    [(t, value)] for a scalar field; nested keys use dots (``circuit.dist_up``)."""

    def __init__(self, max_len: int = 4000, window_s: float = 3600.0) -> None:
        self.buf: Deque[MarketState] = deque(maxlen=max_len)
        self.window_s = window_s

    def push(self, ms: MarketState) -> None:
        self.buf.append(ms)
        cutoff = ms.t - timedelta(seconds=self.window_s)
        while len(self.buf) > 16 and self.buf[0].t < cutoff:
            self.buf.popleft()

    def __len__(self) -> int:
        return len(self.buf)

    def last(self, n: int = 1) -> List[MarketState]:
        return list(self.buf)[-n:]

    def window(self, seconds: float) -> List[MarketState]:
        if not self.buf:
            return []
        cutoff = self.buf[-1].t - timedelta(seconds=seconds)
        return [s for s in self.buf if s.t >= cutoff]

    @staticmethod
    def get(ms: MarketState, path: str) -> Any:
        cur: Any = ms
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = getattr(cur, part, None)
            if cur is None:
                return None
        return cur

    def series(self, path: str, seconds: Optional[float] = None, n: Optional[int] = None):
        states = self.window(seconds) if seconds is not None else list(self.buf)
        if n is not None:
            states = states[-n:]
        out = []
        for s in states:
            v = self.get(s, path)
            if v is not None and not isinstance(v, (dict, list, str, bool)):
                out.append((s.t, float(v)))
        return out

    def values(self, path: str, seconds: Optional[float] = None, n: Optional[int] = None) -> List[float]:
        return [v for _, v in self.series(path, seconds, n)]

    def at_or_before(self, t: datetime) -> Optional[MarketState]:
        for s in reversed(self.buf):
            if s.t <= t:
                return s
        return None


class Mechanism:
    """Base class. Subclasses set ``name``, ``family``, ``requires`` (state
    fields read) and implement ``compute(ms, hist) -> MechanismReading``.
    ``update`` wraps compute with the temporal state machine that turns scores
    into building/active/confirmed/failed/resolved with start time and
    duration, so every mechanism carries the same lifecycle semantics."""

    name: str = "base"
    family: str = "base"
    requires: Sequence[str] = ()
    build_threshold: float = 0.35      # score at which the mechanism starts "building"
    active_threshold: float = 0.60     # score at which it is "active"
    confirm_s: float = 45.0            # seconds active before "confirmed"
    release_threshold: float = 0.25    # score below which an episode ends

    def __init__(self) -> None:
        self._state = "inactive"
        self._start: Optional[datetime] = None
        self._active_since: Optional[datetime] = None
        self._peak = 0.0
        self._start_mid: Optional[float] = None

    def compute(self, ms: MarketState, hist: StateHistory) -> MechanismReading:   # pragma: no cover - abstract
        raise NotImplementedError

    def outcome_positive(self, ms: MarketState) -> Optional[bool]:
        """Whether the episode resolved in the direction the mechanism implies
        (default: mid moved in the direction of ``evidence['direction']``).
        Subclasses override for mechanisms whose resolution is not a price move."""
        return None

    def update(self, ms: MarketState, hist: StateHistory) -> MechanismState:
        r = self.compute(ms, hist)
        score = max(0.0, min(1.0, float(r.score)))
        prev = self._state
        st = prev
        if prev in ("inactive", "failed", "resolved"):
            if score >= self.active_threshold:
                st, self._start, self._active_since = "active", ms.t, ms.t
                self._start_mid = ms.mid
            elif score >= self.build_threshold:
                st, self._start = "building", ms.t
                self._start_mid = ms.mid
            else:
                st, self._start, self._active_since = "inactive", None, None
        elif prev == "building":
            if score >= self.active_threshold:
                st, self._active_since = "active", ms.t
            elif score < self.release_threshold:
                st, self._start, self._start_mid = "inactive", None, None
        elif prev in ("active", "confirmed"):
            if score < self.release_threshold:
                st = self._resolve(ms, r)
                self._active_since = None
            elif prev == "active" and self._active_since is not None and \
                    (ms.t - self._active_since).total_seconds() >= self.confirm_s:
                st = "confirmed"
        self._state = st
        self._peak = max(self._peak, score) if st not in ("inactive",) else 0.0
        dur = (ms.t - self._start).total_seconds() if self._start else 0.0
        ev = dict(r.evidence)
        ev["peak_score"] = round(self._peak, 4)
        if self._start_mid is not None and ms.mid is not None:
            ev["mid_change_since_start"] = ms.mid - self._start_mid
        return MechanismState(name=self.name, family=self.family, score=score, state=st,
                              start_time=self._start, duration_s=dur, evidence=ev, baseline=dict(r.baseline))

    def _resolve(self, ms: MarketState, r: MechanismReading) -> str:
        direction = r.evidence.get("direction")
        pos = self.outcome_positive(ms)
        if pos is None and direction in (1, -1) and self._start_mid is not None and ms.mid is not None:
            pos = (ms.mid - self._start_mid) * direction > 0
        if pos is None:
            return "resolved"
        return "resolved" if pos else "failed"


REGISTRY: Dict[str, Type[Mechanism]] = {}


def register(cls: Type[Mechanism]) -> Type[Mechanism]:
    REGISTRY[cls.name] = cls
    return cls


def all_mechanisms() -> List[Mechanism]:
    return [cls() for cls in REGISTRY.values()]
