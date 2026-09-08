"""Three truth classes, carried on every field of every frame.

OBSERVED        the source delivered this field; the value is the source's.
INFERRED        derived from observed fields by a stated rule (e.g. per-interval
                trades from cumulative totals; trade side from the quote rule).
                Every INFERRED field names its rule and, where meaningful, a
                confidence.
NOT_OBSERVABLE  no obtained source delivers this field. The value is None and
                stays None. It is never filled, guessed or interpolated.

A ``TruthMap`` is a plain dict ``field -> Truth`` attached to a frame or to a
DataFrame's ``attrs["truth"]``. ``merge_truth`` combines maps from several
sources: OBSERVED beats INFERRED beats NOT_OBSERVABLE.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, Mapping, NamedTuple, Optional


class Truth(str, Enum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    NOT_OBSERVABLE = "NOT_OBSERVABLE"

    @property
    def rank(self) -> int:
        return {"OBSERVED": 2, "INFERRED": 1, "NOT_OBSERVABLE": 0}[self.value]


class TV(NamedTuple):
    """A value with its truth class, source and (optional) inference rule."""

    value: Any
    truth: Truth
    source: str
    rule: Optional[str] = None

    def is_observed(self) -> bool:
        return self.truth is Truth.OBSERVED


TruthMap = Dict[str, Truth]


def observed(fields: Iterable[str]) -> TruthMap:
    return {f: Truth.OBSERVED for f in fields}


def inferred(fields: Iterable[str]) -> TruthMap:
    return {f: Truth.INFERRED for f in fields}


def not_observable(fields: Iterable[str]) -> TruthMap:
    return {f: Truth.NOT_OBSERVABLE for f in fields}


def merge_truth(*maps: Mapping[str, Truth]) -> TruthMap:
    out: TruthMap = {}
    for m in maps:
        for k, v in m.items():
            if k not in out or v.rank > out[k].rank:
                out[k] = v
    return out


def truth_summary(tm: Mapping[str, Truth]) -> Dict[str, list]:
    """Group fields by truth class — what the engine prints at the top of a report."""
    out: Dict[str, list] = {t.value: [] for t in Truth}
    for k, v in sorted(tm.items()):
        out[v.value].append(k)
    return out


# The canonical field vocabulary of one synchronized market state. Adapters
# declare which of these they OBSERVE; everything else is NOT_OBSERVABLE for
# that adapter until a richer source is attached.
CANONICAL_FIELDS = (
    # book / depth
    "bid_levels", "ask_levels", "bid_orders_per_level", "ask_orders_per_level",
    "best_bid", "best_ask", "book_depth_count",
    # tape
    "trade_prints", "interval_trades", "interval_volume", "interval_value",
    "interval_vwap", "trade_side",
    # order / event / queue
    "order_events", "queue_position", "level_quantity_delta", "cancel_vs_trade_split",
    # last / summary
    "ltp", "open", "high", "low", "close_published", "yclose",
    "day_trades", "day_volume", "day_value",
    # reference
    "upper_limit", "lower_limit", "tick_size", "breaker_pct", "market_category",
    # market context
    "market_trades", "market_volume", "market_value", "block_prints",
    # timestamps
    "t_source", "t_recv",

    # ---- public-engine vocabulary (added 2026-09-08) -----------------------
    # Additive only. Every name above keeps its meaning and position, because
    # adapters, `tower/normalize.py` and committed evidence all declare against
    # them; a rename would silently reclassify existing frames.
    #
    # identity / provenance
    "source", "symbol", "exchange", "instrument_id", "company_id",
    # time quality — how old the observation is, and whether the source admits delay
    "trading_date", "session_phase", "freshness_ms", "delayed_flag",
    # book shape (the counts and totals sources publish alongside the ladders)
    "spread", "n_bid_levels", "n_ask_levels", "total_buy_volume", "total_sell_volume",
    "buy_percentage", "sell_percentage",
    # market breadth and indices
    "advancing", "declining", "unchanged", "index_value", "index_change", "index_name",
    # reference / session rules
    "market_segment", "session_rules",
    # block board
    "block_trades", "block_quantity", "block_value", "block_max_price", "block_min_price",
    # ownership (monthly-to-quarterly declarations, never a same-day signal)
    "sponsor_director_pct", "government_pct", "institution_pct", "foreign_pct",
    "public_pct", "free_float", "total_shares",
    # fundamentals
    "market_cap", "eps", "nav", "pe", "dividend", "paid_up_capital", "sector",
    # corporate events
    "announcement_type", "announcement_text", "announcement_date", "record_date",
    "agm_date", "rights_ratio", "insider_transaction", "unusual_price_query",
    # regulatory labels — context and outcome labels, never live signals
    "enforcement_case", "investigation_period", "violation_type", "regulator_decision_date",
    # macro / participation regime
    "fx_rate", "policy_rate", "repo_rate", "call_money_rate", "tbill_yield",
    "bo_accounts", "cds_market_value", "shares_in_cds",
)
