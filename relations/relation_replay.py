"""Exact causal replay for the intent-native relation watcher.

The stream contains raw observations only.  There is no outcome event and no
future label to reveal.  Intent cases become decidable inside RelationWatcher
only after the user's causal horizon has actually elapsed.

Seeking always rebuilds from event zero into a fresh watcher.  It never
teleports learned state from the future.
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
    source: str
    data: Dict[str, Any]
    entity: str = ""
    scope: str = "entity"
    advance: bool = True
    observation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayFrame:
    index: int
    time: str
    order: int
    source: str
    entity: str
    scope: str
    advance: bool
    observation_id: str
    input_data: Dict[str, Any]
    resolved_case_count: int
    intelligence_after_event: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HistoricalReplay:
    """Deterministic point-in-time replay over one authoritative raw stream."""

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
        packet = self.watcher.observe(
            time=event.time,
            order=event.order,
            source=event.source,
            data=event.data,
            entity=event.entity,
            scope=event.scope,
            advance=event.advance,
            observation_id=event.observation_id,
        )
        frame = ReplayFrame(
            index=self.cursor,
            time=event.time,
            order=event.order,
            source=event.source,
            entity=event.entity,
            scope=event.scope,
            advance=event.advance,
            observation_id=packet.observation_id,
            input_data=_copy(event.data),
            resolved_case_count=len(self.watcher.cases),
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
        if index < 0 or index > len(self.events):
            raise IndexError(index)
        self.reset(watcher=fresh_watcher)
        while self.cursor < index:
            if self.step() is None:
                break

    def status(self) -> Dict[str, Any]:
        return {
            "cursor": self.cursor,
            "events_total": len(self.events),
            "complete": self.cursor >= len(self.events),
            "resolved_cases": len(self.watcher.cases),
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
            "resolved_cases": len(self.watcher.cases),
            "output": path,
        }


def events_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    time_col: str,
    order_col: str,
    source_col: str,
    entity_col: Optional[str] = None,
    scope_col: Optional[str] = None,
    advance_col: Optional[str] = None,
    observation_id_col: Optional[str] = None,
    excluded_cols: Sequence[str] = (),
) -> List[ReplayEvent]:
    """Mechanical row -> raw replay event conversion.

    No feature engineering, sorting, target creation, or inferred timing occurs
    here.  Input rows must already be in authoritative causal order.
    """
    excluded = {time_col, order_col, source_col, *excluded_cols}
    for optional in (entity_col, scope_col, advance_col, observation_id_col):
        if optional:
            excluded.add(optional)

    events: List[ReplayEvent] = []
    for row in rows:
        data = {str(k): _copy(v) for k, v in row.items() if k not in excluded}
        events.append(
            ReplayEvent(
                time=str(row[time_col]),
                order=int(row[order_col]),
                source=str(row[source_col]),
                entity=str(row[entity_col]) if entity_col else "",
                scope=str(row[scope_col]) if scope_col else "entity",
                advance=bool(row[advance_col]) if advance_col else True,
                observation_id=(str(row[observation_id_col]) if observation_id_col and row.get(observation_id_col) is not None else None),
                data=data,
            )
        )
    _validate_event_stream(events)
    return events


def _validate_event_stream(events: Sequence[ReplayEvent]) -> None:
    previous: Optional[tuple[str, int]] = None
    ids: set[str] = set()
    for i, event in enumerate(events):
        key = (str(event.time), int(event.order))
        if previous is not None and key <= previous:
            raise ValueError(
                f"event stream is not strictly causal at index {i}: {key!r} <= {previous!r}"
            )
        previous = key
        if event.scope not in {"entity", "global"}:
            raise ValueError(f"unsupported replay scope at index {i}: {event.scope!r}")
        if event.scope == "entity" and not event.entity:
            raise ValueError(f"entity event {i} requires entity")
        if event.scope == "global" and event.advance:
            raise ValueError(f"global event {i} may not advance an entity intent horizon")
        if event.observation_id:
            if event.observation_id in ids:
                raise ValueError(f"duplicate observation_id in replay: {event.observation_id}")
            ids.add(event.observation_id)


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default, allow_nan=False))


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    raise TypeError(f"unsupported evidence type: {type(value).__name__}")


__all__ = ["HistoricalReplay", "ReplayEvent", "ReplayFrame", "events_from_rows"]
