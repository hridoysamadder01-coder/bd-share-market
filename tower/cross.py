"""Cross-symbol / sector context engine.

One global ``CrossEngine`` sees every symbol's ``MarketState`` in event order
(``on_state``), the sector map from REFERENCE events (``on_reference``) and the
market-wide breadth from MARKET_STATS events (``on_market_stats`` /
``on_market_breadth``). ``context_for(symbol, now)`` then computes, causally
(only observations at or before ``now``), the ``cross`` and ``sector`` dicts
that the engine stores on the symbol's state.

Rules (all windows are causal step functions of the observed path: the value
of a symbol at time t is its last observation at or before t):

* mid return series: per symbol, log(mid) is stored per update (mid when a
  book exists; ltp only for symbols that never had a mid — the basis is fixed
  per symbol and the series is reset when a mid first appears). The per-update
  log change is also kept (``returns_of``).
* 60-s return at ``now``: L(now) − L(now − 60) where L is the step-function
  log price; None unless both points exist and the last observation is at
  most ``max_gap_s`` old (a stale symbol has no current return).
* market_return_60s: median of the 60-s returns of every symbol with a valid
  return (≥ ``min_market_symbols``); symbol_vs_market_60s = own − median of
  the OTHER symbols (≥ 1 other).
* sector_return_60s: median over the sector's members (including the symbol);
  symbol_vs_sector_60s = own − median of the sector PEERS (excluding the
  symbol, ≥ 1 peer).
* leaders / laggers: both paths are resampled on an absolute 10-s grid
  (anchored to epoch multiples of 10 s, ending at floor(now/10)·10) over the
  last ``corr_window_s`` (900 s); a bin's 10-s return is L(g) − L(g − 10) and
  is valid only when the symbol was observed within ``max_gap_s`` before g.
  For each other symbol O and lag ℓ ∈ {0, 15, 30, 60} s the Pearson
  correlation of r_S(g) with r_O(g − ℓ) is computed over bins where both are
  valid (≥ ``min_overlap`` bins, both series non-constant). O is a leader of S
  when the best lag is > 0 and its correlation ≥ ``min_corr``; laggers are the
  symmetric computation (r_O(g) vs r_S(g − ℓ)). Lists are sorted by
  correlation desc, then symbol. None when no pair reached the overlap
  threshold; [] when pairs were evaluated but none qualified.
* basket_sync: share of sector peers (valid 60-s return) whose return sign
  equals the symbol's (0 is its own sign).
* circuit_cluster: among sector members with observable circuit data, count
  and share that are locked (locked_up/locked_down) or within
  ``limit_near_pct`` % of a limit (dist_up_pct / dist_down_pct).
* simultaneous_liquidity_change: across all current symbols with a positive
  visible liquidity (bid + ask) now and 60 s ago, share whose relative change
  |ΔV / V(now − 60)| ≥ ``liq_change_thr``; sign = +1 / −1 by majority of the
  changers' directions (0 tie), None when none changed.
* synchronized_expansion: share of symbols (≥ ``min_velocity_samples`` |price
  velocity| samples in the ``corr_window_s`` before their current sample) whose
  current |velocity| is at or above the 90th percentile of those earlier
  samples; a symbol whose earlier samples are constant has no defined top
  decile and is excluded.
* sector_pressure: mean of pressure_direction × pressure_strength over sector
  members carrying both; sector_breadth: up/down/flat counts of the members'
  60-s returns.

"Current" membership (circuit, liquidity, pressure, velocity) requires the
member's last state within ``stale_s`` of ``now``. Everything is None when the
inputs are insufficient — never a silent zero. No wall-clock reads: ``now``
defaults to the latest event time seen.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .state import MarketState
from .windows import sign

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _secs(t: datetime) -> float:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (t - _EPOCH).total_seconds()


class _Path:
    """Time-stamped step-function path with causal lookups.

    Points are kept for ``retain_s`` plus the last point before the retention
    cutoff so the step function stays defined over the whole retained window."""

    __slots__ = ("ts", "vs", "retain_s", "_arr", "_arr_len")

    def __init__(self, retain_s: float) -> None:
        self.ts: List[float] = []
        self.vs: List[float] = []
        self.retain_s = float(retain_s)
        self._arr: Optional[Tuple[np.ndarray, np.ndarray]] = None
        self._arr_len = -1

    def push(self, t: float, v: float) -> None:
        if self.ts and t < self.ts[-1]:
            # out-of-order observation: insert to keep the path monotonic in time
            i = bisect.bisect_right(self.ts, t)
            self.ts.insert(i, t)
            self.vs.insert(i, v)
        else:
            self.ts.append(t)
            self.vs.append(v)
        cutoff = t - self.retain_s
        # drop leading points while the NEXT point is still at/before the cutoff
        k = 0
        n = len(self.ts)
        while k + 1 < n and self.ts[k + 1] <= cutoff:
            k += 1
        if k:
            del self.ts[:k]
            del self.vs[:k]
        self._arr = None

    def __len__(self) -> int:
        return len(self.ts)

    def last_t(self) -> Optional[float]:
        return self.ts[-1] if self.ts else None

    def at_or_before(self, t: float) -> Optional[Tuple[float, float]]:
        """(t_obs, value) of the last observation at or before t, None if none."""
        i = bisect.bisect_right(self.ts, t) - 1
        if i < 0:
            return None
        return self.ts[i], self.vs[i]

    def arrays(self) -> Tuple[np.ndarray, np.ndarray]:
        if self._arr is None or self._arr_len != len(self.ts):
            self._arr = (np.asarray(self.ts, dtype=float), np.asarray(self.vs, dtype=float))
            self._arr_len = len(self.ts)
        return self._arr

    def sample(self, grid: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Step-function values and observation ages at every grid time
        (NaN / +inf where nothing was observed at or before the grid time)."""
        ts, vs = self.arrays()
        v = np.full(len(grid), np.nan)
        age = np.full(len(grid), np.inf)
        if len(ts) == 0:
            return v, age
        idx = np.searchsorted(ts, grid, side="right") - 1
        ok = idx >= 0
        v[ok] = vs[idx[ok]]
        age[ok] = grid[ok] - ts[idx[ok]]
        return v, age


@dataclass
class _Sym:
    sector: Optional[str] = None
    sector_source: Optional[str] = None            # "reference" | "watch_sector_id"
    basis: Optional[str] = None                    # "mid" | "ltp"
    logp: _Path = field(default_factory=lambda: _Path(1200.0))
    returns: _Path = field(default_factory=lambda: _Path(1200.0))     # per-update log change
    liq: _Path = field(default_factory=lambda: _Path(300.0))          # visible bid + ask liquidity
    velocity: _Path = field(default_factory=lambda: _Path(1200.0))    # |price_velocity|
    last_t: Optional[float] = None
    last_seq: Optional[int] = None
    circuit: Dict[str, Any] = field(default_factory=dict)
    pressure_direction: Optional[int] = None
    pressure_strength: Optional[float] = None
    updates: int = 0
    # cache of the sampled path on the current grid: (grid_end, n_points, last_t) → (values, ages)
    _sample_key: Optional[Tuple[float, int, float]] = None
    _sample: Optional[Tuple[np.ndarray, np.ndarray]] = None
    # cache of the top-decile verdict for the current velocity sample: key → True/False/None(ineligible)
    _vel_key: Optional[Tuple[int, float, float]] = None
    _vel_top: Optional[bool] = None


class CrossEngine:
    """Global cross-symbol / sector context (see module docstring for the rules)."""

    def __init__(self, ret_window_s: float = 60.0, corr_window_s: float = 900.0, resample_s: float = 10.0,
                 lags_s: Sequence[float] = (0.0, 15.0, 30.0, 60.0), min_overlap: int = 20, min_corr: float = 0.3,
                 max_gap_s: float = 60.0, stale_s: float = 180.0, liq_change_thr: float = 0.20,
                 limit_near_pct: float = 1.0, min_market_symbols: int = 2, min_velocity_samples: int = 10,
                 top_decile_pct: float = 90.0) -> None:
        self.ret_window_s = float(ret_window_s)
        self.corr_window_s = float(corr_window_s)
        self.resample_s = float(resample_s)
        self.lags_s = tuple(float(l) for l in lags_s)
        self.min_overlap = int(min_overlap)
        self.min_corr = float(min_corr)
        self.max_gap_s = float(max_gap_s)
        self.stale_s = float(stale_s)
        self.liq_change_thr = float(liq_change_thr)
        self.limit_near_pct = float(limit_near_pct)
        self.min_market_symbols = int(min_market_symbols)
        self.min_velocity_samples = int(min_velocity_samples)
        self.top_decile_pct = float(top_decile_pct)
        # internal 5-s path grid: gcd of the resample step and the lags so every lag is an index shift
        steps = [self.resample_s] + [l for l in self.lags_s if l > 0]
        g = int(round(steps[0]))
        for s in steps[1:]:
            g = math.gcd(g, int(round(s)))
        self.grid_step_s = float(max(1, g))
        self.n_bins = int(self.corr_window_s // self.resample_s)
        self.syms: Dict[str, _Sym] = {}
        # stacked 10-s bin-return matrices per lag (rows = symbols in index order), kept for the
        # current grid end; a row is recomputed lazily when its symbol updated or the grid moved
        self._idx: Dict[str, int] = {}
        self._names: List[str] = []
        self._R: Dict[float, np.ndarray] = {}
        self._M: Dict[float, np.ndarray] = {}
        self._cap = 0
        self._dirty: set = set()
        self._grid_end: Optional[float] = None
        self._breadth: Optional[Dict[str, Any]] = None          # latest breadth (t, up, down, n, ...)
        self._stats: Optional[Dict[str, Any]] = None            # latest MARKET_STATS payload with its t
        self._last_t: Optional[float] = None

    # ------------------------------------------------------------------ inputs
    def _sym(self, symbol: str) -> _Sym:
        s = self.syms.get(symbol)
        if s is None:
            s = _Sym()
            self.syms[symbol] = s
            self._idx[symbol] = len(self._names)
            self._names.append(symbol)
            self._dirty.add(symbol)
            if len(self._names) > self._cap:
                self._grow()
        return s

    def _grow(self) -> None:
        new_cap = max(64, self._cap * 2)
        for lag in self.lags_s:
            R = np.zeros((new_cap, self.n_bins))
            M = np.zeros((new_cap, self.n_bins), dtype=bool)
            if lag in self._R:
                R[: self._cap] = self._R[lag]
                M[: self._cap] = self._M[lag]
            self._R[lag], self._M[lag] = R, M
        self._cap = new_cap

    def _note_t(self, t: datetime) -> None:
        ts = _secs(t)
        if self._last_t is None or ts > self._last_t:
            self._last_t = ts

    def on_reference(self, symbol: str, sector: Optional[str]) -> None:
        """Sector from a REFERENCE event (authoritative over a watch sector_id)."""
        if not symbol or not sector:
            return
        s = self._sym(symbol)
        s.sector = str(sector).strip()
        s.sector_source = "reference"

    def on_market_stats(self, t: datetime, payload: Dict[str, Any]) -> None:
        """Keep the latest MARKET_STATS payload (totals, up/down/flat/unpriced) with its time."""
        self._note_t(t)
        self._stats = {"t": _secs(t), **{k: v for k, v in (payload or {}).items()}}

    def on_market_breadth(self, t: datetime, up: Optional[float], down: Optional[float],
                          n: Optional[float]) -> None:
        """Breadth counts at t. ``n`` (symbols counted) falls back to up + down + flat
        when the stats payload of the same instant carries ``flat``; else None."""
        self._note_t(t)
        ts = _secs(t)
        if up is None and down is None:
            return
        flat = None
        if self._stats is not None and abs(self._stats.get("t", -1e18) - ts) < 1e-6:
            flat = self._stats.get("flat")
        if n is None and up is not None and down is not None and flat is not None:
            n = up + down + flat
        self._breadth = {"t": ts, "up": _num(up), "down": _num(down), "n": _num(n), "flat": _num(flat)}

    def on_state(self, ms: MarketState) -> None:
        """Ingest one symbol state (event order). Records the log-price point,
        per-update return, visible liquidity, |price velocity|, circuit and
        pressure fields; sector_id from the watch quote when no REFERENCE sector."""
        self._note_t(ms.t)
        s = self._sym(ms.symbol)
        t = _secs(ms.t)
        s.last_t = t
        s.last_seq = ms.seq
        s.updates += 1
        # sector fallback from the watch quote (only when REFERENCE never named one)
        if s.sector is None:
            q = (ms.session_state or {}).get("quote") or {}
            sid = q.get("sector_id") if isinstance(q, dict) else None
            if sid is not None and str(sid) not in ("", "0", "None"):
                s.sector = f"SID:{sid}"
                s.sector_source = "watch_sector_id"
        # price basis: mid when a book exists; ltp only until a mid ever appears
        price: Optional[float] = None
        if ms.mid is not None and ms.mid > 0:
            if s.basis != "mid":
                s.basis = "mid"
                s.logp = _Path(s.logp.retain_s)
                s.returns = _Path(s.returns.retain_s)
                s._sample = None
            price = float(ms.mid)
        elif s.basis in (None, "ltp") and ms.ltp is not None and ms.ltp > 0:
            s.basis = "ltp"
            price = float(ms.ltp)
        if price is not None:
            lp = math.log(price)
            prev = s.logp.at_or_before(t)
            s.logp.push(t, lp)
            if prev is not None:
                s.returns.push(t, lp - prev[1])
            s._sample = None
            self._dirty.add(ms.symbol)
        # visible liquidity (both sides must be observed)
        if ms.visible_bid_liq is not None and ms.visible_ask_liq is not None:
            s.liq.push(t, float(ms.visible_bid_liq) + float(ms.visible_ask_liq))
        if ms.price_velocity is not None:
            s.velocity.push(t, abs(float(ms.price_velocity)))
        c = ms.circuit or {}
        s.circuit = {k: c.get(k) for k in ("locked_up", "locked_down", "dist_up_pct", "dist_down_pct",
                                            "hit_up", "hit_down")}
        s.pressure_direction = ms.pressure_direction
        s.pressure_strength = ms.pressure_strength

    # ------------------------------------------------------------------ accessors
    def sector_of(self, symbol: str) -> Optional[str]:
        s = self.syms.get(symbol)
        return s.sector if s else None

    def sector_members(self, sector: str) -> List[str]:
        return sorted(sym for sym, s in self.syms.items() if s.sector == sector)

    def returns_of(self, symbol: str) -> List[Tuple[float, float]]:
        """Per-update (t_epoch_s, log mid change) series retained for the symbol."""
        s = self.syms.get(symbol)
        return list(zip(s.returns.ts, s.returns.vs)) if s else []

    def return_60s(self, symbol: str, now: datetime) -> Optional[float]:
        s = self.syms.get(symbol)
        return self._ret(s, _secs(now)) if s else None

    # ------------------------------------------------------------------ primitives
    def _ret(self, s: _Sym, now: float, window: Optional[float] = None) -> Optional[float]:
        """Step-function log return over ``window`` s ending at ``now``; None when
        either endpoint is unobserved or the latest observation is older than max_gap_s."""
        w = self.ret_window_s if window is None else window
        cur = s.logp.at_or_before(now)
        if cur is None or now - cur[0] > self.max_gap_s:
            return None
        prev = s.logp.at_or_before(now - w)
        if prev is None:
            return None
        return cur[1] - prev[1]

    def _current(self, s: _Sym, now: float) -> bool:
        return s.last_t is not None and s.last_t <= now and now - s.last_t <= self.stale_s

    def _grid(self, now: float) -> Tuple[float, np.ndarray]:
        end = math.floor(now / self.resample_s) * self.resample_s
        max_shift = int(round(max(self.lags_s) / self.grid_step_s))
        per_bin = int(round(self.resample_s / self.grid_step_s))
        k = per_bin * self.n_bins + max_shift + per_bin + 1
        grid = end - self.grid_step_s * np.arange(k, dtype=float)
        return end, grid

    def _sampled(self, s: _Sym, end: float, grid: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        key = (end, len(s.logp), s.logp.last_t() or -1.0)
        if s._sample is None or s._sample_key != key:
            s._sample = s.logp.sample(grid)
            s._sample_key = key
        return s._sample

    def _bin_returns(self, s: _Sym, end: float, grid: np.ndarray, lag_s: float) -> Tuple[np.ndarray, np.ndarray]:
        """10-s returns r(g − lag) on the resample grid (index 0 = newest bin)
        and their validity mask."""
        v, age = self._sampled(s, end, grid)
        per_bin = int(round(self.resample_s / self.grid_step_s))
        sh = int(round(lag_s / self.grid_step_s))
        n = self.n_bins
        a = v[sh: sh + per_bin * n: per_bin]
        b = v[sh + per_bin: sh + per_bin + per_bin * n: per_bin]
        ag = age[sh: sh + per_bin * n: per_bin]
        valid = ~np.isnan(a) & ~np.isnan(b) & (ag <= self.max_gap_s)
        r = np.where(valid, a - b, 0.0)
        return r, valid

    def _ensure_matrices(self, t_now: float) -> Tuple[float, np.ndarray]:
        """Bring the stacked bin-return matrices up to the grid ending at floor(now/10)·10:
        every row is recomputed when the grid end moved, else only the rows of symbols
        that received a price point since the last query."""
        end, grid = self._grid(t_now)
        if end != self._grid_end:
            self._grid_end = end
            self._dirty = set(self._names)
        if self._dirty:
            for sym in self._dirty:
                i = self._idx[sym]
                s = self.syms[sym]
                for lag in self.lags_s:
                    if len(s.logp) < 2:
                        self._R[lag][i] = 0.0
                        self._M[lag][i] = False
                    else:
                        r, v = self._bin_returns(s, end, grid, lag)
                        self._R[lag][i] = r
                        self._M[lag][i] = v
            self._dirty = set()
        return end, grid

    @staticmethod
    def _masked_corr(x: np.ndarray, vx: np.ndarray, Y: np.ndarray, MY: np.ndarray
                     ) -> Tuple[np.ndarray, np.ndarray]:
        """Pearson correlation of vector x with every row of Y over the bins valid in
        both (vx & MY[row]); returns (corr, n) per row, corr NaN where either series is
        constant on the overlap or the overlap is empty."""
        m = MY & vx[None, :]
        n = m.sum(axis=1).astype(float)
        X = np.where(m, x[None, :], 0.0)
        Ym = np.where(m, Y, 0.0)
        sx, sy = X.sum(axis=1), Ym.sum(axis=1)
        sxx, syy, sxy = (X * X).sum(axis=1), (Ym * Ym).sum(axis=1), (X * Ym).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            nn = np.where(n > 0, n, np.nan)
            varx = sxx - sx * sx / nn
            vary = syy - sy * sy / nn
            cov = sxy - sx * sy / nn
            ok = (varx > 1e-30) & (vary > 1e-30)
            corr = np.where(ok, cov / np.sqrt(np.where(ok, varx * vary, 1.0)), np.nan)
        return corr, n

    def _best_lag(self, x_by_lag: Dict[float, Tuple[np.ndarray, np.ndarray]],
                  Y_by_lag: Dict[float, Tuple[np.ndarray, np.ndarray]]) -> Tuple[np.ndarray, np.ndarray]:
        """Per row: (best lag, its corr) over the lag set, requiring ≥ min_overlap bins;
        a later lag replaces the best only when its correlation is strictly larger."""
        nrows = Y_by_lag[self.lags_s[0]][0].shape[0]
        best_c = np.full(nrows, np.nan)
        best_l = np.full(nrows, np.nan)
        for lag in self.lags_s:
            x, vx = x_by_lag[lag]
            Y, MY = Y_by_lag[lag]
            c, n = self._masked_corr(x, vx, Y, MY)
            c = np.where(n >= self.min_overlap, c, np.nan)
            better = ~np.isnan(c) & (np.isnan(best_c) | (c > best_c + 1e-12))
            best_c = np.where(better, c, best_c)
            best_l = np.where(better, lag, best_l)
        return best_l, best_c

    # ------------------------------------------------------------------ context
    def context_for(self, symbol: str, now: Optional[datetime] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """(cross, sector) dicts for ``symbol`` as of ``now`` (default: latest event time)."""
        if now is None:
            if self._last_t is None:
                return self._empty_cross(), self._empty_sector(None)
            t_now = self._last_t
        else:
            t_now = _secs(now)
        me = self.syms.get(symbol)
        cross = self._empty_cross()
        # ---- breadth (latest at or before now)
        if self._breadth is not None and self._breadth["t"] <= t_now:
            cross["breadth_up"] = self._breadth["up"]
            cross["breadth_down"] = self._breadth["down"]
            cross["breadth_n"] = self._breadth["n"]
            cross["breadth_age_s"] = t_now - self._breadth["t"]
            n = self._breadth["n"]
            if n and self._breadth["up"] is not None and self._breadth["down"] is not None:
                cross["breadth_net"] = (self._breadth["up"] - self._breadth["down"]) / n
        # ---- 60-s returns of every symbol
        rets: Dict[str, float] = {}
        for sym, s in self.syms.items():
            r = self._ret(s, t_now)
            if r is not None:
                rets[sym] = r
        own = rets.get(symbol)
        cross["symbol_return_60s"] = own
        cross["n_symbols_with_return"] = len(rets) if rets else None
        if len(rets) >= self.min_market_symbols:
            cross["market_return_60s"] = float(np.median(list(rets.values())))
        others = [r for sym, r in rets.items() if sym != symbol]
        if own is not None and others:
            cross["symbol_vs_market_60s"] = own - float(np.median(others))
        # ---- leaders / laggers
        if me is not None:
            leaders, laggers, evaluated = self._lead_lag(symbol, me, t_now)
            cross["lead_lag_pairs_evaluated"] = evaluated
            if evaluated > 0:
                cross["leaders"] = leaders
                cross["laggers"] = laggers
        # ---- simultaneous liquidity change (market-wide)
        cross["simultaneous_liquidity_change"] = self._liquidity_change(symbol, t_now)
        # ---- synchronized expansion (market-wide)
        cross["synchronized_expansion"] = self._synchronized_expansion(symbol, t_now)
        # ---- sector
        sector_name = me.sector if me else None
        sector = self._empty_sector(sector_name)
        if sector_name is not None:
            members = self.sector_members(sector_name)
            sector["n"] = len(members)
            sector["sector_source"] = me.sector_source if me else None
            sec_rets = {m: rets[m] for m in members if m in rets}
            peers = {m: r for m, r in sec_rets.items() if m != symbol}
            if len(sec_rets) >= 2:
                sector["sector_return_60s"] = float(np.median(list(sec_rets.values())))
            if peers:
                sector["peer_return_60s"] = float(np.median(list(peers.values())))
                if own is not None:
                    sector["symbol_vs_sector_60s"] = own - sector["peer_return_60s"]
                    same = sum(1 for r in peers.values() if sign(r) == sign(own))
                    cross["basket_sync"] = same / len(peers)
                    cross["basket_sync_n"] = len(peers)
            if sec_rets:
                up = sum(1 for r in sec_rets.values() if r > 0)
                down = sum(1 for r in sec_rets.values() if r < 0)
                flat = len(sec_rets) - up - down
                sector["sector_breadth"] = {"up": up, "down": down, "flat": flat, "n": len(sec_rets),
                                            "net": (up - down) / len(sec_rets)}
            # pressure over current members
            pv = [s.pressure_direction * s.pressure_strength for m in members
                  for s in (self.syms[m],)
                  if self._current(s, t_now) and s.pressure_direction is not None and s.pressure_strength is not None]
            if pv:
                sector["sector_pressure"] = float(sum(pv) / len(pv))
                sector["sector_pressure_n"] = len(pv)
            # circuit clustering over current members with observable circuit data
            cross["circuit_cluster"] = self._circuit_cluster(members, t_now)
            sector["circuit_cluster"] = cross["circuit_cluster"]
        return cross, sector

    # ------------------------------------------------------------------ pieces
    def _lead_lag(self, symbol: str, me: _Sym, t_now: float
                  ) -> Tuple[List[Tuple[str, float, float]], List[Tuple[str, float, float]], int]:
        leaders: List[Tuple[str, float, float]] = []
        laggers: List[Tuple[str, float, float]] = []
        if len(me.logp) < 2:
            return leaders, laggers, 0
        self._ensure_matrices(t_now)
        i = self._idx[symbol]
        nrows = len(self._names)
        R = {lag: self._R[lag][:nrows] for lag in self.lags_s}
        M = {lag: self._M[lag][:nrows] for lag in self.lags_s}
        R0, M0 = R[0.0], M[0.0]
        my_v0 = M0[i]
        if int(my_v0.sum()) < self.min_overlap:
            return leaders, laggers, 0
        # a pair is evaluated when its lag-0 overlap reaches the threshold
        overlap0 = (M0 & my_v0[None, :]).sum(axis=1)
        evaluated_mask = overlap0 >= self.min_overlap
        evaluated_mask[i] = False
        evaluated = int(evaluated_mask.sum())
        if evaluated == 0:
            return leaders, laggers, 0
        # leaders: corr(r_me(g), r_other(g − lag)) — my lag-0 row against the others' lagged rows
        lead_l, lead_c = self._best_lag({lag: (R0[i], my_v0) for lag in self.lags_s},
                                        {lag: (R[lag], M[lag]) for lag in self.lags_s})
        # laggers: corr(r_other(g), r_me(g − lag)) — my lagged rows against the others' lag-0 rows
        lag_l, lag_c = self._best_lag({lag: (R[lag][i], M[lag][i]) for lag in self.lags_s},
                                      {lag: (R0, M0) for lag in self.lags_s})
        for j in np.nonzero(evaluated_mask)[0]:
            other = self._names[j]
            if not math.isnan(lead_c[j]) and lead_l[j] > 0 and lead_c[j] >= self.min_corr:
                leaders.append((other, float(lead_l[j]), round(float(lead_c[j]), 6)))
            if not math.isnan(lag_c[j]) and lag_l[j] > 0 and lag_c[j] >= self.min_corr:
                laggers.append((other, float(lag_l[j]), round(float(lag_c[j]), 6)))
        key = lambda x: (-x[2], x[0])  # noqa: E731
        leaders.sort(key=key)
        laggers.sort(key=key)
        return leaders, laggers, evaluated

    def _liquidity_change(self, symbol: str, t_now: float) -> Optional[Dict[str, Any]]:
        n = up = down = 0
        own_rel: Optional[float] = None
        for sym, s in self.syms.items():
            if not self._current(s, t_now):
                continue
            cur = s.liq.at_or_before(t_now)
            if cur is None or t_now - cur[0] > self.max_gap_s:
                continue
            prev = s.liq.at_or_before(t_now - self.ret_window_s)
            if prev is None or prev[1] <= 0:
                continue
            rel = (cur[1] - prev[1]) / prev[1]
            n += 1
            if sym == symbol:
                own_rel = rel
            if abs(rel) >= self.liq_change_thr:
                if rel > 0:
                    up += 1
                else:
                    down += 1
        if n < 2:
            return None
        count = up + down
        return {"share": count / n, "n": n, "count": count, "count_up": up, "count_down": down,
                "sign": (1 if up > down else -1 if down > up else 0) if count else None,
                "own_rel_change": own_rel, "threshold": self.liq_change_thr, "window_s": self.ret_window_s}

    def _synchronized_expansion(self, symbol: str, t_now: float) -> Optional[Dict[str, Any]]:
        n = count = 0
        own: Optional[bool] = None
        for sym, s in self.syms.items():
            if not self._current(s, t_now):
                continue
            cur = s.velocity.at_or_before(t_now)
            if cur is None or t_now - cur[0] > self.max_gap_s:
                continue
            top = self._velocity_top(s, cur)
            if top is None:
                continue
            n += 1
            if top:
                count += 1
            if sym == symbol:
                own = bool(top)
        if n < 2:
            return None
        return {"share": count / n, "n": n, "count": count, "own_in_top_decile": own,
                "percentile": self.top_decile_pct, "window_s": self.corr_window_s}

    def _velocity_top(self, s: _Sym, cur: Tuple[float, float]) -> Optional[bool]:
        """Whether the sample ``cur`` (t, |velocity|) is at or above the top_decile_pct
        percentile of the symbol's own earlier samples in the corr window before it.
        None when fewer than min_velocity_samples earlier samples exist or they are
        constant. The verdict depends only on the symbol's own path, so it is cached
        until the symbol receives a new sample."""
        key = (len(s.velocity), cur[0], s.velocity.last_t() or -1.0)
        if s._vel_key != key:
            ts, vs = s.velocity.arrays()
            i0 = bisect.bisect_left(ts, cur[0] - self.corr_window_s)
            i1 = bisect.bisect_right(ts, cur[0]) - 1           # index of the current sample
            hist = vs[i0:i1]                                    # earlier samples only
            top: Optional[bool]
            if len(hist) < self.min_velocity_samples or float(hist.max() - hist.min()) <= 1e-12:
                top = None      # too few, or a degenerate (constant) baseline: top decile undefined
            else:
                thr = float(np.percentile(hist, self.top_decile_pct))
                top = bool(cur[1] >= thr and cur[1] > 0)
            s._vel_key, s._vel_top = key, top
        return s._vel_top

    def _circuit_cluster(self, members: List[str], t_now: float) -> Optional[Dict[str, Any]]:
        n = locked = near = 0
        for m in members:
            s = self.syms[m]
            if not self._current(s, t_now):
                continue
            c = s.circuit or {}
            lu, ld = c.get("locked_up"), c.get("locked_down")
            du, dd = c.get("dist_up_pct"), c.get("dist_down_pct")
            if lu is None and ld is None and du is None and dd is None:
                continue
            n += 1
            if lu or ld:
                locked += 1
            elif (du is not None and du <= self.limit_near_pct) or (dd is not None and dd <= self.limit_near_pct):
                near += 1
        if n == 0:
            return None
        return {"count": locked + near, "share": (locked + near) / n, "n": n, "locked": locked, "near": near,
                "near_pct": self.limit_near_pct}

    # ------------------------------------------------------------------ shapes
    @staticmethod
    def _empty_cross() -> Dict[str, Any]:
        return {"breadth_up": None, "breadth_down": None, "breadth_n": None, "breadth_net": None,
                "breadth_age_s": None, "market_return_60s": None, "symbol_return_60s": None,
                "symbol_vs_market_60s": None, "n_symbols_with_return": None,
                "leaders": None, "laggers": None, "lead_lag_pairs_evaluated": 0,
                "basket_sync": None, "basket_sync_n": None, "circuit_cluster": None,
                "simultaneous_liquidity_change": None, "synchronized_expansion": None}

    @staticmethod
    def _empty_sector(name: Optional[str]) -> Dict[str, Any]:
        return {"sector": name, "sector_source": None, "n": None, "sector_return_60s": None,
                "peer_return_60s": None, "symbol_vs_sector_60s": None, "sector_pressure": None,
                "sector_pressure_n": None, "sector_breadth": None, "circuit_cluster": None}


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v
