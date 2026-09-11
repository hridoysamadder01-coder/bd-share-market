"""Intent-driven raw relation discovery with no supplied outcome labels.

The user supplies an intent.  Raw public observations then arrive in causal
order.  The watcher keeps only information that has actually arrived, resolves
whether an earlier anchor satisfied the user's intent only when the intent's
future horizon has elapsed, and asks MDL which raw point-in-time fields shorten
the description of those intent occurrences.

HARD LOCK
=========
No market rule, indicator, trading pattern, hand-made feature combination,
fixed pair/triple search, top-k ranking, probability threshold, BUY/SELL logic,
or historical template exists here.

The ONLY relation acceptance criterion is Minimum Description Length (MDL).
Numeric boundaries come only from observed values.  Recursive tree paths are
whatever raw fields MDL selects; no pair/triple structure is predeclared.
There is no ``outcome`` packet and no caller-supplied outcome label.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .intent import IntentProgram


Json = Any
JsonMap = Dict[str, Any]


@dataclass(frozen=True)
class ObservationPacket:
    observation_id: str
    time: str
    order: int
    entity: str
    source: str
    scope: str
    advance: bool
    data: JsonMap
    data_sha256: str

    def to_dict(self) -> JsonMap:
        return asdict(self)


@dataclass(frozen=True)
class IntentCase:
    observation_id: str
    anchor_time: str
    resolved_time: str
    entity: str
    data: JsonMap
    intent_matched: bool
    intent_evidence: JsonMap

    def to_dict(self) -> JsonMap:
        return {
            "observation_id": self.observation_id,
            "anchor_time": self.anchor_time,
            "resolved_time": self.resolved_time,
            "entity": self.entity,
            "data": _json_copy(self.data),
            "intent_matched": self.intent_matched,
            "intent_evidence": _json_copy(self.intent_evidence),
        }


@dataclass(frozen=True)
class _StatePoint:
    observation_id: str
    time: str
    state: JsonMap


class MDLRelationDiscoverer:
    """Discover raw-data paths that compress occurrences of the user's intent."""

    VERSION = 2

    def __init__(self, intent: IntentProgram) -> None:
        self.intent = intent
        self._cases: List[IntentCase] = []
        self._tree: JsonMap = self._leaf([], field_count=0)
        self._field_order: List[str] = []

    def add_case(self, case: IntentCase) -> None:
        self._cases.append(case)
        self._rebuild()

    def snapshot(self) -> JsonMap:
        return {
            "criterion": "minimum_description_length",
            "version": self.VERSION,
            "intent": self.intent.to_dict(),
            "resolved_cases": len(self._cases),
            "raw_fields_seen": list(self._field_order),
            "relation_tree": _json_copy(self._tree),
            "relation_paths": _relation_paths(self._tree),
        }

    def _rebuild(self) -> None:
        fields = sorted({str(k) for case in self._cases for k in case.data.keys()})
        self._field_order = fields
        self._tree = self._build(list(range(len(self._cases))), fields)

    def _build(self, indexes: List[int], fields: List[str]) -> JsonMap:
        base = self._leaf(indexes, field_count=len(fields))
        if len(indexes) <= 1 or base["intent_true"] in {0, len(indexes)} or not fields:
            return base

        best: Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]], str]] = None
        for field in fields:
            candidate = self._best_split_for_field(indexes, field, len(fields))
            if candidate is None:
                continue
            total_bits, spec, branches = candidate
            if total_bits < base["mdl_bits"]:
                if best is None or total_bits < best[0]:
                    best = (total_bits, spec, branches, field)

        if best is None:
            return base

        total_bits, spec, branches, field = best
        remaining = [f for f in fields if f != field]
        children = [
            {"branch": name, "node": self._build(branch_indexes, remaining)}
            for name, branch_indexes in branches
        ]
        return {
            "type": "relation",
            "criterion": "minimum_description_length",
            "field": field,
            "split": spec,
            "cases": len(indexes),
            "mdl_unsplit_bits": base["mdl_bits"],
            "mdl_split_bits": total_bits,
            "description_gain_bits": base["mdl_bits"] - total_bits,
            "children": children,
        }

    def _best_split_for_field(
        self,
        indexes: List[int],
        field: str,
        field_count: int,
    ) -> Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]]]]:
        values = [(i, self._cases[i].data.get(field, _MISSING)) for i in indexes]
        nonmissing = [(i, v) for i, v in values if v is not _MISSING and v is not None]
        missing = [i for i, v in values if v is _MISSING or v is None]
        if not nonmissing:
            return None
        if all(_is_number(v) for _, v in nonmissing):
            return self._best_numeric_split(nonmissing, missing, field_count)
        return self._categorical_split(values, field_count)

    def _best_numeric_split(
        self,
        nonmissing: List[Tuple[int, Any]],
        missing: List[int],
        field_count: int,
    ) -> Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]]]]:
        ordered = sorted((float(v), i) for i, v in nonmissing)
        unique_count = 1 + sum(1 for j in range(1, len(ordered)) if ordered[j][0] != ordered[j - 1][0])
        if unique_count <= 1:
            return None
        candidate_count = unique_count - 1

        missing_true = sum(1 for i in missing if self._cases[i].intent_matched)
        missing_false = len(missing) - missing_true
        total_true = sum(1 for _, i in ordered if self._cases[i].intent_matched)
        total_false = len(ordered) - total_true
        prefix_true = 0
        prefix_false = 0

        best: Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]]]] = None
        candidate_index = -1
        for pos in range(len(ordered) - 1):
            _, idx = ordered[pos]
            if self._cases[idx].intent_matched:
                prefix_true += 1
            else:
                prefix_false += 1
            left_value = ordered[pos][0]
            right_value = ordered[pos + 1][0]
            if left_value == right_value:
                continue

            candidate_index += 1
            right_true = total_true - prefix_true
            right_false = total_false - prefix_false
            branch_count = 2 + (1 if missing else 0)
            bits = (
                1.0
                + _index_code_bits(field_count)
                + _index_code_bits(candidate_count)
                + _integer_code_bits(branch_count)
                + _empirical_binary_bits(prefix_true, prefix_false)
                + _empirical_binary_bits(right_true, right_false)
            )
            if missing:
                bits += _empirical_binary_bits(missing_true, missing_false)

            cut = (left_value + right_value) / 2.0
            if best is None or bits < best[0]:
                left = [i for v, i in ordered if v <= cut]
                right = [i for v, i in ordered if v > cut]
                branches: List[Tuple[str, List[int]]] = [
                    (f"<= {repr(cut)}", left),
                    (f"> {repr(cut)}", right),
                ]
                if missing:
                    branches.append(("MISSING", list(missing)))
                best = (
                    bits,
                    {
                        "kind": "numeric_boundary_from_observed_values",
                        "value": cut,
                        "candidate_count_from_data": candidate_count,
                    },
                    branches,
                )
        return best

    def _categorical_split(
        self,
        values: List[Tuple[int, Any]],
        field_count: int,
    ) -> Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]]]]:
        groups: Dict[str, List[int]] = {}
        exemplars: Dict[str, Any] = {}
        for i, value in values:
            key = "__MISSING__" if value is _MISSING or value is None else _canonical(value)
            groups.setdefault(key, []).append(i)
            exemplars.setdefault(key, None if key == "__MISSING__" else _json_copy(value))
        if len(groups) <= 1:
            return None

        ordered_keys = sorted(groups)
        branches = [
            ("MISSING" if key == "__MISSING__" else _canonical(exemplars[key]), groups[key])
            for key in ordered_keys
        ]
        bits = 1.0 + _index_code_bits(field_count) + _integer_code_bits(len(branches))
        for _, branch_indexes in branches:
            t = sum(1 for i in branch_indexes if self._cases[i].intent_matched)
            bits += _empirical_binary_bits(t, len(branch_indexes) - t)
        return (
            bits,
            {"kind": "exact_observed_value_partition", "values": [exemplars[k] for k in ordered_keys]},
            branches,
        )

    def _leaf(self, indexes: List[int], field_count: int) -> JsonMap:
        true_count = sum(1 for i in indexes if self._cases[i].intent_matched)
        false_count = len(indexes) - true_count
        return {
            "type": "leaf",
            "criterion": "minimum_description_length",
            "cases": len(indexes),
            "intent_true": true_count,
            "intent_false": false_count,
            "mdl_bits": 1.0 + _empirical_binary_bits(true_count, false_count),
        }


class RelationWatcher:
    """Causal raw-state watcher + autonomous intent relation discoverer.

    ``advance=False`` lets any raw/public source update the point-in-time context
    without creating a new anchor.  ``advance=True`` marks an authoritative
    anchor stream.  This distinction is wiring, not a research rule: it prevents
    unrelated source polling cadence from silently redefining the user's intent
    horizon.
    """

    STATE_VERSION = 4

    def __init__(
        self,
        *,
        intent: IntentProgram,
        forbidden_keys: Sequence[str] = (),
    ) -> None:
        self.intent = intent
        self.forbidden_keys = {str(k) for k in forbidden_keys}
        self.observations: Dict[str, ObservationPacket] = {}
        self.global_state: JsonMap = {}
        self.entity_state: Dict[str, JsonMap] = {}
        self.entity_history: Dict[str, List[_StatePoint]] = {}
        self.cases: List[IntentCase] = []
        self.discoverer = MDLRelationDiscoverer(intent)
        self._sequence = 0

    def observe(
        self,
        *,
        time: Any,
        order: int,
        source: Any,
        data: Mapping[str, Any],
        entity: Any = "",
        scope: str = "entity",
        advance: bool = True,
        observation_id: Optional[str] = None,
    ) -> ObservationPacket:
        if scope not in {"entity", "global"}:
            raise ValueError("scope must be 'entity' or 'global'")
        clean = _json_copy(dict(data))
        _guard_forbidden_keys(clean, self.forbidden_keys)
        t = str(time)
        src = str(source)
        ent = str(entity)
        if scope == "entity" and not ent:
            raise ValueError("entity observation requires entity")
        if scope == "global" and advance:
            raise ValueError("global context may not advance an entity intent horizon")

        if observation_id is None:
            observation_id = "obs:" + _hash_json(
                {
                    "time": t,
                    "order": int(order),
                    "entity": ent,
                    "scope": scope,
                    "source": src,
                    "sequence": self._sequence,
                    "data": clean,
                }
            )[:24]
        if observation_id in self.observations:
            raise ValueError(f"duplicate observation_id: {observation_id}")

        packet = ObservationPacket(
            observation_id=observation_id,
            time=t,
            order=int(order),
            entity=ent,
            source=src,
            scope=scope,
            advance=bool(advance),
            data=clean,
            data_sha256=_hash_json(clean),
        )
        self.observations[observation_id] = packet
        self._sequence += 1

        flat = _flatten(clean, prefix=("global." if scope == "global" else "") + src)
        if scope == "global":
            self.global_state.update(flat)
            return packet

        current = self.entity_state.setdefault(ent, {})
        current.update(flat)
        if not advance:
            return packet

        snapshot = dict(self.global_state)
        snapshot.update(current)
        history = self.entity_history.setdefault(ent, [])
        history.append(_StatePoint(observation_id=observation_id, time=t, state=_json_copy(snapshot)))
        self._resolve_newly_decidable(ent)
        return packet

    def _resolve_newly_decidable(self, entity: str) -> None:
        history = self.entity_history[entity]
        h = self.intent.horizon_steps
        current_index = len(history) - 1
        anchor_index = current_index - h
        if anchor_index < 0:
            return

        anchor = history[anchor_index]
        future = history[anchor_index + 1 : current_index + 1]
        self.intent.validate_against_schema(anchor.state.keys())
        for row in future:
            self.intent.validate_against_schema(row.state.keys())
        result = self.intent.evaluate(anchor=anchor.state, window=[x.state for x in future])
        case = IntentCase(
            observation_id=anchor.observation_id,
            anchor_time=anchor.time,
            resolved_time=history[current_index].time,
            entity=entity,
            data=_json_copy(anchor.state),
            intent_matched=result.matched,
            intent_evidence=result.evidence,
        )
        self.cases.append(case)
        self.discoverer.add_case(case)

    def intelligence_snapshot(self) -> JsonMap:
        return self.discoverer.snapshot()

    def audit_snapshot(self) -> JsonMap:
        return {
            "state_version": self.STATE_VERSION,
            "criterion": "minimum_description_length",
            "intent": self.intent.to_dict(),
            "observation_count": len(self.observations),
            "resolved_case_count": len(self.cases),
            "forbidden_keys": sorted(self.forbidden_keys),
            "observations": {k: v.to_dict() for k, v in self.observations.items()},
            "cases": [c.to_dict() for c in self.cases],
            "intelligence": self.intelligence_snapshot(),
        }

    def save_audit(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                self.audit_snapshot(),
                fh,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        os.replace(tmp, path)


class _Missing:
    pass


_MISSING = _Missing()


def _flatten(value: Any, prefix: str) -> JsonMap:
    out: JsonMap = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key in sorted(node, key=lambda x: str(x)):
                child = node[key]
                child_path = f"{path}.{key}" if path else str(key)
                walk(child, child_path)
            return
        if isinstance(node, (list, tuple)):
            for i, child in enumerate(node):
                walk(child, f"{path}[{i}]")
            return
        if node is None or isinstance(node, (str, int, float, bool)):
            out[path] = _json_copy(node)

    walk(value, prefix)
    return out


def _relation_paths(tree: JsonMap) -> List[JsonMap]:
    out: List[JsonMap] = []

    def walk(node: JsonMap, conditions: List[JsonMap]) -> None:
        if node.get("type") == "leaf":
            out.append(
                {
                    "conditions": _json_copy(conditions),
                    "cases": node.get("cases", 0),
                    "intent_true": node.get("intent_true", 0),
                    "intent_false": node.get("intent_false", 0),
                }
            )
            return
        field = node.get("field")
        for child in node.get("children", []):
            walk(
                child["node"],
                conditions + [{"field": field, "branch": child["branch"], "split": node.get("split")}],
            )

    walk(tree, [])
    return out


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _canonical(value: Any) -> str:
    return json.dumps(_json_copy(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_copy(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
            allow_nan=False,
        )
    )


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    raise TypeError(f"unsupported evidence type: {type(value).__name__}")


def _hash_json(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _guard_forbidden_keys(value: Any, forbidden: set[str], path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            here = f"{path}.{name}" if path else name
            if name in forbidden:
                raise ValueError(f"forbidden/future field reached watcher input: {here}")
            _guard_forbidden_keys(child, forbidden, here)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _guard_forbidden_keys(child, forbidden, f"{path}[{i}]")


def _empirical_binary_bits(true_count: int, false_count: int) -> float:
    n = true_count + false_count
    if n <= 1 or true_count == 0 or false_count == 0:
        return 0.0
    bits = 0.0
    for count in (true_count, false_count):
        if count:
            p = count / n
            bits -= count * math.log2(p)
    return _integer_code_bits(2) + bits


def _integer_code_bits(n: int) -> float:
    n = max(1, int(n))
    return 1.0 + math.log2(n)


def _index_code_bits(count: int) -> float:
    count = max(1, int(count))
    return math.log2(count) if count > 1 else 0.0


__all__ = [
    "IntentCase",
    "MDLRelationDiscoverer",
    "ObservationPacket",
    "RelationWatcher",
]
