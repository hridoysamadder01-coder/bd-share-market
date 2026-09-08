"""Infer hidden queue pressure from a visible depth ladder.

The visible book shows the top N price levels with a volume on each. It does not show
how much is resting deeper, and it does not show how many orders make up a level. This
module estimates the resting volume beyond the touch from the SHAPE of what is visible,
on one premise:

    a single fat level is weak evidence; volume that builds smoothly across adjacent
    levels is strong evidence.

A lone wall is cheap to place and cheap to pull, so it earns little credibility here. A
ladder whose volume grows steadily as price moves away from the touch is expensive to
fake across many prices, so it earns most of the credibility, and its fitted growth rate
is extrapolated past the last visible level.

Truth class: every number this module returns is INFERRED. The visible ladder is
OBSERVED; per-level order counts and true queue depth are NOT_OBSERVABLE from any source
this repository reaches. `breakout_probability` is a bounded score, monotone in its
drivers, with weights set by prior and NOT fitted to outcomes — `fitted=False` is carried
in every result. It becomes a calibrated probability only after the prospective capture
supplies labelled outcomes; `HiddenPressureInferrer(weights=...)` is where fitted weights
go. This module is NOT part of `micro/MICRO_PREREG.json` and adds nothing to that frozen
feature set.

Dependencies: numpy and pandas only.

    from seeing.features.hidden_pressure import HiddenPressureInferrer
    HiddenPressureInferrer().calculate(book)          # -> dict
    HiddenPressureInferrer().to_json(book)            # -> JSON string
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

__all__ = ["HiddenPressureInferrer"]

_EPS = 1e-12

# Prior weights for the breakout score. NOT fitted to any outcome.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "queue_imbalance": 1.60,      # |inferred queue imbalance|
    "accumulation_gap": 1.10,     # |bid accumulation - ask accumulation|
    "velocity": 1.30,             # |mid drift| in ticks/min, squashed
    "tightness": 0.70,            # 1 tick spread = 1.0, wide spread -> 0
    "wall_fragility": 0.80,       # mean cliff score: a fake barrier is a weak barrier
    "bias": -1.90,                # intercept: all drivers at 0 -> p ~ 0.13
}


class HiddenPressureInferrer:
    """Estimate resting queue volume and a breakout score from a visible ladder.

    Parameters
    ----------
    extrapolation_ticks
        How many ticks past the last visible level the fitted shape is integrated over.
    max_hidden_multiple
        Hard cap: inferred hidden volume may not exceed this multiple of the side's
        visible volume. Without it a positive fitted slope extrapolates explosively.
    spike_cap
        A level this many times its neighbours' mean counts as a full cliff.
    slope_scale
        Ticks-worth of log-volume growth that maps to a mid-range accumulation score.
    velocity_scale
        Mid drift in ticks/min that maps to a mid-range velocity driver.
    default_tick
        Tick size used only when it cannot be inferred and none is supplied.
    weights
        Overrides for the breakout score. Supplying fitted weights flips `fitted` to True.
    """

    def __init__(
        self,
        extrapolation_ticks: int = 10,
        max_hidden_multiple: float = 3.0,
        spike_cap: float = 5.0,
        slope_scale: float = 0.35,
        velocity_scale: float = 2.0,
        default_tick: float = 0.10,
        weights: Optional[Dict[str, float]] = None,
        fitted: bool = False,
    ) -> None:
        if extrapolation_ticks < 0:
            raise ValueError("extrapolation_ticks must be >= 0")
        if max_hidden_multiple < 0:
            raise ValueError("max_hidden_multiple must be >= 0")
        if spike_cap <= 1:
            raise ValueError("spike_cap must be > 1")
        if slope_scale <= 0 or velocity_scale <= 0 or default_tick <= 0:
            raise ValueError("slope_scale, velocity_scale and default_tick must be > 0")
        self.extrapolation_ticks = int(extrapolation_ticks)
        self.max_hidden_multiple = float(max_hidden_multiple)
        self.spike_cap = float(spike_cap)
        self.slope_scale = float(slope_scale)
        self.velocity_scale = float(velocity_scale)
        self.default_tick = float(default_tick)
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            unknown = set(weights) - set(DEFAULT_WEIGHTS)
            if unknown:
                raise ValueError(f"unknown weight keys: {sorted(unknown)}")
            self.weights.update({k: float(v) for k, v in weights.items()})
        self.fitted = bool(fitted or weights)

    # ------------------------------------------------------------------ public API
    def calculate(
        self,
        visible_book: Any,
        history: Optional[Sequence[Any]] = None,
        tick_size: Optional[float] = None,
        minutes_elapsed: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Return inferred queues and a breakout score for one book snapshot.

        `history` is an optional sequence of earlier snapshots, oldest first, used only
        for mid-price velocity. `minutes_elapsed` is the span the history covers; it
        defaults to one minute per prior snapshot, so the velocity driver is in
        ticks per snapshot-interval unless a real elapsed time is supplied.
        """
        bid_px, bid_vol, ask_px, ask_vol, problems = _parse_book(visible_book)
        tick = _resolve_tick(bid_px, ask_px, tick_size, self.default_tick)
        tick_inferred = tick_size is None

        bid = self._side(bid_px, bid_vol, tick, side="bid")
        ask = self._side(ask_px, ask_vol, tick, side="ask")

        # "Waiting below the best bid" / "waiting above the best ask": everything behind
        # the touch, visible plus the credibility-weighted extrapolation past it.
        inferred_buy_queue = float(bid["visible_behind_touch"] + bid["hidden_volume"])
        inferred_sell_queue = float(ask["visible_behind_touch"] + ask["hidden_volume"])

        best_bid = float(bid_px[0]) if bid_px.size else float("nan")
        best_ask = float(ask_px[0]) if ask_px.size else float("nan")
        two_sided = bool(bid_px.size and ask_px.size)
        spread_ticks = float((best_ask - best_bid) / tick) if two_sided else float("nan")
        mid = float((best_bid + best_ask) / 2.0) if two_sided else float("nan")
        if two_sided and best_bid >= best_ask:
            problems.append(f"crossed or locked book: best_bid {best_bid} >= best_ask {best_ask}")

        velocity = self._velocity(history, mid, tick, minutes_elapsed)
        breakout = self._breakout(bid, ask, inferred_buy_queue, inferred_sell_queue,
                                  spread_ticks, velocity, two_sided)

        # A ladder with fewer than two levels on both sides has no shape to read. The
        # score is still returned, but the caller must know it rests on almost nothing.
        thin = bid["n_levels"] < 2 and ask["n_levels"] < 2
        if thin:
            problems.append("fewer than 2 levels on both sides: no ladder shape to read, "
                            "breakout_probability rests on the prior alone")

        return {
            "inferred_buy_queue": inferred_buy_queue,
            "inferred_sell_queue": inferred_sell_queue,
            "breakout_probability": breakout["probability"],
            "breakout_direction": breakout["direction"],
            "queue_imbalance": breakout["queue_imbalance"],
            "drivers": breakout["drivers"],
            "score": breakout["score"],
            "bid_side": bid,
            "ask_side": ask,
            "book": {
                "best_bid": best_bid, "best_ask": best_ask, "mid": mid,
                "spread_ticks": spread_ticks, "tick_size": tick,
                "tick_size_inferred": tick_inferred,
                "n_bid_levels": int(bid_px.size), "n_ask_levels": int(ask_px.size),
                "visible_bid_volume": float(bid["visible_volume"]),
                "visible_ask_volume": float(ask["visible_volume"]),
                "two_sided": two_sided,
            },
            "velocity_ticks_per_min": velocity,
            "insufficient_book": thin,
            "truth": {
                "visible_ladder": "OBSERVED",
                "inferred_buy_queue": "INFERRED",
                "inferred_sell_queue": "INFERRED",
                "breakout_probability": "INFERRED (bounded score; weights unfitted)"
                                        if not self.fitted else "INFERRED (fitted weights)",
                "orders_per_level": "NOT_OBSERVABLE",
                "queue_position": "NOT_OBSERVABLE",
            },
            "fitted": self.fitted,
            "problems": problems,
        }

    def to_json(self, visible_book: Any, indent: int = 2, **kwargs: Any) -> str:
        """`calculate` rendered as JSON. NaN is emitted as null, not as bare NaN."""
        return json.dumps(_json_safe(self.calculate(visible_book, **kwargs)), indent=indent)

    # ------------------------------------------------------------------ side model
    def _side(self, px: np.ndarray, vol: np.ndarray, tick: float, side: str) -> Dict[str, Any]:
        n = int(px.size)
        out: Dict[str, Any] = {
            "side": side, "n_levels": n,
            "visible_volume": float(vol.sum()) if n else 0.0,
            "visible_behind_touch": float(vol[1:].sum()) if n > 1 else 0.0,
            "touch_volume": float(vol[0]) if n else 0.0,
        }
        if n == 0:
            out.update(cliff_score=1.0, herfindahl_cliff=1.0, spike_ratio=float("nan"),
                       smoothness=0.0, depth_confidence=0.0, slope_per_tick=0.0,
                       accumulation=0.0, credibility=0.0, hidden_volume=0.0,
                       hidden_capped=False, note="empty side: nothing to infer from")
            return out

        # Depth in ticks from the touch: 0, then how far each level sits behind it.
        depth = np.abs(px - px[0]) / tick

        # --- cliff detection: is the mass concentrated in one price? -----------------
        share = vol / (vol.sum() + _EPS)
        herf = float((share ** 2).sum())
        herf_cliff = 1.0 if n == 1 else float(np.clip((herf - 1.0 / n) / (1.0 - 1.0 / n), 0.0, 1.0))
        spike_ratio = _max_neighbour_ratio(vol)
        spike_cliff = float(np.clip((spike_ratio - 1.0) / (self.spike_cap - 1.0), 0.0, 1.0)) \
            if np.isfinite(spike_ratio) else 0.0
        cliff_score = float(np.clip(0.5 * herf_cliff + 0.5 * spike_cliff, 0.0, 1.0))

        # --- smoothness: how gently the ladder changes level to level ----------------
        smoothness = _smoothness(share)

        # --- accumulation: does volume grow as price moves away from the touch? ------
        slope, intercept, fit_ok = _loglinear_fit(depth, vol)
        accumulation = float(_sigmoid(slope / self.slope_scale)) if fit_ok else 0.5

        # Few levels means little evidence either way; shrink everything toward neutral.
        depth_confidence = float(1.0 - np.exp(-(n - 1) / 2.0)) if n > 1 else 0.0

        # A ladder is credible when it is not a cliff, changes smoothly, and has enough
        # levels to show a shape at all.
        credibility = float(np.clip((1.0 - cliff_score) * smoothness * depth_confidence, 0.0, 1.0))

        # --- extrapolate the fitted shape past the last visible level ----------------
        hidden_raw = 0.0
        if fit_ok and self.extrapolation_ticks > 0:
            far = depth[-1] + np.arange(1, self.extrapolation_ticks + 1, dtype=float)
            projected = np.expm1(intercept + slope * far)
            hidden_raw = float(np.clip(projected, 0.0, None).sum())
        cap = self.max_hidden_multiple * out["visible_volume"]
        hidden_capped = bool(hidden_raw > cap)
        hidden_volume = float(min(hidden_raw, cap) * credibility)

        out.update(
            cliff_score=cliff_score, herfindahl_cliff=herf_cliff,
            spike_ratio=float(spike_ratio), smoothness=smoothness,
            depth_confidence=depth_confidence, slope_per_tick=float(slope),
            accumulation=accumulation, credibility=credibility,
            hidden_volume=hidden_volume, hidden_volume_uncapped=hidden_raw,
            hidden_capped=hidden_capped, deepest_tick=float(depth[-1]),
        )
        return out

    # ------------------------------------------------------------------ velocity
    def _velocity(self, history: Optional[Sequence[Any]], mid: float, tick: float,
                  minutes_elapsed: Optional[float]) -> float:
        """Mid drift in ticks per minute across the supplied history. 0.0 without history."""
        if not history or not np.isfinite(mid):
            return 0.0
        mids: List[float] = []
        for snap in history:
            try:
                b_px, _, a_px, _, _ = _parse_book(snap)
            except (TypeError, ValueError):
                continue
            if b_px.size and a_px.size:
                mids.append(float((b_px[0] + a_px[0]) / 2.0))
        if not mids:
            return 0.0
        span = float(minutes_elapsed) if minutes_elapsed else float(len(mids))
        if span <= 0:
            return 0.0
        return float((mid - mids[0]) / tick / span)

    # ------------------------------------------------------------------ breakout
    def _breakout(self, bid: Dict[str, Any], ask: Dict[str, Any], qb: float, qa: float,
                  spread_ticks: float, velocity: float, two_sided: bool) -> Dict[str, Any]:
        imbalance = float((qb - qa) / (qb + qa + _EPS)) if (qb + qa) > 0 else 0.0
        acc_gap = float(bid["accumulation"] - ask["accumulation"])
        tightness = float(1.0 / max(spread_ticks, 1.0)) if (two_sided and np.isfinite(spread_ticks)
                                                            and spread_ticks > 0) else 0.0
        # A cliff is weak evidence of pressure, and equally a weak barrier: a wall that is
        # cheap to pull does not hold. Fragility therefore raises the breakout score.
        # Only sides that actually have levels can be fragile — an absent side is missing
        # evidence, not a flimsy wall, and must not inflate the score.
        present = [s["cliff_score"] for s in (bid, ask) if s["n_levels"] > 0]
        fragility = float(np.mean(present)) if present else 0.0
        vel_driver = float(np.tanh(abs(velocity) / self.velocity_scale))

        drivers = {
            "queue_imbalance": abs(imbalance),
            "accumulation_gap": abs(acc_gap),
            "velocity": vel_driver,
            "tightness": tightness,
            "wall_fragility": fragility,
        }
        score = self.weights["bias"] + sum(self.weights[k] * v for k, v in drivers.items())
        probability = float(_sigmoid(score))

        pull = imbalance + acc_gap + np.tanh(velocity / self.velocity_scale)
        direction = "UP" if pull > 0.05 else ("DOWN" if pull < -0.05 else "NEUTRAL")
        return {"probability": probability, "direction": direction, "score": float(score),
                "drivers": drivers, "queue_imbalance": imbalance}


# ---------------------------------------------------------------------- helpers
def _sigmoid(x: float) -> float:
    x = float(np.clip(x, -60.0, 60.0))
    return 1.0 / (1.0 + np.exp(-x))


def _max_neighbour_ratio(vol: np.ndarray) -> float:
    """Largest ratio of a level to the mean of its immediate neighbours."""
    n = vol.size
    if n < 3:
        if n == 2:
            lo, hi = float(min(vol)), float(max(vol))
            return hi / (lo + _EPS) if lo > 0 else float(self_cap_for_zero(hi))
        return float("nan")
    ratios = []
    for i in range(n):
        neigh = [vol[j] for j in (i - 1, i + 1) if 0 <= j < n]
        m = float(np.mean(neigh))
        ratios.append(float(vol[i]) / (m + _EPS) if m > 0 else float(self_cap_for_zero(vol[i])))
    return float(max(ratios))


def self_cap_for_zero(v: float) -> float:
    """A level beside empty neighbours is a spike, but a finite one (avoids inf)."""
    return 10.0 if v > 0 else 1.0


def _smoothness(share: np.ndarray) -> float:
    """1.0 for a ladder whose shape changes gently, → 0 for a jagged one."""
    n = share.size
    if n < 3:
        return 0.5
    second = np.abs(np.diff(share, n=2))
    rough = float(second.mean() * n)      # scale-free: shares already sum to 1
    return float(np.exp(-3.0 * rough))


def _loglinear_fit(depth: np.ndarray, vol: np.ndarray) -> Tuple[float, float, bool]:
    """Least-squares fit of log1p(volume) against depth in ticks.

    Returns (slope, intercept, ok). Positive slope = volume grows away from the touch.
    """
    if depth.size < 2 or np.ptp(depth) <= 0:
        return 0.0, float(np.log1p(vol[0])) if vol.size else 0.0, False
    y = np.log1p(np.clip(vol, 0.0, None))
    try:
        slope, intercept = np.polyfit(depth, y, 1)
    except (np.linalg.LinAlgError, ValueError):
        return 0.0, float(y.mean()), False
    if not (np.isfinite(slope) and np.isfinite(intercept)):
        return 0.0, float(y.mean()), False
    return float(slope), float(intercept), True


def _resolve_tick(bid_px: np.ndarray, ask_px: np.ndarray, tick_size: Optional[float],
                  default_tick: float) -> float:
    if tick_size is not None:
        if tick_size <= 0:
            raise ValueError("tick_size must be > 0")
        return float(tick_size)
    diffs: List[float] = []
    for px in (bid_px, ask_px):
        if px.size >= 2:
            d = np.abs(np.diff(px))
            diffs.extend(d[d > _EPS].tolist())
    if bid_px.size and ask_px.size:
        s = abs(float(ask_px[0]) - float(bid_px[0]))
        if s > _EPS:
            diffs.append(s)
    if not diffs:
        return float(default_tick)
    return float(max(round(min(diffs), 6), 1e-6))


def _parse_book(visible_book: Any) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """Accept the shapes this repository actually produces.

    * DataFrame or dict of columns `buy_price/buy_volume/sell_price/sell_volume`
      (the bdshare / dsebd.org ladder, NaN-padded on the shorter side)
    * dict with `bids`/`asks` as sequences of (price, volume)
    * dict with `BuySellDetails` rows (`BuyPrice/BuyVolume/SellPrice/SellVolume`),
      the broker terminal's shape, optionally wrapped in a `Depth` object

    Bids come back sorted best (highest) first, asks best (lowest) first. Rows with a
    price but no volume are kept with volume 0 and reported; rows with neither are
    dropped.
    """
    problems: List[str] = []
    if visible_book is None:
        raise ValueError("visible_book is None")

    if isinstance(visible_book, dict) and "Depth" in visible_book:
        visible_book = visible_book["Depth"]
    if isinstance(visible_book, dict) and "BuySellDetails" in visible_book:
        rows = visible_book["BuySellDetails"] or []
        visible_book = {
            "buy_price": [r.get("BuyPrice") for r in rows],
            "buy_volume": [r.get("BuyVolume") for r in rows],
            "sell_price": [r.get("SellPrice") for r in rows],
            "sell_volume": [r.get("SellVolume") for r in rows],
        }

    if isinstance(visible_book, dict) and ("bids" in visible_book or "asks" in visible_book):
        bp, bv = _split_pairs(visible_book.get("bids"), "bids", problems)
        ap, av = _split_pairs(visible_book.get("asks"), "asks", problems)
    else:
        df = visible_book if isinstance(visible_book, pd.DataFrame) else pd.DataFrame(visible_book)
        missing = [c for c in ("buy_price", "buy_volume", "sell_price", "sell_volume")
                   if c not in df.columns]
        if len(missing) == 4:
            raise ValueError("unrecognised book shape: expected buy_price/buy_volume/"
                             "sell_price/sell_volume columns, bids/asks pairs, or BuySellDetails")
        for c in missing:
            df[c] = np.nan
            problems.append(f"column {c} absent; that side treated as empty")
        bp, bv = _clean_side(df["buy_price"], df["buy_volume"], "buy", problems)
        ap, av = _clean_side(df["sell_price"], df["sell_volume"], "sell", problems)

    if bp.size:
        order = np.argsort(-bp, kind="stable")
        bp, bv = bp[order], bv[order]
    if ap.size:
        order = np.argsort(ap, kind="stable")
        ap, av = ap[order], av[order]
    return bp, bv, ap, av, problems


def _split_pairs(pairs: Any, name: str, problems: List[str]) -> Tuple[np.ndarray, np.ndarray]:
    if not pairs:
        return np.array([]), np.array([])
    px, vol = [], []
    for i, item in enumerate(pairs):
        try:
            p, v = item[0], item[1]
        except (TypeError, IndexError, KeyError):
            problems.append(f"{name}[{i}] is not a (price, volume) pair: {item!r}"[:160])
            continue
        p = _num(p)
        if p is None:
            problems.append(f"{name}[{i}] has no usable price: {item!r}"[:160])
            continue
        v_num = _num(v)
        if v_num is None:
            problems.append(f"{name}[{i}] price {p} carries no volume; counted as 0")
            v_num = 0.0
        px.append(p)
        vol.append(max(v_num, 0.0))
    return np.asarray(px, dtype=float), np.asarray(vol, dtype=float)


def _clean_side(price: pd.Series, volume: pd.Series, name: str,
                problems: List[str]) -> Tuple[np.ndarray, np.ndarray]:
    p = pd.to_numeric(price, errors="coerce")
    v = pd.to_numeric(volume, errors="coerce")
    keep = p.notna() & (p > 0)
    n_priced_no_vol = int((keep & v.isna()).sum())
    if n_priced_no_vol:
        problems.append(f"{n_priced_no_vol} {name} level(s) priced with no volume; counted as 0")
    return p[keep].to_numpy(dtype=float), v[keep].fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else f


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    # bool before int: isinstance(True, int) is True in Python, so an int branch placed
    # first would render every flag as 0/1.
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if not np.isfinite(f) else round(f, 6)
    if isinstance(obj, (np.integer, int)):
        return int(obj)
    return obj
