# RESEARCH_STATUS — Bangladesh (DSE) market-structure track

> Snapshot **2026-09-01**. Only what is true today.

## Headline

| | |
|---|---|
| Phase | 1–2 complete · Phase 3 rungs 1–2 built · **Phase 4 complete** |
| Data | 392 equity-like symbols · 861,256 usable daily bars · 2012-10-01 → 2026-01-22 |
| Bar frequency | **DAILY (EOD)** — minute data was requested; what exists is end-of-day |
| Research claims | **zero tradeable**; one unvalidated NEGATIVE information finding |
| Tradeable candidates | **none** — 0 of 750 pre-registered hypotheses survived verified brokerage + FDR |
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

## Structural constraints (corrected 2026-09-01)

- **Saleability is UNKNOWN, and is no longer a kill reason.** Settlement (T+2) is
  a clearing mechanic; whether the broker permits an earlier sale is a different
  mechanic and is unverified. `EARLIEST_SALEABILITY_DAYS = None`. Round 2 closed
  a candidate on the assumed version of this rule; that kill has been withdrawn
  and the candidate re-measured (below). **Owner action: confirm actual
  LankaBangla / DSE account behaviour.**
- **Costs in layers, never one number.** Verified brokerage **0.8% round trip**
  is reported separately from an **estimated** band (+0.2% / +0.4%);
  capital-gains tax and slippage are UNKNOWN and not modelled.
- **No short selling** — a Q1−Q5 spread is not tradeable long-only.
- **Floor era** (2022-07-28…2024-01-31) separated, never pooled.
- **Coverage panels never pooled** — `bdlib/panels.py` raises rather than warns
  if a cross-sectional aggregate spans 2024-02-22.

## Re-run of the candidate killed by the assumed T+2 block

`experiments/rerun_saleability_killed.py` re-measured it with no exit horizon
excluded. PRE-FLOOR, 42 events: the same-session exit is **negative**
(−0.47% gross); the only pre-cost profit is Q1 (most-oversold fifth) at the
next-close exit, **+1.49% gross → +0.69% after verified brokerage, t = 0.97** —
not significant, thinning to +0.29% under a 0.4% cost estimate. The POSTBREAK
panel (5 events) is negative throughout.

It also **does not replicate** Round 2's claim that the profit sat in the
same-session window (they reported +2.05% gross there; this implementation finds
it negative). That discrepancy is recorded, not smoothed — neither version is
treated as established until traced.

**Outcome: still not promotable — but now on evidence (weak t, no replication,
contradicted post-break) rather than on an unverified assumption.**

## Phase 3 — state formation engine (rungs 1–2)

`state_engine/run_states.py` emits **861,256 state observations** — PRIMARY
819,534 / POSTBREAK 41,722, fitted and written separately. Rung 1 is the
explainable univariate departure baseline; rung 2 is multivariate novelty (RMS of
the per-symbol z vector), bucketed by each symbol's **own trailing-250 percentile**
into CALM / DRIFT / DEPARTURE / EXTREME. No BUY/SELL is emitted and no outcome
label is consulted while a state is defined.

*Causality proved on the state layer too:* `verify_state_causality.py` — 4
future-corruption cuts, every state field identical before the cut, positive
control caught.

*First observation (not a result):* elevated states last a **median of 1 session**
(longest 30). Departures on DSE daily data are overwhelmingly transient — which
is precisely what Phase 4 has to price before anything can be called a precursor.

## Phase 4 — precursor research (complete)

750 pre-registered hypotheses, panel-separated, date-clustered inference,
benchmarked against the **same-day** base rate, BH-FDR corrected, and run twice
under two entry conventions. Full report: `reports/PHASE4_PRECURSOR_REPORT.md`.

**Result 1 — nothing tradeable.** Zero cohorts positive after the verified 0.8%
brokerage and FDR, in either variant. 91 / 38 cohorts had statistically real
*positive* excess; none survived brokerage alone.

**Result 2 — real information exists, and it is negative.** Sustained
abnormal-activity departure underperforms the same-day market by 0.44%–1.57% at
h = 10 (t = −8 to −11 across ~2,000 dates), stable across both entry conventions.
Not monetisable — no short selling — so its only possible use is avoidance, and
only after Phase 5.

**Result 3 — rung 2 has not earned its complexity.** Plain abnormal volume
(rung 1) carries this at least as well as the multivariate novelty states.
Rungs 2b–5 are therefore NOT to be built on this evidence.

**A data question found on the way:** the `open` field has a mean overnight gap
of +0.385% against a median of exactly 0, with 66% of nonzero gaps upward, stable
across price buckets and robust to trimming. Provenance unresolved. Every
open-entry number inherits a ≈−0.4% intraday drag, which is why the entire phase
was also run with a close-entry variant that avoids `open` entirely.

## What is NOT built

Phase 3 rungs **2b–5** — and Phase 4 gave a positive reason NOT to build them
(rung 1 matches rung 2). Phase 5 (walk-forward) and Phase 6 (economics) are not
built; `WALK_FORWARD_RESULTS.csv` does not exist and has not been fabricated.
`PRECURSOR_CANDIDATES.csv` exists but is **empty by construction** — nothing
qualified.

## Open gaps (owner action)

| # | Gap | Effect |
|---|---|---|
| D-1 | **Minute-level data** — only EOD exists | Intraday structure formation is unobservable; all windows are in days |
| D-2 | The 2024-02-22 coverage break | Handled by panel separation (PRIMARY is primary, POSTBREAK separate, never pooled). Obtaining the missing symbols' post-Feb-2024 history would restore statistical power after the break — POSTBREAK currently yields only 5 usable events for an event study |
| D-6 | **Earliest saleability UNKNOWN** | Decides whether short holds are tradeable at all. Verify against a real LankaBangla / DSE account: can a position bought today be sold today, tomorrow, or only after settlement? *(Phase 4 note: it did not change any conclusion — nothing was tradeable at ANY horizon, including the unrestricted ones.)* |
| D-7 | **`open` field provenance** | Mean overnight gap +0.385% vs median 0, 66% of nonzero gaps upward, stable across price buckets. Is this real DSE opening behaviour or a data-construction artefact? Until answered, open-entry numbers are provisional — hence the close-entry variant |
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
python3 state_engine/run_states.py --tag dse_eod     # Phase 3 rungs 1–2, per panel
python3 state_engine/verify_state_causality.py       # state-layer no-lookahead proof
python3 experiments/rerun_saleability_killed.py      # the withdrawn-T+2 re-measurement
python3 experiments/phase4_precursors.py --entry open    # Phase 4
python3 experiments/phase4_precursors.py --entry close   # robustness variant
```
