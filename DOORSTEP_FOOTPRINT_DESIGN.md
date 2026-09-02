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

## v2 — after the adversarial review of the amended run (2026-09-02)

The amended run above was reviewed by five independent lenses (lookahead,
mechanical artefacts, independent recomputation, statistical validity,
sceptical reader). The recomputation lens reproduced every published number
exactly; the other four converged on defects in what those numbers *measure*.
All of the following were changed after seeing results, and none of them makes
anything look better.

**Defect 1 — the fresh filter was one-sided.** For down-outcomes it removed
only rows with a recent DOWN door, so a footprint firing on or just after a
limit-UP day was scored as a doorstep for the limit-down that followed. That
reversal carried essentially all of the amended run's down-door candidates:
F14 → limit_down k=5 had 38 of its 42 hits on rows with an up-door open in
t−5…t; with no door open either way it had 4 hits in 708 rows (lift 1.65,
t 0.8). **Fix:** a `fresh_both` population (no door of either direction in
t−5…t, applied to occurrences and base) is now the candidacy population. The
`any` and one-sided `fresh` results are still computed and written.

**Defect 2 — the limit proxy had no far bound.** A drop of −20% or −33% is
impossible under a daily price limit; on this data such days open at the new
level, do not close at the low, and are followed by an up day — ex-date /
bonus reference-price resets (no corporate-action table exists, D-4). They
were 27–50% of the "limit-down" days. **Fix:** a limit hit is counted only *at*
the band (95%…100%+0.25% of it); a beyond-band day is a corporate-action
suspect, never a hit, and any window containing one is unmeasurable. Rows with
such a day in t−5…t are excluded from every population.

**Defect 3 — F08 and F16 were unsigned.** An idiosyncratic *drop* was being
scored as a doorstep for a limit-*up* (a bounce), and the "already moved"
reference was a mixture. **Fix:** F08u/F08d and F16u/F16d; a signed footprint
must beat the same-sign reference, an unsigned one must beat the *stronger* of
the two.

**Defect 4 — today's shock was not in the match.** σ_prev excludes session t,
so a row selected on today's return sat in a stratum whose comparators had not
just moved; F08's lift fell from 2.0 to 1.2 when today's |ret|/σ quintile was
added to the match. **Fix:** a third, shock-matched base (date × σ quintile ×
|ret_1|/σ quintile); a candidate must clear it too.

**Defect 5 — serial correlation.** Per-date excess series had lag-1
autocorrelation up to 0.63 for k ≥ 5 and persistent footprints; the iid t was
overstated by 20–50%. **Fix:** Newey-West t (Bartlett, L = 10) is the gated
statistic; the iid t is kept beside it.

**Defect 6 — one door counted many times.** A persistent footprint fires on
several sessions before one crash; F12 → limit_down k=10 had 102 "hits" that
were 28 distinct (symbol, door-date) events on 25 symbols. **Fix:** distinct
door events are counted, reported, and gated (≥ 30).

**Defect 7 — nested horizons.** limit_up within 10 contains within 5 contains
within 1; for up-doors the k=3/5/10 rows were the k=1 row diluted. **Fix:**
the *incremental* outcome (first door in (k_prev, k]) is scored, and a horizon
above 1 stands as a finding only if its increment passes lift and t on its
own.

**Defect 8 — "beats both references" was a point comparison** (21 of 63 rows
passed by < 10%). **Fix:** a date-block bootstrap lower 2.5% bound of the lift
ratio must exceed 1, and footprints that are subsets of a reference (F07, F17,
F01, F14 ⊂ F15; F08u ⊂ F16u; …) also report a within-parent paired increment.

**Also added:** a placebo (every footprint shifted +20 sessions) to calibrate
what the gates pass on stale information; unmeasurable-share per row; FDR
restricted to door outcomes and non-reference footprints and reported as
non-binding; `activity` measurability aligned with the price outcomes; the
recall base restricted to pre-door rows; survivorship stated (no symbol in the
universe ends before 2019 — discovery is conditioned on survival); the
`turnover derived` line in the Phase-1 QA report corrected; three footprints
renamed to what they measure (F03/F04 "dip recovered on volume", F12
"volume-weighted closing strength, 10 sessions", F14 "volume without range
expansion").

**Pre-registration, honestly stated.** The frozen sections above were written
before the first run, but design, code and results were committed together
after it (commit `56fa8ab`), so pre-registration rests on the author's word,
not on version history. Every amendment and every v2 change was made after
seeing results. The sealed holdout — which Round 2 and Phase 4 both inspected
descriptively before this seal existed, both ledgered — is the only real test.

### v2 candidate criterion (frozen before the v2 run)

On `fresh_both`, DISCOVERY, door outcomes, non-reference footprints:
n ≥ 200 · dates ≥ 30 · **distinct doors ≥ 30** · vol-matched **and**
shock-matched lift ≥ 1.5 · Newey-West t ≥ 3 on both · bootstrap lower bound of
the lift ratio > 1 vs F15 **and** vs the signed / stronger F16 · k = 1 or the
incremental window passes. `PHASE5_CANDIDATES.csv` carries one primary row per
(footprint, direction) — the shortest passing horizon — with every other
passing horizon listed, and a fixed Phase-5 test specification per row.

## What Phase 4.5 does NOT do

- No BUY/SELL, no position, no direction, no size.
- No cost layer — deliberately.
- No walk-forward — that is Phase 5, on the sealed holdout, only if this phase
  produces at least one candidate.
- No intraday structure — the data is daily.
