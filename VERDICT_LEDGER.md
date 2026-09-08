# VERDICT_LEDGER

> **The single cumulative record of every research candidate.** Append-only.
> Read this before proposing, testing or re-testing anything.
>
> This ledger supersedes shallow KEEP/KILL labelling. It does not replace
> `REJECTED_CANDIDATES.md` (rounds 1–2, owner-supplied prior work) or
> `SURVIVING_RESEARCH_LEADS.md` — both remain authoritative for what they cover
> and are cross-referenced by ID here.

## How to use this file

**Before testing any candidate**, search this ledger and `REJECTED_CANDIDATES.md`
for the mechanism — not the name. If substantially the same mechanism is already
KILLED or MECHANICAL_ARTIFACT, stop and cite the prior failure. Then answer, in
writing: *what is materially new compared with the closest previously tested
candidate?* A new threshold, a new weighting, a rename, or a combination with
another dead candidate is **not** an answer.

### Loop-prevention rules in force

| | |
|---|---|
| **A** | KILLED means CLOSED. No resurrection by threshold, rename, minor condition, or combination with another killed feature. |
| **B** | NOT_OBSERVABLE means WAIT FOR DATA. Never approximate missing historical truth to make a test possible. |
| **C** | A surviving parent does not rescue a killed child; a killed child does not kill the parent. |
| **D** | Never confuse association with causation, prediction with tradeability, statistical significance with economic edge, price echo with pre-move signal, or missing evidence with negative evidence. |
| **E** | Search this ledger first. Cite the prior failure instead of rerunning it. |
| **F** | Every new candidate states what is materially new. If the answer is weak, do not run it. |
| **G** | Full falsification effort before promotion, never after. |
| **H** | Failed hypotheses are assets. Preserve enough detail that the failure need not be rediscovered. |

### Verdict vocabulary

`VALIDATED_CANDIDATE` · `PROMISING_NOT_VALIDATED` · `WEAK` · `KILLED` ·
`NOT_OBSERVABLE` · `INSUFFICIENT_SAMPLE` · `MECHANICAL_ARTIFACT` ·
`ECONOMICALLY_UNUSABLE`

---

## Index

| ID | Candidate | Verdict | Failure mechanism | Reopen |
|---|---|---|---|---|
| V-001 | `D_shallow_pullback` | MECHANICAL_ARTIFACT | overlap inflation 13.4x + low-volatility + liquidity + floor-regime confound | CLOSED |
| V-002 | `F_at_limit` | MECHANICAL_ARTIFACT | circuit-mechanical price echo; untradeable entry | CLOSED |
| V-003 | `CTX_xs_rank_top` | KILLED | future leakage (60 s bucket groupby) | superseded by V-004 |
| V-004 | `E_xsrank_*` causal leadership | WEAK | no incremental value once causal | conditional |
| V-005 | `volume_compression_activity` (SAI) | KILLED | no effect; negative t at two cutoffs | CLOSED |
| V-006 | Pre-move abnormal activity | KILLED | reversed — echo, not lead | CLOSED |
| V-007 | F07 "quiet market" interpretation | KILLED | no incremental value over parent F15 | CLOSED |
| V-008 | `F15` abnormal volume (parent) | WEAK | small but real; survives as context | alive |
| V-009 | FFV / free-float velocity | NOT_OBSERVABLE | 3.81 % PIT ownership coverage | data |
| V-010 | Micro confirmation of daily buildup | NOT_OBSERVABLE | zero overlapping rows | data |
| V-011 | `D_sell_pressure_drop` | WEAK | small, unmatched for volatility | conditional |
| V-012 | `CHAIN_3_shock_premove_below` | WEAK | below cost; parent hypothesis dead | conditional |
| V-013 | `B_relvol2_atbreakout` | KILLED | **duplicate of R2-1, should never have run** | CLOSED |

---

## V-001 — `D_shallow_pullback`

**1. NAME** `D_shallow_pullback`, `research/bigmove/candidates.py` family `tightening`, run `2026-09-08-bigmove`.

**2. ORIGINAL CLAIM** Supply tightening: a share holding near its 20-day high with only a shallow pullback is being absorbed, and should expand. Discovery instead returned lift 0.536 / t −11.60, so it was carried forward as a possible **VETO / trap state** — predicting that a big move will *not* happen.

**3. EXACT DEFINITION** `downside_excursion_5 >= -0.03` AND `dist_from_20d_high >= -0.05`, where `downside_excursion_5 = min(low[t-5..t-1]) / close[t] - 1` and `dist_from_20d_high = close[t] / max(high[t-20..t-1]) - 1`. Both trailing windows are shifted by one session, so day t is excluded from its own baseline. Decision at the close of day t; outcomes from t forward. Features NaN where the trailing window spans a gap > 30 calendar days.

**4. DATA USED** 587,474 rows · 392 symbols · 2012-10-01…2026-01-22 · sealed holdout 2019-01-01…2022-07-27 and reserved slice from 2026-01-25 dropped and asserted absent. Signal: **96,875 rows → 7,232 independent episodes** (13.4x overlap), 377 symbols, 12 years, 1,778 dates. Regime mix: 51.9 % floor-era vs 14.1 % of the rest; 2023 alone 38 % of signal rows. Coverage break at 2024-02-22 (381→88 symbols) respected.

**5. PRIMARY RESULT** (+10 % within 20 sessions)

| stage | n | hit | matched control | lift | t (vs population) |
|---|---:|---:|---:|---:|---:|
| as originally measured | 96,855 | 14.67 % | 27.28 % | 0.538 | −11.60 |
| + volatility matched | 96,855 | 14.67 % | 22.93 % | 0.640 | −11.60 |
| + full matching | 96,855 | 14.67 % | 23.68 % | 0.620 | −11.60 |
| **independent episodes + full matching** | **7,231** | **27.66 %** | **26.48 %** | **1.045** | −4.54 |

Median forward return **0.000000** at 5d, 20d and 30d. MFE 20d +1.67 % (rest +6.98 %); **MAE 20d −0.91 % (rest −6.09 %)**; median days to +10 % 14 vs 10.

**6. ADVERSARIAL CHECKS**
- *Leakage*: none — features pass the corrupt-the-future test (`test_no_daily_feature_reads_the_future`).
- *Overlap / de-duplication*: **13.4x inflation found**; episode de-overlap applied (first day of each run, ≥20-session separation).
- *Volatility confound*: signal median `vol20` 0.59 % vs 2.29 %. By quintile — V0 (76 % of rows) lift 0.600, V1 0.877, **V2 1.026**, V3 0.976, V4 0.786. Effect gone by the third quintile.
- *Liquidity confound*: adv20 1.35mn vs 6.63mn. L0 (44 % of rows) lift 0.371; all other buckets 0.77–0.85.
- *Price level*: included in matching throughout.
- *Regime*: FLOOR 0.744 (n=50,297), PRE_FLOOR 0.885, POST_FLOOR 0.655.
- *Corporate actions*: ±5 sessions around every overnight gap ≥ 20 % excluded (143 rows) → lift 0.620 → **0.622**. Cleanly ruled out.
- *Circuit / floor*: see regime; floor era is half the sample.
- *LOYO*: 12 folds, lift 0.579–0.736. *LOSO*: top 20 symbols, 0.606–0.624. Both stable.
- *Response surface*: 6×6 grid, two matching sets, 72 cells. Monotone in "deadness", minimum 0.332 at the tightest cell.
- *Matched controls*: four strata sets, 20 draws each; match share falls 99.99 %→26.3 % as strata harden, which is itself a warning that the hardest matching is thin.
- *Multiple testing*: Benjamini–Hochberg q=0.10 rejects 34/36 cells under both matching sets — the effect is real and mechanical, not a multiplicity accident.
- *Economic reality*: not reached; the effect did not survive to that stage.
- *Data quality*: `range_pos_20d` found numerically unsafe on frozen shares (mean 5.7e8); guarded and pinned by test.

**7. FAILURE MECHANISM** Four compounding causes, in order of size:
1. **Overlap inflation.** A signal run lasts a median 13.4 sessions and every day was counted as independent. Taking episode entry alone moves the hit rate 14.67 %→27.66 %. The original measurement was dominated by days 5–30 of long motionless runs, i.e. it was largely measuring *"this share has not moved for two weeks"* and predicting it would not move — circular, not predictive.
2. **Low-volatility confound.** The original strata carried **no volatility term**. A share that cannot move 3 % in five days cannot reach +10 % in twenty. 76 % of signal rows sit in the lowest volatility quintile.
3. **Liquidity confound.** 44 % in the lowest liquidity quintile, where lift is 0.371.
4. **Floor-regime concentration.** 51.9 % of rows inside the 2022–24 hard floor, when many shares were pinned by regulation.

It is **not** an anti-signal: MAE is *smaller* in magnitude than the control (−0.91 % vs −6.09 %). It predicts **nothing happening**, not downside.

**8. VERDICT** `MECHANICAL_ARTIFACT`

**9. WHAT EXACTLY IS KILLED** The specific claim that *"shallow pullback near the 20-day high is a tradeable veto/trap state predicting the absence of a big move."* Killed with it: the reading of the original lift 0.536 as an effect of the geometry.
**Not killed:** the general idea that supply/tightening geometry might carry information — only this operationalisation of it, and only as measured on overlapping rows without volatility matching. Also not killed: the observation itself, which is true and boring — motionless shares stay motionless.

**10. WHAT REMAINS ALIVE** One methodological asset, not a signal: **volatility must be in the matching strata for any candidate that selects on range, pullback depth or distance-to-high.** `EV.add_strata` is now parameterised and `STRATA_FULL` exists for exactly this. Second: episode de-overlap (`antisignal.episode_id`) is now reusable and must be applied to any persistent-state candidate.

**11. REOPEN CONDITION** `REOPEN: CLOSED` as a veto/trap-state signal. The one narrow path that would *not* be a rename: a **materially different mechanism** — e.g. order-book-observable ask-side withdrawal during the pullback, from intraday depth data that does not exist for this panel (see V-010). That would be a new candidate with new data, not this one reopened.

**12. DO-NOT-REPEAT NOTE** Do not re-run: shallow-pullback / coiling-near-high geometry on daily EOD rows; any variant that changes the −0.03 / −0.05 thresholds (the whole 6×6 surface is measured and monotone in deadness); any combination of this with range-compression or down-volume-share; any test of it that does not de-overlap episodes and match on volatility.

**13. DECISION IMPACT** *Veto logic*: nothing added — no veto is adopted. *Research route*: closed for daily EOD. *Method*: matching and de-overlap requirements above are now mandatory for the whole programme. *Entry logic, confirmation layer, data collection*: unchanged.

**14. EVIDENCE POINTERS**
- Report `results/big_move_discovery/2026-09-08-antisignal/ANTISIGNAL_REPORT.md`
- Data `ANTISIGNAL_RESULTS.csv` (161 rows, 16 checks), `RESPONSE_SURFACE.csv` (72 cells), `MECHANISM.csv`, `MANIFEST.json`
- Code `research/bigmove/antisignal.py`, `research/bigmove/run_antisignal.py`, `research/bigmove/evaluate.py`
- Tests `tests/test_antisignal.py` (20), `tests/test_bigmove_panel.py::test_a_frozen_share_has_no_position_within_a_zero_range`
- Parent commit `0230fb20e981bc214419b59bdcc9438a57bbe4e1`, branch `claude/big-move-ultra-push`

---

## V-002 — `F_at_limit`

**1. NAME** `F_at_limit`, family `constraint`, run `2026-09-08-bigmove`.
**2. ORIGINAL CLAIM** None — it was the deliberate control twin of `F_room_available`, testing whether circuit room is an opportunity constraint. It then reached PROMISING on all 8 outcomes.
**3. EXACT DEFINITION** `rel_volume >= 2.0` AND `at_limit_up`, where `at_limit_up = day_ret >= circuit_pct - 0.0025` and `circuit_pct` comes from `bdlib.config.CIRCUIT_BANDS_UNVERIFIED` applied to the close.
**4. DATA USED** 3,132 signal rows, 367 symbols, same panel as V-001.
**5. PRIMARY RESULT** +5 %/5d: 81.96 % vs 46.39 %, lift 1.767, t **37.97**, 12/12 years. +10 %/20d lift 1.434. **Median forward return 20d −2.53 %** against −0.34 % for abnormal volume not at limit.
**6. ADVERSARIAL CHECKS** Prior-return profile (median day return +9.87 %, prior 3d +12.80 %, `range_pos_20d` 1.164 — already above its 20-day high); time-to-target distribution (**70.2 % reach +5 % on the first forward day** vs 15.3 %); forward return; tradeability inspection.
**7. FAILURE MECHANISM** **Mechanical circuit effect + price-move echo + untradeable entry assumption.** It reaches +5 % because it limit-ups again the next session — the band is ~10 %, so the target is inside one day's move. It then loses more over 20 days than the group it beat. And a share closing at its limit has no offer, so the assumed entry (buy at the close of day t) does not exist.
**8. VERDICT** `MECHANICAL_ARTIFACT` (also `ECONOMICALLY_UNUSABLE`; the mechanical failure is primary).
**9. WHAT EXACTLY IS KILLED** Any use of limit-up state as a long-entry or expansion predictor. **Not killed:** circuit *room* as an opportunity constraint — `F_room_available` remains the correct framing and is separately WEAK (lift 0.987–1.079).
**10. WHAT REMAINS ALIVE** The evaluation lesson: a candidate can clear lift, t=38, bootstrap CI and 12/12 year consistency simultaneously and still be worthless. Entry feasibility must be checked **before** statistical promotion.
**11. REOPEN CONDITION** `REOPEN: CLOSED` for directional use. Limit state may be re-used as a *filter* (excluding untradeable rows), which is not a reopening.
**12. DO-NOT-REPEAT NOTE** Do not test limit-up, limit-down, or "closed at the band" as directional predictors at any horizon or threshold. Do not report a candidate as promoted without an entry-feasibility check.
**13. DECISION IMPACT** *Entry logic*: rows at the limit must be excluded from any entry universe as untradeable. *Veto/confirmation/data*: unchanged.
**14. EVIDENCE POINTERS** `results/big_move_discovery/2026-09-08-bigmove/BIG_MOVE_REPORT.md` §F.1, `CANDIDATE_RANKING.csv`; commit `0230fb2`.

---

## V-003 — `CTX_xs_rank_top`

**1. NAME** `CTX_xs_rank_top`, `research/edge_discovery/families.py`.
**2. ORIGINAL CLAIM** Top decile of instantaneous cross-sectional pressure predicts short-horizon direction. Reported **+21.75 pp**.
**3. EXACT DEFINITION (as it was)** `f["_tbin"] = t_frame.dt.floor("60s")`, then `groupby("_tbin")["P"].transform("mean")` and `.rank(pct=True)`.
**4. DATA USED** Micro frames, 14 symbols, 2026-09-06.
**5. PRIMARY RESULT** **Withdrawn.** Not restated, because the number was produced by a construction that could not have been computed live.
**6. ADVERSARIAL CHECKS** `tests/test_bigmove_pit.py::test_A_the_bucket_implementation_fails_this_test` demonstrates the leak directly: appending one observation at 10:00:55 changes the output for a row at 10:00:05.
**7. FAILURE MECHANISM** **Future leakage.** A bucket is not an instant; every row in a 60-second bin saw every other row in it, including its own future.
**8. VERDICT** `KILLED`
**9. WHAT EXACTLY IS KILLED** The +21.75 pp measurement and any claim resting on it. **Not killed:** cross-sectional leadership as a concept — rebuilt causally as V-004.
**10. WHAT REMAINS ALIVE** `research/bigmove/pit.py::asof_cross_section` and its 13 adversarial tests, which are now the only permitted way to compute cross-sectional state.
**11. REOPEN CONDITION** Superseded, not reopenable: the question is now answered by V-004.
**12. DO-NOT-REPEAT NOTE** Never compute cross-sectional state with `floor(freq)` + `groupby.transform`. Any new cross-sectional feature must go through `asof_cross_section` and pass tests A–D.
**13. DECISION IMPACT** *Research route*: all prior cross-sectional micro results are suspect until recomputed. *Confirmation layer*: no leadership term admitted.
**14. EVIDENCE POINTERS** `results/big_move_discovery/2026-09-08-bigmove/CAUSALITY_AUDIT.md` §1–3; `research/bigmove/pit.py`; `tests/test_bigmove_pit.py`; commit `6235e34`.

---

## V-004 — `E_xsrank_*` causal cross-sectional leadership

**1. NAME** `E_xsrank_ge90/95/99`, `E_xsz_ge3`, `E_xsrank95_premove`.
**2. ORIGINAL CLAIM** The causal replacement for V-003: a share leading the market cross-section predicts a big move.
**3. EXACT DEFINITION** `xs_rank_asof` of `rel_volume` from `asof_cross_section(value="rel_volume", entity="symbol", time="date", min_others=20)`, computed **per panel** so no rank spans the 2024-02-22 coverage break. Leave-one-out: the share is excluded from its own market.
**4. DATA USED** 587,474 rows; 580,565 with a defined rank; 392 symbols; 12 years.
**5. PRIMARY RESULT** +10 %/20d: rank ≥ 0.99 lift 1.039 (n=6,546); ≥ 0.95 lift 1.012; ≥ 0.90 lift **0.999**. +20 %/30d: 1.016 / 1.002 / 0.993. With a pre-move filter: 0.973 / 0.935.
**6. ADVERSARIAL CHECKS** Causality tests A–D passed; per-panel computation; matched controls (original strata, 20 draws); NW t on per-date differences; year consistency 0.83–0.92.
**7. FAILURE MECHANISM** **No incremental value.** Monotone in the right direction but flat: the top 1 % beats its matched control by 3.9 % relative, the top decile by nothing. Adding a pre-move filter pushes it below 1.0.
**8. VERDICT** `WEAK`
**9. WHAT EXACTLY IS KILLED** The claim that cross-sectional leadership is a *usable* daily big-move predictor. **Not killed:** the measurement itself, which is now causally sound and reusable as a descriptive context field.
**10. WHAT REMAINS ALIVE** `xs_rank_asof`, `share_z_asof`, `market_median_asof` and the freshness fields as **context/logging**, not as entry terms. The monotonicity is a genuine (if tiny) regularity.
**11. REOPEN CONDITION** Reopenable only with (a) intraday depth breadth across the market — the whole-market runner begins accumulating this — so leadership can be measured on pressure rather than on volume alone, or (b) sector labels as a point-in-time field, enabling sector-relative leadership, currently unavailable (see V-009 note on sector).
**12. DO-NOT-REPEAT NOTE** Do not re-test daily cross-sectional rank of *relative volume* at any decile cutoff; the surface is measured and flat. Do not combine it with the pre-move filter (V-006) — that is combining two dead things.
**13. DECISION IMPACT** *Confirmation layer*: admitted as context only. *Data collection*: motivates market-wide intraday depth. *Entry/veto*: nothing.
**14. EVIDENCE POINTERS** `CANDIDATE_RANKING.csv` family `leadership`; `BIG_MOVE_REPORT.md` §F.4; `research/bigmove/pit.py`; commit `0230fb2`.

---

## V-005 — `volume_compression_activity` (the SAI shape)

**1. NAME** `volume_compression_activity`, deliberately renamed from "SAI / silent accumulation index" to avoid asserting the mechanism in the name.
**2. ORIGINAL CLAIM** High volume with a small candle body indicates silent accumulation and precedes expansion.
**3. EXACT DEFINITION** `(volume / mean(volume[t-10..t-1])) * (1 - |close-open| / (high-low + 1e-12))`. Tested at the 95th, 98th and 99th percentile of its own distribution, plus a pre-move-filtered variant and a `rel_volume>=3 & body_ratio<=0.25` variant.
**4. DATA USED** 587,474 rows; signal 5,806–29,023 by cutoff; 392 symbols; 12 years.
**5. PRIMARY RESULT** +10 %/20d lift: q99 1.027 (**t −0.40**), q98 1.004, q95 1.004, q95+pre-move 1.018 (**t −0.89**), big-vol-small-body 0.997. **+20 %/30d: every cell below 1.0** (0.944–0.972).
**6. ADVERSARIAL CHECKS** Three cutoffs (response curve, not one threshold); pre-move conditioning; a structurally different formulation of the same idea; matched controls; NW t; year consistency.
**7. FAILURE MECHANISM** **No effect.** Not a confound story — the quantity simply carries no information about subsequent expansion. Two cells have negative t, and the +20 % surface is uniformly below the control.
**8. VERDICT** `KILLED`
**9. WHAT EXACTLY IS KILLED** The Gemini-style SAI construction and the "high volume + small body = accumulation" interpretation on daily bars. **Not killed:** abnormal volume itself (V-008), which is a different and weakly live quantity.
**10. WHAT REMAINS ALIVE** Nothing from this candidate.
**11. REOPEN CONDITION** Reopenable **only** with executed buy/sell side data (signed volume), which would make "absorption" an observable rather than a candle-shape guess. That data does not exist for the daily panel (V-009 §3). A daily-bar reformulation is not a reopening.
**12. DO-NOT-REPEAT NOTE** Do not re-test any product of a volume ratio and a body/range ratio on daily bars, at any percentile, with or without a pre-move filter. Do not rename it and retry.
**13. DECISION IMPACT** *Entry logic*: nothing. *Data collection*: strengthens the case for tape/signed-flow capture. *Research route*: candle-shape accumulation proxies are closed on daily data.
**14. EVIDENCE POINTERS** `CANDIDATE_RANKING.csv` family `compression`; `BIG_MOVE_REPORT.md` §F.5; `research/bigmove/candidates.py`; commit `0230fb2`.

---

## V-006 — Pre-move abnormal activity (the central hypothesis)

**1. NAME** `B_relvol{2,3}_premove_p3lt{2,5}`, `CHAIN_2_shock_premove`, and the pre-move columns of the response surface.
**2. ORIGINAL CLAIM** The central hypothesis of the big-move programme: abnormal activity predicts a large multi-day move **specifically when price has not already run** — activity shock → not yet expanded → absorption → expansion.
**3. EXACT DEFINITION** `rel_volume >= {2,3}` AND `prior_3d_ret < {0.02, 0.05}`; the complement `prior_3d_ret >= 0.05` tested as the explicit post-move control. `rel_volume = volume[t] / median(volume[t-20..t-1])`.
**4. DATA USED** 587,474 rows; pre-move n=65,401, post-move n=34,622; 392 symbols; 12 years. Response surface: 7 volume thresholds × 3 prior-move filters × 2 outcomes = 42 cells.
**5. PRIMARY RESULT**

| | n | +10 %/20d lift (t) | +20 %/30d lift (t) |
|---|---:|---|---|
| shock, prior 3d ≥ +5 % (**already run**) | 34,622 | **1.091 (9.84)** | **1.106 (8.84)** |
| shock, prior 3d < +2 % (**not run**) | 65,401 | 1.025 (1.13) | 0.991 (1.40) |

Response surface for +20 %/30d: **every cell carrying a pre-move filter is below 1.0** (0.936–0.999); the unfiltered column is 1.00–1.015.

**6. ADVERSARIAL CHECKS** Explicit complement test (post-move); full 42-cell response surface across 7 thresholds; matched controls including `prior5_bucket`; NW t on per-date differences; year consistency; chain decomposition.
**7. FAILURE MECHANISM** **Price-move echo rather than lead — the hypothesis is reversed, not merely unsupported.** Abnormal volume predicts a big move *better* after the move has begun. The pre-move filter removes the significance entirely, and at +20 %/30d it inverts the sign of the edge.
**8. VERDICT** `KILLED`
**9. WHAT EXACTLY IS KILLED** The pre-move / accumulation *interpretation* of abnormal activity, and the ordering of the hypothesised chain. **Not killed:** abnormal volume as a weak momentum-continuation context (V-008); it is the *lead* claim that dies, not the quantity.
**10. WHAT REMAINS ALIVE** The measured fact that abnormal volume is a (small) momentum echo. If any route forward exists it is continuation, not anticipation — and continuation is already largely closed by `REJECTED_CANDIDATES.md` R2-1.
**11. REOPEN CONDITION** Reopenable only with a **new point-in-time observable that distinguishes accumulation from ordinary turnover** — signed executed flow, order-book absorption, or ownership change at a usable frequency. All three are currently NOT_OBSERVABLE (V-009, V-010). A different volume threshold or prior-return window is **not** a reopening; the surface is measured.
**12. DO-NOT-REPEAT NOTE** Do not re-test "abnormal volume + price hasn't moved yet" at any volume threshold (1.5–8× measured), any prior-return window, or any horizon 3–30d. Do not chain it with leadership (V-004) or tightening (V-001).
**13. DECISION IMPACT** *Research route*: the stated big-move chain is abandoned in its published order. *Data collection*: the case for signed flow and ownership frequency is now the binding constraint, not more daily features. *Entry/veto/confirmation*: nothing adopted.
**14. EVIDENCE POINTERS** `BIG_MOVE_REPORT.md` §F.2–F.3; `RESPONSE_CURVES.csv`; `CANDIDATE_RANKING.csv` families `premove`, `chain`; commit `0230fb2`.

---

## V-007 — F07 "quiet market" interpretation

**1. NAME** `F07_idio_activity` (DISCOVERY / fresh_both / abn_up / h3), `experiments/phase45_footprints.py:223`.
**2. ORIGINAL CLAIM** Abnormal share-level volume **on a quiet market day** is a distinct and stronger footprint than abnormal volume alone — i.e. the market-quiet condition carries information.
**3. EXACT DEFINITION** `(vol_z >= 2.0) & (xs_breadth_abnormal <= 0.05)`. Parent `F15_REF_abnormal_volume = vol_z >= 2.0`.
**4. DATA USED** DISCOVERY regime; F07 n=2,911 (145 doors, 101 symbols); F15 n=9,140.
**5. PRIMARY RESULT** Reproduced **exactly** from the stored artifact: F07 163 hits, 5.5995 %, matched base 2.5924 %, lift 2.1599, NW t 6.5945. But against the parent: **within-parent t = +0.708**, diff +0.0046. F15 alone: 6.357 % hit (higher), matched base 3.621 %, lift 1.756, **NW t 8.221** on 3× the sample. Sibling conditions: F01 (calm price) within-parent t **−2.616**; F17 (persistence) **−2.333**.
**6. ADVERSARIAL CHECKS** Within-parent conditional test (the decisive one); volume-matched and shock-matched baselines; NW t; sibling comparison across four related footprints; sample-size comparison.
**7. FAILURE MECHANISM** **Baseline artifact + no incremental value over parent.** F07's higher *lift* comes entirely from selecting into quiet days where the matched base rate is lower (2.59 % vs 3.62 %); its actual hit rate is **lower** than the parent's. The market-quiet condition contributes t = +0.71 — nothing.
**8. VERDICT** `KILLED` (the interpretation)
**9. WHAT EXACTLY IS KILLED** Precisely: *"quiet-market abnormal activity as a distinct early-accumulation footprint."* **NOT killed:** `F15` abnormal share-level volume, which is the parent and is where the entire effect lives (V-008). Rule C applies — the killed child does not kill the parent.
**10. WHAT REMAINS ALIVE** F15 / abnormal share-level activity, and the negative results on calm-price and persistence conditioning, which are informative in their own right: **conditioning abnormal volume on stillness makes it worse, every time it has been tried.**
**11. REOPEN CONDITION** `REOPEN: CLOSED`. The within-parent test is the right test and it was decisive at n=2,911.
**12. DO-NOT-REPEAT NOTE** Do not re-test market-quiet, calm-price or persistence conditioning on abnormal volume in any combination — F01, F07, F09, F17, F18 all measured, all ≤ parent. Do not report a lift without its within-parent comparison when a parent exists.
**13. DECISION IMPACT** *Research route*: the "quiet" family is closed. *Method*: any conditioned candidate must report the within-parent incremental test, not just lift vs base.
**14. EVIDENCE POINTERS** `results/DOORSTEP_FOOTPRINT_ANALYSIS.csv` columns `within_parent`, `within_parent_t_nw`; `experiments/phase45_footprints.py`; `reports/PHASE45_DOORSTEP_REPORT.md`.

---

## V-008 — `F15_REF_abnormal_volume` / `A_relvol_ge2` (the parent)

**1. NAME** `F15_REF_abnormal_volume` (`vol_z >= 2.0`) and its daily analogue `A_relvol_ge2` (`rel_volume >= 2.0`).
**2. ORIGINAL CLAIM** Abnormal share-level volume marks a state from which large moves are more likely.
**3. EXACT DEFINITION** As above; trailing baselines exclude the current day.
**4. DATA USED** F15 n=9,140 (Phase-4/5 panel); A_relvol_ge2 n=123,720 rows, 392 symbols, 12 years (big-move panel).
**5. PRIMARY RESULT** F15: 6.357 % vs 3.621 %, lift 1.756, NW t 8.221. A_relvol_ge2 at +10 %/20d: 35.83 % vs 34.19 %, lift 1.048, t 6.17, 11/12 years; at +20 %/30d lift 1.014.
**6. ADVERSARIAL CHECKS** Matched and shock-matched controls; 7-point threshold response surface (1.5–8×); NW t; LOYO via year consistency; sibling/child comparisons (V-006, V-007).
**7. FAILURE MECHANISM** Not failed, but **statistically predictive and economically marginal**: a 1.05× lift on a 34 % base is far below the 0.8 % verified round-trip cost at any realistic hit-rate translation, and R2-1 already showed the tradeable version of this family net-negative over ~11,000 trades.
**8. VERDICT** `WEAK`
**9. WHAT EXACTLY IS KILLED** Nothing here. Its *children* are killed (V-006, V-007) and its *tradeable breakout form* is killed by R2-1.
**10. WHAT REMAINS ALIVE** Abnormal volume as a **context/regime field** — a state marker for conditioning other work, not an entry.
**11. REOPEN CONDITION** N/A — alive but weak. Promotion to VALIDATED_CANDIDATE requires an economic test that clears cost, which no variant has yet done.
**12. DO-NOT-REPEAT NOTE** Do not re-test bare abnormal-volume thresholds; the 1.5–8× surface is measured at two horizons. Do not pair it with a breakout condition (R2-1) or a pre-move filter (V-006).
**13. DECISION IMPACT** *Confirmation layer*: retained as context. *Entry logic*: not admitted.
**14. EVIDENCE POINTERS** `CANDIDATE_RANKING.csv`; `RESPONSE_CURVES.csv`; `REJECTED_CANDIDATES.md` R2-1; commit `0230fb2`.

---

## V-009 — FFV / free-float velocity (and the float family)

**1. NAME** `FFV = daily traded shares / free-float shares`; signed variant `SFF = (executed buy − executed sell) / free-float shares`.
**2. ORIGINAL CLAIM** Float absorption — repeated turnover of a small free float with limited price movement — precedes expansion.
**3. EXACT DEFINITION** Not constructed. Would require point-in-time free-float shares = shares outstanding × (1 − closely-held %), both at the decision date.
**4. DATA USED / MISSING** Ownership: 1,220 rows, 419 symbols, but DSE publishes at most 3 as-on dates per company and keeps no archive; 750 of 1,220 rows are 2026. Matched against the panel: **22,377 of 587,474 rows (3.81 %) have any as-on observation at or before their own date** — 0.0 % before 2016, 0.5 % 2017, 5.8 % 2022, 44.1 % 2025, 80.5 % 2026; 106 of 392 symbols. Shares outstanding: 406 symbols, **single 2026-09-08 snapshot only**. Execution side: absent from the daily panel entirely.
**5. PRIMARY RESULT** None. No test was run, because running one would require inventing the denominator.
**6. ADVERSARIAL CHECKS** Coverage quantified per year against the actual panel rather than asserted; the bonus/stock-dividend record captured 2026-09-08 (371 symbols, 2,984 rows) demonstrates share counts changed materially over the panel, so today's snapshot is provably wrong for historical dates.
**7. FAILURE MECHANISM** **Insufficient PIT data.** Not a negative result — an absence of the observable.
**8. VERDICT** `NOT_OBSERVABLE`
**9. WHAT EXACTLY IS KILLED** Nothing. No claim about float absorption has been tested or refuted.
**10. WHAT REMAINS ALIVE** The whole family, waiting. Also alive: a candidate *reconstruction* path — historical shares outstanding could be INFERRED by dividing today's count by the cumulative bonus factor from the captured dividend history. That would be INFERRED, would cover only symbols with a complete bonus record, and would still lack the ownership split; it is a future project, not a substitute.
**11. REOPEN CONDITION** **New point-in-time observable**: ≥ 24 monthly ownership snapshots accumulated prospectively (the whole-market sweep now takes one per month), giving a two-year panel; or a licensed historical shareholding series. Rule B applies — do **not** approximate the missing history to make a test possible.
**12. DO-NOT-REPEAT NOTE** Do not build FFV using today's `total_shares` for historical dates. Do not substitute market cap / price as a float proxy. Do not test SFF on daily data at all — the execution side does not exist there.
**13. DECISION IMPACT** *Data collection*: monthly ownership capture is now the binding long-lead item and must not lapse. *Research route*: the float family is parked, not closed.
**14. EVIDENCE POINTERS** `results/big_move_discovery/2026-09-08-bigmove/DATA_GAPS.md` §1; `evidence/public_engine/2026-09-08-full/extract/ownership.csv`, `fundamentals.csv`, `company_profile.csv`; commit `6b93936`.

---

## V-010 — Micro confirmation of daily buildup

**1. NAME** Micro-confirmation layer: E1, TLPI(λ), causal QT fusion, signed interval flow (B9), touch geometry applied as confirmation for daily big-move candidates.
**2. ORIGINAL CLAIM** Intraday order-book state at a daily buildup event improves the 3/5/10-day big-move probability or reduces MAE.
**3. EXACT DEFINITION** Not constructed — requires a join between daily candidate dates and intraday frames for the same (symbol, date).
**4. DATA USED / MISSING** Micro captures exist for **2026-09-06 and 2026-09-08** (14 symbols). The discovery panel ends **2026-01-22** because the reserved slice begins 2026-01-25. **Overlap: zero rows.** Both micro sessions sit inside the reserved slice, which no phase may open.
**5. PRIMARY RESULT** None — the join is empty.
**6. ADVERSARIAL CHECKS** Date ranges verified directly against both the panel and the micro session index.
**7. FAILURE MECHANISM** **Insufficient PIT data** — specifically a coverage disjunction, not a negative result.
**8. VERDICT** `NOT_OBSERVABLE`
**9. WHAT EXACTLY IS KILLED** Nothing. The micro survivors (notably causal QT) are untouched by this; they simply cannot be tested in this role yet.
**10. WHAT REMAINS ALIVE** The whole confirmation-layer question, and the micro features themselves.
**11. REOPEN CONDITION** Either (a) enough prospective micro sessions accumulate to form a panel overlapping a future daily window — the whole-market intraday runner scheduled Sun–Thu begins this — or (b) the reserved slice is deliberately opened under an explicit protocol decision. Option (b) is a governance decision, not a research one.
**12. DO-NOT-REPEAT NOTE** Do not attempt this join until the overlap is non-empty; do not weaken the reservation to create one.
**13. DECISION IMPACT** *Data collection*: the market-wide intraday capture is the unblocking item. *Everything else*: unchanged.
**14. EVIDENCE POINTERS** `DATA_GAPS.md` §2; `micro/sessions/`; `seeing/capture/market_day.py`; trigger `trig_01JEa7YFFqTsuttd1SLL8kBD`.

---

## V-011 — `D_sell_pressure_drop`

**1. NAME** `D_sell_pressure_drop`, family `tightening`.
**2. ORIGINAL CLAIM** A falling share of down-day volume indicates sell-side exhaustion and precedes expansion.
**3. EXACT DEFINITION** `down_vol_share_5[t-1] − down_vol_share_5[t] >= 0.15`, where `down_vol_share_5 = Σ(volume on down days over 5) / Σ(volume over 5)`.
**4. DATA USED** n=94,844 rows, 392 symbols, 12 years. **Not de-overlapped; not volatility-matched.**
**5. PRIMARY RESULT** +20 %/30d lift 1.065 (t 4.05); +10 %/20d lift 1.061 (t 4.78); year consistency 0.83.
**6. ADVERSARIAL CHECKS** Matched controls (original strata only); NW t; year consistency. **Not yet subjected to** episode de-overlap or volatility matching — the two checks that destroyed V-001.
**7. FAILURE MECHANISM** None established. The result is small and **untested against the confounds now known to matter**, so it must not be treated as validated.
**8. VERDICT** `WEAK`
**9. WHAT EXACTLY IS KILLED** Nothing yet.
**10. WHAT REMAINS ALIVE** The candidate, pending the V-001 treatment.
**11. REOPEN CONDITION** N/A — alive. **Required before any promotion:** episode de-overlap and `STRATA_FULL` matching, per the V-001 lesson. Given that it lives in the same family as V-001 and shares its "quiet share" flavour, the prior should be that it collapses similarly.
**12. DO-NOT-REPEAT NOTE** Do not report this candidate again without the two checks named above.
**13. DECISION IMPACT** *Research route*: one queued re-test, not a new family. *Nothing else changes.*
**14. EVIDENCE POINTERS** `CANDIDATE_RANKING.csv` family `tightening`; commit `0230fb2`.

---

## V-012 — `CHAIN_3_shock_premove_below`

**1. NAME** `CHAIN_3_shock_premove_below`.
**2. ORIGINAL CLAIM** The three-link chain: activity shock + price not yet run + still below the 20-day high.
**3. EXACT DEFINITION** `rel_volume >= 2.0` AND `prior_3d_ret < 0.02` AND `dist_from_20d_high <= -0.05`.
**4. DATA USED** n=34,895 rows; 392 symbols; 12 years. Not de-overlapped.
**5. PRIMARY RESULT** +10 %/20d: 39.11 % vs 36.25 %, lift 1.079, t 6.37, 11/12 years, MFE +7.8 % / MAE −6.6 %. +20 %/30d lift 1.058, t 5.05, 12/12 years.
**6. ADVERSARIAL CHECKS** Chain decomposition (each link added and removed); matched controls; NW t; year consistency.
**7. FAILURE MECHANISM** **Statistically predictive but economically useless**, and built on a dead premise: its middle link is V-006, which is killed. The only link that adds anything is "below the 20-day high" (1.025 → 1.079); adding leadership drops it to 0.969 and the full five-link chain to 0.993. A 1.079 lift on a 36 % base does not clear the 0.8 % round trip.
**8. VERDICT** `WEAK` (bordering `ECONOMICALLY_UNUSABLE`)
**9. WHAT EXACTLY IS KILLED** The chain as a *chain* — the hypothesised sequence does not compound. **Not killed:** the single observation that "still below the 20-day high" adds a little, which is a mean-reversion-toward-range flavour, not a buildup story.
**10. WHAT REMAINS ALIVE** Only that one link, and only descriptively.
**11. REOPEN CONDITION** Not reopenable as a chain while V-006 stands killed (rule A: combining with a killed feature is not a reopening). The isolated "below 20-day high" term may be tested on its own — that is a different, and materially simpler, candidate.
**12. DO-NOT-REPEAT NOTE** Do not re-test 4- or 5-link chains built from V-001/V-004/V-006 components in any order or weighting.
**13. DECISION IMPACT** *Entry logic*: nothing adopted. *Research route*: chain-building is suspended until at least one link is independently validated.
**14. EVIDENCE POINTERS** `BIG_MOVE_REPORT.md` §F.3; `CANDIDATE_RANKING.csv` family `chain`; commit `0230fb2`.

---

## V-013 — `B_relvol2_atbreakout` — a rule-E violation, recorded

**1. NAME** `B_relvol2_atbreakout`.
**2. ORIGINAL CLAIM** Abnormal volume at a 20-day breakout precedes expansion.
**3. EXACT DEFINITION** `rel_volume >= 2.0` AND `dist_from_20d_high >= -0.01`.
**4. DATA USED** n=38,754 rows; 392 symbols; 12 years.
**5. PRIMARY RESULT** +10 %/20d lift 1.008 (t 3.77); +20 %/30d lift **0.993** (t 3.60); year consistency 0.75.
**6. ADVERSARIAL CHECKS** Matched controls; NW t; year consistency — **and, belatedly, a ledger search.**
**7. FAILURE MECHANISM** **No effect** — and, more importantly, **this candidate should never have been run.** `REJECTED_CANDIDATES.md` R2-1 already closed "volume spike + N-day breakout" on this exact universe over ~11,000 trades, net −0.59 % to −2.11 % across three periods with t from −3.43 to −7.19, and explicitly recorded: *"not to be re-proposed as a candidate without a materially different definition and a stated reason the old measurement does not apply."* No such reason was stated. My result is consistent with R2-1 and adds nothing.
**8. VERDICT** `KILLED` (duplicate of a prior kill)
**9. WHAT EXACTLY IS KILLED** Already killed by R2-1; this entry exists only to record the duplication.
**10. WHAT REMAINS ALIVE** Nothing. The useful output is procedural: this is a worked example of the loop this ledger exists to prevent, found by searching the prior rejections **after** the run rather than before.
**11. REOPEN CONDITION** `REOPEN: CLOSED` — see R2-1.
**12. DO-NOT-REPEAT NOTE** Do not test volume-spike-plus-breakout in any parameterisation. **And: run the ledger search before writing the candidate list, not after running it.**
**13. DECISION IMPACT** *Process*: the candidate-generation step must begin with a ledger search, and each candidate must carry its "what is materially new" answer in the code beside its definition.
**14. EVIDENCE POINTERS** `REJECTED_CANDIDATES.md` R2-1; `prior_rounds/round2.py`, `prior_rounds/round2_full_output.txt`; `CANDIDATE_RANKING.csv`; commit `0230fb2`.

---

## Cross-cutting method rules established by these verdicts

1. **Volatility must be in the matching strata** for any candidate selecting on range, pullback depth, or distance-to-high (V-001).
2. **Persistent-state candidates must be de-overlapped into episodes** before any count or t-statistic is reported (V-001).
3. **Entry feasibility is checked before statistical promotion**, not after (V-002).
4. **Cross-sectional state is computed only through `asof_cross_section`** and must pass tests A–D (V-003).
5. **A conditioned candidate reports its within-parent incremental test**, not only lift vs base (V-007).
6. **`t_nw_date` and `lift` answer different questions** — t compares to the population, lift to matched controls. Never read one as corroborating the other (V-001 §defects).
7. **The ledger search precedes candidate generation** (V-013).
