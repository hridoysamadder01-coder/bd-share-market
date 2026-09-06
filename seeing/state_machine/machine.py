"""State machine.

States (one per frame, per symbol; a description of the market, never an instruction):

    NO_BOOK              both sides empty
    ONE_SIDED            only one side displayed
    LOCKED_LIMIT_UP      best bid == upper limit (door open on the up side)
    LOCKED_LIMIT_DOWN    best ask == lower limit
    CROSSED_OR_LOCKED    bid ≥ ask without a limit explanation (sensor/timing artefact)
    STALE                book payload unchanged for ≥ stale_unchanged_run frames
    BID_PRESSURE_BUILDING    persistent bid pressure or ask thinning, composite not (yet) complete
    BID_PRESSURE_CONFIRMED   the full composite holds
    BID_PRESSURE_FAILED      a composite episode ended with the mid not higher than at its start
    BID_PRESSURE_RESOLVED    a composite episode ended with the mid higher than at its start
    ASK_PRESSURE_BUILDING / ASK_PRESSURE_CONFIRMED   the mirrored states
    BALANCED             none of the above

Transitions are logged with (symbol, t_frame, from, to). ``state_age`` counts
frames in the current state. The machine only reads columns produced by
``seeing.features.micro.features`` — all causal.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from ..experiment.design import DESIGN, Design

STATES = ("NO_BOOK", "ONE_SIDED", "LOCKED_LIMIT_UP", "LOCKED_LIMIT_DOWN", "CROSSED_OR_LOCKED", "STALE",
          "BID_PRESSURE_BUILDING", "BID_PRESSURE_CONFIRMED", "BID_PRESSURE_FAILED", "BID_PRESSURE_RESOLVED",
          "ASK_PRESSURE_BUILDING", "ASK_PRESSURE_CONFIRMED", "BALANCED")


def classify_frame(r: pd.Series, d: Design = DESIGN) -> str:
    if bool(r.get("empty", False)):
        return "NO_BOOK"
    if bool(r.get("one_sided", False)):
        return "ONE_SIDED"
    if bool(r.get("bid_at_upper_limit", False)):
        return "LOCKED_LIMIT_UP"
    if bool(r.get("ask_at_lower_limit", False)):
        return "LOCKED_LIMIT_DOWN"
    if bool(r.get("crossed", False)) or bool(r.get("locked", False)):
        return "CROSSED_OR_LOCKED"
    if bool(r.get("stale_book", False)):
        return "STALE"
    if bool(r.get("composite", False)):
        return "BID_PRESSURE_CONFIRMED"
    if bool(r.get("mirror_composite", False)):
        return "ASK_PRESSURE_CONFIRMED"
    if bool(r.get("persistent_bid_pressure", False)) or bool(r.get("ask_thinning", False)):
        return "BID_PRESSURE_BUILDING"
    imb = r.get("imb_top5", np.nan)
    if pd.notna(imb) and imb < -d.theta_imb:
        return "ASK_PRESSURE_BUILDING"
    return "BALANCED"


def run_state_machine(f: pd.DataFrame, d: Design = DESIGN) -> pd.DataFrame:
    out = f.sort_values(["symbol", "t_frame"], kind="mergesort").copy()
    states: List[str] = []
    ages: List[int] = []
    transitions: List[Dict] = []
    for sym, g in out.groupby("symbol", sort=False):
        prev = None
        age = 0
        ep_start_mid = None
        for idx, r in g.iterrows():
            s = classify_frame(r, d)
            # episode resolution: when a CONFIRMED run ends, label this frame by the outcome of the run
            if prev == "BID_PRESSURE_CONFIRMED" and s != "BID_PRESSURE_CONFIRMED":
                if ep_start_mid is not None and pd.notna(r.get("mid")):
                    s = "BID_PRESSURE_RESOLVED" if r["mid"] > ep_start_mid else "BID_PRESSURE_FAILED"
                ep_start_mid = None
            if s == "BID_PRESSURE_CONFIRMED" and prev != "BID_PRESSURE_CONFIRMED":
                ep_start_mid = r.get("mid")
            if s == prev:
                age += 1
            else:
                if prev is not None:
                    transitions.append({"symbol": sym, "t_frame": r["t_frame"], "from": prev, "to": s,
                                        "frame_no": r.get("frame_no")})
                age = 0
            states.append(s)
            ages.append(age)
            prev = s
    out["state"] = states
    out["state_age"] = ages
    out["is_transition"] = out["state_age"] == 0
    out.attrs["transitions"] = pd.DataFrame(transitions)
    out.attrs["states"] = STATES
    return out


def transition_matrix(out: pd.DataFrame) -> pd.DataFrame:
    t = out.attrs.get("transitions")
    if t is None or not len(t):
        return pd.DataFrame()
    return pd.crosstab(t["from"], t["to"])
