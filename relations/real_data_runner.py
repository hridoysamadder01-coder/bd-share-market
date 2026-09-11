"""Real-data entry points for the intent-native relation watcher.

This module performs wiring only. It does not invent an intent, target, feature,
threshold, indicator, strategy, pair/triple, or missing timestamp.

Supported evidence lanes:
- extended DSE EOD parquet (day-resolution, one authoritative anchor per symbol/day)
- public minute CSV directory (minute-resolution, k-way merged without loading 43M rows)
- existing tower raw capture (normalized by tower.normalize, caller explicitly names
  which source(s) advance the intent horizon)
- already point-in-time JSONL ReplayEvent rows

Different timestamp resolutions are intentionally not fused here. A day-only EOD row
cannot be placed before/after an intraday minute/book event without inventing a time.
Run those lanes separately unless an upstream source provides an authoritative common
clock.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import heapq
import json
import math
import os
from dataclasses import asdict
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence

from .intent import IntentProgram
from .relation_replay import ReplayEvent
from .relation_watcher import RelationWatcher
from .source_bridge import tower_event_to_replay


JsonMap = Dict[str, Any]


def load_intent(path: str) -> IntentProgram:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("intent file must be a JSON object")
    allowed = {"text", "horizon_steps", "expression"}
    extra = sorted(set(raw) - allowed)
    missing = sorted(allowed - set(raw))
    if extra:
        raise ValueError("unexpected intent keys: " + ", ".join(extra))
    if missing:
        raise ValueError("missing intent keys: " + ", ".join(missing))
    return IntentProgram(
        text=str(raw["text"]),
        horizon_steps=int(raw["horizon_steps"]),
        expression=dict(raw["expression"]),
    )


def _clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_eod_events(
    path: str,
    *,
    symbols: Optional[Sequence[str]] = None,
    max_events: Optional[int] = None,
) -> Iterator[ReplayEvent]:
    """Yield the extended EOD parquet as raw daily anchors.

    ``source`` from the parquet is provenance metadata, not a relation candidate.
    The logical field namespace is therefore stable as ``dse_eod.*`` across the
    owner CSV and archive extension portions.
    """
    import pandas as pd

    df = pd.read_parquet(path)
    time_col = "ts" if "ts" in df.columns else ("date" if "date" in df.columns else None)
    if time_col is None or "symbol" not in df.columns:
        raise ValueError("EOD parquet requires symbol and ts/date columns")
    if symbols:
        wanted = {str(x).upper() for x in symbols}
        df = df[df["symbol"].astype(str).str.upper().isin(wanted)]
    # Source resolution is daily. Lexical symbol order is only a deterministic
    # tie-break inside one day, never represented as exchange micro-order.
    df = df.sort_values([time_col, "symbol"], kind="mergesort")
    excluded = {time_col, "symbol", "source"}
    emitted = 0
    for order, (_, row) in enumerate(df.iterrows()):
        if max_events is not None and emitted >= max_events:
            break
        data = {str(k): _clean_scalar(row[k]) for k in df.columns if k not in excluded}
        t = row[time_col]
        if hasattr(t, "isoformat"):
            t = t.isoformat()
        yield ReplayEvent(
            time=str(t),
            order=order,
            source="dse_eod",
            entity=str(row["symbol"]).upper(),
            scope="entity",
            advance=True,
            data=data,
        )
        emitted += 1


def _minute_row(reader: csv.DictReader, symbol: str, previous_ts: Optional[str]) -> Optional[tuple[str, JsonMap]]:
    while True:
        row = next(reader, None)
        if row is None:
            return None
        ts = str(row.get("timestamp", "")).strip()
        if not ts:
            raise ValueError(f"{symbol}: minute row missing timestamp")
        if previous_ts is not None and ts < previous_ts:
            raise ValueError(f"{symbol}: minute file is non-monotonic at {ts} < {previous_ts}")
        try:
            data = {
                "closing": float(row["closing"]),
                "opening": float(row["opening"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "volume": float(row["volume"]),
            }
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{symbol}: invalid minute row at {ts}: {exc}") from exc
        return ts, data


def iter_minute_events(
    minute_dir: str,
    *,
    symbols: Optional[Sequence[str]] = None,
    max_events: Optional[int] = None,
) -> Iterator[ReplayEvent]:
    """K-way merge real minute CSV files in timestamp order without bulk loading.

    When multiple symbols share the same minute timestamp, symbol filename order is
    the deterministic source-granularity tie-break. It is not claimed to be exchange
    order inside that minute.
    """
    wanted = {str(x).upper() for x in symbols} if symbols else None
    paths = []
    for path in sorted(glob.glob(os.path.join(minute_dir, "*.csv"))):
        stem = os.path.splitext(os.path.basename(path))[0].upper()
        if stem in {"SUMMARY", "__SUMMARY__"}:
            continue
        if wanted is None or stem in wanted:
            paths.append((stem, path))
    if not paths:
        raise FileNotFoundError("no minute symbol CSV files matched")

    handles = []
    readers = []
    previous: list[Optional[str]] = []
    heap: list[tuple[str, str, int, JsonMap]] = []
    try:
        for idx, (symbol, path) in enumerate(paths):
            fh = open(path, "r", encoding="utf-8-sig", newline="")
            handles.append(fh)
            rd = csv.DictReader(fh)
            readers.append(rd)
            previous.append(None)
            first = _minute_row(rd, symbol, None)
            if first is not None:
                ts, data = first
                previous[idx] = ts
                heapq.heappush(heap, (ts, symbol, idx, data))

        order = 0
        while heap:
            if max_events is not None and order >= max_events:
                break
            ts, symbol, idx, data = heapq.heappop(heap)
            yield ReplayEvent(
                time=ts,
                order=order,
                source="minute_dataset",
                entity=symbol,
                scope="entity",
                advance=True,
                data=data,
            )
            order += 1
            nxt = _minute_row(readers[idx], symbol, previous[idx])
            if nxt is not None:
                nts, ndata = nxt
                previous[idx] = nts
                heapq.heappush(heap, (nts, symbol, idx, ndata))
    finally:
        for fh in handles:
            fh.close()


def iter_capture_events(
    capture: str,
    *,
    advance_sources: Sequence[str],
    symbols: Optional[Sequence[str]] = None,
    max_events: Optional[int] = None,
) -> Iterator[ReplayEvent]:
    """Bridge an existing tower capture without choosing an anchor source silently."""
    if not advance_sources:
        raise ValueError("capture mode requires at least one explicit --advance-source")
    from tower.normalize import normalize_store

    wanted = [str(x).upper() for x in symbols] if symbols else None
    events, _stats = normalize_store(capture, symbols=wanted)
    anchors = {str(x) for x in advance_sources}
    emitted = 0
    for order, event in enumerate(events):
        if max_events is not None and emitted >= max_events:
            break
        advance = event.symbol is not None and event.source in anchors
        yield tower_event_to_replay(event, order=order, advance=advance)
        emitted += 1


def iter_jsonl_events(path: str, *, max_events: Optional[int] = None) -> Iterator[ReplayEvent]:
    with open(path, "r", encoding="utf-8") as fh:
        emitted = 0
        for line_no, line in enumerate(fh, 1):
            if max_events is not None and emitted >= max_events:
                break
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                yield ReplayEvent(
                    time=str(row["time"]),
                    order=int(row["order"]),
                    source=str(row["source"]),
                    entity=str(row.get("entity", "")),
                    scope=str(row.get("scope", "entity")),
                    advance=bool(row.get("advance", True)),
                    observation_id=(str(row["observation_id"]) if row.get("observation_id") is not None else None),
                    data=dict(row["data"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid replay JSONL line {line_no}: {exc}") from exc
            emitted += 1


def run_stream(
    events: Iterable[ReplayEvent],
    *,
    intent: IntentProgram,
    out_dir: str,
    input_info: Mapping[str, Any],
    forbidden_keys: Sequence[str] = (),
) -> JsonMap:
    """Feed events one-by-one. No future event is preloaded into the watcher."""
    os.makedirs(out_dir, exist_ok=True)
    watcher = RelationWatcher(intent=intent, forbidden_keys=forbidden_keys)
    previous: Optional[tuple[str, int]] = None
    count = 0
    first_time = None
    last_time = None
    for event in events:
        key = (str(event.time), int(event.order))
        if previous is not None and key <= previous:
            raise ValueError(f"event stream is not strictly causal: {key!r} <= {previous!r}")
        previous = key
        if event.scope == "global" and event.advance:
            raise ValueError("global context may not advance an entity intent horizon")
        watcher.observe(
            time=event.time,
            order=event.order,
            source=event.source,
            data=event.data,
            entity=event.entity,
            scope=event.scope,
            advance=event.advance,
            observation_id=event.observation_id,
        )
        count += 1
        first_time = event.time if first_time is None else first_time
        last_time = event.time

    snapshot = watcher.intelligence_snapshot()
    snap_path = os.path.join(out_dir, "RELATION_SNAPSHOT.json")
    with open(snap_path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    run = {
        "intent": intent.to_dict(),
        "input": dict(input_info),
        "events_processed": count,
        "first_time": first_time,
        "last_time": last_time,
        "resolved_cases": len(watcher.cases),
        "criterion": "minimum_description_length",
        "outcome_packets": 0,
        "relation_snapshot": snap_path,
    }
    with open(os.path.join(out_dir, "RUN.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    return run


def _symbols(value: str) -> Optional[list[str]]:
    vals = [x.strip().upper() for x in value.split(",") if x.strip()]
    return vals or None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--intent", required=True, help="IntentProgram JSON; no intent is inferred")
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--max-events", type=int, default=None, help="explicit smoke/debug truncation; omitted = all")
    sub = ap.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("eod")
    p.add_argument("--path", default="data/raw/dse_eod_extended.parquet")

    p = sub.add_parser("minute")
    p.add_argument("--dir", required=True)

    p = sub.add_parser("capture")
    p.add_argument("--capture", required=True)
    p.add_argument("--advance-source", action="append", default=[])

    p = sub.add_parser("jsonl")
    p.add_argument("--path", required=True)

    a = ap.parse_args(argv)
    intent = load_intent(a.intent)
    symbols = _symbols(a.symbols)

    if a.mode == "eod":
        if not os.path.exists(a.path):
            raise FileNotFoundError(a.path)
        events = iter_eod_events(a.path, symbols=symbols, max_events=a.max_events)
        info = {
            "lane": "eod",
            "path": os.path.abspath(a.path),
            "sha256": _sha256(a.path),
            "clock_resolution": "day",
            "logical_source": "dse_eod",
            "truncated": a.max_events is not None,
        }
    elif a.mode == "minute":
        events = iter_minute_events(a.dir, symbols=symbols, max_events=a.max_events)
        info = {
            "lane": "minute",
            "dir": os.path.abspath(a.dir),
            "clock_resolution": "minute",
            "logical_source": "minute_dataset",
            "truncated": a.max_events is not None,
        }
    elif a.mode == "capture":
        events = iter_capture_events(
            a.capture,
            advance_sources=a.advance_source,
            symbols=symbols,
            max_events=a.max_events,
        )
        info = {
            "lane": "tower_capture",
            "capture": os.path.abspath(a.capture),
            "advance_sources": list(a.advance_source),
            "clock_resolution": "receipt/exchange timestamps as carried by capture",
            "truncated": a.max_events is not None,
        }
    else:
        if not os.path.exists(a.path):
            raise FileNotFoundError(a.path)
        events = iter_jsonl_events(a.path, max_events=a.max_events)
        info = {
            "lane": "jsonl",
            "path": os.path.abspath(a.path),
            "sha256": _sha256(a.path),
            "clock_resolution": "as supplied",
            "truncated": a.max_events is not None,
        }

    run = run_stream(events, intent=intent, out_dir=a.out, input_info=info)
    print(json.dumps(run, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
