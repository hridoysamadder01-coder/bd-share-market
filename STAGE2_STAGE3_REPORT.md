<!--
Stage 2 + Stage 3 report. Append-only, like PROJECT_MAP.md and ROADMAP.md.
Discovery output, NOT a result. Nothing here has been through Stage 11 falsification,
nothing here is tradeable, and no sealed data was opened to produce it.
-->

# Stage 2 + Stage 3 — the unified state, and the shape of the book

**2026-09-08.** Stage 2 (Unified Market State) and Stage 3 (Current-State Mathematics)
built and gated on `evidence/capture/2026-09-06`: 4,257 states, 14 listings, one session.

> **One session is one block.** The prereg's unit of resampling is a whole session, and
> there is exactly one. Every ranked number below is therefore **INSUFFICIENT_SAMPLE**,
> and the intervals shown are over rows or symbols *within* that session. Discovery is
> allowed to be aggressive; it is not allowed to call a within-session interval a result.

---

## Stage 2 — one state per listing per instant, hashed

`seeing/fusion/unified.py`, gated by `seeing/fusion/gate.py`. **6/6 PASS**
(`evidence/unified/2026-09-06/STAGE2_GATE.json`).

| check | result |
|---|---|
| states built | **4,257** over 14 listings, 03:55:51 → 08:20:00 UTC |
| run hash stable across two independent replays from raw bytes | `87e00d2a…` twice |
| all 4,257 state hashes stable, positionally | 0 mismatched |
| no future reference | `ref_status` OBSERVED on 4,257 / 4,257 |
| identity from the Stage 0 spine | 14 listings, 0 unresolved, spine `c516aed2…` |
| consensus reported, not resolved | 8 consensus columns, 2,312 states carry a disagreement |

Both passes go back to the raw segments and re-parse, re-fuse and re-unify. A second pass
that reused the first one's tables would test `copy()`.

### The look-ahead that was removed

`fuse.py` used to fall back to the day's **first** circuit row when no poll preceded a
frame, flag it `ref_from_future=True`, and carry on. Every limit-derived quantity on those
frames — `shares_to_door`, `bid_at_upper_limit`, distance-to-band — was then computed from a
reference the market had not yet published. A flagged look-ahead is still a look-ahead, and
a filter on the flag is something a reader has to remember to apply.

It is gone. A frame with no preceding observation keeps NaN limits and records
`ref_status = NOT_YET_OBSERVED`; where no circuit source was captured at all, `NO_CIRCUIT_SOURCE`.
`assert_no_future_reference` fails the gate if the column ever returns, if a limit appears
without an observation behind it, or if `ref_age_s` goes negative.

A second, quieter instance of the same fault was found alongside it: the tick used to scale
every book was `circuit.groupby("symbol")["tick_size"].last()` — the tick observed at 08:00
handed to a book reconstructed at 04:00. It now takes the **first** observed tick.
`test_the_tick_used_to_scale_a_book_is_not_one_observed_later_in_the_day` pins it.

### What the consensus layer found

The depth page and the all-symbol watch both publish an LTP and day totals for the same
instrument. They disagree on **54 % of states**, and the disagreement is not symmetric:

| field | agree | disagree | shape of the disagreement |
|---|---|---|---|
| `yclose` | 99.9 % | 0.1 % | — |
| `open` / `high` / `low` | ~99 % | ~1 % | — |
| `ltp` | 73.0 % | 27.0 % | median gap **exactly one tick**, 571 low / 578 high — two sensors sampled a moment apart |
| `day_volume` | 45.7 % | **54.3 %** | watch ahead of depth in **2,056 of 2,312**, median shortfall 0.23 % of the day |

**The depth page's own day totals lag the watch poll.** At 04:01 the depth page reported
`day_volume = 0` for BEXIMCO while the watch reported 135,167. Anyone reading cumulative
activity off the depth page is reading a stale number half the time. The reference fields
(`yclose`, `open`, `high`, `low`) agree; it is specifically the *cumulative activity* block
that trails. `cons_*` is filled only where both sources report and agree; nothing is averaged.

---

## Stage 3 — the mathematics of the book's shape

`seeing/features/geometry.py`. **Stage 3 gate 5/5 PASS**: every quantity computable on real
data, every quantity varies, every quantity documented, plus the two structural identities.

### TLPI(λ) — a family that contains the existing baselines

    TLPI(λ) = (B(λ) − A(λ)) / (B(λ) + A(λ))
    B(λ) = Σ_bids q·exp(−λ·d),   d = |p − mid| / tick

λ is how fast attention decays with distance from the mid. The family is testable rather
than decorative because it *contains* both endpoints this repository already uses, and both
identities hold on the real data:

* **TLPI(0) ≡ `imb_all`** — max absolute difference **0.0** over 3,758 states.
* **TLPI(8) → E1** — Pearson **r = 0.9996** over 3,758 states.

So the λ grid is a continuum between two existing baselines, and the response curve has a
shape worth reading.

### The response curve peaks in the interior

Horizon 180 s, 1,997 eligible rows, identical rows at every λ:

| λ | 0 | 0.25 | 0.5 | **1** | **2** | 4 | 8 |
|---|---|---|---|---|---|---|---|
| AUC (tick distance) | 0.539 | 0.605 | 0.642 | 0.676 | **0.692** | 0.688 | 0.683 |
| AUC (distance relative to mid) | 0.539 | 0.539 | 0.539 | 0.540 | 0.541 | 0.542 | 0.544 |

Two readings, one positive and one negative:

**The whole-book imbalance is nearly useless (0.539) and the near-book imbalance is not
(0.692).** The peak sits at λ ≈ 2 — an e-folding distance of about half a tick — and both
endpoints are worse than the interior. Depth far from the touch is noise at this horizon.

**The scale that matters is ticks, not percent.** The relative-to-mid variants are flat at
0.539–0.544 across the entire grid. Decay measured in price percentage sees nothing; decay
measured on the tick grid sees a lot. That is a statement about the grid traders actually
look at, and it is a clean negative result for the scale-free formulation.

### Where distance beats level rank — and where it provably cannot

This is the honest limit of the construction. On a **mirror-symmetric ladder** — bids at
10.00/9.90/9.80 against asks at 10.10/10.20/10.30 — level *k* is the same distance from the
mid on both sides, the decay weight cancels level by level, and TLPI is algebraically an
ordinary rank-decayed imbalance. Distance can only contribute on a **gapped** book, where
one side's quantity is parked further out in ticks at the same level index. On this session
that is 40 % of frames (35 % gapped by more than a tick).

So the ablation — TLPI(λ) against the identical decay applied to level *rank* — is run
inside each population rather than pooled (control **C7**):

| λ = 1 | n | TLPI (distance) | twin (rank) | margin | 95 % CI (symbol blocks) |
|---|---|---|---|---|---|
| **symmetric** | 1,179 | 0.6587 | 0.6582 | +0.0005 | [+0.0000, +0.0022] |
| **gapped** | 717 | 0.6810 | 0.6330 | **+0.0479** | **[+0.0262, +0.0806]** |
| pooled | 1,997 | 0.6760 | 0.6591 | +0.0169 | [+0.0060, +0.0309] |

**The contribution of tick distance appears exactly where the algebra says it must and
vanishes exactly where the algebra says it must.** On symmetric books the margin is
+0.0005, two orders of magnitude below the gapped-book margin. It is not identically zero
because the `symmetric` bucket is a tolerance (mismatch ≤ 0.01 ticks over the levels *both*
sides display) and a side showing more levels than the other still contributes weights that
do not cancel; the exact algebraic identity is asserted on a genuinely mirror-symmetric
ladder in `test_on_a_mirror_symmetric_ladder_distance_decay_is_level_rank_decay`. On gapped
books the margin is +0.048 with an interval well clear of zero. The gap in the book is the
information.

This also explains the pooled result: 59 % of frames are symmetric, where TLPI *cannot*
add anything, so pooling dilutes a real effect with rows that are silent about it.

### The incremental gate — where the new mathematics does not win

The gate that killed P45-5. Every new quantity, against the strongest existing baseline
(`imb_l1` = E1), paired on identical rows, symbol-block bootstrap:

| candidate | AUC | margin over E1 | 95 % CI | established? |
|---|---|---|---|---|
| `tlpi_2` | 0.6920 | +0.0106 | [−0.0066, +0.0274] | **no** |
| `c_e1_minus_market` | 0.6913 | +0.0098 | [−0.0061, +0.0249] | **no** |
| `tlpi_1` | 0.6760 | −0.0055 | [−0.0340, +0.0227] | no |
| `x_e1_minus_tlpi0` | 0.6645 | −0.0169 | [−0.0373, +0.0061] | no |
| `geom_divergence` | 0.6580 | −0.0212 | [−0.0384, −0.0009] | worse |
| `v_e1_vetoed` | 0.6573 | −0.0242 | [−0.0418, −0.0076] | worse |

**Nothing beats E1 with an established margin.** TLPI(2)'s +0.011 spans zero. Stated
plainly: on this session the λ family explains *why* E1 works — because distance matters
and only the near book carries information — without beating it overall. Its established
advantage is confined to gapped books.

`v_e1_vetoed` scoring below plain E1 is not evidence against the risk veto. Zeroing a score
destroys ranking information, and directional AUC is the wrong test for a quantity whose
purpose is to avoid a bad exit. The veto needs a cost-and-path measurement, which is
**Stage 10.5**, not this.

### The controls all four candidates passed

| control | result |
|---|---|
| **C1** constant / random score | 0.5000 and 0.5131 — the metric is not reading the session's 43 % up mix |
| **C2** the past move (P45-4's killer) | `mid_change_1_ticks` **0.487**, `mid_change_w_ticks` **0.455** — below 0.5. This is not "already moved" |
| **C3** per symbol | TLPI(2) above 0.5 in **14 of 14**, median 0.705, range 0.578–0.830 |
| **C4** first half vs second half | 0.691 vs 0.689 — stable |
| **C6** episode-anchored (prereg 300 s cooldown) | 681 episodes, AUC 0.690 vs 0.692 on all rows — not a duplicate-row artefact |

C2 is the one worth pausing on. The confound that collapsed P45-4 from 1.84 to 1.29 is
absent here: the recent move is *weakly contrarian*, so the book's shape is not a proxy for
what just happened.

### What is flat

**Rates of change carry almost nothing at these horizons.** Acceleration sits at
0.500–0.510 across every λ and for E1 — indistinguishable from a coin. Velocity does a
little better (`TLPI(0)_vel` 0.526, `E1_vel` 0.556, best of the family 0.557) but stays far
below the levels it is the derivative of. The *level* of the book's imbalance is
informative and the *derivative* is not — at 60/180/600 s, on this session. That is a
negative finding, and it is recorded so the next round does not re-ask it.

`geom_deep_imb` (the book behind the touch, alone) is 0.525 — consistent with the λ curve:
the deep book is nearly uninformative on its own.

---

## What could not be measured, and why it is not a kill

`micro/sessions/INDEX.json` reports **0 accepted sessions**. The micro DEV set is empty; the
first live session is being captured now. So:

* **No DEV ranking on micro sessions exists yet.** The ranking above is on the 2026-09-06
  *calibration* session, which the prereg marks as already seen and never countable as DEV,
  VALIDATION or HOLDOUT. Measuring on it spends no unseen data — and establishes nothing.
* **No VAL check is possible.** VALIDATION needs the 9th–12th accepted sessions.
* **The block bootstrap the prereg specifies cannot run.** Its block is a whole session and
  there is one, so `ci_kind` reads `within_session_rows_only` on every row of `CANDIDATES.csv`.

All 132 evaluations are **INSUFFICIENT_SAMPLE**. That is the correct status and it is
**not** KILLED: nothing here has been looked for and found absent.

Nothing in the prereg was touched. θ = 0.20 is imported and never fitted; the outcome,
universe, splits and horizons are reproduced verbatim; the sealed holdout was not read, and
`check_sessions` refuses to run at all if the seal ledger cannot be verified.

---

## The falsifiable prediction this leaves

The gapped-book result makes a specific, checkable claim for the DEV sessions:

> **TLPI(λ≈1–2) will beat a rank-decayed imbalance on gapped books and not on symmetric
> ones, and the margin will scale with `ladder_mismatch_ticks`.**

If the margin appears on symmetric books too, the mechanism is wrong and something else is
producing it. If it appears nowhere, the family collapses to E1 and should be dropped. Both
outcomes are informative, and both are decided by data that does not exist yet.

## Next

1. **Accumulate DEV sessions.** Nothing above can be promoted past INSUFFICIENT_SAMPLE
   until there are at least 3 blocks, and the prereg wants 8.
2. **Re-run this exact pipeline on DEV**, unchanged, and check the gapped-book prediction.
3. **Stage 4** (market → sector → share) — `c_e1_minus_market` scoring level with raw E1
   suggests the cross-sectional part is doing real work and deserves its own tier.
4. **Stage 10.5** before the risk veto can be evaluated at all.
5. **HOLDOUT stays sealed.**
