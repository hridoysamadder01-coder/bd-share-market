"""Historical real DSE trade-minute data (Muntasib-creator/DSE_dataset, MIT).

Columns: timestamp, closing, opening, high, low, volume — one row per minute
that had a print (2015-10 → 2024-01; QA'd in reports/MINUTE_DATA_QA_REPORT.md).
This is real, previously captured dynamic DSE data. It carries a minute tape
(OBSERVED: minute OHLC + volume) and nothing about the book (NOT_OBSERVABLE:
levels, orders, prints, side). It is used here to exercise the tape
reconstruction and the price-response labels on real data; it cannot feed the
book-dependent components of the experiment.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Optional

from ...clock import parse_source_local
from .base import Parsed, capability_map


@dataclass
class MinuteDatasetAdapter:
    name: str = "minute_dataset"
    kind: str = "tape"
    observes = ("ltp", "open", "high", "low", "interval_volume", "t_source", "t_recv")

    def parse(self, body: bytes, key: Optional[str] = None) -> Parsed:
        out = Parsed(self.name, truth=capability_map(self.observes))
        rd = csv.DictReader(io.StringIO(body.decode("utf-8", "replace")))
        for i, r in enumerate(rd):
            t = parse_source_local(r.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
            if t is None:
                out.problems.append(f"row {i}: unparsed timestamp {r.get('timestamp')!r}")
                continue
            try:
                out.frames.append({"symbol": (key or "").upper(), "t_source_utc": t.isoformat(),
                                   "t_source_str": r["timestamp"], "open": float(r["opening"]),
                                   "high": float(r["high"]), "low": float(r["low"]), "close": float(r["closing"]),
                                   "minute_volume": float(r["volume"])})
            except (KeyError, ValueError) as e:
                out.problems.append(f"row {i}: {e}")
        return out
