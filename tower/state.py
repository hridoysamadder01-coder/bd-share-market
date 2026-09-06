"""MarketState — the one synchronized, continuously evolving per-symbol state.

Every field is either OBSERVED (copied from a source), INFERRED (computed by a
named engine from observed inputs) or None with truth NOT_OBSERVABLE. The
``provenance`` map records, per fused field, which source supplied it and
whether the other sources agreed. ``to_dict()`` is the wire format used by the
timeline store, the UI and the determinism test (``state_hash``).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SourceStatus:
    source: str
    last_update: Optional[datetime] = None       # t_recv of the last accepted event
    t_exch: Optional[datetime] = None            # last exchange stamp carried
    freshness_s: Optional[float] = None          # now − last_update
    duplicate: bool = False                      # last payload identical to the previous one
    stale: bool = False                          # freshness beyond the source's expected cadence
    field_coverage: Tuple[str, ...] = ()         # canonical fields this source delivers
    updates: int = 0
    duplicates: int = 0
    gaps: int = 0
    agreement: Dict[str, bool] = field(default_factory=dict)      # field → agreed with the other source
    disagreement: Dict[str, Any] = field(default_factory=dict)    # field → {this, other, other_source}
    cadence_s: Optional[float] = None            # learned median inter-update gap (None until 1 gap seen)


@dataclass
class MechanismState:
    name: str
    family: str
    score: float = 0.0                           # 0..1 evidence strength
    state: str = "inactive"                      # inactive | building | active | confirmed | failed | resolved
    start_time: Optional[datetime] = None
    duration_s: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)   # the supporting measurements (calculated, never hardcoded)
    baseline: Dict[str, Any] = field(default_factory=dict)   # simple-baseline values at the same instant


@dataclass
class Transition:
    t: datetime
    from_state: str
    to_state: str
    layer: str                                   # "pressure" | "liquidity" | "circuit" | "mechanism:<name>" | ...
    duration_prev_s: float


@dataclass
class MarketState:
    symbol: str
    t: datetime                                  # frame time (t_recv of the event that produced it)
    seq: int = 0                                 # engine-level monotonic per symbol
    session_phase: str = "CLOSED"
    venue: str = "DSE"

    # ---- book / L1
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bid_qty1: Optional[float] = None
    ask_qty1: Optional[float] = None
    spread: Optional[float] = None
    spread_ticks: Optional[float] = None
    mid: Optional[float] = None
    microprice: Optional[float] = None
    ltp: Optional[float] = None
    tick_size: Optional[float] = None
    bids: List[Tuple[float, float]] = field(default_factory=list)      # displayed levels (best first)
    asks: List[Tuple[float, float]] = field(default_factory=list)
    bid_orders: Optional[List[Optional[int]]] = None                   # per level when a source carries it
    ask_orders: Optional[List[Optional[int]]] = None
    book_source: Optional[str] = None
    book_age_s: Optional[float] = None
    crossed: bool = False
    locked: bool = False
    one_sided: bool = False
    empty_book: bool = True

    # ---- imbalance / liquidity geometry
    imb_l1: Optional[float] = None
    imb_topk: Optional[float] = None             # K = 5
    imb_weighted: Optional[float] = None         # distance-weighted
    visible_bid_liq: Optional[float] = None
    visible_ask_liq: Optional[float] = None
    depth_ratio: Optional[float] = None          # visible bid / (bid+ask)
    depth_concentration_bid: Optional[float] = None    # HHI over displayed levels
    depth_concentration_ask: Optional[float] = None
    depth_slope_bid: Optional[float] = None      # cumulative depth vs distance-from-touch slope
    depth_slope_ask: Optional[float] = None
    depth_curvature_bid: Optional[float] = None
    depth_curvature_ask: Optional[float] = None
    hollow_bid: Optional[int] = None             # count of missing ticks inside the displayed range
    hollow_ask: Optional[int] = None
    wall_bid: Optional[Dict[str, Any]] = None    # {price, qty, share, persistence_s, migrated_ticks}
    wall_ask: Optional[Dict[str, Any]] = None
    depth_migration_bid: Optional[float] = None  # weighted-mean distance change of bid depth (ticks)
    depth_migration_ask: Optional[float] = None
    side_asymmetry: Optional[float] = None       # (bid geometry − ask geometry) summary

    # ---- book dynamics
    book_change_velocity: Optional[float] = None      # |level Δqty| per second (rolling)
    book_change_acceleration: Optional[float] = None
    depth_added_bid: Optional[float] = None           # last-update additions/removals
    depth_removed_bid: Optional[float] = None
    depth_added_ask: Optional[float] = None
    depth_removed_ask: Optional[float] = None
    ofi: Optional[float] = None                       # per-update order-flow imbalance (Cont–Kukanov–Stoikov)
    ofi_window: Optional[float] = None                # rolling sum

    # ---- trade / tape
    trade_count: Optional[float] = None               # day total when observed
    trade_volume: Optional[float] = None
    trade_value: Optional[float] = None
    interval_trades: Optional[float] = None           # since the previous tape update
    interval_volume: Optional[float] = None
    interval_vwap: Optional[float] = None
    trade_flow_direction: Optional[float] = None      # −1..1 (quote rule / aggressor when carried)
    trade_intensity: Optional[float] = None           # trades per minute (rolling)
    trade_acceleration: Optional[float] = None
    signed_flow_window: Optional[float] = None        # Σ direction × volume (rolling)
    last_print: Optional[Dict[str, Any]] = None       # when individual prints exist
    tape_source: Optional[str] = None
    tape_age_s: Optional[float] = None

    # ---- price response
    price_velocity: Optional[float] = None            # ticks per minute (mid)
    price_acceleration: Optional[float] = None
    price_impact: Optional[float] = None              # ticks moved per unit signed flow (rolling)
    price_only_response: Optional[float] = None       # mid change over the response window (ticks)
    volume_only_response: Optional[float] = None      # volume over the response window
    failed_response: Optional[bool] = None            # pressure without price follow-through

    # ---- liquidity response
    liquidity_response: Optional[float] = None        # depth-share change since the last book shock (< 600 s old), written by resilience
    liquidity_depletion: Optional[float] = None       # share of touch depth consumed in the window
    liquidity_replenishment: Optional[float] = None   # share rebuilt after depletion
    liquidity_retreat: Optional[bool] = None
    liquidity_vacuum: Optional[bool] = None

    # ---- pressure
    pressure_direction: Optional[int] = None          # +1 bid, −1 ask, 0 balanced
    pressure_strength: Optional[float] = None         # 0..1
    pressure_persistence_s: Optional[float] = None
    pressure_reversal: Optional[bool] = None
    book_pressure: Optional[float] = None
    trade_pressure: Optional[float] = None
    combined_pressure: Optional[float] = None
    pressure_divergence: Optional[float] = None       # book vs trade pressure

    # ---- resilience
    resilience_state: Optional[str] = None            # none | shocked | recovering | recovered | partial | vacuum | overshoot
    recovery_speed: Optional[float] = None            # depth recovered per second
    recovery_asymmetry: Optional[float] = None        # bid vs ask recovery speed difference
    recovery_curve: Optional[List[Tuple[float, float]]] = None    # (seconds since shock, recovered share)

    # ---- circuit / auction / session
    circuit: Dict[str, Any] = field(default_factory=dict)
    auction: Dict[str, Any] = field(default_factory=dict)
    session_state: Dict[str, Any] = field(default_factory=dict)

    # ---- context
    cross: Dict[str, Any] = field(default_factory=dict)
    sector: Dict[str, Any] = field(default_factory=dict)

    # ---- sources
    sources: Dict[str, SourceStatus] = field(default_factory=dict)
    source_agreement: Dict[str, bool] = field(default_factory=dict)
    source_disagreement: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, str] = field(default_factory=dict)          # fused field → source

    # ---- mechanics / timeline
    mechanisms: Dict[str, MechanismState] = field(default_factory=dict)
    active_mechanisms: List[str] = field(default_factory=list)
    transitions: List[Transition] = field(default_factory=list)       # transitions produced at this update
    layer_states: Dict[str, str] = field(default_factory=dict)        # current state per layer
    layer_since: Dict[str, datetime] = field(default_factory=dict)

    truth: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return _jsonable(d)

    def state_hash(self) -> str:
        """Hash of the state content, excluding wall-clock-dependent fields, for determinism tests."""
        d = self.to_dict()
        for k in ("sources",):
            d.pop(k, None)
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, datetime):
        return x.isoformat()
    if hasattr(x, "value") and not isinstance(x, (int, float)):
        return x.value
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):   # NaN / ±inf → null
        return None
    return x
