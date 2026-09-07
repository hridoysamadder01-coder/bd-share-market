"""State timeline: layer state machines with explicit transition rules,
durations and history.

Layers and their graphs (all rules read the current MarketState and the
layer's previous state; nothing looks ahead):

pressure     balanced → pressure_building → expansion
                                          → rejection → reversal
liquidity    normal → depletion → recovery → continuation
                               → no_recovery → vacuum
circuit      free → approach → hit → lock → unlock → relock (→ lock …)
accumulation none → accumulation_like → breakout → continuation
                                      → failed_pressure
streak       none → streak → weakening → break
mechanism:<name>  inactive → building → active → confirmed → resolved | failed

Every change is a Transition(t, from, to, layer, duration_prev_s) and is
appended to the per-symbol history and to the JSONL timeline store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .state import MarketState, Transition

LAYERS = ("pressure", "liquidity", "circuit", "accumulation", "streak")


def _pressure_state(ms: MarketState, prev: str) -> str:
    d, s = ms.pressure_direction, ms.pressure_strength or 0.0
    vel = ms.price_velocity
    resp = ms.price_only_response
    if d in (None, 0) or s < 0.30:
        # a rejection resolves into reversal when the direction flips; otherwise back to balanced
        if prev == "rejection" and d not in (None, 0):
            return "reversal"
        return "balanced" if prev not in ("reversal",) or s < 0.30 else "balanced"
    # d is ±1 and strong
    if prev in ("balanced", "reversal"):
        return "pressure_building"
    if prev == "pressure_building":
        if vel is not None and vel * d >= 1.0:          # ≥ 1 tick/min with the pressure
            return "expansion"
        if resp is not None and resp * d <= -1.0 and ms.pressure_reversal:
            return "rejection"
        return "pressure_building"
    if prev == "expansion":
        if resp is not None and resp * d <= -1.0:
            return "rejection"
        return "expansion"
    if prev == "rejection":
        return "reversal" if ms.pressure_reversal else "rejection"
    return prev


def _liquidity_state(ms: MarketState, prev: str) -> str:
    dep = ms.liquidity_depletion or 0.0
    rep = ms.liquidity_replenishment
    rs = ms.resilience_state or "none"
    vac = bool(ms.liquidity_vacuum) or rs == "vacuum"
    if vac:
        return "vacuum"
    if prev in ("normal", "continuation"):
        return "depletion" if (dep >= 0.40 or rs == "shocked") else "normal"
    if prev == "depletion":
        if rs in ("recovering", "recovered", "overshoot") or (rep is not None and rep >= 0.5):
            return "recovery"
        if rs == "partial" or (rs == "shocked" and (rep is not None and rep < 0.3)):
            return "no_recovery"
        return "depletion"
    if prev == "recovery":
        if rs in ("recovered", "overshoot") and ms.price_velocity is not None and abs(ms.price_velocity) >= 0.5:
            return "continuation"
        if rs in ("shocked",) or dep >= 0.40:
            return "depletion"
        return "recovery" if rs in ("recovering",) else "normal"
    if prev == "no_recovery":
        if rs in ("recovering", "recovered"):
            return "recovery"
        return "no_recovery"
    if prev == "vacuum":
        return "recovery" if rs in ("recovering", "recovered") else "vacuum"
    return prev


def _circuit_state(ms: MarketState, prev: str) -> str:
    c = ms.circuit or {}
    locked = bool(c.get("locked_up") or c.get("locked_down"))
    hit = bool(c.get("hit_up") or c.get("hit_down"))
    dist = c.get("dist_up_pct") if c.get("dist_up_pct") is not None else None
    dd = c.get("dist_down_pct")
    near = None
    for v in (dist, dd):
        if v is not None:
            near = v if near is None else min(near, v)
    vel = c.get("approach_velocity")
    if locked:
        if prev in ("unlock",):
            return "relock"
        return "lock" if prev not in ("lock", "relock") else prev
    if prev in ("lock", "relock"):
        return "unlock"
    if hit:
        return "hit"
    if near is not None and near <= 2.0 and (vel or 0) > 0:
        return "approach"
    if prev == "unlock" and near is not None and near <= 2.0:
        return "unlock"
    return "free"


def _accumulation_state(ms: MarketState, prev: str) -> str:
    m = ms.mechanisms
    def st(name: str) -> str:
        x = m.get(name)
        return x.state if x else "inactive"
    acc = st("accumulation_like") in ("active", "confirmed") or st("stealth_accumulation") in ("active", "confirmed")
    breakout = st("ignition") in ("active", "confirmed") or st("liquidity_sweep") in ("active", "confirmed")
    failed = st("false_breakout") in ("active", "confirmed") or st("failed_sweep") in ("active", "confirmed")
    vel = ms.price_velocity
    if prev == "none":
        return "accumulation_like" if acc else "none"
    if prev == "accumulation_like":
        if failed:
            return "failed_pressure"
        if breakout and (vel or 0) > 0:
            return "breakout"
        return "accumulation_like" if acc else "none"
    if prev == "breakout":
        if failed or (vel is not None and vel < -0.5):
            return "failed_pressure"
        if vel is not None and vel >= 0.5:
            return "continuation"
        return "breakout"
    if prev == "continuation":
        if failed or (vel is not None and vel < -0.5):
            return "failed_pressure"
        return "continuation" if (vel or 0) > 0 else "none"
    if prev == "failed_pressure":
        return "accumulation_like" if acc else "none"
    return prev


def _streak_state(ms: MarketState, prev: str) -> str:
    c = ms.circuit or {}
    up = c.get("consecutive_upper_streak") or 0
    dn = c.get("consecutive_lower_streak") or 0
    if c.get("break_day"):
        return "break"
    if up >= 2 or dn >= 2:
        return "weakening" if c.get("streak_weakening") else "streak"
    return "none"


RULES = {"pressure": _pressure_state, "liquidity": _liquidity_state, "circuit": _circuit_state,
         "accumulation": _accumulation_state, "streak": _streak_state}
INITIAL = {"pressure": "balanced", "liquidity": "normal", "circuit": "free", "accumulation": "none", "streak": "none"}


@dataclass
class _LayerTrack:
    state: str
    since: datetime


@dataclass
class Timeline:
    tracks: Dict[Tuple[str, str], _LayerTrack] = field(default_factory=dict)
    mech_tracks: Dict[Tuple[str, str], _LayerTrack] = field(default_factory=dict)
    history: Dict[str, List[Transition]] = field(default_factory=dict)

    def on_state(self, ms: MarketState) -> List[Transition]:
        out: List[Transition] = []
        for layer in LAYERS:
            key = (ms.symbol, layer)
            tr = self.tracks.get(key)
            prev = tr.state if tr else INITIAL[layer]
            new = RULES[layer](ms, prev)
            if tr is None:
                self.tracks[key] = _LayerTrack(new, ms.t)
                if new != INITIAL[layer]:
                    out.append(Transition(ms.t, INITIAL[layer], new, layer, 0.0))
            elif new != prev:
                out.append(Transition(ms.t, prev, new, layer, (ms.t - tr.since).total_seconds()))
                self.tracks[key] = _LayerTrack(new, ms.t)
            ms.layer_states[layer] = new
            ms.layer_since[layer] = self.tracks[key].since
        for name, mstate in ms.mechanisms.items():
            key = (ms.symbol, name)
            tr = self.mech_tracks.get(key)
            prev = tr.state if tr else "inactive"
            if tr is None:
                self.mech_tracks[key] = _LayerTrack(mstate.state, ms.t)
                if mstate.state != "inactive":
                    out.append(Transition(ms.t, "inactive", mstate.state, f"mechanism:{name}", 0.0))
            elif mstate.state != prev:
                out.append(Transition(ms.t, prev, mstate.state, f"mechanism:{name}", (ms.t - tr.since).total_seconds()))
                self.mech_tracks[key] = _LayerTrack(mstate.state, ms.t)
        ms.transitions = out
        if out:
            self.history.setdefault(ms.symbol, []).extend(out)
        return out

    def current(self, symbol: str) -> Dict[str, Tuple[str, datetime]]:
        return {layer: (tr.state, tr.since) for (s, layer), tr in self.tracks.items() if s == symbol}
