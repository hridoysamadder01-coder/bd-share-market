"""Preset-free outcome relation discovery using one domain-neutral criterion: MDL.

HARD LOCK
=========
No market rule, indicator, trading pattern, hand-made feature combination,
fixed pair/triple search, top-k ranking, probability threshold, BUY/SELL logic,
or historical template exists in this file.

The ONLY relation criterion is Minimum Description Length (MDL):
a relation is accepted only when describing the observed outcomes using a
data-derived partition of raw point-in-time fields shortens the total
description length versus leaving that node unsplit.

Important:
- Candidate split values come only from observed raw values.
- No fixed cutoff is used. "Strictly shorter MDL" is the entire acceptance law.
- Relations can involve any number of fields through recursive paths.
- Outcomes update the discoverer only when they are explicitly revealed.
- Raw observations are immutable and hashed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


Json = Any
JsonMap = Dict[str, Any]


@dataclass(frozen=True)
class ObservationPacket:
    observation_id: str
    time: str
    entity: str
    data: JsonMap
    data_sha256: str

    def to_dict(self) -> JsonMap:
        return asdict(self)


@dataclass(frozen=True)
class OutcomePacket:
    observation_id: str
    ready_time: str
    outcome: Json
    outcome_sha256: str

    def to_dict(self) -> JsonMap:
        return asdict(self)


@dataclass(frozen=True)
class SettledCase:
    observation_id: str
    time: str
    entity: str
    data: JsonMap
    outcome: Json
    outcome_key: str

    def to_dict(self) -> JsonMap:
        return {
            "observation_id": self.observation_id,
            "time": self.time,
            "entity": self.entity,
            "data": _json_copy(self.data),
            "outcome": _json_copy(self.outcome),
            "outcome_key": self.outcome_key,
        }


class MDLRelationDiscoverer:
    """Discover raw-data relations to outcomes with MDL and nothing else.

    The model is rebuilt from all *revealed* settled cases whenever a new
    outcome arrives. This makes the state at every replay instant reproducible.

    Tree paths are not predeclared combinations. They are whatever sequence of
    raw fields MDL itself selects from the evidence available at that instant.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._cases: List[SettledCase] = []
        self._tree: JsonMap = self._leaf([], field_count=0)
        self._field_order: List[str] = []

    def add_case(self, case: SettledCase) -> None:
        self._cases.append(case)
        self._rebuild()

    def snapshot(self) -> JsonMap:
        return {
            "criterion": "minimum_description_length",
            "version": self.VERSION,
            "settled_cases": len(self._cases),
            "raw_fields_seen": list(self._field_order),
            "relation_tree": _json_copy(self._tree),
        }

    # ------------------------------ model build ------------------------------

    def _rebuild(self) -> None:
        fields = sorted(
            {
                str(k)
                for case in self._cases
                for k in case.data.keys()
            }
        )
        self._field_order = fields
        indexes = list(range(len(self._cases)))
        self._tree = self._build(indexes, fields)

    def _build(self, indexes: List[int], fields: List[str]) -> JsonMap:
        base = self._leaf(indexes, field_count=len(fields))
        if len(indexes) <= 1 or len(base["outcomes"]) <= 1 or not fields:
            return base

        best: Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]], str]] = None

        for field in fields:
            candidate = self._best_split_for_field(indexes, field, len(fields))
            if candidate is None:
                continue
            total_bits, spec, branches = candidate
            # No arbitrary epsilon or threshold: only exact strict MDL improvement.
            if total_bits < base["mdl_bits"]:
                if best is None or total_bits < best[0]:
                    best = (total_bits, spec, branches, field)

        if best is None:
            return base

        total_bits, spec, branches, field = best
        remaining = [f for f in fields if f != field]
        children = []
        for branch_name, branch_indexes in branches:
            children.append(
                {
                    "branch": branch_name,
                    "node": self._build(branch_indexes, remaining),
                }
            )

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
            return self._best_numeric_split(indexes, field, nonmissing, missing, field_count)

        return self._categorical_split(indexes, field, values, field_count)

    def _best_numeric_split(
        self,
        indexes: List[int],
        field: str,
        nonmissing: List[Tuple[int, Any]],
        missing: List[int],
        field_count: int,
    ) -> Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]]]]:
        unique = sorted({float(v) for _, v in nonmissing})
        if len(unique) <= 1:
            return None

        # Every boundary comes from the observed values themselves. No preset cutoff.
        cuts = [(unique[i] + unique[i + 1]) / 2.0 for i in range(len(unique) - 1)]
        best: Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]]]] = None

        for cut_index, cut in enumerate(cuts):
            left = [i for i, v in nonmissing if float(v) <= cut]
            right = [i for i, v in nonmissing if float(v) > cut]
            if not left or not right:
                continue

            branches: List[Tuple[str, List[int]]] = [
                (f"<= {repr(cut)}", left),
                (f"> {repr(cut)}", right),
            ]
            if missing:
                branches.append(("MISSING", list(missing)))

            bits = self._split_mdl(
                indexes=indexes,
                branches=branches,
                field_count=field_count,
                candidate_count=len(cuts),
                candidate_index=cut_index,
            )
            spec = {
                "kind": "numeric_boundary_from_observed_values",
                "value": cut,
                "candidate_count_from_data": len(cuts),
            }
            if best is None or bits < best[0]:
                best = (bits, spec, branches)

        return best

    def _categorical_split(
        self,
        indexes: List[int],
        field: str,
        values: List[Tuple[int, Any]],
        field_count: int,
    ) -> Optional[Tuple[float, JsonMap, List[Tuple[str, List[int]]]]]:
        groups: Dict[str, List[int]] = {}
        raw_values: Dict[str, Any] = {}

        for i, v in values:
            key = "__MISSING__" if v is _MISSING or v is None else _canonical(v)
            groups.setdefault(key, []).append(i)
            raw_values.setdefault(key, None if key == "__MISSING__" else _json_copy(v))

        if len(groups) <= 1:
            return None

        ordered = sorted(groups)
        branches = [
            (
                "MISSING" if key == "__MISSING__" else _canonical(raw_values[key]),
                groups[key],
            )
            for key in ordered
        ]

        bits = self._split_mdl(
            indexes=indexes,
            branches=branches,
            field_count=field_count,
            candidate_count=1,
            candidate_index=0,
        )
        spec = {
            "kind": "exact_observed_value_partition",
            "values": [raw_values[k] for k in ordered],
        }
        return bits, spec, branches

    def _split_mdl(
        self,
        *,
        indexes: List[int],
        branches: List[Tuple[str, List[int]]],
        field_count: int,
        candidate_count: int,
        candidate_index: int,
    ) -> float:
        # Structural code: leaf/split flag + field identity + candidate identity
        # + number of branches. There are no tunable coefficients.
        structure_bits = (
            1.0
            + _index_code_bits(field_count)
            + _index_code_bits(candidate_count)
            + _integer_code_bits(len(branches))
        )

        outcome_bits = 0.0
        for _, branch_indexes in branches:
            outcome_bits += _empirical_outcome_bits(
                [self._cases[i].outcome_key for i in branch_indexes]
            )

        # Exact branch membership itself does not get encoded again; the split
        # rule and raw field values determine membership deterministically.
        return structure_bits + outcome_bits

    def _leaf(self, indexes: List[int], field_count: int) -> JsonMap:
        keys = [self._cases[i].outcome_key for i in indexes] if indexes else []
        counts: Dict[str, int] = {}
        exemplars: Dict[str, Any] = {}
        for i in indexes:
            key = self._cases[i].outcome_key
            counts[key] = counts.get(key, 0) + 1
            exemplars.setdefault(key, _json_copy(self._cases[i].outcome))

        return {
            "type": "leaf",
            "criterion": "minimum_description_length",
            "cases": len(indexes),
            "mdl_bits": 1.0 + _empirical_outcome_bits(keys),
            "outcomes": [
                {
                    "value": exemplars[key],
                    "count": counts[key],
                }
                for key in sorted(counts)
            ],
        }


class RelationWatcher:
    """Point-in-time evidence watcher + autonomous MDL relation discoverer."""

    STATE_VERSION = 3

    def __init__(
        self,
        *,
        forbidden_keys: Sequence[str] = (),
    ) -> None:
        self.forbidden_keys = {str(k) for k in forbidden_keys}
        self.observations: Dict[str, ObservationPacket] = {}
        self.outcomes: Dict[str, OutcomePacket] = {}
        self.discoverer = MDLRelationDiscoverer()
        self._sequence = 0

    def observe(
        self,
        *,
        time: Any,
        entity: Any,
        data: Mapping[str, Any],
        observation_id: Optional[str] = None,
    ) -> ObservationPacket:
        clean = _json_copy(dict(data))
        _guard_forbidden_keys(clean, self.forbidden_keys)

        t = str(time)
        e = str(entity)
        if observation_id is None:
            observation_id = "obs:" + _hash_json(
                {
                    "time": t,
                    "entity": e,
                    "sequence": self._sequence,
                    "data": clean,
                }
            )[:24]

        if observation_id in self.observations:
            raise ValueError(f"duplicate observation_id: {observation_id}")

        packet = ObservationPacket(
            observation_id=observation_id,
            time=t,
            entity=e,
            data=clean,
            data_sha256=_hash_json(clean),
        )
        self.observations[observation_id] = packet
        self._sequence += 1
        return packet

    def settle(
        self,
        *,
        observation_id: str,
        ready_time: Any,
        outcome: Any,
    ) -> OutcomePacket:
        if observation_id not in self.observations:
            raise KeyError(f"unknown observation_id: {observation_id}")
        if observation_id in self.outcomes:
            raise ValueError(f"outcome already settled: {observation_id}")

        clean_outcome = _json_copy(outcome)
        packet = OutcomePacket(
            observation_id=observation_id,
            ready_time=str(ready_time),
            outcome=clean_outcome,
            outcome_sha256=_hash_json(clean_outcome),
        )
        self.outcomes[observation_id] = packet

        obs = self.observations[observation_id]
        self.discoverer.add_case(
            SettledCase(
                observation_id=observation_id,
                time=obs.time,
                entity=obs.entity,
                data=_json_copy(obs.data),
                outcome=clean_outcome,
                outcome_key=_canonical(clean_outcome),
            )
        )
        return packet

    def intelligence_snapshot(self) -> JsonMap:
        return self.discoverer.snapshot()

    def audit_snapshot(self) -> JsonMap:
        return {
            "state_version": self.STATE_VERSION,
            "criterion": "minimum_description_length",
            "observation_count": len(self.observations),
            "outcome_count": len(self.outcomes),
            "forbidden_keys": sorted(self.forbidden_keys),
            "observations": {k: v.to_dict() for k, v in self.observations.items()},
            "outcomes": {k: v.to_dict() for k, v in self.outcomes.items()},
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


# -------------------------------- utilities --------------------------------

class _Missing:
    pass


_MISSING = _Missing()


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _canonical(v: Any) -> str:
    return json.dumps(
        _json_copy(v),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_copy(v: Any) -> Any:
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


def _json_default(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    raise TypeError(f"unsupported evidence type: {type(v).__name__}")


def _hash_json(v: Any) -> str:
    return sha256(_canonical(v).encode("utf-8")).hexdigest()


def _guard_forbidden_keys(value: Any, forbidden: set[str], path: str = "") -> None:
    if isinstance(value, dict):
        for k, child in value.items():
            key = str(k)
            here = f"{path}.{key}" if path else key
            if key in forbidden:
                raise ValueError(f"forbidden/future field reached watcher input: {here}")
            _guard_forbidden_keys(child, forbidden, here)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _guard_forbidden_keys(child, forbidden, f"{path}[{i}]")


def _empirical_outcome_bits(keys: Sequence[str]) -> float:
    n = len(keys)
    if n <= 1:
        return 0.0

    counts: Dict[str, int] = {}
    for k in keys:
        counts[k] = counts.get(k, 0) + 1

    bits = 0.0
    for count in counts.values():
        p = count / n
        bits -= count * math.log2(p)

    # Encode how many distinct outcome values exist at the node.
    return _integer_code_bits(len(counts)) + bits


def _integer_code_bits(n: int) -> float:
    """Self-delimiting positive-integer codelength, no tunable parameter."""
    n = max(1, int(n))
    return 1.0 + math.log2(n)


def _index_code_bits(count: int) -> float:
    count = max(1, int(count))
    return math.log2(count) if count > 1 else 0.0


__all__ = [
    "MDLRelationDiscoverer",
    "ObservationPacket",
    "OutcomePacket",
    "RelationWatcher",
    "SettledCase",
]
