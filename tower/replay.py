"""Deterministic replay of a raw capture through the engine.

    python3 -m tower.replay --capture DIR --out DIR [--speed 0|1|10] [--step] [--symbols A,B]
                            [--from ISO] [--to ISO] [--sources s1,s2]

speed 0 (default) = as fast as possible; 1 = original event timing; N = N× accelerated.
--step waits for a newline on stdin before each event (step-by-step; 'p' pauses / resumes,
'q' quits). ``Replayer`` exposes the same controls programmatically (pause/resume/step/run).

Determinism: the same capture with the same filters yields the same event
sequence (Event.sort_key) and hence the same MarketState sequence; RUN.json
records the input segment hashes and the final state_hash per symbol so two
runs can be compared byte for byte.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .engine import Engine, EngineConfig
from .events import Event, utc
from .normalize import normalize_store
from .state import MarketState
from .store import StateStore


class Replayer:
    def __init__(self, capture: str, out: str, symbols: Optional[List[str]] = None, t_from: Optional[str] = None,
                 t_to: Optional[str] = None, sources: Optional[List[str]] = None, speed: float = 0.0,
                 cfg: Optional[EngineConfig] = None) -> None:
        self.capture, self.out = capture, out
        self.symbols = [s.upper() for s in symbols] if symbols else None
        self.t_from, self.t_to = (utc(t_from) if t_from else None), (utc(t_to) if t_to else None)
        self.sources = sources
        self.speed = speed
        self.engine = Engine(cfg)
        self.store = StateStore(out)
        self._pause = threading.Event()
        self._pause.set()                    # set = running
        self._stop = False
        self.events: List[Event] = []
        self.stats = None
        self.pos = 0

    def load(self) -> int:
        self.events, self.stats = normalize_store(self.capture, symbols=self.symbols, t_from=self.t_from,
                                                  t_to=self.t_to, sources=self.sources)
        return len(self.events)

    # controls
    def pause(self) -> None: self._pause.clear()
    def resume(self) -> None: self._pause.set()
    def stop(self) -> None: self._stop = True; self._pause.set()

    def step(self) -> Optional[MarketState]:
        """Process exactly one event (regardless of pause state)."""
        if self.pos >= len(self.events):
            return None
        ev = self.events[self.pos]
        self.pos += 1
        ms = self.engine.process(ev)
        if ms is not None:
            self.store.append(ms)
        return ms

    def run(self, on_state=None, flush_every: int = 500) -> Dict[str, Any]:
        if not self.events and self.stats is None:
            self.load()
        prev_t: Optional[datetime] = None
        n = 0
        while self.pos < len(self.events) and not self._stop:
            self._pause.wait()
            if self._stop:
                break
            ev = self.events[self.pos]
            if self.speed and prev_t is not None:
                dt = (ev.t_recv - prev_t).total_seconds() / self.speed
                if dt > 0:
                    time.sleep(min(dt, 30.0))
            prev_t = ev.t_recv
            ms = self.step()
            n += 1
            if ms is not None and on_state:
                on_state(ms)
            if n % flush_every == 0:
                self.store.flush()
                self.store.write_metrics(self.engine.metrics_snapshot())
        return self.finish()

    def finish(self) -> Dict[str, Any]:
        self.store.flush()
        self.store.write_metrics(self.engine.metrics_snapshot())
        run = {"capture": os.path.abspath(self.capture), "symbols": self.symbols, "t_from": str(self.t_from),
               "t_to": str(self.t_to), "sources": self.sources, "events": len(self.events), "processed": self.pos,
               "qa": (self.stats.to_dict() if hasattr(self.stats, "to_dict") else str(self.stats)),
               "segments": _segment_hashes(self.capture), "engine_metrics": self.engine.metrics_snapshot()}
        self.store.write_run(run)
        self.store.close()
        return run


def _segment_hashes(capture: str) -> Dict[str, str]:
    mpath = os.path.join(capture, "MANIFEST.json")
    if not os.path.exists(mpath):
        return {}
    man = json.load(open(mpath))
    return {s["path"]: s.get("sha256") for s in man.get("segments", [])}


def replay_hashes(capture: str, out: str, **kw) -> Dict[str, str]:
    r = Replayer(capture, out, **kw)
    r.load()
    r.run()
    return dict(r.store.hashes)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--speed", type=float, default=0.0)
    ap.add_argument("--step", action="store_true")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--from", dest="t_from", default=None)
    ap.add_argument("--to", dest="t_to", default=None)
    ap.add_argument("--sources", default="")
    ap.add_argument("--mech-interval", type=float, default=2.0)
    a = ap.parse_args(argv)
    syms = [s for s in a.symbols.split(",") if s.strip()] or None
    srcs = [s for s in a.sources.split(",") if s.strip()] or None
    r = Replayer(a.capture, a.out, symbols=syms, t_from=a.t_from, t_to=a.t_to, sources=srcs, speed=a.speed,
                 cfg=EngineConfig(mechanics_min_interval_s=a.mech_interval))
    n = r.load()
    print(f"loaded {n} events; qa: {r.stats}")
    if a.step:
        print("step mode: <enter> = next event, p = pause/resume, q = quit")
        while r.pos < len(r.events):
            line = sys.stdin.readline()
            if not line or line.strip() == "q":
                break
            if line.strip() == "p":
                (r.resume if not r._pause.is_set() else r.pause)()
                continue
            ms = r.step()
            if ms is not None:
                print(f"{ms.t.isoformat()} {ms.symbol} bb={ms.best_bid} ba={ms.best_ask} ltp={ms.ltp} "
                      f"active={ms.active_mechanisms} layers={ms.layer_states}")
        run = r.finish()
    else:
        run = r.run()
    print(json.dumps({k: run[k] for k in ("events", "processed")} | {"states": run["engine_metrics"]["states_out"],
                     "failures": run["engine_metrics"]["reconstruction_failures"], "out": a.out}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
