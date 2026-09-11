"""Mechanical bridges from existing repo data surfaces into relation replay.

Nothing in this module computes a feature, signal, score, threshold or relation.
It only preserves fields already present in the repo's raw/normalized public
observations and carries their source identity into the intent watcher.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from .relation_replay import ReplayEvent


# Only observed market values enter ``data``.  Source/entity/time/order and
# provenance metadata stay on ReplayEvent/ObservationPacket so MDL cannot learn
# a fake relation from identifiers about the feed itself.
_EVENT_VALUE_FIELDS = (
    "side",
    "price",
    "qty",
    "level",
    "order_count",
    "aggressor",
    "status",
)


def tower_event_to_replay(
    event: Any,
    *,
    order: Optional[int] = None,
    advance: bool = False,
    observation_id: Optional[str] = None,
) -> ReplayEvent:
    """Convert one ``tower.events.Event`` without inventing any field.

    ``payload`` is preserved exactly as the normalized event carries it.  A
    market-wide event (``symbol is None``) becomes global context and can never
    advance an entity intent horizon.  Entity events may be marked ``advance``
    only by the caller that owns the authoritative anchor cadence.
    """
    symbol = getattr(event, "symbol", None)
    scope = "global" if symbol is None else "entity"
    if scope == "global":
        advance = False

    data: Dict[str, Any] = {}
    for name in _EVENT_VALUE_FIELDS:
        value = getattr(event, name, None)
        if value is not None:
            data[name] = value

    payload = getattr(event, "payload", None)
    if isinstance(payload, Mapping):
        data["payload"] = dict(payload)

    t_recv = getattr(event, "t_recv")
    time = t_recv.isoformat() if hasattr(t_recv, "isoformat") else str(t_recv)
    seq_local = int(getattr(event, "seq_local", 0))
    use_order = seq_local if order is None else int(order)

    return ReplayEvent(
        time=time,
        order=use_order,
        source=str(getattr(event, "source")),
        entity="" if symbol is None else str(symbol),
        scope=scope,
        advance=bool(advance),
        observation_id=observation_id,
        data=data,
    )


def raw_row_to_replay(
    row: Mapping[str, Any],
    *,
    time: Any,
    order: int,
    source: str,
    entity: str,
    value_fields: Optional[Sequence[str]] = None,
    advance: bool = True,
    observation_id: Optional[str] = None,
) -> ReplayEvent:
    """Convert an already point-in-time historical row mechanically.

    If ``value_fields`` is omitted, every row field is preserved.  The function
    does not sort, fill, interpolate, derive, or infer timestamps.
    """
    if value_fields is None:
        data = {str(k): v for k, v in row.items()}
    else:
        data = {str(k): row[k] for k in value_fields if k in row}
    return ReplayEvent(
        time=str(time),
        order=int(order),
        source=str(source),
        entity=str(entity),
        scope="entity",
        advance=bool(advance),
        observation_id=observation_id,
        data=data,
    )


__all__ = ["raw_row_to_replay", "tower_event_to_replay"]
