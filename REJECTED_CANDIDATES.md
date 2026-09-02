# REJECTED_CANDIDATES

> Permanent ledger. A rejected idea is evidence and is never deleted — deleting
> rejections is how a research programme fools itself into rediscovering the same
> dead end. Append only.

## Round 2 — preserved 2026-09-01 from owner-supplied material

Source, committed verbatim: `prior_rounds/round2.py` (the exact script) and
`prior_rounds/round2_full_output.txt` (its full output). Definitions below are
quoted from that run, not reconstructed from memory. Universe: 392 equity-like
DSE instruments after filtering index/bond/MF — the same filter this workspace
now uses in `data/ingest_dse_eod.py`, and the same count it reproduces.

Period definitions used there: **P1** 2013-01-01…2017-12-31 · **P2**
2018-01-01…2022-07-27 · **FLOOR ERA** 2022-07-28…2024-01-31 (separated, never
pooled) · **P3** 2024-02-01…end. Cost bracket 0.8% / 1.0% / 1.2% round trip.

### R2-1 — A-family: volume spike + N-day breakout ❌ NEVER WORKED

| Variant | Definition | P1 full | P2 full | P3 full |
|---|---|---|---|---|
| A1 | spike > 3.0× ADV20, close > 20-day high, TP 8% / SL 4% / 15d | n=3918, net@1% **−0.66%**, t −5.35 | n=3806, **−0.92%**, t −6.93 | n=395, **−2.11%**, t −5.75 |
| A2 | spike > 2.5× ADV20, close > 15-day high | n=5014, **−0.59%**, t −5.46 | n=4831, **−0.83%**, t −7.19 | n=526, **−1.84%**, t −5.67 |
| A3 | spike > 4.0× ADV20, close > 30-day high | n=2382, **−0.78%**, t −4.76 | n=2404, **−1.06%**, t −6.27 | n=222, **−1.79%**, t −3.43 |

Verdict recorded: *"NEVER WORKED — P3 'failure' is not special; family negative
in early periods too."* Same-symbol P2-vs-P3 matching (59 symbols, S2) showed the
raw deterioration survives universe matching, so it is **not** a coverage
artifact. The 3-period matched universe is only **6 symbols** — declared
underpowered rather than reported as a result.

**Consequence for this workspace:** "volume spike + breakout" is a closed
question on DSE daily data, measured over ~11,000 trades. It is not to be
re-proposed as a candidate without a materially different definition and a
stated reason the old measurement does not apply.

### R2-2 — Panic-day rebound ❌ NOT CAPTURABLE

Event definition: equal-weight market return ≤ −2%, ≥ 70% decliners, ≥ 50 names;
entry at the **next open**; ex-date suspects (1-day drop > 15%) excluded; 58
independent events (≥ 5 days apart) — 40 pre-floor, 17 post-floor, 1 in-floor.

Every horizon, every cost, both regimes: **negative**. Pre-floor 3d/5d/10d net@1%
= −1.56% / −1.92% / −1.22%; post-floor = −1.78% / −2.87% / −2.49%. Liquid-100
restriction did not rescue it.

Decisive finding (S3): the "rebound" **lives entirely in the overnight gap** —
panic-day close → next open averaged +0.51% pre-floor and +0.83% post-floor. A
decision taken at the panic-day close cannot be filled before the next open, so
that gap is not capturable. *"Any backtest entering at panic-day close books this
gap as fictional profit."*

### R2-3 — Independent report's panic-regime cross-sectional mean reversion ❌ CLOSED BY T+2

Re-implemented independently and **replicated** on event count (32 pre-floor vs
their 29); **0 post-floor events**, so no persistence evidence was even possible.

| Exit | Gross | Net @1% | t | Legal under T+2? |
|---|---|---|---|---|
| close e+1 | +2.05% | **+1.05%** | 1.69 | ❌ **illegal** — their headline zone |
| close e+2 | +1.32% | +0.32% | 0.43 | ✅ earliest legal |
| close e+3 | +1.19% | +0.19% | 0.18 | ✅ |
| close e+5 | +0.96% | −0.04% | −0.03 | ✅ |

*"Their +1.98% net was measured at an exit the T+2 rule forbids. Move the exit
one day to the first legal session and the candidate decays to noise. The rebound
is front-loaded precisely inside the settlement-blocked window."*

The Q1−Q5 cross-sectional spread at the legal e+2 exit is real (+1.49%, 75%
positive, t = 3.15) — but capturing it **requires shorting Q5, unavailable in
Bangladesh**. The long-only capturable piece is the +0.32% / t = 0.43 row: noise.

**Status when Round 2 closed it: CLOSED as a trade**, on the assumption that the
earliest legal sale is entry + 2 sessions. **That kill reason has been WITHDRAWN**
— see the re-run below.

### R2-3 RE-RUN 2026-09-01 — the T+2 kill reason withdrawn, candidate re-measured

Settlement and broker-level saleability are different mechanics. Round 2 closed
this candidate on the second, which is **UNKNOWN** until confirmed against real
LankaBangla / DSE account behaviour (`config.EARLIEST_SALEABILITY_DAYS = None`).
A candidate must not stay dead on an unverified assumption, so it was
independently re-implemented and re-measured with **no exit horizon excluded**,
verified brokerage separated from estimated costs, and panels never pooled.
Script: `experiments/rerun_saleability_killed.py`; rows:
`results/rerun_saleability_killed.csv`.

**PRE-FLOOR panel, 42 events** (Round 2 had 32 on its own universe construction):

| Cohort · exit | Gross | t(gross) | Net after **verified** 0.8% | t | +0.2% est | +0.4% est |
|---|---|---|---|---|---|---|
| whole cohort · same-session | −0.47% | −1.17 | −1.27% | −3.17 | −1.47% | −1.67% |
| whole cohort · sell next close | +0.50% | +1.02 | −0.30% | −0.60 | −0.50% | −0.70% |
| whole cohort · 2 sessions | +0.02% | +0.02 | −0.78% | −1.13 | −0.98% | −1.18% |
| **Q1 (most oversold fifth) · sell next close** | **+1.49%** | **+2.09** | **+0.69%** | **+0.97** | +0.49% | +0.29% |
| Q1 · same-session | −0.33% | −0.55 | −1.13% | −1.88 | −1.33% | −1.53% |
| Q1 · 2 sessions | +0.74% | +0.80 | −0.06% | −0.07 | −0.26% | −0.46% |
| Q1−Q5 spread · sell next close | +1.36% | +1.56 | *not long-only capturable* | — | — | — |

**POSTBREAK panel (2024-02-22 →), 5 usable events: every cohort and every exit
negative.** Post-floor inside the primary panel: **1 event** — nothing inferable.

**Replication discrepancy, stated rather than smoothed:** Round 2 reported the
profit sitting in the *same-session* exit (gross +2.05%). This re-implementation
finds the same-session exit **negative** (−0.47% whole cohort, −0.33% Q1), with
the only pre-cost profit at the *next-close* exit and only in Q1. The definitions
differ somewhere — most likely the liquidity universe or the cohort construction
(Round 2's headline may have been a quintile, not the whole cohort). **Neither
version is treated as established** until the difference is traced.

**Outcome: still NOT promotable — but now for defensible reasons, not an
assumption.** Best cell (Q1, next close) is +0.69% after verified brokerage with
**t = 0.97** on 42 events — not significant; it survives only the verified-cost
layer and thins to +0.29% under a 0.4% estimate; it does not replicate the prior
result's location; and the post-break panel contradicts it outright. The
saleability question stays open and is now recorded as **owner action**, not as a
verdict.

### What Round 2 established as structure, not opinion

These now live in `bdlib/config.py` and constrain every future candidate:

- ~~**T+2 settlement** — a return measured at an exit earlier than entry + 2
  sessions was never capturable.~~ **WITHDRAWN 2026-09-01.** Settlement (T+2) is
  a clearing mechanic; broker-level saleability is a different one and may be
  earlier. `EARLIEST_SALEABILITY_DAYS = None` (UNKNOWN) until verified against
  actual LankaBangla / DSE account behaviour. No candidate may be killed by it
  while unknown; short holds are labelled with the question, not filtered out.
- **No short selling** — a long/short spread is not a strategy here.
- **Floor era 2022-07-28…2024-01-31** — a distinct regime; pooling it with free
  periods mixes two different markets.
- **Costs are reported in layers, never as one number** — verified brokerage
  **0.8% round trip** (evidence) is kept separate from an **estimated** band of
  additional costs (+0.2% / +0.4%), with capital-gains tax and slippage recorded
  as UNKNOWN and not modelled. At DSE daily-horizon effect sizes the cost layer
  is usually the entire result, so which layer a candidate dies at is the finding.
- Capital-gains tax **unknown**, modelled as 0 — so every net figure above is
  optimistic by an unknown amount.

## Phase 4.5 — doorstep footprints rejected (2026-09-02)

Question: does a pre-defined footprint at the close of *t* precede an abnormal
price event in *t+1 … t+k* beyond chance, the same-day market, volatility
clustering and today's shock? DISCOVERY window 2012–2018 only; holdout sealed;
full detail in `reports/PHASE45_DOORSTEP_REPORT.md`. All lifts below are
vol-matched on the `fresh_both` population (no door of either direction open in
t−5 … t), Newey-West t.

### P45-1 — "Quiet accumulation" (abnormal volume with a calm price) ❌ SUBTRACTS INFORMATION
F01 quiet_volume → abn_up k=3: lift 1.59 vs plain abnormal volume 1.76; inside
F15 the "calm" condition lowers the hit rate (paired t −2.6). F05
departure_calm: lift 0.98 (none) — inside F06 the calm condition subtracts
(t −6.8). F02, F13, F18: ratios to plain volume 0.68–0.91. On daily bars the
quiet part of "quiet accumulation" removes the informative occurrences.

### P45-2 — "Absorption" / dip-recovered-on-volume (F03, F04) ❌ BELOW THE GATES
→ abn_down k=3: 2.11 / 3.25 lift but shock-matched t 2.6 / 2.8 and 60 / 36
distinct doors; F04 carried by 2013 alone. → up-doors: ≤ 1.6, below plain
volume. Renamed from "absorption": a hammer bar is indistinguishable from a
bounce on EOD data.

### P45-3 — Closing strength (F11 single-session; F12 ten-session) ❌ NO INFORMATION
F11 → abn_up k=3 1.38 (÷F15 0.79); F12 1.04. F12's v1 limit-down "lift 4.2"
was a reversal after an open up-door (P45-6).

### P45-4 — Idiosyncratic move (F08u / F08d) ❌ IS "ALREADY MOVED"
93% of F08 lies inside F16. Once today's |ret|/σ enters the match, F08u →
limit_up k=5 falls 1.84 → 1.29 (t 3.1); its increment over any same-sign 2σ
move is 3–15%. F08d → limit_up was a bounce after a drop. Post-move footprints
are excluded from candidacy by construction (like F16).

### P45-5 — Persistence (F17, second abnormal-volume day) ❌ DIES AT THE INCREMENTAL GATE
→ abn_down k=3: 2.46 (t 3.6), shock 2.03 (t 3.3), 62 doors, bootstrap reference
bounds 1.05 / 1.05 — clears every gate up to step 5, then the incremental
window (2,3] gives lift 2.39 with NW t 2.67, just under the t ≥ 3 gate; at
k ≤ 2 F17 does not clear the reference bound. Nearest miss in the whole phase.

### P45-6 — v1's "down-door" candidates (F14 → limit_down 5.6×, F12 4.2×, F03, F17 → limit_down) ❌ REVERSALS, NOT DOORSTEPS
The v1 fresh filter was one-sided: for down-outcomes it removed only rows with a
recent DOWN door, so a footprint on or just after a limit-UP day was scored as a
doorstep for the limit-down that followed. F14 → limit_down k=5: 38 of 42 hits
had an up-door open; 4/708 otherwise. On `fresh_both`: F14 1.30 (t 0.3, 2 hits),
F12 2.39 (t 0.8, 3 hits), F03 1.62 (t 1.3), F17 1.05 (t 0.9). Also ~30–50% of
the unbounded "limit-down" days were beyond-band ex-date resets (I-008).

### P45-7 — Compression → volume (F10) ❌ UNMEASURABLE (213 occurrences)

### P45-8 — Any footprint → limit_up ❌ NONE PASSES
Best: F08u 1.84 (shock 1.29), F07 1.72 (shock 1.46, t 2.7, 1.00× F16u), F15
1.55. Limit-up doors at 1–10 sessions are not anticipated by any footprint here
beyond what today's activity and today's move already say.

**Survived (one, to Phase 5):** F07 idio_activity → abn_up k=3 — see
`SURVIVING_RESEARCH_LEADS.md`.

## Rejected implementation choices (kept so they are not retried)

| # | Date | Choice | Why rejected | Evidence |
|---|---|---|---|---|
| I-007 | 2026-09-02 | **One-sided "fresh door" filter** (remove only same-direction prior doors) | A footprint firing on a limit-UP day counted as a doorstep for the limit-DOWN reversal that followed; produced every v1 down-door "candidate" | Review M1/R1/S-01/LK-1; F14 → limit_down 38/42 hits with an up-door open. Replaced by `fresh_both` |
| I-008 | 2026-09-02 | **Unbounded limit proxy** (`\|R\| ≥ 0.95·band` with no far bound) | Ex-date / bonus reference-price resets (−15%, −20%, −33%) counted as limit-downs: 27–50% of "limit-down" days; they open at the new level, do not close at the low, next day up | Review M3/R2/S-02/LK-4. Now at-band only; beyond-band = corporate-action suspect, window unmeasurable |
| I-009 | 2026-09-02 | **Unsigned F08 / F16** | An idiosyncratic drop scored as a doorstep for a limit-up (bounce); the "already moved" reference was a mixture that the fresh filter truncated asymmetrically | Review LK-1/LK-2/M4/S-03/S-10. Now signed, reference matched by sign |
| I-010 | 2026-09-02 | **σ_prev-only volatility match for footprints defined on today's return** | σ_prev excludes t, so a row selected on today's shock sat among comparators that had not just moved; F08 lift 2.0 → 1.2 when today's \|ret\|/σ entered the match | Review SV-5. Shock-matched base added and gated |
| I-011 | 2026-09-02 | **iid t across dates with overlapping k-session windows** | Per-date excess ACF1 up to 0.63 at k=10 for persistent footprints; t overstated 20–50% | Review LK-3/SV-1. Newey-West (L=10) gated |
| I-012 | 2026-09-02 | **Counting every occurrence-hit as an event** | F12 → limit_down k=10: 102 "hits" were 28 distinct (symbol, door-date) events on 25 symbols | Review M7/SV-2. Distinct doors counted, gated ≥ 30 |
| I-013 | 2026-09-02 | **Point comparison "lift ratio > 1" as the reference gate** | 21 of 63 v1 rows passed by < 10%, 11 by < 5% | Review SV-4. Date-block bootstrap lower bound > 1 required |
| I-014 | 2026-09-02 | **Nested horizons counted as separate findings** | For up-doors, k=3/5/10 rows were the k=1 row diluted (F08 limit_up (0,1] lift 3.4; (5,10] 1.25) | Review SV-3/M5. Incremental outcome gate |
| I-001 | 2026-09-01 | Robust z with denominator `1.4826·MAD + eps` | Locked / zero-volume stretches give MAD = 0, so z reached 4·10¹³ and would have poisoned every downstream mean, threshold and model | Observed on the synthetic fixture; replaced by a relative scale floor plus NaN when the baseline is degenerate |
| I-002 | 2026-09-01 | Amihud impact as `\|ret\| / (turnover + eps)` | Zero-turnover bars produced 1.7·10¹⁷ | Caught by the numeric-sanity gate; now NaN when turnover = 0 |
| I-003 | 2026-09-01 | Fixture that set a stale close without re-bracketing high/low | Planted a second, unintended defect and corrupted the planted-vs-detected accounting (39 detected vs 5 planted) | `qa/verify_detectors.py` now reconciles counts exactly |
| I-004 | 2026-09-01 | **Z-scoring raw volume / turnover levels** | On REAL DSE data an illiquid symbol with trailing median volume ≈ 1 share produced `rel_volume_z` ≈ 5·10⁶ the first day it traded — the number described the denominator floor, not the market | Now z-scored in **log space** (`_robust_z_log`), plus a validity guard: fewer than 20 traded days in the trailing window ⇒ NaN |
| I-005 | 2026-09-01 | `vol_regime_ratio = σ_short / (σ_long + eps)` | Floor-era pinning makes σ_long exactly 0 ⇒ ratio 4363 on real data | NaN when σ_long is below price-grid resolution (`min_meaningful_vol`) |
| I-006 | 2026-09-01 | Unbounded z-family features | A pinned-range symbol that suddenly ranges 16% gives a truthful but unusable `range_z` = 1600; a handful of rows would dominate any downstream statistic | Winsorised at ±20 with per-feature clip counts reported (2,827 cells = 0.011% on the real dataset) — the bound is documented, not hidden |
