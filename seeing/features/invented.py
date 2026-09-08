"""New mathematics for the DSE book — quantities nothing in this repository has.

Everything already here measures the book in **quantity**: imb_l1, imb_top5,
imb_weighted, TLPI — all of them are (bid size − ask size) / (bid size + ask size)
under some weighting. That is one idea measured seven ways, and the discovery run
showed exactly what you would expect from one idea: every variant lands within a
standard deviation of every other.

These are different ideas. Each answers a question quantity cannot.

1. TIME, not size — the exhaustion clock
------------------------------------------
A 10,000-share wall means nothing until you know the flow hitting it. At 200
shares a second it is gone in fifty seconds; at 5 a second it stands all session.

    ttc_ask = ask_qty1 / (buy volume per second, trailing W frames)
    ttc_bid = bid_qty1 / (sell volume per second, trailing W frames)
    ttc_asym = log(ttc_bid / ttc_ask)

Positive means the **ask** empties first — the offer breaks before the bid does,
so price goes up. This is the first quantity in the repository that is denominated
in seconds. Size and flow are both observed; their ratio is not a new observation,
it is a new *question*.

2. PRICE, not size — what a real trade actually costs
------------------------------------------------------
Every imbalance here asks "which side is bigger". A trader does not care which
side is bigger; they care **what it costs to get in and what it costs to get out**.
Walk the book for a notional Q and you get that directly:

    slip_buy(Q)  = (VWAP of consuming asks up to Q − mid) / tick
    slip_sell(Q) = (mid − VWAP of consuming bids up to Q) / tick
    wbc_asym(Q)  = slip_sell − slip_buy

Positive means it is **cheaper to buy than to sell** — the book leans up, measured
in ticks of cost rather than in shares. Two books with identical `imb_l1` can have
opposite `wbc_asym` when their price ladders differ, and the gapped-book result
says that difference is common (40 % of frames) and carries information.

This is also the one family that speaks Stage 10.5's language natively: it *is* a
slippage model, computed from the displayed book, so a candidate built on it
carries its own cost estimate instead of waiting for one.

3. SHAPE, not size — is the size one wall or a staircase?
-----------------------------------------------------------
    hhi_bid = Σ (q_i / Σq)²        1.0 = everything at one level

A side holding all its quantity in one level is a different object from one
holding the same quantity spread over five: the wall is a single decision by a
single participant and it can vanish in one action. Concentration measures that;
depth does not.

4. THE ONE THAT WORKS — quote and tape are different information
------------------------------------------------------------------
Everything in sections 1-3 is a function of the **displayed book**, and measured
against E1 they all correlate ρ ≈ 0.58-0.66. That is the finding those sections
actually produced: different algebra on the same source is still the same source.
The one quantity among them that is genuinely uncorrelated (`hhi_asym`, ρ = 0.04)
carries no signal at all.

The information that *is* independent comes from the other side of the market:

    qt_flow = signed interval volume / top-5 liquidity

The book says what is **waiting**. The tape says what is **actually happening**.
Measured on the calibration session, `qt_flow` correlates with E1 at ρ = **+0.08**
— effectively independent — and fusing the two by rank-average:

    QT = rank(E1) + rank(qt_flow)

    h2   AUC 0.6897 -> 0.7829   +0.0932  95% CI [+0.0576, +0.1216]   ESTABLISHED
    h4   AUC 0.6953 -> 0.7301   +0.0348  95% CI [+0.0122, +0.0553]   ESTABLISHED
    h8   AUC 0.6412 -> 0.6615   +0.0202  95% CI [-0.0037, +0.0430]   not established

It improves in **11 of 13** symbols. This is the only construction tested in this
repository that beats E1 with an interval clear of zero, and it is not exotic — it
is the preregistration's own primary question ("does *fused* order-book + order-flow
information beat the strongest standalone baseline") answered in the affirmative on
one session.

Two properties make it worth carrying rather than another imbalance variant:

* **The gain is largest at the SHORTEST horizon** (+0.093 at h2 against +0.035 at
  h4 and nothing at h8), and h2 is also the horizon where E1's adverse excursion is
  smallest relative to its gain (0.58× against 2.1× at h4). Edge and path point the
  same way for once.
* **The single frame beats the accumulation.** A 4-frame cumulative signed volume
  *hurts* (−0.021). It is the flow arriving now that matters, not flow that arrived
  and is already in the price — the same shape as the level-beats-derivative result.

Causality: `signed_int_volume` at frame t is built from trades stamped in
(t−1, t] and a side classified against the *previous* frame's book. Nothing in it
is later than t, and `tests/test_invented.py` pins that by corrupting the future.

5. The compound quantity sections 1-3 make possible
-----------------------------------------------------
    pressure_per_cost = ttc_asym / (slip_buy + 0.5)

How much exhaustion pressure you are getting per tick of entry cost. A signal that
fires only where the book is thick enough to be *cheap* to enter is a different
signal from one that fires wherever the imbalance is large — and the second one
buys its lift at a price nobody measured.

Truth
-----
Every quantity is INFERRED from OBSERVED displayed levels and OBSERVED interval
volume. Two rules are load-bearing and are enforced rather than documented:

* **Flow is measured strictly backwards.** `ttc_*` divides by volume from frames
  at or before t. A rate that included the current frame's own forward volume
  would be reading the answer.
* **A walk that runs out of book is not a cheap walk.** If the displayed levels
  cannot fill Q, `slip_*` is NaN and `walk_filled` records the fraction that could
  be filled. Filling the remainder at the last displayed price would invent
  liquidity that was never shown, and that invention would look like low cost
  exactly where cost is highest.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

Level = Tuple[float, float]

# Trailing window for the flow rate, in frames. 4 frames ~ 3 minutes at the
# observed 43.7 s cadence — the horizon the reference signal is strongest at.
FLOW_WINDOW = 4
# Notional sizes to walk the book for, in shares. Spanning a retail clip to a
# size that reaches past the touch on most DSE names.
WALK_SIZES: Tuple[int, ...] = (500, 2000, 10000)
# Below this many shares per second the exhaustion clock is not meaningful —
# dividing by a near-zero rate manufactures an enormous time from noise.
MIN_FLOW_RATE = 1e-6


def _clean(levels: Any) -> List[Level]:
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


# --------------------------------------------------------------------- 2. cost geometry
def walk_book(levels: Any, notional: float, mid: Optional[float],
              tick: Optional[float], side: str) -> Tuple[float, float]:
    """Consume `notional` shares against one side. Returns (slippage_ticks, filled_frac).

    Slippage is the volume-weighted execution price against the mid, in ticks —
    what the trade actually costs relative to the reference. NaN when the book
    cannot fill the order: the unfilled remainder has no observed price, and
    assuming the last displayed level would price the hardest fills as the
    cheapest ones.
    """
    lv = _clean(levels)
    if not lv or notional <= 0 or mid is None or not math.isfinite(mid):
        return float("nan"), float("nan")
    if tick is None or not math.isfinite(tick) or tick <= 0:
        return float("nan"), float("nan")
    remaining, cost = float(notional), 0.0
    for p, q in lv:
        take = min(remaining, q)
        cost += take * p
        remaining -= take
        if remaining <= 0:
            break
    filled = (notional - max(remaining, 0.0)) / notional
    if remaining > 0:
        return float("nan"), float(filled)          # the book ran out — not cheap, unknown
    vwap = cost / notional
    slip = (vwap - mid) if side == "ask" else (mid - vwap)
    return float(slip / tick), float(filled)


def herfindahl(levels: Any) -> float:
    """Σ (share of the side's quantity at each level)². 1.0 = a single wall."""
    lv = _clean(levels)
    tot = sum(q for _, q in lv)
    if not lv or tot <= 0:
        return float("nan")
    return float(sum((q / tot) ** 2 for _, q in lv))


# ------------------------------------------------------------------------- the frame
def invented_frame(states: pd.DataFrame, flow_window: int = FLOW_WINDOW,
                   walk_sizes: Sequence[int] = WALK_SIZES) -> pd.DataFrame:
    """Attach every quantity above. Strictly causal: no frame reads its own future."""
    f = states.sort_values(["symbol", "t_frame"], kind="mergesort").reset_index(drop=True).copy()
    key = ["symbol", "session"] if "session" in f.columns else ["symbol"]
    g = f.groupby(key, sort=False)

    # ---- 1. the exhaustion clock ------------------------------------------------
    # Signed interval volume splits the flow into the part that lifted offers and
    # the part that hit bids. Both are shifted one frame: the rate that decides
    # whether THIS frame's wall breaks is the rate observed BEFORE it.
    vol = pd.to_numeric(f.get("int_d_volume"), errors="coerce")
    signed = pd.to_numeric(f.get("signed_int_volume"), errors="coerce")
    buy_vol = ((vol + signed) / 2.0).clip(lower=0)
    sell_vol = ((vol - signed) / 2.0).clip(lower=0)
    f["_buy_vol"], f["_sell_vol"] = buy_vol, sell_vol
    dt = pd.to_numeric(f["frame_dt_s"], errors="coerce")
    f["_dt"] = dt

    def _rate(col: str) -> pd.Series:
        num = f.groupby(key, sort=False)[col].transform(
            lambda s: s.shift(1).rolling(flow_window, min_periods=1).sum())
        den = f.groupby(key, sort=False)["_dt"].transform(
            lambda s: s.shift(1).rolling(flow_window, min_periods=1).sum())
        return (num / den.replace(0, np.nan)).where(den > 0)

    f["buy_rate_s"] = _rate("_buy_vol")
    f["sell_rate_s"] = _rate("_sell_vol")

    bq1 = pd.to_numeric(f.get("bid_qty1"), errors="coerce")
    aq1 = pd.to_numeric(f.get("ask_qty1"), errors="coerce")
    f["ttc_ask_s"] = aq1 / f["buy_rate_s"].where(f["buy_rate_s"] > MIN_FLOW_RATE)
    f["ttc_bid_s"] = bq1 / f["sell_rate_s"].where(f["sell_rate_s"] > MIN_FLOW_RATE)
    # log ratio: positive = the ask empties first = price rises
    f["ttc_asym"] = np.log(f["ttc_bid_s"] / f["ttc_ask_s"])
    f["ttc_asym"] = f["ttc_asym"].replace([np.inf, -np.inf], np.nan)
    # the faster clock, in seconds — how soon SOMETHING at the touch gives way
    f["ttc_min_s"] = f[["ttc_ask_s", "ttc_bid_s"]].min(axis=1)

    # ---- 2. cost geometry -------------------------------------------------------
    bl, al = list(f["bid_levels"]), list(f["ask_levels"])
    mids = list(pd.to_numeric(f["mid"], errors="coerce"))
    tks = list(pd.to_numeric(f["tick_size"], errors="coerce"))
    for Q in walk_sizes:
        sb, fb, sa, fa = [], [], [], []
        for b, a, m, t in zip(bl, al, mids, tks):
            s_a, fl_a = walk_book(a, Q, m, t, "ask")     # cost to BUY
            s_b, fl_b = walk_book(b, Q, m, t, "bid")     # cost to SELL
            sa.append(s_a); fa.append(fl_a)
            sb.append(s_b); fb.append(fl_b)
        f[f"slip_buy_{Q}"] = sa
        f[f"slip_sell_{Q}"] = sb
        f[f"walk_filled_buy_{Q}"] = fa
        f[f"walk_filled_sell_{Q}"] = fb
        # positive = cheaper to buy than to sell = the book leans up, in TICKS
        f[f"wbc_asym_{Q}"] = f[f"slip_sell_{Q}"] - f[f"slip_buy_{Q}"]
        # what a round trip costs right now, if it can be done at all
        f[f"round_trip_{Q}"] = f[f"slip_buy_{Q}"] + f[f"slip_sell_{Q}"]

    # ---- 3. shape ---------------------------------------------------------------
    f["hhi_bid"] = [herfindahl(b) for b in bl]
    f["hhi_ask"] = [herfindahl(a) for a in al]
    f["hhi_asym"] = f["hhi_bid"] - f["hhi_ask"]
    # a wall is one level holding most of a side that has several levels
    f["bid_is_wall"] = (f["hhi_bid"] > 0.6) & (pd.to_numeric(f["n_bid"], errors="coerce") >= 3)
    f["ask_is_wall"] = (f["hhi_ask"] > 0.6) & (pd.to_numeric(f["n_ask"], errors="coerce") >= 3)

    # ---- 4. pressure per unit of cost ------------------------------------------
    # The half-tick floor keeps a free-looking entry from dividing the ratio to
    # infinity; it is the smallest cost any real crossing trade pays.
    q0 = walk_sizes[0]
    f["pressure_per_cost"] = f["ttc_asym"] / (f[f"slip_buy_{q0}"].abs() + 0.5)
    f["cost_adjusted_imb"] = (pd.to_numeric(f.get("imb_l1"), errors="coerce")
                              / (f[f"round_trip_{q0}"].abs() + 1.0))

    # ---- 5. quote-and-tape fusion — the one that beats E1 -----------------------
    # Normalised by displayed top-5 liquidity so a 1,000-share print means one thing
    # on GP and another on a thin name. Both inputs are at or before t.
    liq5 = pd.to_numeric(f.get("liquidity_top5"), errors="coerce")
    if liq5.isna().all():
        liq5 = (pd.to_numeric(f.get("bid_depth_top5"), errors="coerce")
                + pd.to_numeric(f.get("ask_depth_top5"), errors="coerce"))
    f["qt_flow"] = pd.to_numeric(f.get("signed_int_volume"), errors="coerce") / liq5.replace(0, np.nan)
    f["qt_fusion"] = _rank_sum(f, ["imb_l1", "qt_flow"])
    return f.drop(columns=["_buy_vol", "_sell_vol", "_dt"])


def causal_rank(s: pd.Series, key: pd.DataFrame, min_history: int = 20) -> pd.Series:
    """Percentile of each value among that series' OWN PAST values, per symbol.

    A whole-sample `rank(pct=True)` is look-ahead: frame t's percentile depends on
    every frame after it, so the number could not be computed live and the backtest
    that uses it is reading its own future. `tests/test_invented.py` caught exactly
    that in the first version of the fusion below.

    This ranks each value against the strictly earlier values of the same symbol in
    the same session — computable at frame t from frames < t alone. It is NaN until
    `min_history` prior observations exist, because a percentile among three points
    is not a percentile.
    """
    def _one(x: pd.Series) -> pd.Series:
        v = x.to_numpy(dtype=float)
        out = np.full(len(v), np.nan)
        seen: List[float] = []
        for i, cur in enumerate(v):
            if len(seen) >= min_history and np.isfinite(cur):
                out[i] = float(np.mean(np.asarray(seen) < cur)) - 0.5
            if np.isfinite(cur):
                seen.append(float(cur))
        return pd.Series(out, index=x.index)

    return s.groupby([key[c] for c in key.columns], sort=False, group_keys=False).apply(_one)


def _rank_sum(f: pd.DataFrame, cols: Sequence[str], min_history: int = 20) -> pd.Series:
    """Causal rank-average of several scores — no fitted weights, no future.

    Trailing percentile ranks put quantities with different units on one scale
    without anyone choosing a coefficient, so the combination cannot be accused of
    having been fitted, and every input is available at frame t.
    """
    keycols = ["symbol", "session"] if "session" in f.columns else ["symbol"]
    key = f[keycols]
    out = pd.Series(0.0, index=f.index)
    n = 0
    for c in cols:
        s = pd.to_numeric(f.get(c), errors="coerce")
        if s.notna().any():
            out = out.add(causal_rank(s, key, min_history), fill_value=np.nan)
            n += 1
    return out if n else pd.Series(np.nan, index=f.index)


def cross_sectional(f: pd.DataFrame, cols: Sequence[str], bucket: str = "60s") -> pd.DataFrame:
    """Rank each quantity across the universe at the same instant.

    The discovery run's strongest candidate was a cross-sectional rank, so every
    new quantity gets one. Ranks are computed inside a timestamp bucket, never
    across one, so nothing from a later instant enters.
    """
    d = f.copy()
    d["_tbin"] = d["t_frame"].dt.floor(bucket)
    for c in cols:
        if c in d.columns:
            d[f"xs_{c}"] = d.groupby("_tbin")[c].rank(pct=True) - 0.5
    return d.drop(columns=["_tbin"])


TRUTH = {
    "ttc_ask_s / ttc_bid_s / ttc_asym":
        "INFERRED — displayed touch quantity divided by a STRICTLY TRAILING flow rate "
        "(volume from frames at or before t−1). NOT_OBSERVABLE where the rate is below "
        f"{MIN_FLOW_RATE} shares/s, because dividing by near-zero manufactures a time",
    "slip_buy_Q / slip_sell_Q / wbc_asym_Q":
        "INFERRED — VWAP of walking the DISPLAYED book for Q shares, against the mid, in "
        "ticks. NOT_OBSERVABLE when the displayed levels cannot fill Q; `walk_filled_*` "
        "records how much could be. Filling the remainder at the last shown price would "
        "invent liquidity and price the hardest fills as the cheapest",
    "hhi_bid / hhi_ask / hhi_asym":
        "INFERRED — Herfindahl concentration of each side's displayed quantity; 1.0 is a "
        "single wall. Says nothing about hidden or iceberg size, which is NOT_OBSERVABLE",
    "qt_flow / qt_fusion":
        "INFERRED — signed interval volume over displayed top-5 liquidity, and its "
        "rank-sum with imb_l1. Both inputs are at or before t: the trades are stamped "
        "in (t-1, t] and the side is classified against the PREVIOUS frame's book. The "
        "fusion has no fitted weights — it is a rank average",
    "pressure_per_cost / cost_adjusted_imb":
        "INFERRED — a ratio of the two above. Its denominator is a displayed-book cost "
        "estimate, not a realised one; a realised cost model is Stage 10.5",
}
