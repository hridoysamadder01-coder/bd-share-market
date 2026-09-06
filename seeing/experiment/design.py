"""Pre-registered experiment design. Fixed BEFORE any live frame was seen
(written 2026-09-06 01:30 UTC; the first live DSE frame arrives 04:00 UTC).

Question
    Does the combined dynamic state
        persistent bid pressure
      + ask-side thinning
      + bid persistence / replenishment
      + positive multi-level pressure transition
      + stable spread
      + time persistence
      + price-response behaviour (price not being sold into while pressure builds)
    carry information about the forward mid-price response beyond simple
    imbalance alone?

Everything below is a parameter of the test, not a tuned quantity. Nothing in
this file may be changed after the live capture without recording the change
in REJECTED_CANDIDATES.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class Design:
    # frame windows (frames; one frame ≈ one LankaBD depth snapshot ≈ 10–20 s)
    W: int = 6                       # persistence / transition window ("transition" = a crossing
                                     # from ≤ theta/2 to > theta on L1, top-3 and top-5 together,
                                     # seen within the last W frames — fixed on synthetic data
                                     # 2026-09-06 01:45 UTC, before any live frame)
    P: int = 2                       # minimum consecutive frames the core state must hold
    horizons: Tuple[int, ...] = (2, 4, 8)
    primary_h: int = 4
    # thresholds
    theta_imb: float = 0.20          # top-K imbalance counted as "bid pressure"
    persist_frac: float = 0.80       # share of the window with imb_top5 > theta
    thinning_ratio: float = 0.80     # ask_depth_top5 now ≤ 80 % of W frames ago
    max_spread_ticks: float = 2.0    # "stable spread": constant over W and ≤ 2 ticks
    theta_pressure: float = 0.10     # one-frame pressure baseline threshold
    # splits (chronological, per symbol, by frame number)
    dev_frac: float = 0.40
    val_frac: float = 0.30
    # inference
    block_len: int = 20              # block bootstrap block (frames)
    n_boot: int = 1000
    n_perm: int = 500
    seed: int = 7
    # decision
    n_min_episodes: int = 30         # distinct composite episodes in the HOLDOUT to decide at all
    n_min_frames: int = 500          # fused frames in total to run the test at all
    alpha: float = 0.05
    # stale / duplicate definitions used by the removal tests
    stale_unchanged_run: int = 3     # book payload unchanged for ≥ 3 consecutive frames
    stale_watch_age_s: float = 120.0
    # matched controls
    match_keys: Tuple[str, ...] = ("symbol", "tod_bucket", "spread_bucket")
    n_tod_buckets: int = 5

    def describe(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


DESIGN = Design()

COMPONENTS = ("persistent_bid_pressure", "ask_thinning", "bid_replenishment", "multi_level_transition",
              "spread_stable", "time_persistence", "price_response_ok")

BASELINES = ("b_imb_l1", "b_imb_top5", "b_imb_weighted", "b_largest_wall_bid", "b_one_frame_pressure")

VERDICT_RULE = """
BLOCKED  if the holdout has fewer than n_min_episodes distinct composite episodes or the
         fused table has fewer than n_min_frames frames (denominator too small to decide).
KEEP     if, on the HOLDOUT, at the primary horizon:
           (a) lift(composite) − lift(best simple baseline) > 0 with block-bootstrap 95 % CI lower bound > 0;
           (b) timestamp-permutation p < alpha for the composite's lift;
           (c) the side-flipped (mirrored) composite predicts DOWN (its down-lift > 0);
           (d) the anchor-shift placebo (signal shifted −h) shows |lift| < ½ of the real lift;
           (e) the sign of the incremental lift is preserved after stale, duplicate, crossed/locked and
               largest-wall removal;
           (f) leave-one-symbol-out: the incremental lift stays > 0 for every left-out symbol;
           (g) the incremental lift is > 0 in both liquidity halves.
KILL     otherwise. The hypothesis is not protected: failing (a) alone kills it.
"""
