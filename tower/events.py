"""The one normalized event contract every source is reduced to.

Snapshot feeds (a full book image per message) and incremental feeds (one
level or one print per message) both become ``Event`` objects; the downstream
engine consumes either. Fields absent from a source are ``None`` — never
invented.

Ordering: events are totally ordered by ``sort_key()`` =
(t_recv, SOURCE_PRIORITY[source], seq_local). Two replays of the same raw
store therefore produce the same sequence (determinism is tested).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class EventType(str, Enum):
    BOOK_SNAPSHOT = "BOOK_SNAPSHOT"    # payload: bids=[(px,qty[,orders])], asks=[...], plus L1/day fields if present
    BOOK_UPDATE = "BOOK_UPDATE"        # incremental: side, price, qty (0 = delete), level, order_count, action
    TRADE = "TRADE"                    # a print: price, qty, trade_id, aggressor
    CUM_TOTALS = "CUM_TOTALS"          # exchange-stamped cumulative day totals: payload cum_trades/cum_volume/cum_value
    QUOTE = "QUOTE"                    # L1-lite / day summary (ltp, open, high, low, close, yclose, day totals)
    REFERENCE = "REFERENCE"            # limits / tick / breaker / sector for a symbol
    MARKET_STATS = "MARKET_STATS"      # market-wide totals and breadth (symbol None)
    BLOCK_PRINT = "BLOCK_PRINT"        # block-board print summary for a symbol
    AUCTION = "AUCTION"                # indicative price / matched qty / imbalance when a source delivers them
    STATUS = "STATUS"                  # session phase / source heartbeat / connection status
    GAP = "GAP"                        # capture-side gap (reason, duration)


# lower number = applied first when t_recv ties (book before tape before context)
SOURCE_PRIORITY: Dict[str, int] = {
    "lankabd_depth": 10, "dsebd_depth": 11, "fix_md": 5, "itch": 5, "broker_l2_export": 12,
    "lankabd_tape": 20, "broker_tns_export": 21, "minute_dataset": 22,
    "lankabd_watch": 30, "dsebd_latest": 31, "lankabd_grid": 32,
    "lankabd_market": 40, "lankabd_block": 41, "lankabd_circuit": 50, "dsebd_hts": 51,
    "heartbeat": 90, "runner": 91,
}


@dataclass(slots=True)
class Event:
    source: str
    event_type: EventType
    t_recv: datetime                                   # UTC, aware
    seq_local: int                                     # per (source) monotonic, assigned by normalize
    symbol: Optional[str] = None
    venue: str = "DSE"
    instrument_id: Optional[str] = None
    t_exch: Optional[datetime] = None                  # exchange / source timestamp when carried
    seq_feed: Optional[int] = None                     # feed-provided sequence when carried
    session_phase: str = "CLOSED"
    side: Optional[str] = None                         # "bid" | "ask" | None
    price: Optional[float] = None
    qty: Optional[float] = None
    level: Optional[int] = None
    order_count: Optional[int] = None
    trade_id: Optional[str] = None
    aggressor: Optional[str] = None                    # "B" | "S" | None
    is_snapshot: bool = False
    is_recovery: bool = False                          # first message after a gap / reconnect
    status: Optional[str] = None
    freshness_s: Optional[float] = None                # t_recv − t_exch when both exist
    payload: Dict[str, Any] = field(default_factory=dict)
    raw_ref: Optional[Tuple[str, int, str]] = None     # (source, raw seq, body_sha256) — provenance
    observed_fields: Tuple[str, ...] = ()              # canonical fields this event OBSERVES
    flags: Dict[str, bool] = field(default_factory=dict)  # duplicate / stale / out_of_order …

    def sort_key(self) -> Tuple[datetime, int, int]:
        return (self.t_recv, SOURCE_PRIORITY.get(self.source, 60), self.seq_local)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        d["t_recv"] = self.t_recv.isoformat()
        d["t_exch"] = self.t_exch.isoformat() if self.t_exch else None
        return d


def utc(ts: Any) -> Optional[datetime]:
    """Coerce ISO strings / naive datetimes / pandas Timestamps to aware UTC."""
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
