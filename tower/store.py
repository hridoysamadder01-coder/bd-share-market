"""State store shared by replay, live tailing and the UI.

    <out>/states/<SYMBOL>.jsonl   one MarketState.to_dict() per line, event order
    <out>/timeline.jsonl          one Transition per line
    <out>/metrics.json            engine metrics (rewritten periodically)
    <out>/latest.json             {symbol: last state dict}
    <out>/RUN.json                inputs, filters, segment sha256s, final state_hash per symbol
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, IO, List, Optional

from .state import MarketState, Transition, _jsonable


class StateStore:
    def __init__(self, out_dir: str) -> None:
        self.out = out_dir
        os.makedirs(os.path.join(out_dir, "states"), exist_ok=True)
        self._files: Dict[str, IO[str]] = {}
        self._timeline = open(os.path.join(out_dir, "timeline.jsonl"), "a", encoding="utf-8")
        self.latest: Dict[str, Dict[str, Any]] = {}
        self.hashes: Dict[str, str] = {}
        self.counts: Dict[str, int] = {}

    def append(self, ms: MarketState) -> None:
        fh = self._files.get(ms.symbol)
        if fh is None:
            fh = open(os.path.join(self.out, "states", f"{ms.symbol}.jsonl"), "a", encoding="utf-8")
            self._files[ms.symbol] = fh
        d = ms.to_dict()
        fh.write(json.dumps(d, separators=(",", ":"), default=str, allow_nan=False) + "\n")
        self.latest[ms.symbol] = d
        self.hashes[ms.symbol] = ms.state_hash()
        self.counts[ms.symbol] = self.counts.get(ms.symbol, 0) + 1
        for tr in ms.transitions:
            rec = {"symbol": ms.symbol, **_jsonable(asdict(tr))}
            self._timeline.write(json.dumps(rec, separators=(",", ":"), default=str, allow_nan=False) + "\n")

    def flush(self) -> None:
        for fh in self._files.values():
            fh.flush()
        self._timeline.flush()
        self.write_latest()

    def write_latest(self) -> None:
        tmp = os.path.join(self.out, "latest.json.tmp")
        with open(tmp, "w") as fh:
            json.dump(self.latest, fh, default=str)
        os.replace(tmp, os.path.join(self.out, "latest.json"))

    def write_metrics(self, metrics: Dict[str, Any]) -> None:
        tmp = os.path.join(self.out, "metrics.json.tmp")
        with open(tmp, "w") as fh:
            json.dump(metrics, fh, indent=1, default=str)
        os.replace(tmp, os.path.join(self.out, "metrics.json"))

    def write_run(self, payload: Dict[str, Any]) -> None:
        payload = dict(payload)
        payload["final_state_hash"] = dict(self.hashes)
        payload["states_written"] = dict(self.counts)
        with open(os.path.join(self.out, "RUN.json"), "w") as fh:
            json.dump(payload, fh, indent=1, default=str)

    def close(self) -> None:
        self.flush()
        for fh in self._files.values():
            fh.close()
        self._timeline.close()


def read_states(out_dir: str, symbol: str) -> List[Dict[str, Any]]:
    p = os.path.join(out_dir, "states", f"{symbol}.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_timeline(out_dir: str) -> List[Dict[str, Any]]:
    p = os.path.join(out_dir, "timeline.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
