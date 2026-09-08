# Review of a proposed "circuit pre-detection engine"

A proposal was brought in from outside this repository on 2026-09-08 and checked against
what is actually here. It defines

```
Circuit Score = W1 x Pre-Open Imbalance + W2 x Historical Volatility + W3 x Historical MAE
```

with weights 0.5 / 0.3 / 0.2, a live fetcher against
`https://www.dse.com.bd/data/data_center_data`, scoring built on `fwd_mae`,
`flag_locked_run` and `flag_stale_run`, a pre-open 9:00-9:30 pressure read, a price-limit
table, and the claim that a 10:1 bid/ask ratio implies "80 %+ circuit lock probability".

Every claim below was executed, not reasoned about.

## What is wrong with it

### 1. The endpoint does not exist — 404 on all three spellings

```
https://www.dse.com.bd/data/data_center_data   -> 404, 146 bytes, text/html
https://dsebd.org/data/data_center_data        -> 404, 146 bytes, text/html
https://www.dse.com.bd/data_center_data        -> 404, 146 bytes, text/html
```

The live-data layer the whole design rests on has no source.

### 2. `fwd_mae` is a forward label — scoring with it is lookahead

| file | rows | `fwd_*` columns |
|---|---|---|
| `results/dse_eod_features.parquet` | 861,256 | **none** |
| `results/dse_eod_labels.parquet` | 861,256 | `fwd_ret_/fwd_mfe_/fwd_mae_/fwd_move_` at 5, 15, 30, 60 |

The separation is deliberate: `fwd_mae_5` is the worst adverse excursion over the *next*
five sessions. Feeding it into today's score means the score already knows the answer.
Any accuracy measured that way is arithmetic, not prediction — the same defect found and
fixed in the queue-emulator code (a label computed from the row it was predicting).

### 3. DSE has no pre-open session, so the largest term has no input

`seeing/clock.py:33` — `PRE_OPEN_START = dtime(9, 45)` carries the note
`# label only: hts.php says pre-open is "Not Applicable"`. The exchange's own trading-hours
page states it. There is no 9:00-9:30 order-imbalance window on this exchange to read, so
the 0.5-weighted term — half the score — cannot be computed at all.

### 4. `flag_locked_run` / `flag_stale_run` are QA gates, not predictors

Both are set in `bdlib/qa.py:108-117` and mark bars whose price did not move for a run of
sessions — a *data-quality* condition used to exclude bars from study. The proposal doubles
the score when they fire, which scores the symbol highest exactly when its data is least
trustworthy.

### 5. The predictive question has already been run here — and rejected

`REJECTED_CANDIDATES.md` records **P45-8 — Any footprint → limit_up ❌ NONE PASSES**:

> Best: F08u 1.84 (shock 1.29), F07 1.72 (shock 1.46, t 2.7, 1.00x F16u), F15 1.55.
> Limit-up doors at 1-10 sessions are not anticipated by any footprint here beyond what
> today's activity and today's move already say.

The proposal's remaining two terms — historical volatility and historical MAE — are that
same family: recent-move and recent-range footprints. **P45-4** additionally shows the
idiosyncratic-move footprint collapses from 1.84 to 1.29 once today's own move enters the
match. This is not an untried idea; it is a tried and killed one, and the 0.5/0.3/0.2
weights and the "80 %+" figure are assertions with nothing fitted behind them.

## What is right about it

**The price ladder is correct.** The proposal guesses 200 Tk → 10 %, 500 Tk → 8.75 %.
Measured against the exchange's published table
(`evidence/public/2026-09-06/normalized/circuit_limits.parquet`, 379 rows after the equity
filter):

| price band | `breaker_pct` | instruments |
|---|---|---|
| ≤ 200 | 10.00 | 334 |
| 200-500 | **8.75** | 23 |
| 500-1000 | **7.50** | 12 |
| > 1000 | 3.75-6.25 | 10 |

Exactly right on both bands it named. But there is no need to guess a ladder: the exact
per-symbol band is already collected, twice and independently — `circuit_limits.parquet`
(LankaBD, 635 rows) and `dse_circuit_breaker_official.parquet` (`dsebd.org/cbul.php`,
636 rows), both `truth = OBSERVED`, both carrying `lower_limit` and `upper_limit` per
symbol. The remaining 217 rows at 2.00 % are all G-SEC T-Bonds, which the equity filter
already drops.

**The target event is real and frequent enough to model.** Measured by
`circuit_lock_base_rate.py` over the extended bar table (917,206 bars, 408 symbols,
2012-10-01 → 2026-09-07):

| | count | rate |
|---|---|---|
| high touched the band | 18,348 | 2.001 % |
| **closed at the band (lock)** | **8,973** | **0.979 %** |
| naive unbounded proxy (I-008) | 9,609 | 1.048 % |
| beyond band — corporate-action suspect | 636 | 6.6 % of the naive count |

388 of 408 symbols have locked up at least once; the last 12 months hold 547 locks in
63,844 bars (0.857 %). Roughly 9,000 positive events over 14 years is a workable sample —
unlike the ownership question, which is blocked at ~200 non-independent observations.

The at-band definition is used rather than the unbounded one because **I-008** rejected the
unbounded proxy: reference-price resets on ex-dates clear the band without a lock. On the
UP side that contamination is 6.6 %, far smaller than the 27-50 % it caused on the DOWN
side, but it is excluded rather than tolerated.

## Verdict

The engine as proposed cannot be built: its data source 404s, its largest input does not
exist on this exchange, its scoring input is a forward label, and its two remaining inputs
belong to a family this repository already tested and rejected in P45-8/P45-4.

What survives is narrower and real: the per-symbol circuit band is **OBSERVED** and in
hand, and **distance to the band** is therefore computable for any bar without inventing
anything. Whether distance-to-band carries information is an open question — and P45-8 is
the reason it would have to go through the full falsification battery (fresh-both filter,
distinct-event counting, date-block bootstrap lower bound, incremental gate against today's
move) rather than being scored and believed.

Nothing here is a result. No candidate was scored; only the frequency of the event and the
correctness of the published band table were measured.
