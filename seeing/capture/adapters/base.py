"""Adapter contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol

from ...truth import CANONICAL_FIELDS, Truth, TruthMap, merge_truth, not_observable, observed
from ..http_client import Fetched


@dataclass
class Parsed:
    """Normalized content of one raw payload. ``frames`` is a list of dicts —
    one per instrument (or one for market-wide payloads). ``truth`` says which
    canonical fields the source observed. ``problems`` records anything the
    parser could not read; the raw bytes stay in the store regardless."""

    source: str
    frames: List[Dict[str, Any]] = field(default_factory=list)
    truth: TruthMap = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)


class SourceAdapter(Protocol):
    name: str
    kind: str            # "book" | "watch" | "tape" | "reference" | "market" | "block" | "l1"
    observes: Iterable[str]

    def fetch(self, key: Optional[str] = None) -> Fetched: ...
    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed: ...


def capability_map(observes: Iterable[str], inferred: Iterable[str] = ()) -> TruthMap:
    """Full truth map over the canonical vocabulary for one source."""
    base = not_observable(CANONICAL_FIELDS)
    inf = {f: Truth.INFERRED for f in inferred}
    return merge_truth(base, inf, observed(observes))
