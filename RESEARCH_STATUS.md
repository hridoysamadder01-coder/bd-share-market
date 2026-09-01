# RESEARCH_STATUS — Bangladesh (DSE) market-structure track

> Snapshot **2026-09-01**. Only what is true today.

## Headline

| | |
|---|---|
| Phase | 1–2 complete, now running on **real DSE data** |
| Data | 392 equity-like symbols · 861,256 usable daily bars · 2012-10-01 → 2026-01-22 |
| Bar frequency | **DAILY (EOD)** — minute data was requested; what exists is end-of-day |
| Research claims | **zero** |
| Surviving leads | **none** (`SURVIVING_RESEARCH_LEADS.md`) |
| Prior rounds | Round 2 preserved verbatim (`prior_rounds/`, ledgered in `REJECTED_CANDIDATES.md`) |
| Isolation from the OYSHE HFT system | ✅ no shared file, import or build |

## What the real data turned out to be

| Finding | Number | Why it matters |
|---|---|---|
| **Coverage regime break 2024-02-22** | reporting symbols **381 → 88** | The dataset is stitched from two sources. Every cross-sectional feature (`xs_*`, `market_ret`) changes basis on that day; a period straddling it cannot be compared with one that does not. Round 2's "P3 = 94 symbols" is the same break seen from the other side. |
| Second break 2020-03-25 | 327 → 243 | Coincides with the COVID-era suspension; separate regime, not a market signal. |
| Locked / one-price bars | **81,474** (9.5%) | Nearly one bar in ten has no intraday range at all. |
| Bars in locked runs ≥ 5 days | 68,235 | Sustained pinning, not isolated quiet days. |
| Bars in stale-close runs ≥ 30 days | 54,075 | A month or more of an unchanged closing price. |
| Floor-era bars (2022-07-28…2024-01-31) | **119,520** (13.9%) | A distinct regime; pooling it with free periods mixes two markets. |
| Corporate-action suspects | 309 | Unadjusted — flagged, never repaired (no action table exists). |
| Hard exclusions | **817** of 862,073 (0.09%) | 775 Saturday stamps, 41 OHLC-inconsistent, 3 non-positive prices. |
| Symbols below 80% day coverage | 180 | Survivorship is not optional here — it is the norm. |

Full detail: `reports/DATA_QA_REPORT.md`; per-row reasons in `qa/EXCLUSIONS.csv`.

## What is built and proved

**Phase 1 — data foundation.** Per-row reason codes; market states (zero volume,
locked bars/runs, stale runs, corporate-action suspects, floor era) flagged and
**kept**; coverage, thin days, listings, survivorship, and now **coverage regime
breaks** reported. Nothing is ever repaired.
*Proof:* `qa/verify_detectors.py` plants 9 defect classes and reconciles planted
vs detected **exactly**, and checks that no input row is silently dropped.

**Phase 2 — adaptive feature layer.** 29 features, self-normalised against each
symbol's strictly trailing baseline plus same-timestamp cross-sectional context;
16 forward-looking labels in a **separate file**, asserted disjoint.
*Proof on the real data:* `features/leakage_test.py` corrupted every bar after a
cut date and required all 29 feature values at earlier bars to be bit-identical —
**5 cuts across 25 symbols / 52,118 bars, all clean**, with a deliberately leaky
control (`close.shift(-1)`) **caught**, so the passes are not vacuous.

## Four real defects the real data exposed (all fixed, all ledgered)

| # | Defect | What it produced before the fix |
|---|---|---|
| I-001 | robust z divided by `MAD + eps` | z = 4·10¹³ on degenerate baselines |
| I-002 | Amihud divided by `turnover + eps` | 1.7·10¹⁷ on zero-turnover bars |
| I-004 | **z-scoring raw volume levels** | `rel_volume_z` ≈ 5·10⁶ for a symbol whose trailing median volume was ~1 share — now measured in **log space**, with a 20-traded-day validity guard |
| I-005 | `σ_short / (σ_long + eps)` | 4,363 during floor-era pinning — now NaN below price-grid resolution |
| I-006 | unbounded z-family | `range_z` = 1,600 — now winsorised at ±20 with per-feature clip counts reported (2,827 cells = 0.011%) |

Every one of these would have silently poisoned downstream means, thresholds and
models rather than failing loudly. They are permanent entries in
`REJECTED_CANDIDATES.md`.

## Structural constraints inherited from Round 2 (now enforced in config)

- **T+2 settlement** — a return measured at an exit earlier than entry + 2
  sessions was never capturable. Round 2's decisive result: a candidate showing
  +1.05% net at an e+1 exit decays to +0.32% (t = 0.43) at the first *legal* exit.
- **No short selling** — the real Q1−Q5 spread (+1.49%, t = 3.15) is not tradeable
  long-only.
- **Floor era** must be separated, never pooled.
- **~1% round-trip cost**, capital-gains tax **unknown** (modelled as 0, so every
  net figure is optimistic by an unknown amount).

## What is NOT built

Phase 3 (state formation engine — designed in `STATE_ENGINE_DESIGN.md`),
Phase 4 (precursor research with failed-footprint denominators), Phase 5
(walk-forward), Phase 6 (economics). `STATE_EVENT_LOG`,
`FAILED_FOOTPRINT_ANALYSIS.csv`, `PRECURSOR_CANDIDATES.csv` and
`WALK_FORWARD_RESULTS.csv` therefore do not exist and have not been fabricated.

## Open gaps (owner action)

| # | Gap | Effect |
|---|---|---|
| D-1 | **Minute-level data** — only EOD exists | Intraday structure formation is unobservable; all windows are in days |
| D-2 | The 2024-02-22 coverage break | Either obtain the missing symbols' post-Feb-2024 history, or every cross-sectional study must stop at 2024-02-20 and say so |
| D-3 | Session hours + holiday calendar unverified | 775 Saturday rows are excluded on an *assumption*; if DSE held special Saturday sessions, those are real bars |
| D-4 | No corporate-action table | 309 gap suspects unadjusted |
| D-5 | Brokerage/charges + capital-gains tax unverified | Phase 6 economics cannot be finalised |

## Honest non-claims

No pattern · no precursor · no edge · no profitability. Phases 1–2 are a
measuring apparatus. The only market statements in this workspace are Round 2's,
and all of them are **negative**.

## Reproduce

```bash
cd bd_research
python3 data/ingest_dse_eod.py                       # CSVs → normalised parquet + manifest
python3 run_phase1_2.py --input data/raw/dse_eod.parquet --tag dse_eod \
                        --frequency DAILY --leak-symbols 25
```
