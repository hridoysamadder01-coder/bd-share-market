<!--
DISCOVERY ROUND 1 — append-only, like PROJECT_MAP.md and ROADMAP.md.
Discovery output, NOT a result. Nothing here has been through Stage 11 falsification,
nothing here is tradeable, and the sealed holdout was not opened to produce it.
-->

# DISCOVERY ROUND 1 — what state tends to precede a move on DSE

**2026-09-08.** First aggressive search over the DEV panel. 28 candidates × 3 horizons
× 3 regime slices = 252 evaluations. **26 PROMISING · 92 WEAK · 134 KILLED.**

> **Discovery ≠ proof.** A PROMISING row means *conditional structure worth mutating*,
> measured against a matched control on a real denominator. It does not mean an edge
> exists. Stage 11 is where survivors get the falsification battery.

## What was and was not available

| | |
|---|---|
| DEV rows | **586,657** (392 symbols, 2012-10-01 → 2026-01-22) |
| Sealed holdout dropped | **274,599 rows**, 2019-01-01 → 2022-07-27 — asserted absent, not merely filtered |
| Reserved slice | 2026-01-25 → 2026-09-07 — the single unseen test, untouched |
| Look-ahead columns removed as inputs | `flag_locked_run`, `flag_stale_run` — `bdlib/qa.py:40` stamps a run's whole length onto every day of it |
| Regime slices, never pooled | PRIMARY/free 425,415 · PRIMARY/floor_era 119,520 · POSTBREAK/free 41,722 |

Panels come from the existing `bdlib/panels.py` unchanged — the 2024-02-22 coverage break
(381 → 88 symbols) is enforced by `assert_single_panel()`, which raises rather than warns.

## How a candidate was measured, and why

`REJECTED_CANDIDATES.md` records exactly how this goes wrong, so the evaluator is built
against those failures:

* **I-012** — 102 "hits" were 28 distinct events. Here every count is **distinct
  (symbol, date) events**, never occurrences.
* **I-013** — "lift > 1" passed rows by under 10 %. Here the discovery bar is lift ≥ 1.25
  **and** positive excess forward move.
* **P45-4** — a footprint collapsed 1.84 → 1.29 once *today's own move* entered the
  comparison. Here every candidate is measured against a **same-date, same-volatility-quintile
  control**: the other symbols trading that day with comparable realized volatility.

That last control is what makes the headline result mean anything.

## The finding

**A stock that CLOSES at its daily price band behaves differently over the next 5
sessions. A stock that goes 80 % of the way to the band and does not close there
does not.**

| | events | fwd 5d | adverse 5d | hit rate | control | lift | excess |
|---|---|---|---|---|---|---|---|
| **C1** closed AT the band | 2,873 | **+3.00 %** | −5.25 % | 50.3 % | 37.9 % | **1.33** | **+3.26 pp** |
| **C2** approached, did NOT close there | 11,944 | −0.46 % | −6.39 % | 36.1 % | 37.2 % | **0.97** | −0.47 pp |

*(PRIMARY/free, 2012-10-01 → 2024-02-20, horizon 5 sessions)*

C2 is the "already moved" control, and it is **KILLED on 11,944 events**. So the structure
is not "this stock moved a lot today" — the confound that ended P45-4. Something about
*finishing* at the band, not about the size of the day, carries it.

It replicates on the independent floor-era slice (991 events, lift 1.478, excess +3.12 pp)
and weakly on POSTBREAK (335 events, lift 1.22). It decays monotonically with horizon —
h=5 PROMISING → h=15 WEAK → h=30 KILLED — which is what real structure looks like and
noise does not.

### This is not P45-8 re-asked

P45-8 asked *"can a footprint predict a limit-up?"* and answered no. This asks
*"what follows a band close?"* — the opposite direction, and it uses **distance to the
daily band**, an input no P45 candidate had (`REJECTED_CANDIDATES.md` P45-1…P45-8 are all
price and volume).

### Mutation round — what sharpens it and what does not

| id | mutation | events | excess | lift | reading |
|---|---|---|---|---|---|
| **M5** | **second consecutive band close** | 653 | **+5.19 pp** | 1.37 | continuation *strengthens*; it is not spent |
| M2 | band close on abnormal volume | 1,340 | +3.15 pp | 1.35 | volume adds a little |
| M6 | band close while the market **fell** | 1,135 | +2.91 pp | 1.33 | — |
| M7 | band close while the market **rose** | 1,225 | +3.17 pp | 1.28 | — |
| M1 | first band close in 20 days | 1,675 | +2.57 pp | 1.32 | freshness does not help |
| M8 | band close, few other symbols abnormal | 632 | +2.49 pp | 1.30 | — |

**M6 ≈ M7 is a real negative finding**: it makes almost no difference whether the market
rose or fell that day, so this is not market beta wearing a disguise.

**M5 is the most interesting mutation** — the *second* consecutive band close carries a
larger excess than the first. That is the opposite of exhaustion.

### The baselines earn their place by failing informatively

The P45 references were carried deliberately. On the floor era, plain abnormal volume
(F15) scores **lift 1.544 — higher than most candidates — with excess −0.117 pp**. It
clears a +1 % threshold more often purely through volatility, in *either* direction. That
is why hit-rate lift alone is not the measurement and excess move is the discriminator.

## The caveat that decides whether any of this is real

**A stock closing at its upper band is usually locked. You very likely cannot buy at that
close.** Every number above is measured from a close that may not have been fillable, so
the forward return may not be capturable at all. Nothing here is tradeable until that is
settled, and settling it is exactly **Stage 10.5 (Cost & Liquidity Model)** in `ROADMAP.md`.

Two further limits, stated rather than buried:

* **The adverse move is larger than the forward move.** +3.0 % forward against −5.25 %
  adverse at h=5. Any position logic must survive that path, not just the endpoint.
* **The band ladder is INFERRED.** Today's published ladder is applied to history;
  `RESEARCH_STATUS.md` D-16 records dated limit changes (a 2 % episode from 2021-04-07,
  3 % on 2024-04-24 widening to 10 % on 2024-08-28) that one ladder does not reproduce.
  Rebuilding it per date is the first correction to make.

## What failed

* **Compression → expansion** (A1–A3), the classic coil-then-break: nothing above the bar.
* **Turnover acceleration** (B1), second-difference of turnover: KILLED.
* **Liquidity depletion and absorption** (E1, E2): KILLED.
* **Cross-sectional standout** (F1, F2): high lift, negative excess — the same volatility
  artefact as the baselines.
* **Relative strength** (D1, D2): D2 shows lift 1.44 on the floor era but with excess
  +0.03 pp — a threshold artefact, not direction.
* **Small-n rows kept visible, not promoted**: M4 shows +8.9 pp on **32 events** and C3 on
  **1**. They are in the table so they are not silently dropped, and they decide nothing.

## Next

1. **Rebuild the band ladder per date** from the DSE circular history — removes the one
   INFERRED input under the whole finding.
2. **Fillability** — measure how often a band close was actually buyable, and at what
   price the next session opened. This can kill the finding outright, so it goes first
   among the analytical steps.
3. **Mutate M5 further** — third and fourth consecutive closes, and what ends the run.
4. **Stage 4** market → sector → share decomposition, to ask whether band closes cluster
   by sector.
5. **VAL only after** the above. **HOLDOUT stays sealed.**
