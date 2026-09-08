<!--
DISCOVERY REPORT — append-only, like PROJECT_MAP.md and ROADMAP.md.
Discovery output, NOT a result. Nothing here has been through Stage 11 falsification,
nothing here is tradeable, and no sealed data was opened to produce it.
-->

# Edge discovery — what the touch knows, and what it does not

**2026-09-08.** 54 candidates × 3 frame horizons on the 2026-09-06 calibration
session: 4,257 unified states, 14 listings, one session.
**39 PROMISING · 12 WEAK · 2 KILLED · 1 INSUFFICIENT_SAMPLE** at the primary horizon.

> **One session is one block.** Nothing here is validated. The micro DEV set is
> empty (`micro/sessions/INDEX.json`: 0 accepted sessions), so the preregistered
> block bootstrap over sessions cannot run at all. What follows is measurement on
> a session the prereg already marks as spent — which is why it may be measured
> on, and why it establishes nothing.

---

## 1. The reference reproduces

θ = 0.20 is imported from `micro/MICRO_PREREG.json`, never fitted. The matched
control is `tower/experiment.py`'s own function, imported rather than
reimplemented: for each signal row, one non-signal row with the same
**(symbol, time-of-day bucket, spread bucket)**.

### E1 — L1 bid imbalance above θ

| horizon | n signal | episodes | symbols | P(up) | control | **matched lift** | handoff reference |
|---|---|---|---|---|---|---|---|
| h2 (≈87 s) | 1,658 | 312 | 14 | 0.2244 | 0.1135 | **+11.09 ± 0.78 pp** | ~+12.6 pp |
| **h4 (≈180 s)** | 1,634 | 312 | 14 | 0.3231 | 0.1654 | **+15.78 ± 0.94 pp** | ~+16.5 pp |
| h8 (≈390 s) | 1,648 | 312 | 14 | 0.3574 | 0.2286 | **+12.88 ± 1.01 pp** | ~+11.9 pp |

Median frame cadence **43.67 s** — the documented 43.7 s. All three horizons land
within ~1.5 pp of the reference and reproduce its shape: h4 is the peak.
**E1 is confirmed.**

### The probability edge and the move are not the same horizon

E1's path, from `REFERENCE_SIGNALS.csv`:

| horizon | matched lift | mean fwd ticks | MFE | MAE | MAE ÷ mean |
|---|---|---|---|---|---|
| **h2** | +11.09 pp | **+0.102** | +0.212 | **−0.059** | **0.58×** |
| h4 | **+15.78 pp** | +0.135 | +0.498 | −0.287 | 2.1× |
| h8 | +12.88 pp | **−0.013** | +0.837 | −0.717 | — |

**At h8 the probability of an up move is 12.9 pp above its control while the mean
move is slightly negative** — the up moves are more frequent and the down moves
are bigger. A direction edge that does not survive into expectation is not an
edge, and only reporting P(up) would have hidden it completely.

**h4 has the largest probability lift; h2 has by far the best path.** At h2 the
adverse excursion is 0.58× the expected gain; at h4 it is 2.1×. If any of this
ever becomes position logic, the horizon that maximizes the probability lift is
not the horizon that survives the path — and that choice belongs to **Stage 10.5**,
not here.

### A correction to how a matched lift must be reported

The matched control is *drawn* with replacement, so a single-seed matched lift is
one sample of a random quantity rather than the quantity. Across 20 seeds:

| candidate | mean | sd | range over 20 seeds |
|---|---|---|---|
| E1 | +15.68 pp | 0.94 | +13.76 … +17.28 |
| `CTX_xs_rank_top` | +21.74 pp | 1.75 | +17.82 … +24.19 |
| `GEO_touch_dominant_bid` | +15.95 pp | 1.45 | +13.77 … +18.77 |

**A one-draw number can sit 1.5 pp from its own mean — larger than most of the
differences between candidates here.** Every matched lift in these artifacts is
now the mean over 20 independent draws with its standard deviation beside it.

This is not a cosmetic change. The first version of this run reported E1 at
+17.28 pp (the top of its range) and, worse, reported **E1 at −2.10 pp in the
opening half hour** — a finding that does not survive averaging (§5). One draw
was enough to invent a phase effect that is not there.

### Touch locality — confirmed, and sharper than expected

The same θ = 0.20 rule applied at different book depths:

| score | h2 | h4 | h8 | mean | handoff reference |
|---|---|---|---|---|---|
| **`imb_l1` (E1)** | +11.09 | +15.78 | +12.88 | **+13.25 pp** | +15.16 pp |
| `imb_top3` | +3.79 | +8.65 | +8.12 | +6.85 pp | — |
| `imb_top5` | +4.98 | +7.65 | +11.22 | **+7.95 pp** | +7.97 pp |
| `imb_weighted` | +5.53 | +6.18 | +9.89 | +7.20 pp | +6.84 pp |
| `imb_all` | −4.03 | −4.68 | +0.67 | **−2.68 pp** | — |

`imb_top5` reproduces to within **0.02 pp** and `imb_weighted` to within 0.4 pp.

The gradient is the finding: **adding levels 2–5 roughly halves the lift, and
adding the whole book turns it negative.** Two independent results say the same:

* **`GEO_deep_only_bid`** — top-5 bid pressure above θ while the touch is *not* —
  scores **−12.21 ± 1.49 pp** on P(up). Deep bid pressure without touch pressure
  predicts a *fall*. It is one of only two KILLED candidates.
* **`DYN_G`** — top-5 positive while L1 is negative, the direct disagreement —
  scores **+15.99 pp on P(down)**. When touch and depth disagree, the touch wins.

---

## 2. The λ family

TLPI(λ) = Σq·exp(−λ·|p−mid|/tick) imbalance. It contains both existing baselines,
and both identities hold on the real states: **TLPI(0) ≡ `imb_all`** (max absolute
difference 0.0 over 3,758 states) and **TLPI(8) → E1** (Pearson r = 0.9996).

| λ | h2 | h4 | h8 | AUC (h4) | MAE (ticks) |
|---|---|---|---|---|---|
| 0.00 | −4.03 ± 1.06 | −4.68 ± 1.18 | +0.67 ± 1.08 | 0.5430 | −0.511 |
| 0.25 | +4.27 ± 0.96 | +4.92 ± 0.77 | +10.00 ± 0.60 | 0.6042 | −0.442 |
| 0.50 | +7.54 ± 0.84 | +11.45 ± 0.79 | +15.07 ± 0.85 | 0.6412 | −0.374 |
| **1.00** | +10.29 ± 0.63 | +15.28 ± 0.56 | **+15.59 ± 0.58** | 0.6752 | −0.310 |
| 2.00 | +10.18 ± 0.47 | +14.18 ± 0.75 | +11.91 ± 0.77 | 0.6931 | −0.314 |
| 4.00 | +10.01 ± 0.66 | +14.10 ± 0.96 | +12.19 ± 1.13 | **0.6942** | −0.297 |
| 8.00 | +10.79 ± 0.54 | +15.43 ± 0.66 | +12.66 ± 0.65 | 0.6927 | **−0.295** |

**Best region: λ ∈ [1, 8].** The curve is *flat* across it — matched lift
14.1–15.4 pp at h4, AUC 0.675–0.694 — so the parameter is not finely tuned, which
is a robustness point rather than a weakness. Only λ = 0 (the whole-book
imbalance) is clearly bad.

**The adverse excursion falls monotonically with λ**: MAE −0.511 ticks at λ = 0
against **−0.295 at λ = 8**, a 42 % reduction. Localization does not only improve
the direction — it nearly halves how far the position goes against you first.
That is the one result here that speaks directly to position risk.

### Where distance beats level rank, and where it provably cannot

On a **mirror-symmetric ladder** the decay weight cancels level by level and TLPI
is algebraically a rank-decayed imbalance. Distance can only contribute on a
**gapped** book, and 40 % of frames are gapped. Two independent metrics agree:

**By AUC** (control C7, symbol blocks):

| λ = 1 | n | TLPI (distance) | twin (rank) | margin | 95 % CI |
|---|---|---|---|---|---|
| symmetric | 1,179 | 0.6587 | 0.6582 | +0.0005 | [+0.0000, +0.0022] |
| **gapped** | 717 | 0.6810 | 0.6330 | **+0.0479** | **[+0.0262, +0.0806]** |

**By matched lift** (`REGIME_RESULTS.csv`, h4):

| book shape | E1 | `TLPI_l1` | difference |
|---|---|---|---|
| symmetric | +14.66 | +14.47 | −0.19 — indistinguishable, as the algebra requires |
| **gapped** | +13.80 | **+18.37** | **+4.57 in TLPI's favour** |

**The contribution appears exactly where the algebra permits it and vanishes
exactly where it does not, on both metrics.** That is a mechanism, not a score.

---

## 3. The incremental gate — nothing beats E1 with an established margin

Two comparisons, and they must both be read.

**Pooled**, only two candidates exceed E1 by more than one combined standard
deviation:

| candidate | lift | E1 | margin | combined sd | n | symbols |
|---|---|---|---|---|---|---|
| **`CTX_xs_rank_top`** | +21.75 ± 1.74 | +15.78 ± 0.94 | **+5.97** | 1.98 (3.0×) | 478 | 14 |
| `CTX_sector_permission` | +18.14 ± 1.42 | +15.78 ± 0.94 | +2.37 | 1.71 (1.4×) | 579 | **8** |

Everything else — `GEO_touch_dominant_bid` +16.18, `GEO_broad_bid` +16.22,
`X_E1_and_TLPI1` +16.18, `TLPI_l8` +15.43, `TLPI_l1` +15.28, `CTX_share_resid`
+15.14 — sits inside a standard deviation of E1 and is **not distinguishable from
it** on this sample.

**Across symbols**, with a symbol-block bootstrap on the matched-lift difference,
nothing survives:

| candidate | margin vs E1 | 95 % CI | established |
|---|---|---|---|
| `CTX_xs_rank_top` | +7.35 | [−0.51, +15.14] | **no** — and by only 0.51 |
| `CTX_sector_permission` | +3.19 | [−2.18, +8.61] | no |
| `TLPI_l8` | −0.23 | [−1.59, +1.45] | no |
| **`DYN_C_level_vel`** | **−5.69** | **[−10.62, −0.44]** | **worse, established** |
| **`VETO_E1_no_risk`** | **−7.17** | **[−7.56, −0.59]** | **worse, established** |

Two results are established, and both are negative:

1. **Requiring positive velocity alongside E1 makes it measurably worse.**
2. **Vetoing E1 on the risk states makes it measurably worse.**

The handoff's own lesson, confirmed rather than assumed: complexity did not
improve prediction here, and two specific ways of adding it did measurable harm.

`CTX_sector_permission` covers **8 symbols, not 14** — only Textile (5) and
Pharmaceuticals (3) have enough members for a real sector tier — so its lift is
not comparable to a 14-symbol candidate's and it is not a rival to E1.

**`CTX_xs_rank_top` is the one candidate with a real claim**: 3.0 combined sd
above E1 pooled, on all 14 symbols, with the best MAE in the top group
(−0.184 against E1's −0.287). Its symbol-block lower bound misses zero by 0.51 pp.
It is the first thing to re-test when DEV sessions exist.

---

## 3b. The dynamics family — every elaboration of the level is worse than the level

Families A…J at h4, ranked. This is the sharpest statement of the whole run.

| id | logic | n | episodes | matched lift | status |
|---|---|---|---|---|---|
| **A** | **P > θ — the plain level (this is E1)** | 1,634 | 312 | **+15.78 ± 0.94** | PROMISING |
| H | P above θ for ≥ 3 consecutive frames | 1,091 | 208 | +12.70 ± 1.35 | PROMISING |
| J | P crosses from below −θ to above +θ | 135 | 145 | +12.23 ± 4.36 | PROMISING (thin) |
| C | P > θ **and** V > 0 | 442 | 457 | +10.05 ± 2.30 | PROMISING |
| D | P > θ **and** V > 0 **and** A > 0 | 418 | 444 | +9.98 ± 1.87 | PROMISING |
| G | top5 > θ while L1 < 0 → scored on P(down) | 636 | 161 | +9.34 ± 1.46 | PROMISING |
| B | P crosses up through θ | 231 | 249 | +7.60 ± 2.02 | PROMISING |
| E | L1 > θ while \|top5\| ≤ θ | 345 | 93 | +4.83 ± 1.19 | WEAK |
| I | P above θ but V < 0 and A < 0 (exhaustion) | 195 | 198 | +1.10 ± 2.81 | WEAK |
| F | L1 > θ and top5 < 0 | 157 | 55 | −3.48 ± 3.14 | WEAK |

**The plain level beats all nine of its elaborations.** Adding velocity costs
5.7 pp; adding acceleration on top costs another 0.1 pp; requiring a crossing
costs 8.2 pp; requiring three-frame persistence costs 3.1 pp. The conceptual
sequence the handoff proposed — *touch pressure → persistence → velocity →
acceleration* — **does not survive contact with this session**: every step after
the first subtracts.

Two of these are informative rather than merely negative. **I (exhaustion)** at
+1.10 pp says pressure that is fading is worth almost nothing, which is the right
shape. **G** at +9.34 pp on P(down) says that when the deep book is bullish and
the touch is bearish, the touch wins — touch locality again, from the other side.

---

## 4. Risk states: none of them predicts a fall

Scored on P(down), the direction they claim:

| risk state | n | episodes | matched lift on P(down) | status |
|---|---|---|---|---|
| `RISK_depletion` | 234 | 229 | −1.44 ± 2.0 pp | WEAK |
| `RISK_any` | 581 | 547 | −3.70 ± 1.76 pp | WEAK |
| `RISK_bid_retreat` | 315 | 328 | −4.74 ± 2.38 pp | WEAK |
| `RISK_spread_widening` | 141 | 151 | −6.04 ± 3.14 pp | KILLED |
| `RISK_hard` | 97 | 104 | −8.16 ± 2.99 pp | **INSUFFICIENT_SAMPLE** |

**Liquidity depletion, bid retreat and spread widening do not predict a fall on
this session** — every one runs the wrong way. Combined with the established harm
the veto does to E1, the risk-veto family as defined fails here.

That is not the same as saying risk context is worthless. What is measured is
*direction*, and a veto's real job is the cost of being wrong — MAE, fillability,
the path — which is **Stage 10.5** and does not exist yet. `RISK_hard` stays
INSUFFICIENT_SAMPLE on 97 rows and is **not** converted to KILLED; `adverse_retreat`
from the earlier Tower work stays INSUFFICIENT_SAMPLE and is untouched by this run.

---

## 4b. Market → sector → share: relative strength, not permission

The three tiers, h4. The two market-conditioned candidates are the same signal
with opposite conditions, and they answer the Stage 8 question directly:

| candidate | logic | n | symbols | matched lift |
|---|---|---|---|---|
| **`CTX_xs_rank_top`** | top decile of cross-sectional pressure at this instant | 478 | 14 | **+21.75 ± 1.74** |
| `CTX_sector_permission` | E1 and sector pressure > 0 | 579 | **8** | +18.14 ± 1.42 |
| **`CTX_market_weak_share_strong`** | **E1 while the market tier is negative** | 527 | 14 | **+16.15 ± 1.21** |
| `CTX_share_resid` | share pressure minus market > θ | 1,582 | 14 | +15.14 ± 1.01 |
| **`CTX_market_permission`** | **E1 and market pressure > 0** | 1,107 | 14 | **+8.05 ± 1.25** |
| — | E1 alone, for comparison | 1,634 | 14 | +15.78 ± 0.94 |

**Requiring the market to agree costs 7.7 pp. Requiring it to *disagree* costs
nothing.** `CTX_market_permission` at +8.05 is far below E1 alone; the same signal
conditioned on a *falling* market is +16.15, and the pure relative-rank candidate
is the best in the whole run at +21.75.

Read plainly: on this session it is **relative** strength that carries the
information, not aligned strength. A share pushing up while the market is not is
a better state than a share pushing up with the market — which is the Stage 8
hypothesis ("market weak but share strong") measured rather than narrated. It
also explains why `CTX_xs_rank_top` leads: it is relative strength in its purest
form, with no threshold on the share's own pressure at all.

**Cross-timescale combinations subtract, without exception except one.** Only
`X_E1_and_TLPI1` (+16.18 ± 0.60) holds level with E1; every added condition costs:
touch-dominant + no veto +13.50, persistence + no veto +10.28, TLPI + velocity
+8.47, TLPI + velocity + acceleration +8.02, E1 + market permission + no veto
+6.23.

---

## 5. Session phase — E1 is weak early, TLPI(1) is not

Matched lift at h4, causal phases fixed before any outcome was read:

| candidate | OPEN_EARLY (390 rows) | MID (1,185) | LATE (1,958) |
|---|---|---|---|
| **E1** | **+4.04** | +17.34 | +17.27 |
| `GEO_touch_dominant_bid` | −0.89 | +17.99 | +19.07 |
| `CTX_market_permission` | −3.74 | +13.61 | +9.29 |
| `DYN_C_level_vel` | −1.81 | +13.09 | +9.30 |
| **`TLPI_l1`** | **+19.07** | +11.09 | +16.25 |
| `TLPI_l2` | +12.91 | +14.82 | +13.75 |

**E1 is roughly a quarter as strong in the opening half hour as it is later**
(+4.04 against +17.3), and `TLPI_l1` is the reverse — its best phase, at +19.07,
is the one where E1 is weakest. This is the opposite of the "morning edge" the
handoff warned against assuming: it is a morning *weakness*, and the λ family may
be covering exactly the gap E1 leaves.

OPEN_EARLY holds 390 rows. This is a lead, not a finding — and the single-draw
version of this same table said E1 was *negative* early, which averaging removed.

---

## 6. Quality splits — cleaner evidence does not help E1

| candidate | ALL_VALID (2,622 rows) | HIGH_QUALITY (911 rows) | change |
|---|---|---|---|
| E1 | +16.98 | **+10.61** | −6.4 |
| `VETO_E1_no_risk` | +14.62 | **+2.97** | −11.7 |
| `TLPI_l2` | +14.20 | +11.67 | −2.5 |
| `DYN_H_persistent` | +13.66 | +10.72 | −2.9 |
| **`TLPI_l1`** | +15.90 | **+14.88** | −1.0 |
| **`GEO_touch_dominant_bid`** | +16.47 | **+15.70** | −0.8 |
| `CTX_market_permission` | +7.45 | +7.35 | −0.1 |

The question the handoff asks — *does signal strength improve when evidence
quality improves?* — answers **no**. E1 loses 6.4 pp on the clean subset and the
veto all but disappears. Some of that is the 65 % row loss, but the candidates
separate sharply: `TLPI_l1`, `GEO_touch_dominant_bid` and `CTX_market_permission`
hold within 1 pp while E1 and the veto do not. **How much a candidate depends on
low-quality frames is itself a discriminator**, and on that measure the
touch-geometry and λ candidates are the robust ones.

---

## 7. Daily context — the three EOD candidates

From `results/DOORSTEP_FOOTPRINT_ANALYSIS.csv`, the committed Phase 4.5 artifact,
DISCOVERY regime, `fresh_both`, k = 3.

**F07 (idiosyncratic activity) — exact.**

| | reproduced | reference |
|---|---|---|
| hit rate | **5.5995 %** | 5.60 % |
| vol-matched base | **2.5924 %** | 2.59 % |
| lift | **2.1599×** | ~2.16× |
| distinct doors | **145** | 145 |
| symbols | 101 | 101 |

Within-parent t **0.708** — the "quiet market" condition's own margin over plain
abnormal volume is still not established, exactly as `RESEARCH_STATUS.md` records.
**DAILY POSITIVE CONTEXT**, not a validated edge.

**F17 (persistent abnormal volume) — exact.**

| | reproduced | reference |
|---|---|---|
| hit rate (abn_down) | **1.8754 %** | 1.88 % |
| vol-matched base | **0.7626 %** | 0.76 % |
| vol-matched lift | **2.4591×** | ~2.46× |
| shock-matched lift | **2.0290×** | ~2.03× |
| distinct doors | **62** | 62 |

Within-parent t 1.32 — the incremental gate it failed in Phase 4.5, unchanged.
**DOWNSIDE RISK CONTEXT.**

**Persistent DEPARTURE — direction confirmed, magnitude about half.**

Rebuilt with `state_engine/run_states.py`'s own rule on the DEV panel (586,657
rows, sealed holdout absent), 10-session forward excess against a same-date,
same-trailing-volatility-quintile control:

| definition | events | dates | symbols | fwd10 | control | **excess** | t (raw) | t (date-clustered) |
|---|---|---|---|---|---|---|---|---|
| DEPARTURE or EXTREME | 53,562 | 2,211 | 392 | +0.41 % | +0.66 % | **−0.25 %** | −4.3 | −5.5 |
| persistent ≥ 2 | 21,188 | 2,134 | 392 | +0.16 % | +0.71 % | **−0.55 %** | −7.0 | −6.4 |
| persistent ≥ 3 | 11,913 | 1,952 | 388 | +0.08 % | +0.67 % | −0.59 % | −5.8 | −5.0 |
| persistent ≥ 5 | 5,532 | 1,430 | 361 | −0.12 % | +0.66 % | **−0.78 %** | −6.2 | −2.9 |

The handoff cites −1.3 % to −1.6 % with t of −8 to −10. **Reproduced values are
about half that magnitude and weaker in t**, reported as measured per the
handoff's own instruction. Two visible reasons, neither a defect: this is the
**DEV panel only** (the sealed 2019-01-01 → 2022-07-27 window is absent), and the
date-clustered t is the honest one because overlapping 10-session windows make the
raw t too generous. The *direction* is robust and deepens monotonically with
persistence, which is what a real state effect looks like.
**AVOID / DOWNSIDE CONTEXT**, not short logic.

---

## 8. Response curves

Decile curves at h4 (`RESPONSE_CURVES.csv`). None is strictly monotone; how close
each comes is the comparison.

| score | P(up), decile 1 → 10 | ρ(P up) | ρ(mean ticks) | ρ(MAE) | inversions |
|---|---|---|---|---|---|
| **`tlpi_1`** | 0.102 → 0.393 | **0.988** | 0.964 | 0.903 | **1** |
| `share_resid` | 0.153 → 0.480 | 0.976 | **0.988** | 0.952 | 2 |
| `imb_l1` | 0.130 → 0.458 | 0.948 | 0.879 | **0.952** | 2 |
| `tlpi_2` | 0.119 → 0.438 | 0.924 | 0.903 | 0.867 | 2 |
| `imb_top5` | 0.150 → 0.226, peaks at decile 8 then *falls* | **0.717** | 0.733 | 0.588 | 3 |

(ρ = Spearman rank correlation across the ten deciles.)

**`tlpi_1` has the cleanest response curve of any candidate** — ρ = 0.988 with one
inversion in nine steps — even though E1 wins on the threshold rule. Monotonicity
is criterion 8 in the ranking priority and it points at λ ≈ 1 rather than at a cutoff.

**ρ(MAE) is strongly positive for every score.** A higher reading does not only
raise P(up); it also *reduces the adverse excursion* — monotonically, across
deciles. The score predicts the path, not only the direction, which is the one
thing here that a position-sizing rule could eventually use.

`imb_top5` is the worst curve on all three measures (ρ = 0.717 / 0.733 / 0.588)
and turns *down* in its top decile: touch locality again, in the shape of the
curve rather than in a single number. The strongest deep-book readings are the
least informative ones.

---

## 9. What was not observable

* **The micro DEV set is empty.** 0 accepted sessions, so no DEV/VAL ranking and
  no block bootstrap over sessions. Ranking criterion 1 (DEV/VAL agreement) is
  recorded as UNAVAILABLE on every row rather than skipped.
* **Six of fourteen symbols are the only member of their sector here** (Bank,
  Financial Institutions, Food & Allied, Fuel & Power, Miscellaneous,
  Telecommunication). Their sector tier would be their own pressure renamed, so it
  is NOT_OBSERVABLE. Only Textile (5) and Pharmaceuticals (3) support a real one.
* **Queue position, order counts, hidden liquidity and participant identity**
  remain NOT_OBSERVABLE from every source reached. Nothing here claims them.
* **`ref_status` is OBSERVED on all 4,257 states**, so removing the future-circuit
  fallback changed no number in this run — it removed a latent defect, not a live
  one.

## 10. Killed, weak, and merely thin

**KILLED** (2): `GEO_deep_only_bid` (−12.21 ± 1.49 pp) and `RISK_spread_widening`
(−6.04 ± 3.14 pp, and its sd is large enough that this is a marginal call).

**Established harm**: `VETO_E1_no_risk` and `DYN_C_level_vel`, both worse than E1
with a symbol-block interval clear of zero.

**WEAK, not killed** (12) — including `REF_all` / `TLPI_l0` at −4.68 ± 1.18 pp.
The single-draw run called the whole-book imbalance KILLED at −5.50; averaging
moves it to −4.68 and inside the WEAK band. It is bad, but it is not decisively
killed and is not recorded as such.

**INSUFFICIENT_SAMPLE — not killed**: `RISK_hard` (97 rows, 104 episodes), and at
the session level *every* candidate, because one session is one block.

## 11. The predictions this leaves

Three specific, checkable claims for the DEV sessions when they exist:

1. **`CTX_xs_rank_top` beats E1.** Pooled margin +5.97 pp at 3.0 combined sd;
   symbol-block CI lower bound −0.51. Either it clears on more sessions or it
   does not — no reinterpretation available.
2. **TLPI(λ≈1) beats a rank-decayed imbalance on gapped books and not on
   symmetric ones**, with the margin scaling in `ladder_mismatch_ticks`. Already
   confirmed twice on this session, by AUC and by matched lift.
3. **E1 stays weak in the opening 30 minutes while TLPI(1) does not.** If E1 is
   strong early on other sessions, this was 390 rows of noise.

Nothing above is tradeable. E1's mean forward move at h4 is **+0.13 ticks against
an MAE of −0.29 ticks** — the adverse excursion is more than twice the expected
gain. Path cost and fillability are **Stage 10.5**, and until that exists none of
this is an edge. It is structure.
