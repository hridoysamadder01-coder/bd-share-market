"""Live: tail a growing raw capture store and feed the same engine.

    python3 -m tower.live --capture evidence/capture/2026-09-06 --out results/tower/live [--poll 2]

The capture runner (seeing.capture.runner or the Go ingest daemon) appends
JSONL records to hourly segments; this process remembers a byte offset per
segment, reads every newly completed line, converts the records to events
with the same normalizer used by replay (so live and replay share one code
path), and processes them in receipt order. Records that arrive out of order
across sources are handled by a small reorder window (``--reorder 3`` s):
events are released once the newest record is older than them by that margin.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from typing import Dict, List, Optional

from .engine import Engine, EngineConfig
from .events import Event, utc
from .normalize import Normalizer, record_time
from .store import StateStore


class Tailer:
    def __init__(self, capture: str, from_end: bool = False) -> None:
        self.capture = capture
        self.offsets: Dict[str, int] = {}
        if from_end:                                       # tail-only: skip everything already on disk
            for path in glob.glob(os.path.join(self.capture, "segments", "*.jsonl")):
                try:
                    self.offsets[path] = os.path.getsize(path)
                except OSError:
                    continue

    def poll(self) -> List[dict]:
        """Return newly completed raw records (as dicts) across all segments, in file order."""
        out: List[dict] = []
        for path in sorted(glob.glob(os.path.join(self.capture, "segments", "*.jsonl"))):
            off = self.offsets.get(path, 0)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size <= off:
                continue
            with open(path, "rb") as fh:
                fh.seek(off)
                data = fh.read(size - off)
            last_nl = data.rfind(b"\n")
            if last_nl < 0:
                continue                                   # no complete line yet
            chunk, consumed = data[:last_nl + 1], last_nl + 1
            for line in chunk.split(b"\n"):
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue                               # partial/corrupt line is retried never; counted by normalizer
            self.offsets[path] = off + consumed
        return out


def run(capture: str, out: str, poll_s: float = 2.0, reorder_s: float = 3.0, symbols: Optional[List[str]] = None,
        once: bool = False, max_seconds: Optional[float] = None, tail_only: bool = False) -> Dict[str, int]:
    """Tail ``capture`` into ``out``.

    The first poll is a *catch-up*: everything already on disk is processed (the day's
    state — cumulative tape, circuit references, day history — needs it), then the loop
    polls for new lines. ``max_seconds`` is a hard wall-clock deadline that is honoured
    inside the catch-up as well: whatever is left unprocessed is reported as
    ``unprocessed_backlog`` in RUN.json rather than silently drained past the deadline.
    ``tail_only`` starts from the current end of every segment instead (no catch-up).
    """
    engine = Engine(EngineConfig(live=True))
    store = StateStore(out)
    # the same causal Normalizer replay uses; events accumulate in norm.events and are drained incrementally
    norm = Normalizer(symbols=[s.upper() for s in symbols] if symbols else None)
    drained = 0
    tailer = Tailer(capture, from_end=tail_only)
    pending: List[Event] = []
    t0 = time.time()
    deadline = (t0 + max_seconds) if max_seconds is not None else None
    n_rec = n_ev = n_state = 0
    catchup_records = None
    timed_out = False
    while True:
        recs = tailer.poll()
        n_rec += len(recs)
        if catchup_records is None:
            catchup_records = len(recs)
        # feed in receipt order across sources (as normalize_store does for a whole store)
        # same receipt clock as the batch path (normalize.record_time: last byte for DATA, else write stamp)
        recs.sort(key=lambda r: (record_time(r), str(r.get("source")), int(r.get("seq", 0))))
        for rec in recs:
            norm.on_record(rec, True)
        if len(norm.events) > drained:
            pending.extend(norm.events[drained:])
            drained = len(norm.events)
        if pending:
            pending.sort(key=lambda e: e.sort_key())
            newest = pending[-1].t_recv
            release = [e for e in pending if (newest - e.t_recv).total_seconds() >= reorder_s] if not once else pending
            pending = pending[len(release):]
            engine.metrics["backlog"] = len(pending)
            for i, ev in enumerate(release):
                if deadline is not None and time.time() >= deadline:
                    pending = release[i:] + pending          # keep order; reported below, not drained
                    timed_out = True
                    break
                n_ev += 1
                ms = engine.process(ev)
                if ms is not None:
                    store.append(ms)
                    n_state += 1
            store.flush()
            store.write_metrics(engine.metrics_snapshot())
        if once or timed_out or (deadline is not None and time.time() >= deadline):
            break
        time.sleep(poll_s)
    # drain on a clean stop (``--once``); a deadline stop leaves the backlog reported, not processed
    if not timed_out:
        for ev in pending:
            n_ev += 1
            ms = engine.process(ev)
            if ms is not None:
                store.append(ms)
                n_state += 1
        pending = []
    engine.metrics["backlog"] = len(pending)
    store.write_metrics(engine.metrics_snapshot())
    store.write_run({"capture": os.path.abspath(capture), "mode": "live", "records": n_rec, "events": n_ev,
                     "states": n_state, "catchup_records": catchup_records or 0, "tail_only": tail_only,
                     "deadline_hit": timed_out, "unprocessed_backlog": len(pending),
                     "elapsed_s": round(time.time() - t0, 3),
                     "engine_metrics": engine.metrics_snapshot(), "qa": norm.stats.to_dict()})
    store.close()
    return {"records": n_rec, "events": n_ev, "states": n_state, "unprocessed_backlog": len(pending)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--reorder", type=float, default=3.0)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--once", action="store_true", help="process what exists now and exit")
    ap.add_argument("--max-seconds", type=float, default=None, help="hard wall-clock deadline (also inside catch-up)")
    ap.add_argument("--tail-only", action="store_true",
                    help="start from the current end of every segment (no catch-up of the day's earlier records)")
    a = ap.parse_args(argv)
    syms = [s for s in a.symbols.split(",") if s.strip()] or None
    r = run(a.capture, a.out, a.poll, a.reorder, syms, once=a.once, max_seconds=a.max_seconds, tail_only=a.tail_only)
    print(json.dumps(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
