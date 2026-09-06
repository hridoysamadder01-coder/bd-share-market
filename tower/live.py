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
from .events import Event
from .normalize import RecordNormalizer
from .store import StateStore


class Tailer:
    def __init__(self, capture: str) -> None:
        self.capture = capture
        self.offsets: Dict[str, int] = {}

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
        once: bool = False, max_seconds: Optional[float] = None) -> Dict[str, int]:
    engine = Engine(EngineConfig(live=True))
    store = StateStore(out)
    norm = RecordNormalizer(symbols=[s.upper() for s in symbols] if symbols else None)
    tailer = Tailer(capture)
    pending: List[Event] = []
    t0 = time.time()
    n_rec = n_ev = n_state = 0
    while True:
        recs = tailer.poll()
        n_rec += len(recs)
        for rec in recs:
            pending.extend(norm.record_to_events(rec))
        if pending:
            pending.sort(key=lambda e: e.sort_key())
            newest = pending[-1].t_recv
            release = [e for e in pending if (newest - e.t_recv).total_seconds() >= reorder_s] if not once else pending
            pending = pending[len(release):]
            engine.metrics["backlog"] = len(pending)
            for ev in release:
                n_ev += 1
                ms = engine.process(ev)
                if ms is not None:
                    store.append(ms)
                    n_state += 1
            store.flush()
            store.write_metrics(engine.metrics_snapshot())
        if once or (max_seconds is not None and time.time() - t0 >= max_seconds):
            break
        time.sleep(poll_s)
    # drain on exit
    for ev in pending:
        ms = engine.process(ev)
        if ms is not None:
            store.append(ms)
            n_state += 1
    store.write_metrics(engine.metrics_snapshot())
    store.write_run({"capture": os.path.abspath(capture), "mode": "live", "records": n_rec, "events": n_ev,
                     "engine_metrics": engine.metrics_snapshot(), "qa": norm.stats.to_dict()})
    store.close()
    return {"records": n_rec, "events": n_ev, "states": n_state}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--reorder", type=float, default=3.0)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--once", action="store_true", help="process what exists now and exit")
    ap.add_argument("--max-seconds", type=float, default=None)
    a = ap.parse_args(argv)
    syms = [s for s in a.symbols.split(",") if s.strip()] or None
    r = run(a.capture, a.out, a.poll, a.reorder, syms, once=a.once, max_seconds=a.max_seconds)
    print(json.dumps(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
