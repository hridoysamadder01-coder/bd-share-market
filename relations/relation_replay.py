"""Exact causal replay for relation_watcher.py.

HARD LOCK
=========
This file does not create strategy logic or discovery rules.
It replays the exact event stream in recorded order.

Every event has:
- time
- order  : exact microstep order inside the same timestamp
- kind   : observation | outcome

An outcome is invisible until its own event arrives.
Seeking replays every prior event into a fresh watcher; it never teleports
learned state from the future.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import time as _time
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from .relation_watcher import RelationWatcher


@dataclass(frozen=True)
class ReplayEvent:
    time: str
    order: int
    kind: str
    observation_id: str
    entity: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    outcome: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayFrame:
    index: int
    time: str
    order: int
    kind: str
    observation_id: str
    entity: Optional[str]
    input_data: Optional[Dict[str, Any]]
    revealed_outcome: Any
    intelligence_after_event: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HistoricalReplay:
    """Deterministic point-in-time replay over one authoritative event stream."""

    def __init__(
        self,
        events: Sequence[ReplayEvent],
        *,
        watcher: RelationWatcher,
    ) -> None:
        self.events = list(events)
        self.watcher = watcher
        _validate_event_stream(self.events)
        self.cursor = 0

    def reset(self, *, watcher: RelationWatcher) -> None:
        self.watcher = watcher
        self.cursor = 0

    def step(self) -> Optional[ReplayFrame]:
        if self.cursor >= len(self.events):
            return None

        event = self.events[self.cursor]

        if event.kind == "observation":
            if event.entity is None or event.data is None:
                raise ValueError(
                    f"observation event {self.cursor} requires entity and data"
                )
            self.watcher.observe(
                time=event.time,
                entity=event.entity,
                data=event.data,
                observation_id=event.observation_id,
            )
            revealed_outcome = None

        elif event.kind == "outcome":
            self.watcher.settle(
                observation_id=event.observation_id,
                ready_time=event.time,
                outcome=event.outcome,
            )
            revealed_outcome = event.outcome

        else:
            raise ValueError(f"unsupported replay event kind: {event.kind!r}")

        frame = ReplayFrame(
            index=self.cursor,
            time=event.time,
            order=event.order,
            kind=event.kind,
            observation_id=event.observation_id,
            entity=event.entity,
            input_data=_copy(event.data) if event.data is not None else None,
            revealed_outcome=_copy(revealed_outcome),
            intelligence_after_event=self.watcher.intelligence_snapshot(),
        )
        self.cursor += 1
        return frame

    def play(
        self,
        *,
        frames_per_second: float = 0.0,
        max_frames: Optional[int] = None,
    ) -> Iterator[ReplayFrame]:
        emitted = 0
        while self.cursor < len(self.events):
            if max_frames is not None and emitted >= max_frames:
                break
            frame = self.step()
            if frame is None:
                break
            yield frame
            emitted += 1
            if frames_per_second > 0:
                _time.sleep(1.0 / frames_per_second)

    def seek(
        self,
        index: int,
        *,
        fresh_watcher: RelationWatcher,
    ) -> None:
        """Seek causally by rebuilding state from event zero."""
        if index < 0 or index > len(self.events):
            raise IndexError(index)

        self.reset(watcher=fresh_watcher)
        while self.cursor < index:
            frame = self.step()
            if frame is None:
                break

    def status(self) -> Dict[str, Any]:
        return {
            "cursor": self.cursor,
            "events_total": len(self.events),
            "complete": self.cursor >= len(self.events),
        }

    def run_to_jsonl(
        self,
        path: str,
        *,
        frames_per_second: float = 0.0,
        max_frames: Optional[int] = None,
    ) -> Dict[str, Any]:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        written = 0

        with open(tmp, "w", encoding="utf-8") as fh:
            for frame in self.play(
                frames_per_second=frames_per_second,
                max_frames=max_frames,
            ):
                fh.write(
                    json.dumps(
                        frame.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=_json_default,
                        allow_nan=False,
                    )
                    + "\n"
                )
                written += 1

        os.replace(tmp, path)
        return {
            "frames_written": written,
            "cursor": self.cursor,
            "events_total": len(self.events),
            "output": path,
        }


def events_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    time_col: str,
    order_col: str,
    kind_col: str,
    observation_id_col: str,
    entity_col: str,
    outcome_col: str,
    excluded_cols: Sequence[str] = (),
) -> List[ReplayEvent]:
    """Mechanical row -> event conversion.

    No feature engineering, no sorting, no inferred timing.
    Input rows must already be in authoritative causal order.
    """
    excluded = {
        time_col,
        order_col,
        kind_col,
        observation_id_col,
        entity_col,
        outcome_col,
        *map(str, excluded_cols),
    }

    events: List[ReplayEvent] = []
    for i, row in enumerate(rows):
        kind = str(row.get(kind_col) or "")
        time_value = row.get(time_col)
        order_value = row.get(order_col)
        oid = row.get(observation_id_col)

        if time_value in (None, ""):
            raise ValueError(f"row {i}: missing {time_col}")
        if order_value in (None, ""):
            raise ValueError(f"row {i}: missing {order_col}")
        if oid in (None, ""):
            raise ValueError(f"row {i}: missing {observation_id_col}")
        if kind not in ("observation", "outcome"):
            raise ValueError(f"row {i}: invalid {kind_col}={kind!r}")

        if kind == "observation":
            entity = row.get(entity_col)
            if entity in (None, ""):
                raise ValueError(f"row {i}: observation missing {entity_col}")
            data = {
                str(k): _plain(v)
                for k, v in row.items()
                if str(k) not in excluded
            }
            outcome = None
        else:
            entity = None
            data = None
            outcome = _plain(row.get(outcome_col))

        events.append(
            ReplayEvent(
                time=str(time_value),
                order=int(order_value),
                kind=kind,
                observation_id=str(oid),
                entity=None if entity is None else str(entity),
                data=data,
                outcome=outcome,
            )
        )

    _validate_event_stream(events)
    return events


def _validate_event_stream(events: Sequence[ReplayEvent]) -> None:
    previous = None
    seen_observations = set()

    for i, event in enumerate(events):
        key = (event.time, int(event.order))

        if previous is not None and key <= previous:
            raise ValueError(
                f"event stream is not strictly causal at index {i}: "
                f"{key!r} <= {previous!r}"
            )
        previous = key

        if event.kind == "observation":
            if event.observation_id in seen_observations:
                raise ValueError(
                    f"duplicate observation_id at event {i}: {event.observation_id}"
                )
            seen_observations.add(event.observation_id)

        elif event.kind == "outcome":
            if event.observation_id not in seen_observations:
                raise ValueError(
                    f"outcome arrived before its observation at event {i}: "
                    f"{event.observation_id}"
                )

        else:
            raise ValueError(f"invalid event kind at index {i}: {event.kind!r}")


def _copy(v: Any) -> Any:
    return json.loads(
        json.dumps(
            v,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
            allow_nan=False,
        )
    )


def _plain(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v


def _json_default(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    raise TypeError(f"unsupported replay value: {type(v).__name__}")


__all__ = [
    "HistoricalReplay",
    "ReplayEvent",
    "ReplayFrame",
    "events_from_rows",
]
