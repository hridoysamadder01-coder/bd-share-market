"""The one synchronized market state — field vocabulary and truth map.

A fused frame is one row per (symbol, t_frame). Columns are grouped below;
``FRAME_TRUTH`` records, for the sources obtained in this repository, which
truth class each group carries. The fusion code attaches this map to the
frames table (``frames.attrs["truth"]``) and the report prints it first.
"""
from __future__ import annotations

FRAME_TRUTH = {
    # BOOK / DEPTH — LankaBD depth (primary clock) + dsebd depth (cross-check)
    "bid_levels / ask_levels (price, qty; top-N as displayed)": "OBSERVED",
    "best_bid / best_ask / spread / mid / microprice / depth_topK / imbalances": "OBSERVED (arithmetic on observed levels)",
    "orders per level": "NOT_OBSERVABLE (no obtained source shows order counts)",
    "book_agree (two independent book sensors identical)": "OBSERVED",
    "book t_source": "NOT_OBSERVABLE (depth pages carry no exchange stamp; frame clock = receipt time)",
    # TAPE
    "cumulative day trades / volume / value (exchange-stamped)": "OBSERVED",
    "interval trades / volume / value / VWAP between frames": "INFERRED (Δ of cumulative totals)",
    "individual prints": "NOT_OBSERVABLE",
    "trade side": "INFERRED (quote rule on interval VWAP) / OBSERVED when book locked",
    # ORDER / EVENT / QUEUE
    "level quantity deltas": "OBSERVED (snapshot diff)",
    "event class (consumed / cancelled / replenish / sweep)": "INFERRED",
    "queue position, order ids, intra-interval add/cancel netting": "NOT_OBSERVABLE",
    # PRICE RESPONSE
    "ltp, open, high, low, published close, yclose": "OBSERVED",
    "forward mid / ltp change (labels)": "OBSERVED (future frames; used only as outcome)",
    # LIQUIDITY / DEPLETION / REPLENISHMENT / PRESSURE / RESILIENCE / STATE — derived
    "liquidity change, depletion/replenishment, pressure build/failure, resilience, state": "INFERRED (rules in seeing.features.micro / seeing.state_machine)",
    # REFERENCE & CONTEXT
    "upper/lower limit, tick, breaker % (circuit table)": "OBSERVED (reference, per day)",
    "shares to the door (ask qty up to the upper limit)": "OBSERVED when the limit is within displayed levels, else LOWER BOUND (flagged)",
    "market-wide trades / volume / value / breadth": "OBSERVED (LankaBD market stats + watch)",
    "block-board prints": "OBSERVED (daily list)",
    "all-symbol L1 with exchange stamp (watch)": "OBSERVED (as-of join; age recorded)",
}

BOOK_COLS = ["best_bid", "best_ask", "bid_qty1", "ask_qty1", "bid_depth_top3", "ask_depth_top3", "bid_depth_top5",
             "ask_depth_top5", "bid_depth_all", "ask_depth_all", "n_bid", "n_ask", "spread", "spread_ticks", "mid",
             "microprice", "crossed", "locked", "one_sided", "empty", "largest_wall_side", "largest_wall_price",
             "largest_wall_qty", "largest_wall_share", "bid_weighted_depth", "ask_weighted_depth", "imb_l1",
             "imb_top3", "imb_top5", "imb_all", "imb_weighted", "unchanged_run", "dup_payload", "n_level_events"]
