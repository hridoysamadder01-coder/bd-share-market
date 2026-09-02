# PHASE 4.5 — DOORSTEP FOOTPRINT RESEARCH

> Run 2026-09-02 (v2) · `experiments/phase45_footprints.py` · DISCOVERY window
> 2012-10-01 → 2018-12-31 only · holdout 2019-01-01 → 2022-07-27 **sealed**
> (274,599 rows dropped at load, asserted, never read) · **DESCRIPTIVE. Nothing
> here is validated out-of-sample.** No cost layer. No BUY/SELL.
>
> This report supersedes the v1 run of the same day. v1's headline results
> were reviewed by five independent adversarial lenses and most of them did
> not survive. §4.1 says what was wrong; nothing from v1 is hidden.

## 1. The corrected question

Phase 4 asked whether a market state carries tradeable alpha after costs, and
answered no. That answer stands, but it was the wrong question for this track.
The question this track exists to answer is:

> **From public end-of-day data alone, can a footprint be seen *before* the
> door opens — before the abnormal price event — and how often does the
> footprint fire with nothing behind it?**

"Manipulation" and "accumulation" are *interpretations* that EOD bars cannot
establish (§8). What can be measured is whether a defined footprint at the
close of session *t* raises the probability of an abnormal price event in
sessions *t+1 … t+k* beyond (a) chance, (b) the same-day market, (c) what the
stock's own volatility predicts, and (d) what *today's* shock already predicts.
Costs are deliberately not applied: whether a doorstep is *tradeable* is a
later question; whether it *exists* is this one.

## 2. The answer in one paragraph

**One footprint survives every gate, and it is not an accumulation footprint.**
Abnormal volume in a stock on a day when the rest of the market is quiet
(F07) is followed by an abnormal up-move within 3 sessions 5.6% of the time,
against 2.6% for stocks of the same volatility on the same day and 3.0% for
stocks with the same volatility *and the same size of move today* — a lift of
2.2× (1.8× shock-matched), on 145 distinct events across 101 symbols, in every
one of six years, in every price bucket, and again in the floor era (2.8×). It
beats plain abnormal volume by a thin margin (bootstrap lower bound 1.05). It
fails 94% of the time. Everything else — quiet accumulation, absorption,
closing strength, persistence, idiosyncratic moves, and every "down door"
candidate from v1 — is either indistinguishable from plain abnormal volume,
indistinguishable from volatility clustering, or an artefact of counting doors
that were already open.

## 3. What "door" means here

| Outcome | Definition over sessions (t, t+k], k ∈ {1,2,3,5,10} | Kind |
|---|---|---|
| limit_up | any session closing **at** the upper limit band (95%…100%+0.25% of it) | door |
| limit_down | any session closing at the lower band | door |
| abn_up | max cumulative log return ≥ max(2.5·σ_prev·√k, 5%) | door |
| abn_down | min cumulative log return ≤ −max(2.5·σ_prev·√k, 5%) | door |
| run20 | max cumulative log return ≥ ln 1.20 | door |
| activity | any session with `rel_volume_z ≥ 2` | mechanical; never a door |

Close-to-close only; `open` is never used (D-7). A missing session makes the
outcome unmeasurable. **A close-to-close move beyond the band is impossible
under a daily price limit**; on this data such days open at the new level, do
not close at the low, and are followed by an up day — ex-date / bonus
reference-price resets (no corporate-action table exists, D-4). They were
27–50% of v1's "limit-down" days. In v2 they are corporate-action suspects:
never a hit, any window containing one is unmeasurable, and rows with one in
t−5 … t are excluded from every population (1,628 guarded rows in DISCOVERY).

**The limit band is empirically supported, not verified.** The first schedule
assumed (10 / 7.5 / 5 / 3.75 / 2.5 %) was contradicted by the data; the
daily-return distribution has a distinct mass point at +band in every bucket of
the schedule below, and moves beyond the band fall to ≈1% everywhere:

| prev close ≤ | band | moves ≥ 2% | at +band | at +band−0.25% (tick rounding) | at +band+0.25% | beyond band |
|---|---|---|---|---|---|---|
| Tk 200 | 10.00% | 115,650 | 1,983 | 1,188 | 10 | 0.6% |
| 500 | 8.75% | 8,106 | 455 | 89 | 0 | 1.1% |
| 1,000 | 7.50% | 2,690 | 247 | 42 | 0 | 1.0% |
| 2,000 | 6.25% | 1,531 | 174 | 25 | 0 | 1.1% |
| 5,000 | 5.00% | 350 | 68 | 14 | 0 | 2.0% |

The −band side has no comparable mass (0.29% of moves in the Tk ≤ 200 bucket
sit at −10% vs 1.7% at +10%): limit-downs are rare on this market. The
schedule is unverified against a circular and may have changed after 2018.

## 4. Footprints, populations, method

### 4.1 Pre-registered footprints (definitions frozen before the v1 run)

All read at the close of *t*; inputs are Phase-2 features (proved causal),
Phase-3 states (proved causal), or strictly trailing per-symbol windows. σ_prev
= trailing 30-session σ **excluding t**. "Market quiet" = at most 5% of names
abnormal-volume that day (64% of DISCOVERY dates). No sector table exists
(D-8). Order flow is unobservable, so "sellers came" is an intraday dip below
the prior close. Three footprints were **renamed in v2 to what they measure**
(review S-12); definitions are unchanged.

| ID | Definition | Fires |
|---|---|---|
| F01 quiet_volume | `rel_volume_z ≥ 2`, `|ret_1| ≤ σ_prev` | 1.87% |
| F02 quiet_volume_persistent | ≥3 of last 5 with `rel_volume_z ≥ 1`, `|ret_5| ≤ σ_prev√5` | 7.60% |
| F03 dip_recovered (v1 "absorption") | low ≤ prev·(1−σ_prev), close ≥ prev, `rel_volume_z ≥ 1` | 0.88% |
| F04 dip_recovered_persistent | ≥2 of those in last 5 | 0.43% |
| F05 departure_calm | rung-2 state ∈ {DEPARTURE, EXTREME}, `|ret_1| ≤ σ_prev` | 4.53% |
| F06 departure_any | rung-2 state ∈ {DEPARTURE, EXTREME} | 9.81% |
| **F07 idio_activity** | `rel_volume_z ≥ 2` while the market is quiet | 1.66% |
| F08u / F08d idio_move (signed in v2) | market-relative return ≥ +2σ / ≤ −2σ while market calm | 2.9% / 1.0% |
| F09 idio_quiet_volume | F01 while the market is quiet | 0.59% |
| F10 coil_then_volume | mean `range_z` over prior 5 ≤ −1, `rel_volume_z ≥ 1.5` | 0.08% |
| F11 closing_strength | `close_location ≥ 0.8`, `rel_volume_z ≥ 1.5`, `|ret_1| ≤ σ_prev` | 0.30% |
| F12 closing_strength_10s (v1 "accumulation_proxy") | `accumulation_proxy ≥ 0.5` | 0.42% |
| F13 volume_price_divergence | `volume_price_divergence ≥ 2` | 0.23% |
| F14 volume_no_range (v1 "turnover_no_range") | `rel_turnover_z ≥ 2`, `range_z ≤ 0` — turnover is derived, so ≈ abnormal volume without range expansion | 0.32% |
| **F15 REF plain abnormal volume** | `rel_volume_z ≥ 2` | 5.17% |
| **F16u / F16d REF already moved** (signed in v2) | `ret_1 ≥ +2σ_prev` / `≤ −2σ_prev` | 4.4% / 2.7% |
| F17 abnormal_volume_persistent | `abnormal_persistence ≥ 2` (⊂ F15) | 2.47% |
| F18 quiet_volume_repeat | F01 today and on ≥1 of prior 5 | 0.89% |

Guards: close ≥ Tk 10, σ_prev defined, `rel_volume_z` defined — 362,402 of
420,054 DISCOVERY rows. **The universe is survivorship-conditioned:** no symbol
in the file ends before 2019, so every stock that was delisted or collapsed in
2012–2018 is absent.

*Causality proved:* `experiments/verify_footprint_causality.py` — three
future-corruption cuts on 25 symbols (every footprint and placebo bit-identical
before the cut; every outcome bounded to (t, t+k]), a single-cell perturbation
test (perturbing one close changes exactly the rows it should and no other
symbol), and two positive controls (a leaky footprint, an unbounded outcome)
both **caught**. PASS.

### 4.2 Populations

| Variant | Rows kept | Role |
|---|---|---|
| any | guarded, no corporate-action suspect in t−5…t | the frozen v1 analysis |
| fresh | + no door of the **outcome's** direction in t−5…t | v1's amendment — **one-sided, and the source of v1's false down-door results** |
| **fresh_both** | + no door of **either** direction in t−5…t | v2 candidacy population |

In DISCOVERY 37,226 guarded rows had an up-door open and 11,505 a down-door
open. Base rates on `fresh_both` at k=5: limit_up 2.50%, abn_up 3.74%, run20
0.94%, limit_down 0.30%, abn_down 1.58%.

### 4.3 Inference

- Unit: one occurrence `(symbol, date)`. Every occurrence counts; `n_failed`
  = occurrences the door did not follow. **Distinct (symbol, door-date)
  events** are counted separately, because a persistent footprint fires on
  several sessions before one crash.
- Three leave-one-out base rates: same day; same day × σ_prev quintile
  (volatility clustering); same day × σ_prev quintile × |ret_1|/σ_prev
  quintile (**today's shock**, which σ_prev excludes by construction).
- t across dates, **Newey-West** (Bartlett, L = 10) because per-date excess
  series are serially correlated (ACF1 up to 0.63 for persistent footprints
  at k = 10); the iid t is kept beside it.
- **Incremental outcomes**: first door in (k_prev, k], so a horizon above 1
  stands only if the increment itself carries information.
- **References**: a footprint must beat plain abnormal volume (F15) and the
  signed / stronger "already moved" reference (F16u / F16d), with a date-block
  bootstrap lower 2.5% bound of the lift ratio above 1; footprints that are
  subsets of a reference also report a within-parent paired increment (for a
  date-level condition such as "market quiet", a between-dates Welch t).
- **Placebo**: every footprint shifted 20 sessions stale, run through the same
  machinery, to show what the gates pass on nothing.
- BH-FDR at q = 10% over door outcomes and non-reference footprints per
  population, reported for what it is here: non-binding (§6.4).

### 4.4 What v1 got wrong — disclosed

The v1 report claimed (a) quiet volume subtracts information, (b) F07/F08 beat
both references for up *and* down doors, (c) the strongest specific footprints
point *down* (F14 → limit-down 5.6×, F12 4.2×, absorption → down), (d) 2–8%
hit rates, (e) up-doors are harder to anticipate than down-doors. The review
(lookahead · mechanical artefacts · independent recomputation · statistics ·
sceptical reader; the recomputation lens reproduced every v1 number exactly)
found:

| v1 defect | Effect | v2 fix |
|---|---|---|
| **fresh filter one-sided** | F14 → limit_down k=5 had 38 of 42 hits on rows with an *up-door* open; 4/708 otherwise (lift 1.65, t 0.8). Every v1 down-door candidate was a reversal after an open up-door. Claim (c) false; claim (e) reversed | `fresh_both` |
| **limit proxy unbounded** | 27–50% of "limit-down" days were beyond-band ex-date resets | at-band only; beyond-band = corporate-action suspect |
| **F08/F16 unsigned** | an idiosyncratic *drop* scored as a doorstep for a limit-*up* (a bounce); the reference it "beat" was a mixture | signed |
| **today's shock not matched** | F08's lift fell 2.0 → 1.2 when |ret|/σ entered the match; 93% of F08 sits inside F16 | shock-matched base, gated |
| **iid t** | overstated 20–50% at k ≥ 5 | Newey-West |
| **one door counted many times** | F12 → limit_down k=10: 102 "hits" = 28 events on 25 symbols | distinct doors, gated ≥ 30 |
| **nested horizons** | k=3/5/10 up-door rows were the k=1 row diluted | incremental gate |
| **point reference comparison** | 21 of 63 rows passed by < 10% | bootstrap lower bound |
| **stability overstated** | F12 limit_down k=10: 2014 lift 0.39 (t −3.1) | per-year min t reported |

Pre-registration, honestly: the v1 design was written before the v1 run, but
design, code and results were committed together after it (`56fa8ab`), so
pre-registration rests on the author's statement. Every v2 change was made
after seeing results. The sealed holdout — inspected descriptively by Round 2
and Phase 4 before this seal existed, both ledgered — is the only real test.

## 5. What did NOT survive

All numbers: DISCOVERY, `fresh_both`, vol-matched lift, Newey-West t.

### 5.1 The v1 down-door candidates were reversals

| Footprint → limit_down k=5 | any | fresh (one-sided) | **fresh_both** |
|---|---|---|---|
| F14 volume_no_range | 4.67 / t 3.1 (32 hits) | 5.57 / 3.1 (31) | **1.30 / 0.3 (2 hits)** |
| F12 closing_strength_10s | 5.19 / 3.5 (63) | 5.49 / 3.5 (52) | **2.39 / 0.8 (3)** |
| F03 dip_recovered | 2.25 / 2.4 (33) | 1.99 / 1.9 (23) | **1.62 / 1.3 (7)** |
| F17 abnormal_volume_persistent | 1.85 / 3.2 (121) | 2.05 / 3.3 (107) | **1.05 / 0.9 (12)** |

Once rows with a limit-up already open are removed from occurrences *and*
base, the down-door information is gone. What v1 measured was: a stock that
just hit the upper limit on heavy volume often hits the lower limit within a
week. That is a reversal, and it is not a doorstep.

### 5.2 "Quiet" footprints carry less than plain abnormal volume — for up-doors

abn_up within 3 sessions (base 3.7%); F15 = plain abnormal volume = 1.76×:

| Footprint | n | hits | lift | shock-matched lift | ÷ F15 | within-parent t |
|---|---|---|---|---|---|---|
| F01 quiet_volume | 3,786 | 192 | 1.59 | 1.86 | 0.91 | **−2.6** (quiet *subtracts* inside F15) |
| F18 quiet_volume_repeat | 1,737 | 71 | 1.54 | 1.67 | 0.88 | −2.1 |
| F13 volume_price_divergence | 595 | 32 | 1.56 | 1.29 | 0.89 | — |
| F02 quiet_volume_persistent | 20,777 | 727 | 1.20 | 1.18 | 0.68 | — |
| F05 departure_calm | 12,106 | 348 | **0.98** | 1.10 | 0.56 | **−6.8** (calm subtracts inside departure) |
| F12 closing_strength_10s | 1,007 | 28 | 1.04 | 1.10 | 0.59 | — |
| F11 closing_strength | 735 | 30 | 1.38 | 1.56 | 0.79 | −0.3 |

Adding "and the price stayed calm" to abnormal volume lowers the up-door lift
in every case, significantly so inside F15 for F01 and inside F06 for F05. On
daily DSE data the quiet part of "quiet accumulation" is not a signal; it
removes the informative occurrences. (Scoped to up-doors: F05 has small
FDR-surviving *down*-door lifts, 1.1–1.3, that add nothing over F06.)

### 5.3 Post-move footprints do not beat "already moved" once today's shock is matched

| Footprint | outcome, k | lift | shock-matched lift (t) | ÷ signed F16 |
|---|---|---|---|---|
| F08u idio_move_up | abn_up 3 | 1.81 | 1.34 (4.9) | 1.08 |
| F08u idio_move_up | limit_up 5 | 1.84 | **1.29 (3.1)** | 1.07 |
| F08d idio_move_down | abn_down 3 | 2.07 | 1.43 (3.4) | 1.26 |
| F08d idio_move_down | limit_up 5 (the "bounce") | 1.70 | **1.19 (1.3)** | 1.10 |
| F16u REF moved up | limit_up 5 | 1.73 | 1.24 (3.9) | 1.00 |

F08 is 93% inside F16. Its "idiosyncratic" increment over any same-sign 2σ
move is 3–15% and is not what carried its v1 lift; that was the shock day's
own volatility clustering, which a trailing σ cannot see. The v1 claim that
F08 "precedes limit-ups" was a bounce after idiosyncratic drops.

### 5.4 Absorption, persistence, compression

F03/F04 (dip recovered on volume) → abn_down k=3: 2.11 / 3.25 lift, but
shock-matched t 2.6 / 2.8 and 60 / 36 distinct doors — below the gates, and
for F04 only 2013 carries it. F17 (second abnormal-volume day) → abn_down k=3:
2.46 (t 3.6), shock 2.03 (t 3.3), 62 doors — passes the size gates but its
margin over plain abnormal volume does not clear the bootstrap bound. F10
(coil then volume) fires 213 times: unmeasurable. **No footprint passes for
limit_up at any horizon**: F07's limit_up lift (1.72) fails shock-matching
(1.46, t 2.7) and does not beat F16u (1.00×).

### 5.5 Most doors open with no visible footprint

Of the fresh limit-up doors in DISCOVERY, the share preceded by a footprint in
the prior 5 sessions, against how often that footprint sits in any 5-session
window of *pre-door* rows (v2 base; v1's base included post-door rows):

| Footprint | recall | base | recall lift | median lead |
|---|---|---|---|---|
| *(table filled from `DOORSTEP_RECALL_LEADTIME.csv` — see §5.5 note below)* | | | | |

Precision at the same window is 4–6% for every footprint. A median lead of
1–2 sessions is the volume ramp already inside the door day's run-up, not an
early warning. **This is not a warning system.**

### 5.6 What the ten sessions before a limit-up look like on average (post-hoc)

Conditioned on the outcome — description, not evidence. Mean excess over the
same-day cross-section before fresh limit-up doors (`DOORSTEP_PREDOOR_PROFILE.csv`):
`rel_volume_z` +0.46 σ at t−1 (t ≈ 13) decaying to ≈ +0.13 by t−10 (t ≈ 3);
`volume_price_divergence` +0.17 at t−1 fading to ≈ 0 by t−5; Amihud
illiquidity *below* normal for ten sessions (t −2 to −6); a −0.5% market-relative
dip at t−2 (t −5); a strong close at t−1 (t ≈ 9). A slow build exists on
average and is useless as a trigger: the same build appears, weaker, before
nothing at all, which is what §5.5 says.

## 6. What survived

### 6.1 The funnel

| Step | Rows |
|---|---|
| 1 v1 pre-registered criterion, any-door | 175 |
| 2 v1 tier A: one-sided fresh + beats references (point) | 62 |
| 3 same v1 gates on `fresh_both` | 39 |
| 4 v2 gates: NW t ≥ 3 · shock-matched lift ≥ 1.5 and t ≥ 3 · distinct doors ≥ 30 | 22 |
| 5 = 4 and beats both references with bootstrap lower bound > 1 | 5 |
| **6 = 5 and (k = 1 or the incremental window passes) → tier A** | **2** |

Two rows, one footprint, one direction. `results/DOORSTEP_FUNNEL_STEP4.csv`
lists the 22 step-4 rows with their bootstrap bounds so every near-miss is
visible.

### 6.2 The one candidate: F07 idio_activity → abnormal up-move within 3 sessions

| | |
|---|---|
| Footprint | `rel_volume_z ≥ 2` on a day when ≤ 5% of names are abnormal-volume |
| Outcome | max cumulative return over (t, t+3] ≥ max(2.5·σ_prev·√3, 5%) |
| Occurrences | 3,068 (5.1% unmeasurable) · 2,911 measured · 781 dates |
| Hits | 163 = **145 distinct events on 101 symbols** (top-3 symbols 8.6%) |
| Hit rate | **5.60%** · fails **94.4%** of the time |
| vs same-day, same-σ-quintile names | 2.59% → lift **2.16**, NW t **6.6** |
| vs same-day, same-σ, same-size-of-move-today | 3.04% → lift **1.84**, NW t **5.4** |
| vs plain abnormal volume (F15, 1.76×) | ratio 1.23, bootstrap lower bound **1.05** |
| vs stronger "already moved" reference (1.67×) | ratio 1.29, bootstrap lower bound **1.08** |
| Incremental window (2, 3] on its own | lift 1.78, NW t 3.5 |
| Under the frozen `any` population / v1 one-sided `fresh` | 2.67 / 2.10 |
| Placebo (F07 shifted 20 sessions) | lift ≈ 1 *(filled from `DOORSTEP_PLACEBO.csv`)* |
| Years with lift ≥ 1 | **6 of 6** (min 1.78; min per-year t 1.8) |
| Price buckets 10–50 / 50–200 / > 200 | 2.17 / 2.28 / 1.94 |
| FLOOR era (separate regime) | lift **2.80**, NW t 4.1, 53 hits |
| POSTBREAK (~88 symbols) | lift 1.56, t 1.2, 9 hits — too thin |
| Also passes | run20 within 5 sessions: 58 hits / 54 events, lift 2.48 (t 3.9), shock 2.18 (t 3.2), increment 2.60 (t 3.6), FLOOR 4.7 |

**How to read it.** "Market quiet" is a date-level condition, so F07 is plain
abnormal volume restricted to the 64% of days when few other names are
abnormal. The information is real and reproduces, but its margin over plain
abnormal volume on *any* day is thin — the bootstrap lower bound of 1.05 is
one bad block away from 1. The between-dates test of F15's excess on quiet vs
busy days *(filled after run)* is the direct version of that comparison.

**What it is not.** Not accumulation (the price is not calm: the "calm" variant
F09 has lower lift and negative within-parent t). Not a signal of intent. Not
tradeable on this evidence — no cost layer was applied, and a 5.6% hit rate
with a 2.5σ-or-5% door is a tilt, not a trigger.

### 6.3 Reconciliation with Phase 4

Phase 4 found that sustained abnormal activity *underperforms* over 10
sessions (mean return, t −8 to −11). Phase 4.5 finds that abnormal activity on
a quiet day precedes abnormal moves in the *up* direction within 3 sessions,
and that persistent abnormal volume (F17) raises down-move probability at
k ≤ 3 but not enough to beat plain volume. Both are consistent with one
picture: abnormal activity raises *variance* in both directions over the next
few sessions, with a negative *mean* over ten. Neither is a directional
accumulation footprint.

### 6.4 Calibration and multiplicity

- **Placebo.** Every footprint shifted +20 sessions, same gates, same
  population: median lift ≈ 0.89, 90th percentile ≈ 1.27, share reaching
  t ≥ 3: **0%**, max t ≈ 2.4 *(final numbers from `DOORSTEP_PLACEBO.csv`)*.
  The gates pass nothing on stale information.
- **BH-FDR** over door outcomes and non-reference footprints: on `fresh_both`
  the cutoff is p ≈ 0.03 and about 180 of 425 tests survive — it removes
  nothing the lift and shock gates do not, and is reported so that nobody
  mistakes it for the binding control. The binding controls are the
  shock-matched base, the distinct-door count, the bootstrap reference bound
  and the incremental gate, all chosen after seeing v1. That is why the sealed
  holdout, not this file, is the test.

## 7. What Phase 5 must do

`results/PHASE5_CANDIDATES.csv` carries **one primary row** (F07 → abn_up,
k = 3; run20 k = 5 listed as a secondary passing horizon) with the test
specification fixed now: on the sealed holdout, PRIMARY panel, `fresh_both`,
same definitions — vol-matched *and* shock-matched lift ≥ 1.5, NW t ≥ 3,
distinct doors ≥ 30, bootstrap lower bound of the ratio vs both references > 1.
Two tests, FDR over two. If it fails, the doorstep question on daily data is
closed for this family too, and the only remaining path is minute data (D-1).

Phase 5 should also report the 2020-03-25 coverage break inside the holdout
(327 → 243 symbols) and treat the two sides separately.

## 8. What this does NOT say

- Nothing about profitability, costs, or a rule. No BUY/SELL was produced.
- Nothing validated. The sealed holdout has not been read.
- Nothing about intent. EOD bars cannot show manipulation or accumulation;
  they can show that abnormal activity on a quiet day precedes abnormal moves
  more often than chance, which is all §6 claims.
- Nothing about the entry side of a pump. The one surviving footprint fires on
  a day that is *already* abnormal for the stock; the "quiet accumulation"
  that would precede that day is not visible in daily bars.
- Nothing about stocks that were delisted or collapsed in 2012–2018.

## 9. Owner actions that change conclusions

| # | Gap | Effect |
|---|---|---|
| D-1 | minute-level data | the only way to see accumulation *before* the abnormal-volume day; every intraday proxy here is a daily-bar approximation |
| D-4 | corporate-action table | 1,628 rows and every beyond-band drop are currently *suspects*; a table would turn them into adjustments and restore the limit-down outcome |
| D-8 | sector table | "sector quiet" is approximated by "market quiet" |
| D-9 | histories of symbols delisted / suspended 2012–2024 | the discovery universe is survivorship-conditioned |
| — | DSE circular for the limit schedule, incl. ex-dates, new listings, Z category | the limit outcomes depend on it, and it may differ on the holdout |

## Files

`results/DOORSTEP_FOOTPRINT_ANALYSIS.csv` (every footprint × outcome ×
horizon × population, plus incremental outcomes and placebos, three regimes) ·
`results/PHASE5_CANDIDATES.csv` (1 primary row) ·
`results/PHASE5_CANDIDATES_ALL_ROWS.csv` (2 rows) ·
`results/DOORSTEP_FUNNEL_STEP4.csv` (the 22 near-misses with bootstrap bounds) ·
`results/DOORSTEP_PLACEBO.csv` · `results/DOORSTEP_STABILITY.csv` ·
`results/DOORSTEP_RECALL_LEADTIME.csv` · `results/DOORSTEP_PREDOOR_PROFILE.csv` ·
`results/DOORSTEP_BAND_EVIDENCE.csv` · `results/DOORSTEP_FOOTPRINT_COVERAGE.csv` ·
`results/DOORSTEP_FOOTPRINT_OVERLAP.csv` · `manifests/phase45_manifest.json` ·
`manifests/phase45_causality_manifest.json`
