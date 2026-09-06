# SEEING — synchronized DSE market-state experiment · capture `2026-09-06`

**VERDICT: BLOCKED**  
- only 1 distinct composite episodes in the holdout < n_min_episodes 30: the denominator is too small to decide; more sessions are required (capture is designed to run unchanged)

## 1. What is visible in one synchronized state (truth classes)

| field group | truth |
|---|---|
| bid_levels / ask_levels (price, qty; top-N as displayed) | OBSERVED |
| best_bid / best_ask / spread / mid / microprice / depth_topK / imbalances | OBSERVED (arithmetic on observed levels) |
| orders per level | NOT_OBSERVABLE (no obtained source shows order counts) |
| book_agree (two independent book sensors identical) | OBSERVED |
| book t_source | NOT_OBSERVABLE (depth pages carry no exchange stamp; frame clock = receipt time) |
| cumulative day trades / volume / value (exchange-stamped) | OBSERVED |
| interval trades / volume / value / VWAP between frames | INFERRED (Δ of cumulative totals) |
| individual prints | NOT_OBSERVABLE |
| trade side | INFERRED (quote rule on interval VWAP) / OBSERVED when book locked |
| level quantity deltas | OBSERVED (snapshot diff) |
| event class (consumed / cancelled / replenish / sweep) | INFERRED |
| queue position, order ids, intra-interval add/cancel netting | NOT_OBSERVABLE |
| ltp, open, high, low, published close, yclose | OBSERVED |
| forward mid / ltp change (labels) | OBSERVED (future frames; used only as outcome) |
| liquidity change, depletion/replenishment, pressure build/failure, resilience, state | INFERRED (rules in seeing.features.micro / seeing.state_machine) |
| upper/lower limit, tick, breaker % (circuit table) | OBSERVED (reference, per day) |
| shares to the door (ask qty up to the upper limit) | OBSERVED when the limit is within displayed levels, else LOWER BOUND (flagged) |
| market-wide trades / volume / value / breadth | OBSERVED (LankaBD market stats + watch) |
| block-board prints | OBSERVED (daily list) |
| all-symbol L1 with exchange stamp (watch) | OBSERVED (as-of join; age recorded) |
| features | INFERRED (causal rules; parameters pre-registered in seeing.experiment.design) |

## 2. Full denominator

| quantity | value |
|---|---|
| n_frames | 4257 |
| n_symbols | 14 |
| t_range | ['2026-09-06 03:55:51.758161+00:00', '2026-09-06 08:20:00.253120+00:00'] |
| median_frame_dt_s | 43.673742 |
| composite_frames | 8 |
| composite_episodes_total | 3 |
| composite_episodes_holdout | 1 |
| bad_book_frames | 499 |
| stale_book_frames | 367 |
| dup_payload_frames | 2405 |
| frames_with_tape_rows | 2215 |
| frames_with_two_book_sensors | 3793 |
| book_agree_rate | 0.9651990508832059 |
| valid_outcomes_h2 | 3615 |
| base_p_up_h2 | 0.1706777316735823 |
| valid_outcomes_h4 | 3588 |
| base_p_up_h4 | 0.2427536231884058 |
| valid_outcomes_h8 | 3533 |
| base_p_up_h8 | 0.2949334842909708 |

**frames_per_symbol**

| key | n |
|---|---|
| BEXIMCO | 305 |
| BRACBANK | 304 |
| BXPHARMA | 304 |
| GP | 304 |
| IPDC | 304 |
| LOVELLO | 304 |
| MALEKSPIN | 304 |
| ORIONPHARM | 303 |
| POWERGRID | 304 |
| PTL | 305 |
| SAIHAMCOT | 304 |
| SAIHAMTEX | 304 |
| SHARPIND | 304 |
| SQURPHARMA | 304 |

**frames_per_split**

| key | n |
|---|---|
| dev | 1696 |
| holdout | 1287 |
| val | 1274 |

**component_frames**

| key | n |
|---|---|
| persistent_bid_pressure | 1845 |
| ask_thinning | 974 |
| bid_replenishment | 2324 |
| multi_level_transition | 441 |
| spread_stable | 1947 |
| time_persistence | 294 |
| price_response_ok | 2231 |

**score_histogram**

| key | n |
|---|---|
| 0 | 185 |
| 1 | 973 |
| 2 | 1214 |
| 3 | 1127 |
| 4 | 570 |
| 5 | 142 |
| 6 | 38 |
| 7 | 8 |

**baseline_frames**

| key | n |
|---|---|
| b_imb_l1 | 1793 |
| b_imb_top5 | 2244 |
| b_imb_weighted | 2139 |
| b_largest_wall_bid | 2525 |
| b_one_frame_pressure | 418 |

**state_frames**

| key | n |
|---|---|
| BID_PRESSURE_BUILDING | 2031 |
| BALANCED | 1168 |
| ASK_PRESSURE_BUILDING | 340 |
| NO_BOOK | 270 |
| ONE_SIDED | 229 |
| STALE | 208 |
| BID_PRESSURE_CONFIRMED | 8 |
| BID_PRESSURE_FAILED | 2 |
| BID_PRESSURE_RESOLVED | 1 |

## 3. Capture health

| source | ok | err | unchanged |
|---|---|---|---|
| lankabd_cidmap | 1 | 0 | 0 |
| lankabd_watch | 463 | 0 | 25 |
| lankabd_circuit | 5 | 0 | 0 |
| dsebd_latest | 5 | 0 | 0 |
| dsebd_hts | 5 | 0 | 0 |
| lankabd_market | 238 | 0 | 13 |
| lankabd_block | 51 | 0 | 16 |
| lankabd_tape | 1167 | 0 | 155 |
| lankabd_depth | 4254 | 1 | 2405 |
| dsebd_depth | 3796 | 459 | 2048 |

client: {'requests': 10456, 'errors': 460, 'tls_fallbacks': 3916} · symbols: MALEKSPIN, SHARPIND, SAIHAMCOT, SAIHAMTEX, BXPHARMA, PTL, LOVELLO, BRACBANK, BEXIMCO, GP, SQURPHARMA, IPDC, POWERGRID, ORIONPHARM

replay counts: {'lankabd_cidmap': 2, 'lankabd_watch': 475, 'lankabd_circuit': 6, 'dsebd_hts': 6, 'lankabd_market': 244, 'lankabd_block': 53, 'dsebd_depth': 3796, 'lankabd_depth': 4254, 'lankabd_tape': 1167, 'dsebd_latest': 5} · replay problems: 8

## 4. Experiment — every signal, every split, horizon h=4 frames (primary)

| split | signal | n_signal | episodes | share_of_frames | p_up | p_down | mean_fwd_ticks | base_p_up | ctrl_p_up | lift_vs_matched | lift_vs_base |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | composite | 5 | 1 | 0.0029 | 0.8000 | 0.0000 | 1.0000 | 0.2696 | 0.6000 | 0.2000 | 0.5304 |
| dev | mirror_composite | 0 | 0 | 0.0000 |  |  |  | 0.2696 |  |  |  |
| dev | b_imb_l1 | 625 | 133 | 0.3685 | 0.3488 | 0.2575 | 0.1096 | 0.2696 | 0.2439 | 0.1050 | 0.0792 |
| dev | b_imb_top5 | 635 | 90 | 0.3744 | 0.3590 | 0.2984 | 0.1664 | 0.2696 | 0.2117 | 0.1473 | 0.0894 |
| dev | b_imb_weighted | 665 | 81 | 0.3921 | 0.3428 | 0.3176 | 0.0802 | 0.2696 | 0.2604 | 0.0823 | 0.0732 |
| dev | b_largest_wall_bid | 929 | 64 | 0.5478 | 0.2987 | 0.3535 | -0.1806 | 0.2696 | 0.3089 | -0.0102 | 0.0291 |
| dev | b_one_frame_pressure | 186 | 182 | 0.1097 | 0.3240 | 0.3296 | -0.0615 | 0.2696 | 0.2303 | 0.0937 | 0.0544 |
| dev | score_ge_3 | 545 | 121 | 0.3213 | 0.3194 | 0.2966 | -0.0447 | 0.2696 | 0.2385 | 0.0809 | 0.0498 |
| dev | score_ge_4 | 195 | 65 | 0.1150 | 0.3704 | 0.2751 | 0.0847 | 0.2696 | 0.2963 | 0.0741 | 0.1008 |
| dev | score_ge_5 | 59 | 26 | 0.0348 | 0.4576 | 0.2542 | 0.1864 | 0.2696 | 0.3220 | 0.1356 | 0.1880 |
| dev | score_ge_6 | 14 | 8 | 0.0083 | 0.6429 | 0.1429 | 0.9286 | 0.2696 | 0.4286 | 0.2143 | 0.3733 |
| dev | score_ge_7 | 5 | 1 | 0.0029 | 0.8000 | 0.0000 | 1.0000 | 0.2696 | 0.6000 | 0.2000 | 0.5304 |
| val | composite | 2 | 1 | 0.0016 | 1.0000 | 0.0000 | 1.5000 | 0.2169 | 0.0000 | 1.0000 | 0.7831 |
| val | mirror_composite | 0 | 0 | 0.0000 |  |  |  | 0.2169 |  |  |  |
| val | b_imb_l1 | 617 | 108 | 0.4843 | 0.2891 | 0.2017 | 0.0975 | 0.2169 | 0.1044 | 0.1847 | 0.0721 |
| val | b_imb_top5 | 827 | 75 | 0.6491 | 0.2154 | 0.2632 | -0.0680 | 0.2169 | 0.1466 | 0.0688 | -0.0016 |
| val | b_imb_weighted | 730 | 72 | 0.5730 | 0.2489 | 0.2959 | -0.0761 | 0.2169 | 0.2198 | 0.0292 | 0.0320 |
| val | b_largest_wall_bid | 839 | 55 | 0.6586 | 0.2218 | 0.3185 | -0.2447 | 0.2169 | 0.2749 | -0.0530 | 0.0049 |
| val | b_one_frame_pressure | 123 | 117 | 0.0965 | 0.2373 | 0.3559 | -0.3136 | 0.2169 | 0.2906 | -0.0533 | 0.0203 |
| val | score_ge_3 | 717 | 99 | 0.5628 | 0.1834 | 0.2664 | -0.1383 | 0.2169 | 0.1367 | 0.0467 | -0.0335 |
| val | score_ge_4 | 325 | 78 | 0.2551 | 0.1804 | 0.2468 | -0.0775 | 0.2169 | 0.1519 | 0.0285 | -0.0366 |
| val | score_ge_5 | 62 | 28 | 0.0487 | 0.2500 | 0.3000 | 0.0500 | 0.2169 | 0.3333 | -0.0833 | 0.0331 |
| val | score_ge_6 | 13 | 6 | 0.0102 | 0.4615 | 0.0769 | 0.5385 | 0.2169 | 0.2308 | 0.2308 | 0.2446 |
| val | score_ge_7 | 2 | 1 | 0.0016 | 1.0000 | 0.0000 | 1.5000 | 0.2169 | 0.0000 | 1.0000 | 0.7831 |
| holdout | composite | 1 | 1 | 0.0008 | 0.0000 | 0.0000 | 0.0000 | 0.2328 | 0.0000 | 0.0000 | -0.2328 |
| holdout | mirror_composite | 1 | 1 | 0.0008 | 0.0000 | 0.0000 | 0.0000 | 0.2328 | 0.0000 | 0.0000 | -0.2328 |
| holdout | b_imb_l1 | 551 | 87 | 0.4281 | 0.3143 | 0.2384 | 0.1530 | 0.2328 | 0.1492 | 0.1652 | 0.0816 |
| holdout | b_imb_top5 | 782 | 59 | 0.6076 | 0.2547 | 0.2766 | -0.0328 | 0.2328 | 0.2316 | 0.0231 | 0.0220 |
| holdout | b_imb_weighted | 744 | 65 | 0.5781 | 0.2649 | 0.2665 | 0.0452 | 0.2328 | 0.1713 | 0.0936 | 0.0322 |
| holdout | b_largest_wall_bid | 757 | 48 | 0.5882 | 0.2511 | 0.3019 | -0.0635 | 0.2328 | 0.1944 | 0.0567 | 0.0184 |
| holdout | b_one_frame_pressure | 109 | 106 | 0.0847 | 0.3258 | 0.2584 | 0.1180 | 0.2328 | 0.2989 | 0.0270 | 0.0931 |
| holdout | score_ge_3 | 623 | 83 | 0.4841 | 0.2318 | 0.2664 | -0.0319 | 0.2328 | 0.2598 | -0.0281 | -0.0010 |
| holdout | score_ge_4 | 238 | 70 | 0.1849 | 0.2333 | 0.2429 | -0.0214 | 0.2328 | 0.2571 | -0.0238 | 0.0006 |
| holdout | score_ge_5 | 67 | 27 | 0.0521 | 0.2787 | 0.1803 | 0.0902 | 0.2328 | 0.2131 | 0.0656 | 0.0459 |
| holdout | score_ge_6 | 19 | 9 | 0.0148 | 0.2353 | 0.2353 | -0.2353 | 0.2328 | 0.3529 | -0.1176 | 0.0025 |
| holdout | score_ge_7 | 1 | 1 | 0.0008 | 0.0000 | 0.0000 | 0.0000 | 0.2328 | 0.0000 | 0.0000 | -0.2328 |

### All horizons — holdout

| signal | h | n_signal | episodes | p_up | ctrl_p_up | lift_vs_matched | lift_vs_base | ticks_vs_base |
|---|---|---|---|---|---|---|---|---|
| composite | 2 | 1 | 1 | 0.0000 | 0.0000 | 0.0000 | -0.1551 | 0.0283 |
| mirror_composite | 2 | 1 | 1 | 0.0000 | 0.0000 | 0.0000 | -0.1551 | 0.0283 |
| b_imb_l1 | 2 | 551 | 87 | 0.2150 | 0.0887 | 0.1263 | 0.0599 | 0.1358 |
| b_imb_top5 | 2 | 782 | 59 | 0.1759 | 0.1302 | 0.0457 | 0.0208 | 0.0347 |
| b_imb_weighted | 2 | 744 | 65 | 0.1803 | 0.1268 | 0.0535 | 0.0252 | 0.0752 |
| b_largest_wall_bid | 2 | 757 | 48 | 0.1676 | 0.1757 | -0.0080 | 0.0125 | 0.0086 |
| b_one_frame_pressure | 2 | 109 | 106 | 0.2308 | 0.1333 | 0.0974 | 0.0756 | 0.1712 |
| score_ge_3 | 2 | 623 | 83 | 0.1676 | 0.1835 | -0.0159 | 0.0124 | 0.0550 |
| score_ge_4 | 2 | 238 | 70 | 0.1757 | 0.1396 | 0.0360 | 0.0205 | 0.0396 |
| score_ge_5 | 2 | 67 | 27 | 0.1905 | 0.2381 | -0.0476 | 0.0353 | 0.0283 |
| score_ge_6 | 2 | 19 | 9 | 0.2222 | 0.2222 | 0.0000 | 0.0671 | 0.0283 |
| score_ge_7 | 2 | 1 | 1 | 0.0000 | 0.0000 | 0.0000 | -0.1551 | 0.0283 |
| composite | 4 | 1 | 1 | 0.0000 | 0.0000 | 0.0000 | -0.2328 | 0.0830 |
| mirror_composite | 4 | 1 | 1 | 0.0000 | 0.0000 | 0.0000 | -0.2328 | 0.0830 |
| b_imb_l1 | 4 | 551 | 87 | 0.3143 | 0.1492 | 0.1652 | 0.0816 | 0.2359 |
| b_imb_top5 | 4 | 782 | 59 | 0.2547 | 0.2316 | 0.0231 | 0.0220 | 0.0502 |
| b_imb_weighted | 4 | 744 | 65 | 0.2649 | 0.1713 | 0.0936 | 0.0322 | 0.1282 |
| b_largest_wall_bid | 4 | 757 | 48 | 0.2511 | 0.1944 | 0.0567 | 0.0184 | 0.0194 |
| b_one_frame_pressure | 4 | 109 | 106 | 0.3258 | 0.2989 | 0.0270 | 0.0931 | 0.2010 |
| score_ge_3 | 4 | 623 | 83 | 0.2318 | 0.2598 | -0.0281 | -0.0010 | 0.0510 |
| score_ge_4 | 4 | 238 | 70 | 0.2333 | 0.2571 | -0.0238 | 0.0006 | 0.0615 |
| score_ge_5 | 4 | 67 | 27 | 0.2787 | 0.2131 | 0.0656 | 0.0459 | 0.1731 |
| score_ge_6 | 4 | 19 | 9 | 0.2353 | 0.3529 | -0.1176 | 0.0025 | -0.1523 |
| score_ge_7 | 4 | 1 | 1 | 0.0000 | 0.0000 | 0.0000 | -0.2328 | 0.0830 |
| composite | 8 | 1 | 1 | 0.0000 | 0.0000 | 0.0000 | -0.3010 | 0.1562 |
| mirror_composite | 8 | 1 | 1 | 1.0000 | 0.0000 | 1.0000 | 0.6990 | 0.6562 |
| b_imb_l1 | 8 | 551 | 87 | 0.3772 | 0.2586 | 0.1186 | 0.0762 | 0.2516 |
| b_imb_top5 | 8 | 782 | 59 | 0.3181 | 0.2470 | 0.0711 | 0.0171 | 0.0733 |
| b_imb_weighted | 8 | 744 | 65 | 0.3463 | 0.1425 | 0.2038 | 0.0453 | 0.2266 |
| b_largest_wall_bid | 8 | 757 | 48 | 0.3170 | 0.1463 | 0.1707 | 0.0160 | 0.0900 |
| b_one_frame_pressure | 8 | 109 | 106 | 0.3587 | 0.3516 | 0.0070 | 0.0577 | 0.3573 |
| score_ge_3 | 8 | 623 | 83 | 0.2901 | 0.3491 | -0.0590 | -0.0109 | 0.0160 |
| score_ge_4 | 8 | 238 | 70 | 0.3046 | 0.2944 | 0.0102 | 0.0035 | 0.0014 |
| score_ge_5 | 8 | 67 | 27 | 0.3966 | 0.2241 | 0.1724 | 0.0955 | 0.5528 |
| score_ge_6 | 8 | 19 | 9 | 0.3333 | 0.2000 | 0.1333 | 0.0323 | -0.0105 |
| score_ge_7 | 8 | 1 | 1 | 0.0000 | 1.0000 | -1.0000 | -0.3010 | 0.1562 |

## 5. Composite vs each simple baseline (holdout, primary horizon)

| baseline | n_baseline | p_up_baseline | lift_baseline_vs_base | n_composite | p_up_composite | incremental_lift | n_both | p_up_both | n_only_baseline | p_up_only_baseline | within_baseline_gain |
|---|---|---|---|---|---|---|---|---|---|---|---|
| b_imb_l1 | 474 | 0.3143 | 0.0816 | 1 | 0.0000 | -0.3143 | 1 | 0.0000 | 473 | 0.3150 | -0.3150 |
| b_imb_top5 | 687 | 0.2547 | 0.0220 | 1 | 0.0000 | -0.2547 | 1 | 0.0000 | 686 | 0.2551 | -0.2551 |
| b_imb_weighted | 653 | 0.2649 | 0.0322 | 1 | 0.0000 | -0.2649 | 1 | 0.0000 | 652 | 0.2653 | -0.2653 |
| b_largest_wall_bid | 669 | 0.2511 | 0.0184 | 1 | 0.0000 | -0.2511 | 1 | 0.0000 | 668 | 0.2515 | -0.2515 |
| b_one_frame_pressure | 89 | 0.3258 | 0.0931 | 1 | 0.0000 | -0.3258 | 0 |  | 89 | 0.3258 |  |

## 6. Falsification battery (holdout)

| test | variant | n_frames | n_signal | episodes | lift_vs_base | incremental_vs_best_baseline | passed | note |
|---|---|---|---|---|---|---|---|---|
| real | holdout | 1287 | 1 | 1 | -0.2328 | -0.3258 | nan | best simple baseline = b_one_frame_pressure |
| baseline_comparison | composite - b_one_frame_pressure | 1287 | 1 | 1 | -0.2328 | -0.3258 | False | block bootstrap 95% CI [-0.4192, -0.2274] (blocks of 20 frames, 649 resamples) |
| baseline | b_imb_l1 | 1287 | 474 | 87 | 0.0816 |  | nan | nan |
| baseline | b_imb_top5 | 1287 | 687 | 59 | 0.0220 |  | nan | nan |
| baseline | b_imb_weighted | 1287 | 653 | 65 | 0.0322 |  | nan | nan |
| baseline | b_largest_wall_bid | 1287 | 669 | 48 | 0.0184 |  | nan | nan |
| baseline | b_one_frame_pressure | 1287 | 89 | 106 | 0.0931 |  | nan | nan |
| graded_score | score_ge_3 | 1287 | 548 | 83 | -0.0010 | -0.0941 | nan | nan |
| graded_score | score_ge_4 | 1287 | 210 | 70 | 0.0006 | -0.0925 | nan | nan |
| graded_score | score_ge_5 | 1287 | 61 | 27 | 0.0459 | -0.0472 | nan | nan |
| graded_score | score_ge_6 | 1287 | 17 | 9 | 0.0025 | -0.0905 | nan | nan |
| graded_score | score_ge_7 | 1287 | 1 | 1 | -0.2328 | -0.3258 | nan | nan |
| timestamp_permutation | circular shift within symbol | 1287 | 1 | 1 | -0.2328 |  | False | p = 1.0000 over 440 permutations; null mean -0.0509, null p95 0.7672 |
| side_flip | mirror_composite → P(down) | 1287 | 1 | 1 | -0.2888 |  | False | down-lift of the side-flipped composite (must be > 0 for a real, symmetric mechanism) |
| anchor_shift | placebo_shift_-h | 1287 | 1 | 1 | -0.2328 | -0.3258 | False | placebo must show |lift| < ½ real lift |
| anchor_shift | leak_control_shift_+h | 1287 | 1 | 1 | -0.2328 | -0.3258 | nan | future-shifted signal: expected to inflate (test sensitivity) |
| removal | stale_removed | 1119 | 1 | 1 | -0.2309 | -0.3258 | True | kept 1119/1287 frames |
| removal | duplicate_removed | 526 | 1 | 1 | -0.2231 | -0.3258 | True | kept 526/1287 frames |
| removal | crossed_locked_removed | 1016 | 1 | 1 | -0.2328 | -0.3258 | True | kept 1016/1287 frames |
| removal | largest_wall_removed | 1287 | 0 | 1 |  |  | False | composite recomputed with the largest visible level removed from the imbalance inputs |
| leave_one_symbol_out | without BEXIMCO | 1195 | 1 | 1 | -0.2410 | -0.3333 | False | nan |
| leave_one_symbol_out | without BRACBANK | 1195 | 1 | 1 | -0.2308 | -0.3372 | False | nan |
| leave_one_symbol_out | without BXPHARMA | 1195 | 1 | 1 | -0.2162 | -0.3158 | False | nan |
| leave_one_symbol_out | without GP | 1195 | 1 | 1 | -0.2322 | -0.3125 | False | nan |
| leave_one_symbol_out | without IPDC | 1195 | 1 | 1 | -0.2244 | -0.3253 | False | nan |
| leave_one_symbol_out | without LOVELLO | 1195 | 0 | 0 |  |  | False | nan |
| leave_one_symbol_out | without MALEKSPIN | 1195 | 1 | 1 | -0.2356 | -0.3462 | False | nan |
| leave_one_symbol_out | without ORIONPHARM | 1196 | 1 | 1 | -0.2383 | -0.3214 | False | nan |
| leave_one_symbol_out | without POWERGRID | 1195 | 1 | 1 | -0.2418 | -0.3333 | False | nan |
| leave_one_symbol_out | without PTL | 1195 | 1 | 1 | -0.2347 | -0.3176 | False | nan |
| leave_one_symbol_out | without SAIHAMCOT | 1195 | 1 | 1 | -0.2338 | -0.3250 | False | nan |
| leave_one_symbol_out | without SAIHAMTEX | 1195 | 1 | 1 | -0.2328 | -0.3258 | False | nan |
| leave_one_symbol_out | without SHARPIND | 1195 | 1 | 1 | -0.2330 | -0.3295 | False | nan |
| leave_one_symbol_out | without SQURPHARMA | 1195 | 1 | 1 | -0.2300 | -0.3289 | False | nan |
| liquidity_split | top | 644 | 1 | 1 | -0.2518 | -0.3478 | False | symbols: BXPHARMA, LOVELLO, MALEKSPIN, PTL, SAIHAMCOT, SAIHAMTEX, SHARPIND |
| liquidity_split | mid | 643 | 0 | 0 |  |  | False | symbols: BEXIMCO, BRACBANK, GP, IPDC, ORIONPHARM, POWERGRID, SQURPHARMA |

## 7. State transitions (rows: from, columns: to)

| from | ASK_PRESSURE_BUILDING | BALANCED | BID_PRESSURE_BUILDING | BID_PRESSURE_CONFIRMED | BID_PRESSURE_FAILED | BID_PRESSURE_RESOLVED | NO_BOOK | ONE_SIDED | STALE |
|---|---|---|---|---|---|---|---|---|---|
| ASK_PRESSURE_BUILDING | 0 | 31 | 22 | 0 | 0 | 0 | 10 | 0 | 21 |
| BALANCED | 30 | 0 | 116 | 0 | 0 | 0 | 34 | 2 | 42 |
| BID_PRESSURE_BUILDING | 19 | 111 | 0 | 3 | 0 | 0 | 69 | 7 | 77 |
| BID_PRESSURE_CONFIRMED | 0 | 0 | 0 | 0 | 2 | 1 | 0 | 0 | 0 |
| BID_PRESSURE_FAILED | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| BID_PRESSURE_RESOLVED | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| NO_BOOK | 13 | 53 | 61 | 0 | 0 | 0 | 0 | 6 | 0 |
| ONE_SIDED | 2 | 1 | 3 | 0 | 0 | 0 | 5 | 0 | 0 |
| STALE | 20 | 28 | 81 | 0 | 0 | 0 | 5 | 4 | 0 |

## 8. Verdict rule (pre-registered)

```
BLOCKED  if the holdout has fewer than n_min_episodes distinct composite episodes or the
         fused table has fewer than n_min_frames frames (denominator too small to decide).
KEEP     if, on the HOLDOUT, at the primary horizon:
           (a) lift(composite) − lift(best simple baseline) > 0 with block-bootstrap 95 % CI lower bound > 0;
           (b) timestamp-permutation p < alpha for the composite's lift;
           (c) the side-flipped (mirrored) composite predicts DOWN (its down-lift > 0);
           (d) the anchor-shift placebo (signal shifted −h) shows |lift| < ½ of the real lift;
           (e) the sign of the incremental lift is preserved after stale, duplicate, crossed/locked and
               largest-wall removal;
           (f) leave-one-symbol-out: the incremental lift stays > 0 for every left-out symbol;
           (g) the incremental lift is > 0 in both liquidity halves.
KILL     otherwise. The hypothesis is not protected: failing (a) alone kills it.
```

## 9. Design parameters

| parameter | value |
|---|---|
| W | 6 |
| P | 2 |
| horizons | [2, 4, 8] |
| primary_h | 4 |
| theta_imb | 0.2 |
| persist_frac | 0.8 |
| thinning_ratio | 0.8 |
| max_spread_ticks | 2.0 |
| theta_pressure | 0.1 |
| dev_frac | 0.4 |
| val_frac | 0.3 |
| block_len | 20 |
| n_boot | 1000 |
| n_perm | 500 |
| seed | 7 |
| n_min_episodes | 30 |
| n_min_frames | 500 |
| alpha | 0.05 |
| stale_unchanged_run | 3 |
| stale_watch_age_s | 120.0 |
| match_keys | ['symbol', 'tod_bucket', 'spread_bucket'] |
| n_tod_buckets | 5 |


_git commit 00b69043a9fdc3a75a62863be9b179c824aa1734 · written 2026-09-06T08:46:00.366242+00:00_
