# `D_shallow_pullback` — adversarial validation

**VERDICT: KILLED.** It is not an anti-signal, not a veto, and not a trap state.
It is a dead-share detector, and its apparent power came from three confounds and
one counting error.

Definition tested, verbatim from the discovery run:
`downside_excursion_5 >= -0.03` **and** `dist_from_20d_high >= -0.05` — the worst
low of the last five sessions within 3 % of today's close, with today's close
within 5 % of the 20-day high.

---

## The single number that ends it

| | rows | hit +10 % / 20d | matched control | **lift** |
|---|---:|---:|---:|---:|
| **1** as originally measured | 96,855 | 14.67 % | 27.28 % | **0.538** |
| **7a** + volatility matched | 96,855 | 14.67 % | 22.93 % | 0.640 |
| **7c** + full matching | 96,855 | 14.67 % | 23.68 % | 0.620 |
| **2b** **independent episodes, full matching** | **7,231** | **27.66 %** | **26.48 %** | **1.045** |

De-overlapping the sample and matching honestly does not weaken the effect. It
**reverses** it. The final number sits marginally *above* its control.

---

## 2 + 3. The counting error

A signal run lasts a median of **13.4 consecutive sessions**, and every day of it
was counted as an independent observation sharing almost the same 20-day forward
window.

| | |
|---|---:|
| signal rows | 96,875 |
| **independent episodes** | **7,232** |
| overlap inflation | **13.4x** |
| episode symbols / years / dates | 377 / 12 / 1,778 |

Taking only the first day of each episode moves the hit rate from **14.67 % to
27.66 %** — it nearly doubles. The original measurement was dominated by days 5
through 30 of long dead runs, so it was largely measuring *"this share has been
motionless for two weeks"*, which predicts that it will stay motionless. That is
circular, not predictive.

## 10. What it actually predicts: nothing at all

| metric (median) | signal | rest |
|---|---:|---:|
| forward return 5d | **0.000000** | −0.41 % |
| forward return 20d | **0.000000** | −1.00 % |
| forward return 30d | **0.000000** | −1.28 % |
| MFE 20d | +1.67 % | +6.98 % |
| **MAE 20d** | **−0.91 %** | **−6.09 %** |
| days to +10 % | 14 | 10 |
| vol20 | 0.59 % | 2.29 % |
| adv20 turnover | 1.35 mn | 6.63 mn |

Answering the four hypotheses put to it explicitly:

* **Lower probability of +10 % / +20 % expansion?** Yes — but see the confounds.
* **Higher downside / MAE?** **No, the opposite.** MAE is −0.91 % against −6.09 %.
  A veto state that protects you from a 6 % drawdown by putting you in something
  that cannot fall is not a veto, it is a money-market fund.
* **Delayed recovery?** Weakly — 14 days to +10 % against 10 — consistent with
  frozen, not with distribution.
* **Mean reversion after a completed rally?** **No.** 70 % of signal rows have a
  prior 10-day return at or below zero, and after a genuine rally
  (prior 10d +10…30 %) the lift is **0.947 on n=316** — no effect.

The median signal share does not fall. It does not move. All three forward
returns are exactly zero to fourteen decimal places.

## 5. Where the effect lives — three confounds

**Volatility** (76 % of signal rows sit in the lowest quintile):

| vol quintile | n | lift |
|---|---:|---:|
| V0 lowest | 73,387 | **0.600** |
| V1 | 16,403 | 0.877 |
| V2 | 5,241 | **1.026** |
| V3 | 1,544 | 0.976 |
| V4 highest | 280 | 0.786 |

By the third quintile the effect is **gone**. Quintile matching could not remove
it because the signal selects the bottom of the bottom quintile.

**Liquidity** (44 % of signal rows in the lowest quintile): L0 lift **0.371**,
every other bucket 0.77–0.85.

**Regime**: 51.9 % of signal rows fall in the 2022-07-28 … 2024-01-31 hard-floor
era against 14.1 % of the rest; 2023 alone supplies 38 %. FLOOR lift 0.744,
PRE_FLOOR 0.885. A share pinned at a regulatory floor cannot reach +10 %.

## 6. Not a corporate-action artifact

Excluding ±5 sessions around every suspected ex-date (overnight gap ≥ 20 %;
143 rows) changes the lift from 0.620 to **0.622**. This one confound is cleanly
ruled out.

## 4. Leave-one-out: stable, and that is not a defence

LOYO lift range **0.579 … 0.736** over 12 years; LOSO over the top 20 symbols
**0.606 … 0.624**. The effect is highly stable — because a structural confound is
stable. Stability distinguishes a real regularity from noise; it does not
distinguish a real regularity from a mechanical one.

## 8 + 9. Response surface — monotone in deadness, not in geometry

Lift at +10 %/20d under full matching, rows = distance from the 20-day high,
columns = allowed 5-day downside:

| dh ≥ | −0.10 | −0.07 | −0.05 | −0.03 | −0.02 | −0.01 |
|---|---|---|---|---|---|---|
| −0.20 | 0.668 | 0.768 | 0.789 | 0.797 | 0.803 | 0.888 |
| −0.15 | 0.708 | 0.735 | 0.740 | 0.757 | 0.761 | 0.847 |
| −0.10 | 0.626 | 0.739 | 0.743 | 0.766 | 0.761 | 0.884 |
| −0.05 | 0.530 | 0.612 | 0.565 | **0.620** | 0.647 | 1.045 |
| −0.02 | 0.394 | 0.418 | 0.332 | 0.446 | 0.645 | 3.471 |
| 0.00 | 0.952 | 0.992 | 0.960 | 1.060 | 1.381 | 4.068 |

It is monotone, and that is the problem: the lift falls as the two thresholds
tighten, reaching **0.332** in the corner that means *"within 2 % of the 20-day
high and its five-day low within 5 % of the close"* — the deadest possible cell.
The surface is monotone in **how little the share moves**, not in any buildup
geometry. The bottom row (`dh ≥ 0`, a share at or above its 20-day high) flips
above 1.0, which is the breakout case and a different state entirely.

## 11. Multiple testing

Benjamini–Hochberg at q = 0.10 over the 36-cell surface rejects **34 of 36** under
both matching sets. The effect is not a multiple-comparisons accident — it is
real and it is mechanical. Correcting for multiplicity was never going to save or
sink this one; the confounds did.

---

## Two defects found while doing this

**The reported `t_nw_date` answers a different question from `lift`.** It compares
the signal's per-date mean against the whole **population** on those dates, while
`lift` compares against **matched controls**. That is why the episode result shows
lift 1.045 with t −4.54 simultaneously: episodes really do underperform the
average share that day (they are low-volatility and illiquid), and do **not**
underperform comparable shares. Any future reading of these tables must not treat
the t as corroborating the lift. Recorded rather than silently reconciled.

**`range_pos_20d` is numerically unsafe.** It divides by `hi20 - lo20 + 1e-12`,
and for a frozen share the 20-day high equals the 20-day low, so the mean over
signal rows came out as 5.7e8. The median (0.167) is unaffected and no result in
this run depends on it, but it needs a guard before anything else uses it.

---

## Decision

Per the stated rule: the effect **collapsed**, so it is KILLED, and the
complement-geometry test is not run — there is no validated veto whose opposite
would be worth testing.

`D_shallow_pullback` should be recorded in `REJECTED_CANDIDATES.md` as: *a
low-volatility / low-liquidity / floor-era selector, measured 13.4x over on
overlapping windows; on independent episodes with volatility-aware matching its
lift is 1.045.*

The one durable lesson: **an effect can be large, stable across 12 leave-one-year-out
folds and 20 leave-one-symbol-out folds, survive BH correction on 34 of 36 cells,
and still be nothing.** None of those tests can see a confound that is itself
stable. Only matching on the confound, and counting the sample honestly, could.
