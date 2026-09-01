# PHASE 4 — PRECURSOR RESEARCH

> Run 2026-09-01 · `experiments/phase4_precursors.py` · panel-separated ·
> **FULL-SAMPLE DESCRIPTIVE. Nothing here is validated** — no walk-forward has
> been run (that is Phase 5). A cohort that looks strong below has survived
> nothing yet.

## The question

Not "what should I buy". The question is: **after a state or a state-transition
path, does what follows differ from what the market did that same day, by more
than multiple testing explains?**

## Method

| Element | Choice | Why |
|---|---|---|
| Unit of observation | one state occurrence `(symbol, date)` | |
| Denominator | **every** occurrence, including all the times nothing happened | occurrences lost to untradeability are counted on their own line, never removed from the base |
| Cohorts | mechanically enumerated: 4 states × {all, entering, held ≥3}, all 1-step paths, all 2-step paths with n ≥ 200, the 3 rung-1 baselines, and the base rate | nothing is chosen after seeing an outcome |
| Hypotheses | **750** (94 cohorts × 5 horizons in PRIMARY, 56 × 5 in POSTBREAK) | counted and corrected, not ignored |
| Correction | Benjamini–Hochberg FDR at q = 10%, per panel | |
| Inference | t computed **across dates** (average within a date first) | occurrences cluster on market-wide days; the naive per-occurrence t is optimistic and is reported alongside |
| Benchmark | the **same-day** mean of all tradeable names, tested date-paired | "it went up" is not information if everything went up that day |
| Costs | gross · net of **verified** 0.8% brokerage · then an **estimated** band (+0.2%, +0.4%) | never summed into one number; CGT and slippage remain UNKNOWN and unmodelled |
| Panels | PRIMARY and POSTBREAK computed and reported separately | pooling across the 2024-02-22 coverage break is refused by `bdlib/panels.py` |

### Execution status per horizon — saleability is UNKNOWN, so horizons are labelled, not dropped

A state is read at the **close** of day *t*, so the earliest possible action is
session *t+1*.

| Horizon | Meaning | Execution status |
|---|---|---|
| h = 1 | same-session round trip from entry | **UNKNOWN** — needs intraday netting / same-day sell |
| h = 2 | sell 1 session after purchase | **UNKNOWN** — depends on broker saleability |
| h = 3 | sell 2 sessions after purchase | OK — unaffected by the settlement question |
| h = 5, 10 | longer holds | OK |

### A data question that gates the short horizons

The `open` field carries a structural asymmetry: mean overnight gap **+0.385%**
while the **median is exactly 0**, with 25.2% of opens exactly equal to the prior
close and **66% of the nonzero gaps upward**. It is stable across price buckets
(so not tick rounding — DSE tick is Tk 0.10) and survives 1–99% trimming (so not
outliers). Every open-entry number therefore inherits a ≈ −0.4% intraday drag of
unresolved provenance.

So the whole analysis was run **twice**: entry at the next open, and entry at the
next **close** (which touches `open` nowhere). Where both agree, the finding does
not depend on that open question. **They agree on everything below.**

## Result 1 — nothing is tradeable

**0 of 750 hypotheses** produced a positive expectation after the *verified*
0.8% brokerage that survives FDR correction — in **both** entry variants.

Of the cohorts whose excess vs the same-day base rate is statistically real,
**91 have positive excess (open-entry) / 38 (close-entry) — and ZERO of them
remain positive after verified brokerage alone**, before any estimated cost, tax
or slippage is added. On this market at daily horizons, the cost layer is not a
detail; it is the whole result.

## Result 2 — there IS real information, and it is negative

230 (open-entry) / 165 (close-entry) cohorts have an excess vs same-day base rate
that survives FDR. The strongest are the same family in both variants:

| Cohort | h | n | dates | excess vs base (open-entry) | t | excess (close-entry) | t |
|---|---|---|---|---|---|---|---|
| `rung1_volume_departure` | 10 | 37,7xx | 2,562 | **−1.10%** | −10.1 | **−1.18%** | −11.0 |
| `DEPARTURE→DEPARTURE→DEPARTURE` (sustained) | 10 | 6,33x | 2,015 | **−1.57%** | −9.8 | **−1.32%** | −8.2 |
| `DEPARTURE→DEPARTURE` | 10 | 15,48x | 2,438 | −1.06% | −9.9 | −0.87% | −8.2 |
| `state=DEPARTURE` | 10 | 54,1xx | 2,580 | −0.48% | −8.6 | −0.44% | −7.9 |

Read plainly: **a stock whose activity has departed from its own baseline — and
especially one where that departure has *persisted* — underperforms the market
over the following ~10 sessions.** ~2,000 distinct dates, t ≈ −8 to −11, stable
across entry conventions and across horizon.

The failure rate is the honest companion number: even in the *most* negative
cohort, only ~63% of occurrences lost money net of verified brokerage. The
information is a distributional tilt, not a rule that fires.

**This is not a trade.** Short selling is unavailable in Bangladesh, so negative
information cannot be monetised directly. Its only legitimate use is as an
**avoidance / exclusion filter**, and even that belongs to Phase 6 — after Phase 5
has shown the effect survives walk-forward.

## Result 3 — rung 2 has not earned its complexity

`STATE_ENGINE_DESIGN.md` requires each rung to beat the one below it. The
simplest rung-1 baseline — plain abnormal volume — is **as strong or stronger**
than the rung-2 multivariate novelty states (t = −10.1 / −11.0 vs −9.8 / −8.2 at
h = 10). On this evidence:

- rung 2 is **not** justified as an improvement over rung 1;
- rungs 2b (full covariance), 3 (change-point), 4 (clustering) and 5 (transition
  models) are **not** to be built on the strength of this result.

The multivariate machinery stays in the repository because it is causally sound
and cheap to rerun, not because it has demonstrated value.

## Result 4 — persistence

Elevated states (DEPARTURE/EXTREME) last a **median of 1 session** (longest 30 in
PRIMARY, 17 in POSTBREAK). Most departures are one-day events. The subset that
*persists* is both rarer and more strongly negative — which is why
`state=DEPARTURE (held ≥3)` is the strongest cell in the table.

## POSTBREAK panel

Reported separately throughout and never pooled. With ~88 symbols and far fewer
occurrences it contributes no cohort that survives correction; it is not evidence
for or against the PRIMARY findings, only insufficient.

## What this does NOT say

- Nothing about profitability. The one robust effect is negative and unshortable.
- Nothing validated: no walk-forward, no untouched test set, no out-of-sample.
- Nothing about intraday structure — the data is daily.
- No BUY/SELL, direction, size or rule was produced by this phase.

## Files

`results/FAILED_FOOTPRINT_ANALYSIS.csv` (750 rows, open-entry) ·
`results/FAILED_FOOTPRINT_ANALYSIS_closeentry.csv` (robustness variant) ·
`results/PRECURSOR_CANDIDATES.csv` (**empty by construction — nothing qualified**) ·
`manifests/phase4_manifest.json`.

## What Phase 5 should take forward

Exactly one family: **persistent abnormal-activity departure as a negative
predictor at h = 5–10**, tested walk-forward with an untouched holdout, on the
PRIMARY panel, using the close-entry convention (which is independent of the
open-field question). Everything else in this phase is noise or is dead on costs.
