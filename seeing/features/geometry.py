"""Stage 3 — what the book's *shape* is, and how fast that shape is changing.

`micro.py` measures the book at fixed cut-offs: level 1, top 3, top 5, all. Those
are four arbitrary lines drawn through a continuous object. Two books with the
same `imb_top5` can be completely different shapes — one with its quantity pressed
against the touch, one with it parked eight ticks away — and every existing
imbalance in this repository scores them identically.

This module measures the shape itself.

TLPI — the family that contains the existing baselines
------------------------------------------------------
::

    TLPI(λ) = (B(λ) - A(λ)) / (B(λ) + A(λ))

    B(λ) = Σ_bids  q_i · exp(-λ · d_i)      d_i = |p_i - mid| / tick
    A(λ) = Σ_asks  q_j · exp(-λ · d_j)

λ is how fast attention decays with distance from the mid, in ticks. The family
is not a new indicator sitting beside the old ones — it *contains* them, and that
is what makes it testable rather than decorative:

* **λ = 0** — every level counts equally. TLPI(0) is algebraically identical to
  `imb_all`, the existing all-levels imbalance.
* **λ → ∞** — only the nearest level survives the decay. TLPI(∞) converges to
  E1, the L1 imbalance the preregistered θ = 0.20 rule is built on.

So the λ grid is a *continuum between two baselines this repository already uses*,
and the empirical question has a shape: if the response curve over λ is flat,
distance carries nothing and the family should be abandoned; if it peaks in the
interior, there is a decay scale the market has and neither endpoint captures.
Both identities are asserted in the tests, so a refactor that quietly breaks the
family's relationship to its own endpoints fails.

**Where distance is and is not different from level rank.** This is the honest
limit of the construction and it decides how much of the family is new. When the
two sides display a mirror-symmetric price ladder — bids at 10.00, 9.90, 9.80
against asks at 10.10, 10.20, 10.30 — level *k* sits the same distance from the
mid on both sides, the decay weight cancels level by level, and TLPI(λ) reduces
to an ordinary level-rank-decayed imbalance. Distance adds nothing there, and
must not be claimed to.

It differs only where the ladders differ: a **gapped** book, where one side's
quantity is parked further out in ticks than the other's at the same level index.
On the calibration session that is 40 % of frames (35 % gapped by more than a
tick), so the distinction is common enough to matter and rare enough that pooling
the two cases dilutes it. `ladder_mismatch_ticks` measures it per frame so the
two populations can be scored apart instead of averaged together.

Velocity and acceleration, on a clock that is not regular
---------------------------------------------------------
The frame cadence is irregular (median 43.7 s on the calibration session, with a
long tail). A per-frame difference therefore measures *two* things at once — how
much the book changed, and how long we happened to wait — and confounds them. Every
rate here is per **second**, from the frame's own `frame_dt_s`, and acceleration
is the change in that rate divided by elapsed time again. `Δt` below a floor is
treated as NOT_OBSERVABLE rather than dividing by something near zero.

Touch versus deep
-----------------
The touch (level 1) and the depth behind it are different populations. A bid that
is thick at the touch and hollow behind it is not the same object as one that is
thin at the touch with a wall four ticks back, and `geom_divergence` is the number
that separates them: the touch imbalance minus the deep imbalance. The centroid
pair says *where* each side's mass sits, in ticks, and the gap between them says
which side is standing closer.

Truth
-----
Every quantity here is INFERRED, derived from OBSERVED displayed levels by the
rules above. Two honesty rules are load-bearing:

* **Tick-scaled quantities are NOT_OBSERVABLE when the tick is not.** Stage 2
  leaves `tick_size` NaN before the first circuit poll, and this module does not
  fill it — a λ measured in ticks with a guessed tick is a guessed λ. The
  `*_rel` variants use relative distance `|p - mid| / mid` instead and stay
  computable, which is a different measurement and is named differently.
* **A one-sided or empty book has no imbalance.** It returns NaN, not 0.0. Zero
  means "balanced", which is the opposite of what an empty side means.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

Level = Tuple[float, float]

# The preregistered λ grid. Frozen here so a later run cannot quietly widen it and
# then report the best λ as if the grid had always been that size.
LAMBDA_GRID: Tuple[float, ...] = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)

# θ for E1. `micro/MICRO_PREREG.json` froze 0.20 and forbids threshold changes.
# It is imported, not chosen, and this module never fits it.
E1_THRESHOLD = 0.20

# Below this, a frame gap is too short to divide by: the resulting rate is noise
# amplified by an arbitrarily small denominator, so the rate is NOT_OBSERVABLE.
MIN_DT_S = 1.0
# ... and above this, the two frames are not adjacent observations of one process.
MAX_DT_S = 600.0


def _clean(levels: Any) -> List[Level]:
    """Displayed levels as (price, qty), dropping anything unusable. Order kept."""
    if levels is None or (isinstance(levels, float) and math.isnan(levels)):
        return []
    out: List[Level] = []
    for lv in levels:
        try:
            p, q = float(lv[0]), float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(p) and math.isfinite(q) and q > 0:
            out.append((p, q))
    return out


def decay_weight(distance: float, lam: float) -> float:
    """exp(-λd), with λ=0 giving 1 for every level (the unweighted endpoint)."""
    if lam == 0.0:
        return 1.0
    return math.exp(-lam * max(distance, 0.0))


def side_weight(levels: Sequence[Level], mid: float, scale: float, lam: float) -> float:
    """Σ q·exp(-λ·|p-mid|/scale) for one side. `scale` is a tick or the mid."""
    if not levels or not (scale and math.isfinite(scale) and scale > 0):
        return float("nan")
    return float(sum(q * decay_weight(abs(p - mid) / scale, lam) for p, q in levels))


def tlpi(bid_levels: Any, ask_levels: Any, mid: Optional[float],
         scale: Optional[float], lam: float) -> float:
    """TLPI(λ) for one book. NaN when a side is empty or the scale is unknown.

    A one-sided book is not balanced and is not "all bid": it is a book whose
    imbalance is undefined, and it says so.
    """
    b, a = _clean(bid_levels), _clean(ask_levels)
    if not b or not a or mid is None or not math.isfinite(mid):
        return float("nan")
    if scale is None or not math.isfinite(scale) or scale <= 0:
        return float("nan")
    wb = side_weight(b, mid, scale, lam)
    wa = side_weight(a, mid, scale, lam)
    tot = wb + wa
    if not math.isfinite(tot) or tot <= 0:
        return float("nan")
    return (wb - wa) / tot


def e1(bid_qty1: Optional[float], ask_qty1: Optional[float]) -> float:
    """The L1 imbalance the preregistered θ=0.20 rule reads. Reproduced, not redefined."""
    try:
        b, a = float(bid_qty1), float(ask_qty1)
    except (TypeError, ValueError):
        return float("nan")
    if not (math.isfinite(b) and math.isfinite(a)) or (b + a) <= 0:
        return float("nan")
    return (b - a) / (b + a)


def centroid(levels: Any, mid: Optional[float], scale: Optional[float]) -> float:
    """Quantity-weighted mean distance from the mid, in units of `scale`.

    Where the side's mass actually sits. A book with 100 lots one tick away and a
    book with 100 lots six ticks away have the same depth and different centroids.
    """
    lv = _clean(levels)
    if not lv or mid is None or not math.isfinite(mid) or not scale or scale <= 0:
        return float("nan")
    tot = sum(q for _, q in lv)
    if tot <= 0:
        return float("nan")
    return float(sum(q * abs(p - mid) / scale for p, q in lv) / tot)


def deep_imbalance(bid_levels: Any, ask_levels: Any) -> float:
    """Imbalance of everything BEHIND the touch — levels 2..N, both sides."""
    b, a = _clean(bid_levels)[1:], _clean(ask_levels)[1:]
    sb, sa = sum(q for _, q in b), sum(q for _, q in a)
    if not b or not a or (sb + sa) <= 0:
        return float("nan")
    return (sb - sa) / (sb + sa)


def touch_share(levels: Any) -> float:
    """How much of a side's displayed quantity is at the touch. 0..1."""
    lv = _clean(levels)
    tot = sum(q for _, q in lv)
    if not lv or tot <= 0:
        return float("nan")
    return float(lv[0][1] / tot)


def level_rank_imbalance(bid_levels: Any, ask_levels: Any, lam: float) -> float:
    """The same decay applied to level RANK instead of tick distance.

    The ablation twin for TLPI. It is what a reader would build without the
    distance idea, so the difference between the two is exactly what the distance
    idea contributes — and on a mirror-symmetric ladder that difference is zero
    by construction, which is the point. Reported, never used as a candidate in
    its own right.
    """
    b, a = _clean(bid_levels), _clean(ask_levels)
    if not b or not a:
        return float("nan")
    wb = sum(q * decay_weight(float(k), lam) for k, (_, q) in enumerate(b))
    wa = sum(q * decay_weight(float(k), lam) for k, (_, q) in enumerate(a))
    return (wb - wa) / (wb + wa) if (wb + wa) > 0 else float("nan")


def ladder_mismatch_ticks(bid_levels: Any, ask_levels: Any, mid: Optional[float],
                          scale: Optional[float]) -> float:
    """How far the two sides' price ladders diverge, in ticks — the gap in the book.

    Zero means the displayed levels mirror each other and distance decay is
    algebraically identical to level-rank decay. A large value means one side's
    quantity is standing much further out than the other's at the same level, and
    that difference is the only thing TLPI can see that a rank-based imbalance
    cannot. Comparing only the levels both sides actually show: a side with fewer
    levels is silent about the rest, not in disagreement about it.
    """
    b, a = _clean(bid_levels), _clean(ask_levels)
    if not b or not a or mid is None or not math.isfinite(mid) or not scale or scale <= 0:
        return float("nan")
    k = min(len(b), len(a))
    if k == 0:
        return float("nan")
    return max(abs(abs(b[i][0] - mid) / scale - abs(a[i][0] - mid) / scale) for i in range(k))


def book_slope(levels: Any, mid: Optional[float], scale: Optional[float]) -> float:
    """How fast a side thickens with distance: OLS slope of log1p(q) on distance.

    Positive = the size is behind the touch (a wall further out); negative = the
    size is at the touch and thins away. NaN with fewer than two usable levels,
    because one point has no slope — it is not a slope of zero.
    """
    lv = _clean(levels)
    if len(lv) < 2 or mid is None or not math.isfinite(mid) or not scale or scale <= 0:
        return float("nan")
    d = np.array([abs(p - mid) / scale for p, _ in lv], dtype=float)
    y = np.log1p(np.array([q for _, q in lv], dtype=float))
    dv = d.var()
    if dv <= 0:                       # every level at the same distance: no slope
        return float("nan")
    return float(((d - d.mean()) * (y - y.mean())).mean() / dv)


# ------------------------------------------------------------------ frame-level table
def geometry_frame(states: pd.DataFrame, lambdas: Sequence[float] = LAMBDA_GRID) -> pd.DataFrame:
    """One row per state: the whole shape vocabulary, strictly from frame t.

    Nothing here reads a later frame. Rates use the frame's own `frame_dt_s`,
    which is the gap to the frame *before* it.
    """
    if states is None or not len(states):
        return pd.DataFrame()
    f = states.sort_values(["symbol", "t_frame"], kind="mergesort").reset_index(drop=True).copy()

    mid = f["mid"].astype(float)
    tick = f["tick_size"].astype(float) if "tick_size" in f else pd.Series(np.nan, index=f.index)

    out: Dict[str, List[float]] = {}
    bl, al = list(f["bid_levels"]), list(f["ask_levels"])
    m, tk = list(mid), list(tick)

    for lam in lambdas:
        key = f"tlpi_{lam:g}".replace(".", "p")
        out[key] = [tlpi(b, a, mm, t, lam) for b, a, mm, t in zip(bl, al, m, tk)]
        # tick-free twin: computable when the circuit reference was not yet observed
        out[key + "_rel"] = [tlpi(b, a, mm, (mm if (mm and math.isfinite(mm)) else None), lam)
                             for b, a, mm in zip(bl, al, m)]
        # ablation twin: the same decay on level RANK. Identical to `key` on a
        # mirror-symmetric ladder; the difference is what distance contributes.
        out[key + "_rank"] = [level_rank_imbalance(b, a, lam) for b, a in zip(bl, al)]
    for k, v in out.items():
        f[k] = v

    f["ladder_mismatch_ticks"] = [ladder_mismatch_ticks(b, a, mm, t)
                                  for b, a, mm, t in zip(bl, al, m, tk)]
    # the population split the ablation must be read inside, not across
    f["ladder_symmetric"] = f["ladder_mismatch_ticks"] <= 0.01
    f["book_shape"] = np.where(f["ladder_mismatch_ticks"].isna(), "unknown",
                               np.where(f["ladder_mismatch_ticks"] <= 0.01, "symmetric",
                                        np.where(f["ladder_mismatch_ticks"] <= 1.0,
                                                 "slightly_gapped", "gapped")))

    f["e1"] = [e1(b, a) for b, a in zip(f["bid_qty1"], f["ask_qty1"])]
    f["e1_fires_up"] = f["e1"] > E1_THRESHOLD
    f["e1_fires_down"] = f["e1"] < -E1_THRESHOLD

    f["geom_touch_imb"] = f["e1"]
    f["geom_deep_imb"] = [deep_imbalance(b, a) for b, a in zip(bl, al)]
    f["geom_divergence"] = f["geom_touch_imb"] - f["geom_deep_imb"]
    f["geom_touch_share_bid"] = [touch_share(b) for b in bl]
    f["geom_touch_share_ask"] = [touch_share(a) for a in al]
    f["geom_touch_share_gap"] = f["geom_touch_share_bid"] - f["geom_touch_share_ask"]
    f["geom_centroid_bid"] = [centroid(b, mm, t) for b, mm, t in zip(bl, m, tk)]
    f["geom_centroid_ask"] = [centroid(a, mm, t) for a, mm, t in zip(al, m, tk)]
    # positive = the ask's mass is further from the mid than the bid's; the bid is
    # standing closer, which is a different statement from "the bid is bigger".
    f["geom_centroid_gap"] = f["geom_centroid_ask"] - f["geom_centroid_bid"]
    f["geom_slope_bid"] = [book_slope(b, mm, t) for b, mm, t in zip(bl, m, tk)]
    f["geom_slope_ask"] = [book_slope(a, mm, t) for a, mm, t in zip(al, m, tk)]
    f["geom_slope_gap"] = f["geom_slope_bid"] - f["geom_slope_ask"]

    return add_rates(f, [f"tlpi_{l:g}".replace(".", "p") for l in lambdas] +
                     ["e1", "geom_divergence", "geom_centroid_gap"])


def add_rates(f: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Per-second velocity and acceleration for each named column.

    Per second, not per frame: on an irregular clock a per-frame difference
    reports how long we waited as if it were how much the book moved. A gap
    outside [MIN_DT_S, MAX_DT_S] yields NaN — too short to divide by, or too long
    for the two frames to be adjacent observations of the same process.
    """
    g = f.groupby("symbol", sort=False)
    dt = f["frame_dt_s"].astype(float)
    usable = dt.between(MIN_DT_S, MAX_DT_S)
    dt_ok = dt.where(usable)
    for c in columns:
        if c not in f.columns:
            continue
        v = g[c].diff() / dt_ok
        f[f"{c}_vel"] = v
        # acceleration divides the change in velocity by elapsed time again, so it
        # needs BOTH this gap and the previous one to be usable.
        f[f"{c}_acc"] = f.groupby("symbol", sort=False)[f"{c}_vel"].diff() / dt_ok
    return f


# ------------------------------------------------------------------------- risk context
def risk_context(f: pd.DataFrame, depletion_frames: int = 3) -> pd.DataFrame:
    """The state of the exit, not of the opportunity.

    Two conditions decide whether a directional reading is *actionable* rather
    than merely present, and both are about what happens if it is wrong:

    * **liquidity depletion** — the top-5 liquidity now against its own level a
      few frames back. A book that is emptying is one where an exit costs more
      than the entry implied.
    * **adverse retreat** — the side you would sell into stepping *away*. The bid
      falling while you hold a long is the adverse case, and it is measured
      directly rather than inferred from price.

    `risk_veto` is their union. It is a **veto**, not a signal: it can only
    suppress, never generate, and nothing downstream may read it as direction.
    """
    d = f.copy()
    g = d.groupby("symbol", sort=False)
    k = depletion_frames

    liq = d["liquidity_top5"] if "liquidity_top5" in d else (d["bid_depth_top5"] + d["ask_depth_top5"])
    d["liq_top5"] = liq
    prev = g["liq_top5"].shift(k) if "liq_top5" in d else np.nan
    d["liq_ratio_k"] = d["liq_top5"] / prev.replace(0, np.nan)
    d["liquidity_depleting"] = d["liq_ratio_k"] < 0.70          # lost 30 % of the top-5 book

    d["bid_retreat_ticks"] = (g["best_bid"].diff() / d["tick_size"]).where(d["tick_size"] > 0)
    d["ask_retreat_ticks"] = (g["best_ask"].diff() / d["tick_size"]).where(d["tick_size"] > 0)
    d["adverse_retreat_long"] = d["bid_retreat_ticks"] < 0       # the exit for a long is falling
    d["adverse_retreat_short"] = d["ask_retreat_ticks"] > 0

    d["spread_widening"] = g["spread_ticks"].diff() > 0
    d["risk_veto"] = (d["liquidity_depleting"].fillna(False) |
                      d["adverse_retreat_long"].fillna(False) |
                      d["adverse_retreat_short"].fillna(False))
    return d


TRUTH = {
    "tlpi_<λ>": "INFERRED — Σq·exp(-λ·|p-mid|/tick) imbalance over displayed levels; "
                "NOT_OBSERVABLE where tick_size or a book side is missing",
    "tlpi_<λ>_rel": "INFERRED — the same rule with distance relative to the mid instead "
                    "of the tick; computable when the circuit reference is not yet observed, "
                    "and a DIFFERENT measurement from the tick-scaled one",
    "e1": "INFERRED — (bid_qty1-ask_qty1)/(bid_qty1+ask_qty1) on OBSERVED L1 quantities; "
          f"θ={E1_THRESHOLD} is preregistered in micro/MICRO_PREREG.json and never fitted here",
    "tlpi_<λ>_rank": "INFERRED — the ablation twin: the same decay on level rank rather than "
                     "tick distance. Identical to tlpi_<λ> on a mirror-symmetric ladder",
    "ladder_mismatch_ticks": "INFERRED — the largest per-level tick-distance difference between "
                             "the two displayed ladders; 0 means distance decay and rank decay "
                             "are algebraically the same on that frame",
    "geom_*": "INFERRED — touch/deep decomposition, centroids and slopes of the displayed book",
    "*_vel / *_acc": "INFERRED — per-second first and second differences; NaN where the frame "
                     f"gap is outside [{MIN_DT_S}, {MAX_DT_S}] s",
    "risk_veto": "INFERRED — a suppression condition on liquidity depletion or adverse retreat. "
                 "It is never a direction and must not be read as one",
}
