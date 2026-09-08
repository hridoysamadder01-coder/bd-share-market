"""The candidate families, and the state each one is built from.

Every candidate is a boolean signal on a frame, built only from information at or
before that frame. They are grouped by the idea being tested, because a family
that fails as a whole is a more useful finding than one candidate that failed.

REFERENCE      E1 and the deeper-book imbalances at the same preregistered θ.
               This is the benchmark every new idea must answer to.
TLPI           the λ family at the same θ.
DYNAMICS       A…J — level, crossing, velocity, acceleration, persistence,
               exhaustion, reversal, and the two touch-vs-deep disagreements.
GEOMETRY       the six touch/deep states, named rather than scored.
RISK           depletion, retreat, spread deterioration, and their union.
VETO           a reference signal with the risk states removed.
CONTEXT        market and sector permission, and the share-specific residual.
CROSS          the cross-timescale combinations.

Two rules run through all of them:

* **A veto can only subtract.** Every VETO candidate is `signal & ~risk`, never
  `signal | something`, so it can lose coverage and cannot invent it. Its value
  is judged on what it does to the matched lift *and* to the episode count —
  a veto that doubles the lift by keeping nine rows has not helped.
* **A sector aggregate needs a sector.** Six of the fourteen captured symbols are
  the only member of their sector here, so their "sector pressure" would be their
  own pressure wearing a different name. Those are NOT_OBSERVABLE for the sector
  tier rather than silently self-referential, and `MIN_SECTOR_MEMBERS` is what
  enforces it.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from seeing.features.geometry import E1_THRESHOLD, LAMBDA_GRID

# A sector tier computed from fewer than this many members is the share itself.
MIN_SECTOR_MEMBERS = 3
# Cross-sectional context is computed inside a timestamp bucket, never across one.
XS_BUCKET = "60s"


def _col(d: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(d[name], errors="coerce") if name in d.columns else pd.Series(
        np.nan, index=d.index)


def add_dynamics(d: pd.DataFrame, pressure_col: str = "imb_l1",
                 lags: Sequence[int] = (1, 2, 4), persist_w: int = 4) -> pd.DataFrame:
    """Velocity, acceleration and persistence of a pressure series, causally.

    Differences are taken per symbol and per session with `shift`, so frame 0 of a
    symbol has no velocity rather than borrowing the previous symbol's last frame.
    `_run` counts how many consecutive frames the pressure has been above θ,
    *including* the current one — a persistence count that peeked one frame ahead
    would make "persistent" mean "about to continue".
    """
    f = d.sort_values(["symbol", "t_frame"], kind="mergesort").reset_index(drop=True).copy()
    g = f.groupby(["symbol", "session"], sort=False)
    P = _col(f, pressure_col)
    f["P"] = P
    for k in lags:
        f[f"dP{k}"] = P - g["P"].shift(k)
    f["V"] = f["dP1"]
    f["A"] = f["V"] - g["V"].shift(1)

    above = (P > E1_THRESHOLD).astype(float)
    below = (P < -E1_THRESHOLD).astype(float)
    f["P_above"] = above.astype(bool)
    f["P_below"] = below.astype(bool)
    # consecutive frames at or above θ, current frame included
    f["run_above"] = above.groupby([f["symbol"], f["session"]]).transform(
        lambda s: s.groupby((s == 0).cumsum()).cumsum())
    f["run_below"] = below.groupby([f["symbol"], f["session"]]).transform(
        lambda s: s.groupby((s == 0).cumsum()).cumsum())
    f["P_prev"] = g["P"].shift(1)
    f["P_max_w"] = P.groupby([f["symbol"], f["session"]]).transform(
        lambda s: s.rolling(persist_w, min_periods=1).max())
    f["P_mean_w"] = P.groupby([f["symbol"], f["session"]]).transform(
        lambda s: s.rolling(persist_w, min_periods=persist_w).mean())
    return f


def add_context(d: pd.DataFrame, sector_of: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """Market and sector pressure at the same instant, plus the share residual.

    Both tiers are computed **inside a timestamp bucket**, so nothing from a later
    instant enters, and the sector tier is left NaN where the sector has fewer
    than `MIN_SECTOR_MEMBERS` symbols in the capture. `share_resid` is the share's
    own pressure minus its market tier — the part that is not the market.
    """
    f = d.copy()
    f["sector"] = f["symbol"].map(sector_of or {}).fillna("UNKNOWN")
    f["_tbin"] = f["t_frame"].dt.floor(XS_BUCKET)

    f["market_pressure"] = f.groupby("_tbin")["P"].transform("mean")
    f["market_n"] = f.groupby("_tbin")["P"].transform("count")

    members = f.groupby("sector")["symbol"].nunique()
    big = set(members[members >= MIN_SECTOR_MEMBERS].index)
    sp = f.groupby(["_tbin", "sector"])["P"].transform("mean")
    sn = f.groupby(["_tbin", "sector"])["P"].transform("count")
    f["sector_pressure"] = sp.where(f["sector"].isin(big))
    f["sector_n"] = sn.where(f["sector"].isin(big))
    f["sector_observable"] = f["sector"].isin(big)

    f["share_resid"] = f["P"] - f["market_pressure"]
    f["share_resid_sector"] = f["P"] - f["sector_pressure"]
    f["xs_rank"] = f.groupby("_tbin")["P"].rank(pct=True) - 0.5
    f["market_up"] = f["market_pressure"] > 0
    f["sector_up"] = f["sector_pressure"] > 0
    return f.drop(columns=["_tbin"])


def add_risk(d: pd.DataFrame, k: int = 3, deplete_ratio: float = 0.70) -> pd.DataFrame:
    """The risk states, each measurable and each about the exit rather than the entry."""
    f = d.copy()
    g = f.groupby(["symbol", "session"], sort=False)
    liq = _col(f, "liquidity_top5")
    f["liq_top5"] = liq
    f["liq_ratio_k"] = liq / g["liq_top5"].shift(k).replace(0, np.nan)
    f["r_liquidity_depleting"] = f["liq_ratio_k"] < deplete_ratio
    tick = _col(f, "tick_size")
    f["r_bid_retreat"] = (g["best_bid"].diff() / tick).where(tick > 0) < 0
    f["r_ask_replenish"] = (g["ask_depth_top5"].diff() > 0)
    f["r_spread_widening"] = g["spread_ticks"].diff() > 0
    f["r_negative_accel"] = f["A"] < 0
    f["r_market_weak"] = f["market_pressure"] < 0
    f["r_sector_weak"] = f["sector_pressure"] < 0        # NaN where sector unobservable
    comp = ["r_liquidity_depleting", "r_bid_retreat", "r_spread_widening"]
    f["risk_count"] = sum(f[c].fillna(False).astype(int) for c in comp)
    f["risk_veto"] = f["risk_count"] > 0
    f["risk_veto_hard"] = f["risk_count"] >= 2
    return f


# --------------------------------------------------------------------------- the battery
def build(d: pd.DataFrame) -> List[Dict[str, Any]]:
    """Every candidate as (id, family, logic, signal). Signals are booleans on `d`."""
    th = E1_THRESHOLD
    C: List[Dict[str, Any]] = []

    def add(cid: str, family: str, logic: str, sig: Any, formula: str = "",
            truth: str = "INFERRED", outcome: str = "up") -> None:
        """`outcome` is the direction the candidate CLAIMS, and it is scored on that.

        An ask-side or risk candidate asserts a fall. Scoring it on P(up) and
        recording the resulting negative lift as KILLED would label a correct
        downside signal as a failed upside one — the sign of the finding
        inverted by the choice of denominator.
        """
        s = pd.Series(np.asarray(sig), index=d.index).fillna(False).astype(bool)
        C.append({"candidate_id": cid, "family": family, "logic": logic,
                  "formula": formula, "truth_class": truth, "signal": s,
                  "outcome": outcome})

    L1, T3, T5, W, ALL = (_col(d, c) for c in
                          ("imb_l1", "imb_top3", "imb_top5", "imb_weighted", "imb_all"))

    # ---- REFERENCE: the benchmark every new idea answers to
    add("E1", "reference", f"imb_l1 > {th}", L1 > th,
        "(bid_qty1-ask_qty1)/(bid_qty1+ask_qty1)", "INFERRED from OBSERVED L1 quantities")
    add("REF_top3", "reference", f"imb_top3 > {th}", T3 > th)
    add("REF_top5", "reference", f"imb_top5 > {th}", T5 > th)
    add("REF_weighted", "reference", f"imb_weighted > {th}", W > th)
    add("REF_all", "reference", f"imb_all > {th}", ALL > th)

    # ---- TLPI λ family, identical threshold
    for lam in LAMBDA_GRID:
        c = f"tlpi_{lam:g}".replace(".", "p")
        add(f"TLPI_l{lam:g}", "tlpi", f"TLPI({lam:g}) > {th}", _col(d, c) > th,
            "Σq·exp(-λ·|p-mid|/tick) imbalance")
        add(f"TLPI_l{lam:g}_rank", "tlpi_rank_ablation",
            f"rank-decayed twin of TLPI({lam:g}) > {th}", _col(d, c + "_rank") > th)

    # ---- DYNAMICS A…J on the E1 pressure series
    P, Pp, V, A = _col(d, "P"), _col(d, "P_prev"), _col(d, "V"), _col(d, "A")
    add("DYN_A_level", "dynamics", f"P > {th}", P > th)
    add("DYN_B_crossing", "dynamics", f"P crosses up through {th}", (P > th) & (Pp <= th))
    add("DYN_C_level_vel", "dynamics", "P > θ and V > 0", (P > th) & (V > 0))
    add("DYN_D_level_vel_acc", "dynamics", "P > θ and V > 0 and A > 0",
        (P > th) & (V > 0) & (A > 0))
    add("DYN_E_touch_not_deep", "dynamics", "L1 > θ while |top5| ≤ θ (touch only)",
        (L1 > th) & (T5.abs() <= th))
    add("DYN_F_l1_pos_top5_weak", "dynamics", "L1 > θ and top5 < 0", (L1 > th) & (T5 < 0))
    # The touch and the deep book disagree. The touch-locality hypothesis says the
    # TOUCH wins, so this candidate claims a FALL and is scored on P(down).
    add("DYN_G_top5_pos_l1_weak", "dynamics", "top5 > θ and L1 < 0 — deep bullish, touch bearish",
        (T5 > th) & (L1 < 0), outcome="down")
    add("DYN_H_persistent", "dynamics", "P above θ for ≥ 3 consecutive frames",
        _col(d, "run_above") >= 3)
    add("DYN_I_exhaustion", "dynamics", "P above θ but V < 0 and A < 0",
        (P > th) & (V < 0) & (A < 0))
    add("DYN_J_reversal", "dynamics", "P crosses from below −θ to above +θ",
        (P > th) & (Pp < -th))

    # ---- GEOMETRY: the six named touch/deep states
    loc = L1 - T5
    add("GEO_touch_dominant_bid", "geometry", "L1 > θ and L1 − top5 > θ",
        (L1 > th) & (loc > th))
    add("GEO_broad_bid", "geometry", "L1 > θ and top5 > θ", (L1 > th) & (T5 > th))
    add("GEO_deep_only_bid", "geometry", "top5 > θ and L1 ≤ θ", (T5 > th) & (L1 <= th))
    add("GEO_touch_dominant_ask", "geometry", "L1 < −θ and L1 − top5 < −θ",
        (L1 < -th) & (loc < -th), outcome="down")
    add("GEO_broad_ask", "geometry", "L1 < −θ and top5 < −θ", (L1 < -th) & (T5 < -th),
        outcome="down")
    add("GEO_deep_only_ask", "geometry", "top5 < −θ and L1 ≥ −θ", (T5 < -th) & (L1 >= -th),
        outcome="down")

    # ---- RISK states, measured on their own before they are used as vetoes
    # Risk states assert a fall, so they are scored on P(down) — the direction they
    # claim. Scored on P(up) a working downside state reads as a failed upside one.
    for cid, col, logic in (
            ("RISK_depletion", "r_liquidity_depleting", "top-5 liquidity lost ≥30 % over 3 frames"),
            ("RISK_bid_retreat", "r_bid_retreat", "best bid stepped down"),
            ("RISK_spread_widening", "r_spread_widening", "spread widened"),
            ("RISK_any", "risk_veto", "any of depletion / bid retreat / spread widening"),
            ("RISK_hard", "risk_veto_hard", "two or more risk states at once")):
        add(cid, "risk", logic, d[col] if col in d else False, outcome="down")

    # ---- VETO: the reference signal with risk removed. Subtracts only.
    veto = d["risk_veto"].fillna(False).astype(bool) if "risk_veto" in d else pd.Series(False, index=d.index)
    hard = d["risk_veto_hard"].fillna(False).astype(bool) if "risk_veto_hard" in d else pd.Series(False, index=d.index)
    add("VETO_E1_no_risk", "veto", "E1 and no risk state", (L1 > th) & ~veto)
    add("VETO_E1_no_hard_risk", "veto", "E1 and fewer than two risk states", (L1 > th) & ~hard)
    add("VETO_E1_no_depletion", "veto", "E1 and liquidity not depleting",
        (L1 > th) & ~d.get("r_liquidity_depleting", pd.Series(False, index=d.index)).fillna(False).astype(bool))

    # ---- CONTEXT: market and sector permission, and the share residual
    mkt_up = d["market_up"] if "market_up" in d else pd.Series(False, index=d.index)
    sec_up = d["sector_up"] if "sector_up" in d else pd.Series(np.nan, index=d.index)
    add("CTX_market_permission", "context", "E1 and market pressure > 0", (L1 > th) & mkt_up.fillna(False))
    add("CTX_sector_permission", "context", "E1 and sector pressure > 0 (observable sectors only)",
        (L1 > th) & sec_up.fillna(False).astype(bool))
    add("CTX_share_resid", "context", f"share pressure minus market > {th}",
        _col(d, "share_resid") > th)
    add("CTX_xs_rank_top", "context", "top decile of cross-sectional pressure at this instant",
        _col(d, "xs_rank") > 0.4)
    add("CTX_market_weak_share_strong", "context",
        "E1 while the market tier is negative — the decoupling case",
        (L1 > th) & (_col(d, "market_pressure") < 0))

    # ---- CROSS-TIMESCALE combinations, transparent and un-weighted
    t1 = _col(d, "tlpi_1")
    add("X_E1_and_TLPI1", "cross", "E1 and TLPI(1) both above θ", (L1 > th) & (t1 > th))
    add("X_TLPI1_vel", "cross", "TLPI(1) > θ and its velocity > 0",
        (t1 > th) & (_col(d, "tlpi_1_vel") > 0))
    add("X_TLPI1_vel_acc", "cross", "TLPI(1) > θ, velocity > 0, acceleration > 0",
        (t1 > th) & (_col(d, "tlpi_1_vel") > 0) & (_col(d, "tlpi_1_acc") > 0))
    add("X_E1_market_noveto", "cross", "E1, market permission, no risk state",
        (L1 > th) & mkt_up.fillna(False) & ~veto)
    add("X_E1_persist_noveto", "cross", "E1 persistent ≥3 frames and no risk state",
        (_col(d, "run_above") >= 3) & ~veto)
    add("X_touch_dominant_noveto", "cross", "touch-dominant bid and no risk state",
        (L1 > th) & (loc > th) & ~veto)
    return C


def sector_map(identity_map_path: Optional[str] = None) -> Dict[str, str]:
    """symbol → sector name, straight from the Stage 0 spine. OBSERVED or absent."""
    import json
    from seeing.fusion.unified import DEFAULT_IDENTITY_MAP
    with open(identity_map_path or DEFAULT_IDENTITY_MAP, encoding="utf-8") as fh:
        doc = json.load(fh)
    out: Dict[str, str] = {}
    for l in doc.get("core", doc).get("listings", []):
        if l.get("exchange") != "DSE":
            continue
        sn = (l.get("attributes") or {}).get("sector_name") or {}
        if sn.get("value"):
            out[l["code"]] = str(sn["value"])
    return out
