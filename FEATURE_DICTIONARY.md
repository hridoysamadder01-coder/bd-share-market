# FEATURE_DICTIONARY

> Implementation: `bd_research/bdlib/features.py`. Causality proved by
> `bd_research/features/leakage_test.py` (future-corruption test + positive control).

## Notation and the causality contract

For symbol *s* at bar *t*:

- `c_t, o_t, h_t, l_t` = close/open/high/low · `v_t` = volume · `q_t` = turnover
- **W** = `baseline_window` = 60 bars · **k** = `short_window` = 10 bars ·
  `vol_window` = 30 · `autocorr_window` = 60 · `min_history` = 90
- **Trailing baseline** = the window **[t−W, t−1]** — it *excludes bar t*, implemented as
  `series.shift(1).rolling(W)`. The current bar never dilutes the baseline it is judged against.
- **Robust z**: `z_t = (x_t − med) / scale`, `med` = median of the trailing window,
  `scale = max(1.4826·MAD, 0.01·|med|)`. **NaN** when `scale ≈ 0` — a locked or
  zero-volume stretch has no dispersion, so "unusual" is undefined rather than infinite.
  (Without that rule z reached 4·10¹³ on the fixture; the numeric-sanity gate in
  `run_features.py` now fails the build if any |value| exceeds 10⁶.)
- Nothing is emitted for a symbol's first `min_history` bars.
- Cross-sectional features compare symbols **at the same timestamp** — same-time
  information, never later.

## A. Price and range

| Feature | Definition | Reads as |
|---|---|---|
| `ret_1` | `ln(c_t / c_{t−1})` | one-bar log return |
| `ret_k` | `ln(c_t / c_{t−k})` | k-bar log return |
| `range_pct` | `(h_t − l_t) / c_t` | bar range, price-scaled |
| `close_location` | `(c_t − l_t) / (h_t − l_t)`, NaN when `h_t = l_t` | 1 = closed on the high (buyers finished in control), 0 = on the low |
| `gap_open` | `ln(o_t / c_{t−1})` | bar-to-bar gap; meaningful mainly at a session boundary (see `flag_session_first_bar`) |

## B. Activity, normalised to the symbol's own recent self

| Feature | Definition | Reads as |
|---|---|---|
| `rel_volume_z` | robust z of `v_t` vs trailing W | how unusual **this stock's** volume is **now** — not "> some global X" |
| `rel_turnover_z` | robust z of `q_t` vs trailing W | same in money terms (size-neutral across price levels) |
| `range_z` | robust z of `range_pct` | range expansion vs its own norm |
| `range_compression` | `−range_z` | positive ⇒ tighter than usual (coiling) |
| `volume_persistence` | share of the last k bars with `v > median(v over trailing W)` | is elevated activity *sustained* or a single print |
| `activity_concentration` | `Σ v² / (Σ v)²` over the last k bars (Herfindahl) | 1/k = evenly spread; → 1 = one bar carried everything |

## C. Volatility regime

| Feature | Definition | Reads as |
|---|---|---|
| `realized_vol` | σ of `ret_1` over the last `vol_window` | current volatility level |
| `vol_regime_ratio` | σ(k bars) / σ(`vol_window` bars) | > 1 = volatility accelerating right now |

## D. Impact and liquidity

| Feature | Definition | Reads as |
|---|---|---|
| `amihud_z` | robust z of `\|ret_1\| / q_t` (NaN when `q_t = 0`) | price move bought per unit of money — rising = thinning book |
| `hl_spread_proxy` | `2(h_t − l_t)/(h_t + l_t)` | crude spread/friction proxy from bars alone |
| `illiquidity_persistence` | share of the last k bars with `amihud_z > 2` | is the thinness sustained |

## E. Divergence and accumulation proxies

| Feature | Definition | Reads as |
|---|---|---|
| `volume_price_divergence` | `rel_volume_z × quiet`, where `quiet = 1 − min(1, \|ret_k\| / (realized_vol·√k))` | **volume without a matching move** — the classic accumulation/distribution suspicion. High only when activity is abnormal *and* price stayed quieter than this symbol's own volatility predicts |
| `accumulation_proxy` | mean over last k of `(close_location − 0.5) × rel_volume_z` | are the abnormal-volume bars closing high (absorption) or low (distribution) |
| `ret_autocorr_1` | `corr(ret_t, ret_{t−1})` over `autocorr_window` | trending (>0) vs mean-reverting (<0) microstructure |

## F. Cross-sectional / market context (same timestamp)

| Feature | Definition | Reads as |
|---|---|---|
| `xs_rank_rel_volume` | percentile rank of `rel_volume_z` across symbols at t | is this stock unusual *relative to the whole market right now* |
| `xs_rank_rel_turnover` | same for `rel_turnover_z` | money-weighted version |
| `xs_volume_abnormality` | `rel_volume_z − median(rel_volume_z at t)` | isolated move vs everyone moving together |
| `market_ret` | median `ret_1` across symbols at t | robust market proxy (no index needed) |
| `market_relative_ret` | `ret_1 − market_ret` | idiosyncratic move |
| `xs_breadth_abnormal` | share of symbols with `rel_volume_z > 2` at t | market-wide activity surge vs single-name event |
| `xs_symbols_at_ts` | count of symbols reporting at t | coverage guard — a low count makes the cross-section unreliable |

## G. State persistence

| Feature | Definition | Reads as |
|---|---|---|
| `abnormal_persistence` | consecutive bars **ending at t** with `rel_volume_z > 2` | transient spike vs a state that is *holding* |
| `bars_since_abnormal` | consecutive bars ending at t **without** abnormality | age of the current quiet state |

## Outcome labels — NEVER features

Written to a **separate file** (`*_labels.parquet`), all prefixed `fwd_`, asserted
disjoint from the feature set at build time and again in the leakage test.

| Label | Definition |
|---|---|
| `fwd_ret_h` | `ln(c_{t+h} / c_t)` for h ∈ {5, 15, 30, 60} bars |
| `fwd_mfe_h` | `ln(max high over (t, t+h] / c_t)` — maximum favourable excursion |
| `fwd_mae_h` | `ln(min low over (t, t+h] / c_t)` — maximum adverse excursion |
| `fwd_move_h` | 1 if `\|fwd_ret_h\| ≥ 2%` |

These exist so Phase 4 can ask *"what happened after this state?"* — including
every time nothing happened, which is the failed-footprint denominator.

## Data-quality flags carried alongside (from Phase 1)

`flag_zero_volume` · `flag_locked_bar` · `flag_locked_run` · `flag_stale_run` ·
`flag_large_overnight_gap` · `flag_session_first_bar`. These are **market states**,
not errors: research must be able to condition on them (e.g. exclude locked
stretches from a baseline, or study them deliberately).

## Deliberately NOT included

No RSI, MACD, Bollinger bands, fixed breakout levels or any absolute threshold
rule. The question this layer asks is *"how far is this symbol from its own recent
normal, and is the market doing it too?"* — a state measurement, not a pattern match.
