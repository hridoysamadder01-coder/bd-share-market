"""User-intent program evaluated only from causally revealed raw observations.

This module does not define market rules, indicators, thresholds, strategies, or
relation shapes.  It only gives the relation engine a small, auditable language
for expressing the event the USER asked for.  The relation itself is discovered
elsewhere from raw point-in-time data.

There is intentionally no silent natural-language guesser here.  A caller may
compile a user's words into this generic program, but every referenced field has
to exist in the observed raw schema and every constant must come from the user's
intent.  If an intent cannot be grounded, the caller must fail closed rather
than inventing meaning.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, Mapping, Sequence


Json = Any
JsonMap = Dict[str, Any]

_ALLOWED = {
    "const",
    "field",
    "delta",
    "pct_change",
    "window_min",
    "window_max",
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "and",
    "or",
    "not",
}


@dataclass(frozen=True)
class IntentResult:
    matched: bool
    evidence: JsonMap


@dataclass(frozen=True)
class IntentProgram:
    """An auditable grounding of the user's intent against raw field paths.

    ``horizon_steps`` is not a discovery threshold.  It is part of the user's
    requested event definition: how many later observations are required before
    the intent can be decided for an anchor observation.
    """

    text: str
    horizon_steps: int
    expression: JsonMap

    def __post_init__(self) -> None:
        if not str(self.text).strip():
            raise ValueError("intent text must not be empty")
        if int(self.horizon_steps) < 1:
            raise ValueError("horizon_steps must be >= 1")
        _validate_expr(self.expression)

    def referenced_fields(self) -> tuple[str, ...]:
        out: set[str] = set()
        _collect_fields(self.expression, out)
        return tuple(sorted(out))

    def validate_against_schema(self, fields: Sequence[str]) -> None:
        available = {str(x) for x in fields}
        missing = [p for p in self.referenced_fields() if p not in available]
        if missing:
            raise ValueError(
                "intent references fields not present in raw schema: "
                + ", ".join(missing)
            )

    def evaluate(
        self,
        *,
        anchor: Mapping[str, Any],
        window: Sequence[Mapping[str, Any]],
    ) -> IntentResult:
        if len(window) < self.horizon_steps:
            raise ValueError("intent evaluated before its causal horizon was available")
        used = list(window[: self.horizon_steps])
        trace: JsonMap = {}
        value = _eval(self.expression, anchor=anchor, window=used, trace=trace, path="$")
        if not isinstance(value, bool):
            raise ValueError("intent expression must evaluate to boolean")
        return IntentResult(
            matched=value,
            evidence={
                "intent": self.text,
                "horizon_steps": self.horizon_steps,
                "expression": _copy(self.expression),
                "trace": trace,
            },
        )

    def to_dict(self) -> JsonMap:
        return {
            "text": self.text,
            "horizon_steps": self.horizon_steps,
            "expression": _copy(self.expression),
            "referenced_fields": list(self.referenced_fields()),
        }


def _validate_expr(node: Any) -> None:
    if not isinstance(node, dict):
        raise ValueError("intent expression node must be an object")
    op = node.get("op")
    if op not in _ALLOWED:
        raise ValueError(f"unsupported intent op: {op!r}")

    if op == "const":
        if "value" not in node:
            raise ValueError("const requires value")
        return

    if op in {"field", "window_min", "window_max"}:
        if not isinstance(node.get("path"), str) or not node["path"]:
            raise ValueError(f"{op} requires a non-empty path")
        if op == "field" and node.get("at", "anchor") not in {"anchor", "latest"}:
            raise ValueError("field.at must be anchor or latest")
        return

    if op in {"delta", "pct_change"}:
        if not isinstance(node.get("path"), str) or not node["path"]:
            raise ValueError(f"{op} requires a non-empty path")
        to = node.get("to", "latest")
        if to not in {"latest", "window_min", "window_max"}:
            raise ValueError(f"unsupported {op}.to: {to!r}")
        return

    if op in {"eq", "neq", "gt", "gte", "lt", "lte"}:
        _validate_expr(node.get("left"))
        _validate_expr(node.get("right"))
        return

    if op in {"and", "or"}:
        args = node.get("args")
        if not isinstance(args, list) or not args:
            raise ValueError(f"{op} requires non-empty args")
        for child in args:
            _validate_expr(child)
        return

    if op == "not":
        _validate_expr(node.get("arg"))
        return


def _collect_fields(node: JsonMap, out: set[str]) -> None:
    op = node["op"]
    if op in {"field", "window_min", "window_max", "delta", "pct_change"}:
        out.add(node["path"])
    if op in {"eq", "neq", "gt", "gte", "lt", "lte"}:
        _collect_fields(node["left"], out)
        _collect_fields(node["right"], out)
    elif op in {"and", "or"}:
        for child in node["args"]:
            _collect_fields(child, out)
    elif op == "not":
        _collect_fields(node["arg"], out)


def _eval(
    node: JsonMap,
    *,
    anchor: Mapping[str, Any],
    window: Sequence[Mapping[str, Any]],
    trace: JsonMap,
    path: str,
) -> Any:
    op = node["op"]

    if op == "const":
        return _copy(node.get("value"))

    if op == "field":
        src = anchor if node.get("at", "anchor") == "anchor" else window[-1]
        value = _get(src, node["path"])
        trace[path] = {"op": op, "path": node["path"], "value": _copy(value)}
        return value

    if op in {"window_min", "window_max"}:
        vals = [_number(_get(row, node["path"])) for row in window]
        vals = [v for v in vals if v is not None]
        if not vals:
            raise ValueError(f"no numeric values for intent field {node['path']!r}")
        value = min(vals) if op == "window_min" else max(vals)
        trace[path] = {"op": op, "path": node["path"], "value": value}
        return value

    if op in {"delta", "pct_change"}:
        start = _number(_get(anchor, node["path"]))
        if start is None:
            raise ValueError(f"non-numeric anchor value for {node['path']!r}")
        to = node.get("to", "latest")
        if to == "latest":
            end = _number(_get(window[-1], node["path"]))
        else:
            vals = [_number(_get(row, node["path"])) for row in window]
            vals = [v for v in vals if v is not None]
            if not vals:
                end = None
            else:
                end = min(vals) if to == "window_min" else max(vals)
        if end is None:
            raise ValueError(f"non-numeric future value for {node['path']!r}")
        if op == "delta":
            value = end - start
        else:
            if start == 0:
                raise ValueError(f"pct_change anchor is zero for {node['path']!r}")
            value = ((end - start) / abs(start)) * 100.0
        trace[path] = {
            "op": op,
            "path": node["path"],
            "from": start,
            "to": end,
            "value": value,
        }
        return value

    if op in {"eq", "neq", "gt", "gte", "lt", "lte"}:
        left = _eval(node["left"], anchor=anchor, window=window, trace=trace, path=path + ".left")
        right = _eval(node["right"], anchor=anchor, window=window, trace=trace, path=path + ".right")
        if op == "eq":
            return left == right
        if op == "neq":
            return left != right
        try:
            if op == "gt":
                return left > right
            if op == "gte":
                return left >= right
            if op == "lt":
                return left < right
            return left <= right
        except TypeError as exc:
            raise ValueError(f"intent comparison failed at {path}: {exc}") from exc

    if op == "and":
        return all(bool(_eval(x, anchor=anchor, window=window, trace=trace, path=f"{path}.{i}")) for i, x in enumerate(node["args"]))
    if op == "or":
        return any(bool(_eval(x, anchor=anchor, window=window, trace=trace, path=f"{path}.{i}")) for i, x in enumerate(node["args"]))
    if op == "not":
        return not bool(_eval(node["arg"], anchor=anchor, window=window, trace=trace, path=path + ".arg"))

    raise AssertionError(op)


def _get(data: Mapping[str, Any], path: str) -> Any:
    if path not in data:
        raise ValueError(f"intent field missing at evaluation time: {path}")
    return data[path]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


__all__ = ["IntentProgram", "IntentResult"]
