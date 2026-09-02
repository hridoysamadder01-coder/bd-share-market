# DOORSTEP_FOOTPRINT_DESIGN — Phase 4.5

> Written **before** `experiments/phase45_footprints.py` was run. Everything in
> the "pre-registered" sections is frozen at that point; the report
> (`reports/PHASE45_DOORSTEP_REPORT.md`) records what happened, including the
> footprints that failed.

## The corrected hypothesis

Phase 4 asked whether a market state carries **tradeable positive alpha after
costs**. It answered no, and that answer is kept. But it was the wrong question
for this track.

The actual question is:

> **From public end-of-day data alone, can the footprint of accumulation or
> manipulation be detected *before* the door opens — before the abnormal price
> event?**

Concretely: does a pre-defined footprint at the close of session *t* raise the
probability of an abnormal price event in sessions *t+1 … t+k* by more than
chance, than the same-day market, and than what volatility clustering alone
predicts — and how often does the footprint fire with nothing following?

Costs are **not** applied in this phase. Whether a doorstep footprint is
*tradeable* is a later question; whether it *exists* is this one.

## Sample discipline (frozen)

| Window | Dates | Role |
|---|---|---|
| **DISCOVERY** | 2012-10-01 → 2018-12-31 (PRIMARY panel) | the only window statistics are tuned or read on |
| **HOLDOUT** | 2019-01-01 → 2022-07-27 | **SEALED.** Dropped at load; an assertion refuses to continue if any row survives. Reserved for Phase 5 walk-forward |
| FLOOR | 2022-07-28 → 2024-01-31 | separate regime, descriptive only — the floor forbids down-moves, so "price did not fall" is mechanical there |
| POSTBREAK | 2024-02-22 → | separate panel (~88 symbols), descriptive only |

Honesty note: Phase 4 ran full-sample *descriptive* state statistics across
the holdout before this seal existed. The seal is new as of Phase 4.5 and
applies to every footprint defined below.

## Pre-registered footprints (frozen before any outcome is read)

All read at the **close of session t**; all inputs are Phase-2 features (proved
causal), Phase-3 states (proved causal), or strictly trailing per-symbol windows.
σ_prev = the symbol's trailing 30-session return σ **excluding t**. "Quiet market"
= at most 5% of names abnormal-volume that day. No footprint touches `open`.

| ID | Family | Definition |
|---|---|---|
| F01 quiet_volume | quiet volume | `rel_volume_z ≥ 2` and `|ret_1| ≤ σ_prev` |
| F02 quiet_volume_persistent | quiet volume | ≥ 3 of the last 5 sessions `rel_volume_z ≥ 1` and `|ret_5| ≤ σ_prev·√5` |
| F03 absorption | absorption | `low ≤ prev_close·(1−σ_prev)` and `close ≥ prev_close` and `rel_volume_z ≥ 1` |
| F04 absorption_persistent | absorption | ≥ 2 absorption sessions in the last 5 |
| F05 departure_calm | own-baseline departure | rung-2 state ∈ {DEPARTURE, EXTREME} and `|ret_1| ≤ σ_prev` |
| F06 departure_any | own-baseline departure | rung-2 state ∈ {DEPARTURE, EXTREME} (Phase-4 cohort, comparison) |
| F07 idio_activity | idiosyncratic | `rel_volume_z ≥ 2` while the market is quiet |
| F08 idio_move | idiosyncratic | `|market_relative_ret| ≥ 2σ_prev` while `|market_ret| ≤ σ_market_prev` |
| F09 idio_quiet_volume | idiosyncratic | F01 while the market is quiet |
| F10 coil_then_volume | compression → activity | mean `range_z` over the prior 5 ≤ −1 and `rel_volume_z ≥ 1.5` today |
| F11 closing_strength | absorption | `close_location ≥ 0.8` and `rel_volume_z ≥ 1.5` and `|ret_1| ≤ σ_prev` |
| F12 accumulation_proxy | quiet volume | `accumulation_proxy ≥ 0.5` |
| F13 volume_price_divergence | quiet volume | `volume_price_divergence ≥ 2` (= rung1_quiet_accumulation) |
| F14 turnover_no_range | quiet volume | `rel_turnover_z ≥ 2` and `range_z ≤ 0` |
| **F15 REF_abnormal_volume** | REFERENCE | `rel_volume_z ≥ 2` — plain abnormal volume. Every "quiet" footprint must beat this or "quiet" adds nothing |
| **F16 REF_already_moved** | REFERENCE | `|ret_1| ≥ 2σ_prev` — the price already moved. The momentum / volatility-clustering reference. **Not a doorstep footprint by construction; excluded from candidacy** |
| F17 abnormal_volume_persistent | persistence | `abnormal_persistence ≥ 2` |
| F18 quiet_volume_repeat | quiet volume | F01 today and F01 on ≥ 1 of the prior 5 sessions |

Guards (define *where a measurement exists*, not what counts): `close ≥ Tk 10`
(one tick is 1% there; below it a "limit" is a couple of ticks), σ_prev defined
and above grid resolution, `rel_volume_z` defined. The same guard applies to
footprint occurrences and to the base population.

**No sector table exists** (owner gap D-8), so "sector quiet" is approximated by
"market quiet". Order flow is unobservable on EOD data, so "sell came" is
approximated by an intraday dip below the prior close.

## Outcomes (the "door"), over sessions (t, t+k], k ∈ {1, 2, 3, 5, 10}

Date-aligned on the regime's trading calendar; a missing session makes the
outcome **unmeasurable**, never interpolated. Close-to-close only.

| Outcome | Definition | Kind |
|---|---|---|
| limit_up | any session with close/prev ≥ 95% of the upper band | door |
| limit_down | any session with close/prev ≤ −95% of the band | door |
| abn_up | max cumulative log return ≥ max(2.5·σ_prev·√k, 5%) | door |
| abn_down | min cumulative log return ≤ −max(2.5·σ_prev·√k, 5%) | door |
| run20 | max cumulative log return ≥ ln 1.20 | door |
| activity | any session with `rel_volume_z ≥ 2` | **mechanical** (volume autocorrelates) — reported, never a door, never a candidate |

**Circuit band.** The schedule by previous close (±10% ≤ Tk 200, 7.5% ≤ 500,
5% ≤ 1000, 3.75% ≤ 2000, 2.5% ≤ 5000, 1.25% above) is **UNVERIFIED**. The
script prints the empirical mass points of the daily-return distribution per
band bucket as evidence; a bucket whose returns do not pile up at ± band is not
trusted. (Pre-run check on the full non-floor sample, prev close < Tk 200:
+10% is a distinct mass point and 84% of ≥ 9.5% days close on the high.)

## Inference (frozen)

- Unit: one footprint occurrence `(symbol, date)`. **Every** occurrence counts;
  `n_failed` = occurrences the door did not follow.
- **Base rate 1 — same day.** Leave-one-out share of all guarded names that
  day for which the outcome followed.
- **Base rate 2 — same day, same volatility quintile.** Same, within the
  occurrence's σ_prev quintile that day. This is the volatility-clustering
  control: an abnormal stock is more volatile, and a more volatile stock hits
  any threshold more often, doorstep or not.
- `excess` = hit rate − base; `lift` = hit rate / base. t is computed **across
  dates** (average within a date first) for both excesses.
- Multiple testing: 18 × 6 × 5 = **540 hypotheses per regime**, BH-FDR at
  q = 10% per regime, for each base rate separately.
- Incremental information: `lift_vm_vs_ref_volume` = a footprint's vol-matched
  lift divided by F15's at the same outcome and horizon. Below 1 means plain
  abnormal volume carries the information and the extra condition subtracts.

**Recall / lead time.** The other direction: of the fresh doors that happened
(a limit-up or abnormal-up day with no such day in the prior 5), what share
had the footprint in the prior 5 / 10 sessions, against how often the
footprint sits in *any* prior-5 / prior-10 window; and the lead-time
distribution from the most recent footprint to the door.

**Pre-door profile.** POST-HOC and conditioned on the outcome: mean excess of
nine features at 1 … 10 sessions before a fresh limit-up door. Description
only. Any footprint it suggests is *not* a candidate — it goes to a fresh test
on the sealed holdout.

## Candidate criterion for `PHASE5_CANDIDATES.csv` (frozen)

A row enters the Phase 5 file only if, in DISCOVERY:

1. door outcome (never `activity`), footprint ≠ F16 (post-move by construction);
2. eligible: n ≥ 200 measured occurrences on ≥ 30 distinct dates;
3. same-day excess > 0 **and** survives BH-FDR 10%;
4. vol-matched excess > 0 **and** vol-matched date-clustered t ≥ 3;
5. vol-matched lift ≥ 1.5 — economically meaningful, not merely significant.

Nothing enters by looking promising. An empty file is a valid result.

## Amendments after the first run (recorded, not hidden)

The first run of the frozen design above produced **209** rows passing the
pre-registered criterion, with the *"already moved"* reference (F16) the
strongest hypothesis of all (t ≈ 21). That exposed two defects in the design
and one in a constant. All three were fixed **after seeing results**, which is
why they are listed here rather than folded into the sections above, and why
both the frozen and the amended analyses are written to disk.

1. **Doors already open were counted as doors.** A limit-up day at *t* followed
   by another at *t+1* is a continuation, not a doorstep — the design's own
   recall section already said so ("fresh door"), but the outcome analysis did
   not apply it. A **`fresh` variant** now removes every row with an up-door
   (limit-up or abnormal-up day) in *t−5 … t* from both the occurrences and
   the base population for up-outcomes, and likewise for down-doors. The
   frozen `any` variant is still computed and reported.
2. **A footprint was not asked to add anything.** `lift_vm_vs_ref_volume` was
   designed as a report column, not a gate. **Tier A** now requires vol-matched
   lift above *both* references (plain abnormal volume F15 and already-moved
   F16) at the same outcome and horizon, on the fresh population. The
   pre-registered criterion alone is reported as the top of the funnel.
3. **The circuit band schedule was wrong above Tk 200.** The assumed
   10 / 7.5 / 5 / 3.75 / 2.5 schedule was contradicted by the data (26–71% of
   moves in those buckets exceeded it). The daily-return distribution has a
   distinct mass point at +8.75% (200–500), +7.5% (500–1000), +6.25%
   (1000–2000), +5% (2000–5000): the schedule steps down by 1.25% per tier.
   `config.CIRCUIT_BANDS_UNVERIFIED` now carries that schedule; it is still
   unverified against a circular, and it may have changed after 2018 — which is
   one more reason the sealed holdout is a separate test.

Also added: year-by-year and price-bucket stability tables for every tier-A
row, so a single year or the penny end cannot carry a candidate unnoticed.

`PHASE5_CANDIDATES.csv` = tier A only. The funnel counts (frozen → fresh →
tier A) are printed on every run and written to the manifest.

## What Phase 4.5 does NOT do

- No BUY/SELL, no position, no direction, no size.
- No cost layer — deliberately.
- No walk-forward — that is Phase 5, on the sealed holdout, only if this phase
  produces at least one candidate.
- No intraday structure — the data is daily.
