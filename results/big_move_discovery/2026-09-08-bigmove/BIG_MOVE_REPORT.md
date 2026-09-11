# Big-move discovery — results

Run `2026-09-08-bigmove` · branch `claude/big-move-ultra-push` · commit `6235e34` · parent `1cb176b9063a`

Panel 587,474 rows · 392 symbols · 2012-10-01 … 2026-01-22 · dataset sha256 `2d9c77ffed30c997`

Sealed holdout 2019-01-01 … 2022-07-27 and the slice from 2026-01-25 are dropped at load and asserted absent.


## A. What survived

1 candidates in 8 candidate x outcome cells reached PROMISING (lift >= 1.25, NW t >= 2.5, bootstrap CI above control, year consistency >= 0.6).

| candidate | outcome | N | symbols | hit | control | lift | NW t | CI lo | yr cons |
|---|---|---|---|---|---|---|---|---|---|
| F_at_limit | hit_p5_5d | 3132 | 367 | 81.96% | 46.39% | 1.767 | 37.971 | 80.50% | 1.000 |
| F_at_limit | hit_p10_10d | 3132 | 367 | 56.42% | 32.30% | 1.747 | 22.611 | 54.32% | 1.000 |
| F_at_limit | hit_p10_20d | 3132 | 367 | 62.96% | 43.91% | 1.434 | 15.460 | 61.07% | 1.000 |
| F_at_limit | hit_p20_20d | 3132 | 367 | 37.68% | 21.38% | 1.762 | 14.673 | 35.47% | 1.000 |
| F_at_limit | clean_p10_20d | 3132 | 367 | 56.16% | 37.01% | 1.517 | 14.610 | 54.23% | 1.000 |
| F_at_limit | hit_p20_30d | 3132 | 367 | 42.31% | 27.44% | 1.542 | 13.172 | 39.98% | 1.000 |
| F_at_limit | hit_p10_30d | 3132 | 367 | 66.92% | 50.35% | 1.329 | 12.441 | 65.00% | 1.000 |
| F_at_limit | clean_p20_30d | 3132 | 367 | 35.50% | 21.41% | 1.659 | 11.538 | 33.59% | 1.000 |

## B. What died

KILLED 266 cells · WEAK 54 · INSUFFICIENT_SAMPLE 0


The most decisively negative, by lift:

| candidate | outcome | N | symbols | hit | control | lift | NW t | CI lo | yr cons |
|---|---|---|---|---|---|---|---|---|---|
| D_shallow_pullback | hit_p5_5d | 96855 | 377 | 10.76% | 23.56% | 0.457 | -26.442 | 10.10% | 0.000 |
| D_shallow_pullback | hit_p20_20d | 96855 | 377 | 4.85% | 10.37% | 0.468 | -10.113 | 4.38% | 0.167 |
| D_shallow_pullback | hit_p10_10d | 96855 | 377 | 7.44% | 15.78% | 0.471 | -16.883 | 6.89% | 0.000 |
| D_shallow_pullback | hit_p20_30d | 96855 | 377 | 8.06% | 16.89% | 0.477 | -8.465 | 7.44% | 0.167 |
| D_shallow_pullback | clean_p20_30d | 96855 | 377 | 7.66% | 15.67% | 0.489 | -6.481 | 6.99% | 0.167 |
| D_shallow_pullback | hit_p10_20d | 96855 | 377 | 14.67% | 27.35% | 0.536 | -11.598 | 13.70% | 0.167 |
| D_shallow_pullback | clean_p10_20d | 96855 | 377 | 14.25% | 26.03% | 0.547 | -9.303 | 13.32% | 0.167 |
| D_shallow_pullback | hit_p10_30d | 96855 | 377 | 20.00% | 36.16% | 0.553 | -9.165 | 18.80% | 0.167 |
| CHAIN_5_full | clean_p20_30d | 1377 | 323 | 17.21% | 18.91% | 0.910 | 0.670 | 15.16% | 0.583 |
| CHAIN_5_full | hit_p20_30d | 1377 | 323 | 20.84% | 22.79% | 0.915 | 1.162 | 18.43% | 0.583 |
| CHAIN_4_shock_premove_lead | hit_p20_30d | 13239 | 390 | 17.11% | 18.39% | 0.930 | 1.052 | 15.50% | 0.500 |
| D_range_compression | hit_p5_5d | 6631 | 383 | 31.79% | 34.00% | 0.935 | 0.623 | 29.74% | 0.417 |
| E_xsrank95_premove | hit_p20_30d | 13349 | 390 | 17.21% | 18.40% | 0.935 | 1.111 | 15.79% | 0.500 |
| CHAIN_4_shock_premove_lead | clean_p20_30d | 13239 | 390 | 14.19% | 15.16% | 0.936 | 0.168 | 12.93% | 0.417 |
| E_xsrank95_premove | clean_p20_30d | 13349 | 390 | 14.29% | 15.23% | 0.939 | 0.181 | 13.00% | 0.417 |

## C. Response surface — relative volume x prior move

The whole surface, not one tuned cell. Rows are the abnormal-volume threshold; `prior3_max` 1.0 means no pre-move filter at all.


**hit_p10_20d**

| relvol >= | prior3 < 0.02 | prior3 < 0.05 | prior3 < 1.0 |
|---|---|---|---|
| 1.5 | 1.021 (n=99,580) | 1.019 (n=132,659) | 1.050 (n=174,038) |
| 2 | 1.025 (n=65,401) | 1.019 (n=88,574) | 1.050 (n=123,187) |
| 2.5 | 1.025 (n=47,699) | 1.013 (n=64,386) | 1.047 (n=93,314) |
| 3 | 1.033 (n=37,428) | 1.011 (n=49,700) | 1.042 (n=74,108) |
| 4 | 1.043 (n=26,458) | 1.018 (n=33,814) | 1.051 (n=51,529) |
| 5 | 1.066 (n=20,739) | 1.037 (n=25,674) | 1.064 (n=39,042) |
| 8 | 1.122 (n=13,754) | 1.095 (n=15,896) | 1.091 (n=22,744) |

**hit_p20_30d**

| relvol >= | prior3 < 0.02 | prior3 < 0.05 | prior3 < 1.0 |
|---|---|---|---|
| 1.5 | 0.991 (n=99,580) | 0.974 (n=132,659) | 1.008 (n=174,038) |
| 2 | 0.991 (n=65,401) | 0.973 (n=88,574) | 1.015 (n=123,187) |
| 2.5 | 0.979 (n=47,699) | 0.959 (n=64,386) | 1.011 (n=93,314) |
| 3 | 0.972 (n=37,428) | 0.945 (n=49,700) | 0.999 (n=74,108) |
| 4 | 0.967 (n=26,458) | 0.936 (n=33,814) | 0.997 (n=51,529) |
| 5 | 0.970 (n=20,739) | 0.936 (n=25,674) | 1.003 (n=39,042) |
| 8 | 0.999 (n=13,754) | 0.969 (n=15,896) | 1.010 (n=22,744) |

## D. Population outcome rates

What a randomly chosen row achieves, the number every lift is against.

| target | 3d | 5d | 10d | 20d | 30d |
|---|---|---|---|---|---|
| +5% | 24.21% | 32.45% | 44.49% | 56.19% | 62.44% |
| +8% | 11.51% | 17.69% | 28.41% | 40.67% | 47.94% |
| +10% | 6.78% | 11.67% | 21.01% | 32.74% | 40.14% |
| +15% | 2.53% | 5.10% | 11.12% | 20.43% | 27.15% |
| +20% | 1.03% | 2.44% | 6.22% | 13.06% | 18.65% |
| +30% | 0.24% | 0.75% | 2.36% | 5.97% | 9.53% |

## E. Method

- Matched controls on year, liq_bucket, price_bucket, prior5_bucket, 20 draws with replacement; the reported control is the mean over draws and `control_rate_sd` its spread. A single draw is one sample of a random quantity, not the quantity.

- Significance on a per-DATE difference series with Newey-West lags equal to the horizon. Overlapping horizons make the row count the wrong inferential unit.

- 400-draw symbol-block bootstrap CI; symbols are the cluster, not rows.

- Every result carries MFE, MAE and, where reached, median days to +10 %.

---

## F. Verdict — the one survivor is an artifact, and the hypothesis is reversed

### F.1 `F_at_limit` passed every statistical gate and is worthless

It was the only candidate to reach PROMISING, on all eight outcomes, with NW t up
to 38. It is not a buildup signal. It is a mechanical echo:

| | at_limit (n=3,132) | abnormal volume, not at limit (n=120,632) |
|---|---:|---:|
| median day return | **+9.87 %** (the ~10 % band, by construction) | −0.00 % |
| median prior 3-day return | **+12.80 %** | +1.39 % |
| median prior 5-day return | +14.49 % | +2.20 % |
| `range_pos_20d` | **1.164** — already above its 20-day high | 0.600 |
| reaches +5 % on the **first** forward day | **70.2 %** | 15.3 % |
| median days to +5 % | **1.0** | 4.0 |
| **median 20-day forward return** | **−2.53 %** | −0.34 % |

It "hits +5 % within five days" because it limit-ups again tomorrow, and then it
**loses more over twenty days than the group it beat**. MFE +14.5 % against MAE
−9.3 %. On top of that it is structurally untradeable: a share closing at its
limit has no offer, so the entry the whole evaluation assumes does not exist.

**Status: KILLED for the stated purpose.** Kept in the ranking so the next reader
sees why a candidate can clear lift, t, bootstrap CI and 12/12 year consistency
simultaneously and still be nothing.

### F.2 The pre-move hypothesis is contradicted, not merely unsupported

The central question was whether abnormal activity still predicts a big move when
price has **not** already run. It predicts **worse**:

| candidate | outcome | n | lift | NW t |
|---|---|---:|---:|---:|
| shock + prior 3d ≥ +5 % (**already run**) | +10 % in 20d | 34,622 | **1.091** | **9.84** |
| shock + prior 3d < +2 % (**not run**) | +10 % in 20d | 65,401 | 1.025 | 1.13 |
| shock + prior 3d ≥ +5 % (**already run**) | +20 % in 30d | 34,622 | **1.106** | **8.84** |
| shock + prior 3d < +2 % (**not run**) | +20 % in 30d | 65,401 | 0.991 | 1.40 |

The response surface says the same thing at every threshold. For +20 % in 30 days,
**every** cell carrying a pre-move filter sits below 1.0 (0.936–0.999), while the
unfiltered column sits at 1.00–1.015. Abnormal volume on DSE is a momentum echo,
not accumulation.

### F.3 The chain does not chain

Each added link makes it worse, except "still below the 20-day high":

| chain | +10 % in 20d lift | NW t |
|---|---:|---:|
| shock + not run | 1.025 | 1.13 |
| + below 20-day high | **1.079** | 6.37 |
| + light sell side | 1.044 | 3.04 |
| + causal leadership | 0.969 | 1.18 |
| full five-link chain | 0.993 | 1.95 |

### F.4 Causal leadership: the +21.75 pp result does not survive the PIT fix

Rebuilt with `asof_cross_section`, cross-sectional leadership of relative volume
is worth essentially nothing at the daily scale:

| candidate | +10 % in 20d lift | +20 % in 30d lift |
|---|---:|---:|
| causal rank ≥ 0.99 | 1.039 | 1.016 |
| causal rank ≥ 0.95 | 1.012 | 1.002 |
| causal rank ≥ 0.90 | 0.999 | 0.993 |
| causal rank ≥ 0.95 + pre-move | 0.973 | 0.935 |

Monotone in the right direction but flat: the top 1 % of the causal cross-section
beats its matched control by 3.9 % relative, and the top 10 % by nothing at all.

### F.5 The SAI shape is dead

`volume_compression_activity = (V / avgV10) x (1 - |C-O|/(H-L))`, tested
neutrally at three percentile cutoffs and with a pre-move filter:

| cutoff | +10 % in 20d lift | NW t | +20 % in 30d lift |
|---|---:|---:|---:|
| 99th pct | 1.027 | **−0.40** | 0.972 |
| 98th pct | 1.004 | 0.52 | 0.959 |
| 95th pct | 1.004 | 2.31 | 0.972 |
| 95th + pre-move | 1.018 | **−0.89** | 0.963 |
| 3x volume, body ≤ ¼ range | 0.997 | 1.48 | 0.944 |

Every +20 %/30d cell is **below 1.0**. Two cells have negative t. **KILLED.**

### F.6 Supply tightening: one weak positive, one strong negative

* `D_sell_pressure_drop` (down-volume share falls > 15 points): lift 1.065,
  t = 4.05 at +20 %/30d, n = 94,844. **WEAK** — real but small.
* `D_shallow_pullback` (shallow low near the 20-day high): lift **0.536**,
  t = **−11.60**, and 0.477 at +20 %/30d. Strongly and consistently **negative**:
  on DSE, a share holding near its 20-day high with a shallow pullback
  *underperforms* its matched control by roughly half. This is the largest
  effect in the whole run and it points the opposite way to the narrative that
  motivated it. **KILLED as stated; recorded as an inverted finding worth its own
  test.**
