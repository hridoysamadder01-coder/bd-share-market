# RESEARCH_STATUS — Bangladesh (DSE) market-structure track

> Snapshot **2026-09-01**. Only what is true today.

## Headline

| | |
|---|---|
| Phase | 1–2 complete (data foundation + adaptive feature layer) |
| Real DSE data in hand | ❌ **none** — the pipeline has never seen a real bar |
| Research claims made | **zero** |
| Surviving leads | **none** (`SURVIVING_RESEARCH_LEADS.md`) |
| Isolation from the OYSHE HFT system | ✅ no shared file, import or build |

## What is built and proved

**Phase 1 — data foundation** (`bdlib/qa.py`, `qa/run_qa.py`)
Detects, with a stable reason code per row: duplicate bars, unparsable
timestamps, missing/non-numeric OHLCV, non-positive prices, negative volume,
OHLC inconsistency, out-of-session and non-trading-weekday stamps. Flags — and
keeps — market states: zero volume, locked/one-price bars, locked runs, stale
price runs, large overnight gaps (corporate-action suspects), session-first bars.
Reports coverage, thin days, late listings, early endings and survivorship
shortfalls. **Nothing is repaired**; exclusions land in `qa/EXCLUSIONS.csv`.

*Proof it works:* `qa/verify_detectors.py` plants 9 defect classes at known rows
and reconciles planted against detected **exactly** — 5/5 OHLC, 3/3 NaN, 2/2
non-positive, 8/8 duplicates, 3/3 out-of-session, 40/40 zero-volume, 15/15
locked, 1/1 corporate-action gap, 44/44 stale-run — plus structural checks
(late listing, thin day, coverage shortfall, no row silently dropped, every
exclusion carries a reason).

**Phase 2 — adaptive feature layer** (`bdlib/features.py`, `FEATURE_DICTIONARY.md`)
28 features, all self-normalised against **each symbol's own strictly trailing
baseline** (window `[t−W, t−1]`, so the current bar never dilutes the baseline it
is judged against), plus same-timestamp cross-sectional context. No RSI, no
MACD, no fixed thresholds. 16 forward-looking labels are built **into a separate
file** and asserted disjoint from the feature set.

*Proof of causality:* `features/leakage_test.py` corrupts every bar after a cut
timestamp and requires all 28 feature values at earlier bars to be **bit-identical**;
8 random cuts, all clean. A deliberately leaky control column (`close.shift(-1)`)
is included and **is caught** — so the passes are not vacuous.

## Two real defects found and fixed while verifying

| # | Defect | Consequence had it shipped |
|---|---|---|
| I-001 | Robust z divided by `1.4826·MAD + eps` | Locked/zero-volume baselines have MAD = 0 → z reached **4·10¹³**, silently poisoning every downstream mean and threshold |
| I-002 | Amihud impact divided by `turnover + eps` | Zero-turnover bars produced **1.7·10¹⁷** |

Both now yield **NaN** ("no measurable baseline"), and `run_features.py` carries a
numeric-sanity gate that fails the build on any infinity or `\|value\| > 10⁶`.
Recorded permanently in `REJECTED_CANDIDATES.md`.

## What is NOT built

- **Phase 3** state formation engine — designed only (`STATE_ENGINE_DESIGN.md`),
  with the build order fixed (univariate departure → multivariate distance →
  change-point → clustering → transition analysis) and the rule that each rung
  must beat the previous one on held-out data before the next is written.
- **Phase 4** precursor research and failed-footprint accounting.
- **Phase 5** walk-forward (train / validation / untouched test).
- **Phase 6** economics (brokerage, charges, slippage, saleability).
- Therefore: `STATE_EVENT_LOG`, `FAILED_FOOTPRINT_ANALYSIS.csv`,
  `PRECURSOR_CANDIDATES.csv`, `WALK_FORWARD_RESULTS.csv` do not exist yet. They
  are listed in the Phase 7 output contract and will be produced by those phases,
  not fabricated now.

## Blocking gaps (owner action)

| # | Gap | Why it blocks |
|---|---|---|
| D-1 | **No minute-level DSE data** | Everything so far ran on a synthetic fixture built to test the machinery. No statement about the Bangladeshi market is possible until real bars exist in `data/raw/`. |
| D-2 | Session hours + holiday calendar unverified | `OUT_OF_SESSION` / `NON_TRADING_WEEKDAY` exclusions are only as right as the assumption; a wrong assumption would silently discard real bars |
| D-3 | No corporate-action table | Splits/bonuses are indistinguishable from real gaps; suspects are flagged, never adjusted |
| D-4 | Tick size / circuit bands unverified | Floor-price and locked regimes can only be detected by proxy |
| D-5 | Brokerage + regulatory charges unverified | Phase 6 economics cannot be computed |

All five print on every QA report, so no result can quietly depend on an
unverified convention.

## Honest non-claims

No pattern · no precursor · no edge · no profitability · nothing about the DSE at
all. The synthetic fixture is a test instrument for the code, and its numbers
describe the code's behaviour, not any market.

## Reproduce

```bash
cd bd_research && python3 run_phase1_2.py
```
