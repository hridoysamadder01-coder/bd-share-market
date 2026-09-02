# SURVIVING_RESEARCH_LEADS

> A lead is promoted to a *tradeable* candidate ONLY after it survives all three
> gates: a complete failed-footprint denominator (Phase 4 / 4.5), walk-forward
> on the untouched holdout (Phase 5), and realistic costs (Phase 6). Nothing
> reaches that status by looking promising.

## Tradeable candidates

**NONE.**

## What goes to Phase 5 — two objects, one sealed holdout, no double-dipping

The holdout **2019-01-01 → 2022-07-27** (PRIMARY panel) is sealed; Phase 4.5
drops it at load and asserts. Round 2 and Phase 4 inspected it descriptively
before the seal existed (both ledgered); no footprint or threshold below was
tuned on it. Phase 5 is **three pre-registered tests on that window, FDR over
three**, run once.

### Lead A (Phase 4) — sustained abnormal-activity departure as a NEGATIVE mean predictor

| | |
|---|---|
| Object | mean market-relative return over h = 5–10 sessions after `rung1_volume_departure` (and the DEPARTURE→DEPARTURE→DEPARTURE path) |
| Discovery evidence | excess −0.44% to −1.57% at h = 10, t = −8 to −11 across ~2,000 dates, both entry conventions (full-sample descriptive, 2012–2024) |
| Phase 5 test | close-entry convention, PRIMARY panel, holdout only; pass = same sign, date-clustered Newey-West t ≤ −3 |
| Use if it survives | avoidance / exclusion filter only — no short selling in Bangladesh |
| Status | UNVALIDATED |

### Lead B (Phase 4.5) — abnormal volume on a market-quiet day → abnormal up-move within 3 sessions

| | |
|---|---|
| Footprint | `F07_idio_activity`: `rel_volume_z ≥ 2` on a day when ≤ 5% of names are abnormal-volume |
| Outcome | max cumulative return over (t, t+3] ≥ max(2.5·σ_prev·√3, 5%) |
| Population | `fresh_both` — no door of either direction, no corporate-action suspect in t−5 … t |
| Discovery evidence (2012–2018) | hit rate **5.60%** vs 2.59% same-day/same-σ names (lift **2.16**, NW t 6.6) and vs 3.04% same-size-of-move-today names (lift **1.84**, NW t 5.4); **145 distinct events, 101 symbols**; 6 of 6 years; all price buckets; floor era 2.80 (t 4.1); increment (2,3] passes; beats plain abnormal volume with bootstrap lower bound **1.05** and the stronger "already moved" reference with 1.08 |
| Secondary horizon | run20 within 5 sessions (54 events, lift 2.48, increment passes) |
| Phase 5 test | same definitions on the holdout: vol-matched **and** shock-matched lift ≥ 1.5, NW t ≥ 3, distinct doors ≥ 30, bootstrap lower bound of the ratio vs both references > 1 |
| What it is not | not accumulation (price is not calm — the calm variant is weaker), not intent, not tradeable (no cost layer; fails 94% of the time) |
| Status | UNVALIDATED — `results/PHASE5_CANDIDATES.csv` |

**Why the margin matters.** F07 is plain abnormal volume on the 64% of days
when the rest of the market is quiet. Its edge over abnormal volume on *any*
day is thin (lower bound 1.05). If Phase 5 shows F07 ≈ F15 on the holdout, the
honest reading is "abnormal volume raises near-term variance" — already known
from Phase 4 — and the doorstep question on daily data closes.

## What is explicitly NOT carried

Everything in `REJECTED_CANDIDATES.md` §Phase 4.5: quiet accumulation,
absorption / dip-recovered, closing strength, persistence, idiosyncratic moves,
every v1 down-door candidate, and every limit-up footprint.

Detail: `reports/PHASE45_DOORSTEP_REPORT.md`, `reports/PHASE4_PRECURSOR_REPORT.md`.
