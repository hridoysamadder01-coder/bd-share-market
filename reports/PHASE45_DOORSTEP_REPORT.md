# PHASE 4.5 — DOORSTEP FOOTPRINT RESEARCH

> Run 2026-09-02 · `experiments/phase45_footprints.py` · DISCOVERY window
> 2012-10-01 → 2018-12-31 only · holdout 2019-01-01 → 2022-07-27 **sealed**
> (274,599 rows dropped at load, never read) · **DESCRIPTIVE. Nothing here is
> validated out-of-sample.** No cost layer. No BUY/SELL.

## 1. The corrected question

Phase 4 asked whether a market state carries tradeable alpha after costs, and
answered no. That answer stands, but it was the wrong question for this track.
The question this track exists to answer is:

> **From public end-of-day data alone, can the footprint of accumulation or
> manipulation be seen *before* the door opens — before the abnormal price
> event — and how often does the footprint fire with nothing behind it?**

So this phase measures, for each of 18 pre-registered footprints read at the
close of session *t*, whether an abnormal price event in sessions *t+1 … t+k*
becomes more likely than (a) chance, (b) the same-day market, and (c) what the
stock's own volatility already predicts. Costs are deliberately not applied:
whether a doorstep is *tradeable* is a later question; whether it *exists* is
this one.

## 2. What "door" means here

| Outcome | Definition over sessions (t, t+k], k ∈ {1,2,3,5,10} | Kind |
|---|---|---|
| limit_up | any session with close/prev-close ≥ 95% of the upper limit band | door |
| limit_down | any session ≤ −95% of the band | door |
| abn_up | max cumulative log return ≥ max(2.5·σ_prev·√k, 5%) | door |
| abn_down | min cumulative log return ≤ −max(2.5·σ_prev·√k, 5%) | door |
| run20 | max cumulative log return ≥ ln 1.20 | door |
| activity | any session with `rel_volume_z ≥ 2` | mechanical — volume autocorrelates; reported, never a door |

Close-to-close only. The `open` field is not used anywhere (its provenance is
still open, D-7). A missing session makes the outcome unmeasurable, never
interpolated.

**The limit band is now evidence, not assumption.** The schedule first assumed
(10 / 7.5 / 5 / 3.75 / 2.5 %) was contradicted by the data — 26–71% of moves in
the upper price buckets exceeded it. The daily-return distribution has a
distinct mass point at exactly +band in every bucket of the schedule below,
and moves beyond the band fall to ≈1% everywhere:

| prev close ≤ | band | moves ≥ 2% | share exactly at +band | share beyond band |
|---|---|---|---|---|
| Tk 200 | 10.00% | 115,650 | 1.7% | 0.6% |
| 500 | 8.75% | 8,106 | 5.6% | 1.1% |
| 1,000 | 7.50% | 2,690 | 9.2% | 1.0% |
| 2,000 | 6.25% | 1,531 | 11.4% | 1.1% |
| 5,000 | 5.00% | 350 | 19.4% | 2.0% |

Still unverified against a DSE/BSEC circular, and it may have changed after
2018 — one more reason the sealed holdout is a separate test.

## 3. The pre-registered footprints

All read at the close of *t*; all inputs are Phase-2 features (proved causal),
Phase-3 states (proved causal), or strictly trailing per-symbol windows. σ_prev
is the trailing 30-session σ *excluding t*. "Market quiet" = at most 5% of names
abnormal-volume that day. No sector table exists (D-8), so sector-quiet is
approximated by market-quiet. Order flow is unobservable on EOD data, so
"sellers came" is approximated by an intraday dip below the prior close.

| ID | Family | Definition | Fires (share of guarded rows) |
|---|---|---|---|
| F01 quiet_volume | quiet volume | `rel_volume_z ≥ 2`, `|ret_1| ≤ σ_prev` | 1.87% |
| F02 quiet_volume_persistent | quiet volume | ≥3 of last 5 with `rel_volume_z ≥ 1`, `|ret_5| ≤ σ_prev√5` | 7.60% |
| F03 absorption | absorption | low ≤ prev·(1−σ_prev), close ≥ prev, `rel_volume_z ≥ 1` | 0.88% |
| F04 absorption_persistent | absorption | ≥2 absorption sessions in last 5 | 0.43% |
| F05 departure_calm | own baseline | rung-2 state ∈ {DEPARTURE, EXTREME}, `|ret_1| ≤ σ_prev` | 4.53% |
| F06 departure_any | own baseline | rung-2 state ∈ {DEPARTURE, EXTREME} | 9.81% |
| F07 idio_activity | idiosyncratic | `rel_volume_z ≥ 2` while market quiet | 1.66% |
| F08 idio_move | idiosyncratic | `|market_relative_ret| ≥ 2σ_prev` while `|market_ret| ≤ σ_mkt` | 3.81% |
| F09 idio_quiet_volume | idiosyncratic | F01 while market quiet | 0.59% |
| F10 coil_then_volume | compression | mean `range_z` over prior 5 ≤ −1, `rel_volume_z ≥ 1.5` | 0.08% |
| F11 closing_strength | absorption | `close_location ≥ 0.8`, `rel_volume_z ≥ 1.5`, `|ret_1| ≤ σ_prev` | 0.30% |
| F12 accumulation_proxy | quiet volume | `accumulation_proxy ≥ 0.5` | 0.42% |
| F13 volume_price_divergence | quiet volume | `volume_price_divergence ≥ 2` | 0.23% |
| F14 turnover_no_range | quiet volume | `rel_turnover_z ≥ 2`, `range_z ≤ 0` (turnover is derived, so ≈ abnormal volume without range expansion) | 0.32% |
| **F15 REF_abnormal_volume** | reference | `rel_volume_z ≥ 2` — plain abnormal volume | 5.17% |
| **F16 REF_already_moved** | reference | `|ret_1| ≥ 2σ_prev` — the price already moved. Not a doorstep by construction; excluded from candidacy | 7.14% |
| F17 abnormal_volume_persistent | persistence | `abnormal_persistence ≥ 2` | 2.47% |
| F18 quiet_volume_repeat | quiet volume | F01 today and F01 on ≥1 of prior 5 | 0.89% |

Guards define where a measurement exists: close ≥ Tk 10, σ_prev defined,
`rel_volume_z` defined. 362,402 of 420,054 DISCOVERY rows (86.3%) are guarded.

*Causality proved:* `experiments/verify_footprint_causality.py` corrupts every
row after a cut and requires every footprint at earlier rows to be
bit-identical, and every outcome at rows ≥ 16 sessions before the cut to be
unchanged. Three cuts, 25 symbols, all pass; a deliberately leaky footprint
(next session's close) and a deliberately unbounded outcome (max over the whole
future) are both **caught**, so the passes are not vacuous.

## 4. Method

- **Unit:** one footprint occurrence `(symbol, date)`. Every occurrence counts.
  `n_failed` = occurrences the door did not follow.
- **Base rate 1 — same day:** leave-one-out share of all guarded names that day
  for which the outcome followed.
- **Base rate 2 — same day, same volatility quintile:** the volatility-clustering
  control. An abnormal stock is more volatile, and a more volatile stock hits any
  threshold more often, doorstep or not. All lifts quoted below are against this
  base unless stated.
- **Inference:** date-paired excess, t across dates (occurrences cluster on
  market-wide days). 18 × 6 × 5 = 540 hypotheses per variant; BH-FDR at q = 10%.
- **Two references:** F15 (plain abnormal volume) and F16 (already moved). A
  footprint that does not beat both restates one of them.
- **Regimes never pooled:** DISCOVERY is the only window read for decisions;
  FLOOR (2022-07-28 → 2024-01-31) and POSTBREAK (~88 symbols) are reported as
  consistency checks only.

### 4.1 Amendments made after the first run — disclosed

The frozen design produced **202** rows passing the pre-registered criterion,
with the *already moved* reference the strongest hypothesis of all (t ≈ 21).
That exposed two design defects, fixed after seeing results:

1. **Doors already open were being counted.** A limit-up at *t* followed by
   another at *t+1* is a continuation, not a doorstep. The **fresh** variant
   removes every row with an up-door in *t−5 … t* from both occurrences and base
   for up-outcomes (likewise down). 37,831 guarded rows had an up-door already
   open. The frozen "any" variant is still computed and written.
2. **A footprint was not asked to add anything.** Tier A now requires
   vol-matched lift above *both* references at the same outcome and horizon.

The funnel, DISCOVERY:

| Step | Rows |
|---|---|
| 1 pre-registered criterion, any-door (as frozen) | 202 |
| 2 same criterion on the fresh-door population | 128 |
| 3 = 2 and beats both references → **tier A** | **63** |

Those 63 rows are 8 footprints × nested horizons × outcomes; §6 de-duplicates
them.

## 5. What did NOT survive — the accumulation story as usually told

### 5.1 "Abnormal volume with a calm price" adds nothing — it subtracts

Fresh-door, DISCOVERY, vol-matched lift for **limit_up within 5 sessions**
(base rate 2.81%):

| Footprint | n | hit rate | lift | lift ÷ plain volume (F15) |
|---|---|---|---|---|
| F15 REF plain abnormal volume | 9,346 | 4.7% | **1.63** | 1.00 |
| F01 quiet_volume | 3,785 | 4.1% | 1.32 | 0.81 |
| F18 quiet_volume_repeat | 1,735 | 4.8% | 1.35 | 0.83 |
| F09 idio_quiet_volume | 1,203 | 3.9% | 1.29 | 0.80 |
| F13 volume_price_divergence | 610 | 3.9% | 1.23 | 0.76 |
| F02 quiet_volume_persistent | 20,940 | 4.0% | 1.15 | 0.71 |
| F05 departure_calm | 12,289 | 2.7% | **1.00** | 0.61 |
| F11 closing_strength | 734 | 2.6% | **0.91** | 0.56 |

Every footprint that adds "and the price stayed calm" to abnormal volume
carries *less* information about an upcoming limit-up than abnormal volume
alone. `departure_calm` carries none. The same ordering holds for abn_up at
k = 3 and for limit_up at k = 10. On daily DSE data, the quiet part of "quiet
accumulation" is not a signal; it is a filter that throws away the informative
occurrences.

### 5.2 "Absorption" and "closing strength" do not precede up-doors

F03/F04 (sellers pushed, close recovered on volume) and F11 (closed near the
high on volume, price calm) have limit_up lifts of 0.9–1.4 — below plain
volume — and recall lifts of 0.9–1.3. Where they carry information it is about
**down** doors (§6.3).

### 5.3 Compression → volume is too rare to say anything

F10 fires on 0.08% of guarded rows (212 fresh occurrences). Point estimates
are 1.5–2.2 but never reach significance at any horizon.

### 5.4 Most doors open with no visible footprint

Of the **2,247** fresh limit-up doors in DISCOVERY, the share preceded by a
footprint in the prior 5 sessions, against how often that footprint sits in
*any* 5-session window:

| Footprint | recall | base | recall lift | median lead (sessions) |
|---|---|---|---|---|
| F06 departure_any | 36.8% | 30.1% | 1.22 | 2 |
| F16 REF already moved | 36.8% | 27.0% | 1.37 | 2 |
| F08 idio_move | 23.6% | 16.0% | 1.48 | 2 |
| F15 REF plain volume | 20.6% | 14.0% | 1.47 | 1 |
| F07 idio_activity | 8.8% | 5.6% | 1.58 | 2 |
| F18 quiet_volume_repeat | 4.6% | 2.8% | 1.63 | 2 |
| F12 accumulation_proxy | 1.5% | 0.9% | 1.72 | 3 |

No footprint reaches 40% recall, and none lifts recall by more than 1.7×. For
**abnormal-up** doors (a single session ≥ max(2.5σ, 5%) that is not a limit
hit) recall lifts are ≈ 1.0 across the board: those are, on this data,
unforeseeable by any footprint here.

### 5.5 What the ten sessions before a limit-up look like on average (post-hoc)

Conditioned on the outcome — description, not evidence. Mean excess over the
same-day cross-section before the 2,247 fresh limit-up doors:

| sessions before | 1 | 2 | 3 | 4 | 5 | 6–10 |
|---|---|---|---|---|---|---|
| `rel_volume_z` | +0.46 (t 13.6) | +0.26 (9.3) | +0.20 (6.8) | +0.18 (6.7) | +0.12 (4.3) | +0.13–0.14 (3–4) |
| `volume_price_divergence` | +0.17 (12.5) | +0.11 (8.9) | +0.06 (4.9) | +0.04 (4.0) | ≈0 | ≈0 |
| `amihud_z` | −0.08 (−5.7) | −0.13 (−5.7) | −0.09 (−4.1) | −0.11 (−4.7) | −0.10 (−4.8) | −0.05…−0.08 (−2…−3.5) |
| `market_relative_ret` | −0.2% | **−0.5% (−5.2)** | −0.3% (−2.0) | −0.2% | ≈0 | ≈0 |
| `close_location` | +0.07 (9.4) | −0.02 (−2.6) | ≈0 | ≈0 | ≈0 | ≈0 |

So there *is* a slow build — activity a fraction of a σ above the cross-section
for ten sessions, unusual liquidity (negative Amihud), a small dip two sessions
before, and a strong close the session before. It is real on average and
useless as a trigger: the same build appears, weaker, before nothing at all,
which is exactly what the recall numbers in §5.4 say.

## 6. What survived — VERIFICATION PENDING

*(This section is completed after the adversarial verification workflow
reports. Until then, the tier-A rows in `results/PHASE5_CANDIDATES.csv` are
provisional.)*

## 7. Consistency outside the discovery window

Not validation — the holdout is untouched. The **floor era** (a different
regime: prices could not fall below the floor) reproduces the direction and
rough size of the tier-A families: F07 abn_down k=2 lift 5.9 (t 5.0), F07
limit_up k=3 lift 2.6 (t 3.7), F08 limit_up k=5 lift 1.76 (t 5.5), F17 abn_down
k=3 lift 3.8 (t 6.1). **POSTBREAK** (~88 symbols) yields 1–41 hits per row and
is uninformative either way; it is reported in the analysis CSV and not used.

## 8. What this does NOT say

- Nothing about profitability, costs, or a rule. No BUY/SELL was produced.
- Nothing validated: the sealed holdout has not been read. Phase 5 decides.
- Nothing about intraday structure — the data is daily.
- "Manipulation" is not something EOD data can identify. What can be measured
  is whether abnormal activity precedes abnormal price events more often than
  chance. That is a doorstep footprint, not a finding of intent.

## 9. Owner actions that would change conclusions

| # | Gap | Effect on this phase |
|---|---|---|
| D-1 | minute data | intraday absorption and order-flow footprints are unobservable; every "sellers came" proxy here is a daily-bar approximation |
| D-4 | corporate-action table | down-door hits could include ex-date price adjustments; see verification |
| D-8 | sector table | "sector quiet" approximated by "market quiet" |
| — | circuit band schedule post-2018 | limit proxies on the holdout depend on it |

## Files

`results/DOORSTEP_FOOTPRINT_ANALYSIS.csv` (540 × 2 variants × 3 regimes) ·
`results/PHASE5_CANDIDATES.csv` · `results/DOORSTEP_STABILITY.csv` ·
`results/DOORSTEP_RECALL_LEADTIME.csv` · `results/DOORSTEP_PREDOOR_PROFILE.csv` ·
`results/DOORSTEP_BAND_EVIDENCE.csv` · `results/DOORSTEP_FOOTPRINT_COVERAGE.csv` ·
`results/DOORSTEP_FOOTPRINT_OVERLAP.csv` · `manifests/phase45_manifest.json` ·
`manifests/phase45_causality_manifest.json`
