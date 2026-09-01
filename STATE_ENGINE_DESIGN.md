# STATE_ENGINE_DESIGN — Phase 3 (rungs 1–2 BUILT · rungs 2b–5 designed)

> Status 2026-09-01: **rungs 1–2 BUILT and causality-proved** on real DSE data,
> per panel. Rungs 2b–5 remain designed only. This document fixes the contract
> Phase 3 must satisfy so that it cannot quietly become a signal generator.
>
> Built: `state_engine/run_states.py` → `results/STATE_EVENT_LOG.parquet`
> (861,256 state observations: PRIMARY 819,534 / POSTBREAK 41,722, written and
> fitted separately, never pooled).
> Proved: `state_engine/verify_state_causality.py` — 4 future-corruption cuts,
> `novelty`, `novelty_pct`, `state`, `state_age`, `elevated_run` all identical
> before the cut, and a `novelty.shift(-1)` positive control **caught**.

## What the engine must and must not emit

**Emits:** state observations — `(symbol, ts, state_id, novelty_score, evidence)`.
**Never emits:** BUY / SELL / target / stop. A state is a description of the
market, not an instruction. The conversion of state → position is Phase 6, and
only after Phase 4 has shown the state carries information.

## The distinction being enforced

*Pattern matching* asks "does this window look like that historical window?"
*Pattern formation recognition* asks "is this symbol's joint behaviour departing
from its own recent regime, and has that kind of departure preceded anything?"
Two departures need not look alike on a chart to be the same state.

## Build order — simplest first, complexity only on evidence

1. **Univariate departure (baseline).** ✅ BUILT — `rung1_volume_departure`,
   `rung1_range_compression`, `rung1_quiet_accumulation` over the existing
   z-features. Fully explainable; it is the control every later method must beat.
2. **Multivariate distance.** ✅ BUILT as the diagonal case — RMS of the
   per-symbol-normalised z vector `[rel_volume_z, rel_turnover_z, range_z,
   amihud_z]` (≥3 components required, else NaN), then bucketed by the symbol's
   OWN trailing-250 novelty percentile into CALM / DRIFT / DEPARTURE / EXTREME.
   Independence is assumed deliberately; the full rolling covariance is rung 2b
   and must earn its cost by beating this on held-out data.
   *First observation (not a result):* elevated states last a median of **1
   session** — departures on DSE daily data are overwhelmingly transient, which
   is itself the thing Phase 4 has to price.
3. **Change-point detection.** Online CUSUM / Bayesian online change-point on the
   multivariate score; the *timing* of the regime break is the object of interest.
4. **State segmentation / online clustering.** Only if 1–3 leave structure
   unexplained: cluster centroids fitted on TRAIN, assigned causally afterwards.
5. **State-transition analysis.** Which state sequences precede which outcomes —
   this is where "formation" (a path through states) beats "a state".

Each step must beat the previous one on held-out data before the next is built.
A step that does not beat its predecessor is recorded in `REJECTED_CANDIDATES.md`
and the ladder stops there.

## Hard requirements

- **Causal fitting.** Any parameter (mean, covariance, centroid, threshold) is
  estimated on data strictly before the bar it is applied to. Refits are
  expanding-window or rolling-window, never full-sample.
- **The same leakage test applies.** `features/leakage_test.py` must pass with the
  state columns added — future corruption must not change any earlier state label.
- **Degenerate baselines propagate as NaN**, exactly as in Phase 2. A state
  computed against a locked/zero-volume window is "unknown", not "normal".
- **Every state event is logged**, including the boring ones. `STATE_EVENT_LOG`
  must contain the full population of states, because Phase 4's denominator is
  every occurrence — not the interesting ones.
- **No outcome may be consulted** while defining or tuning a state. State
  definitions are fixed before labels are joined.

## Planned outputs

`state_engine/run_states.py` → `results/STATE_EVENT_LOG.parquet` with columns
`symbol, ts, panel, novelty, novelty_components, novelty_pct, state, state_age,
is_transition, elevated_run, top_component, rung1_*, xs_novelty_rank,
xs_share_departure, xs_symbols` — plus `manifests/state_engine_manifest.json`
recording every parameter, which rungs are built, and that no order is emitted
and no label was consulted.

### Panel discipline (added 2026-09-01)

States are built and written **per panel** (`bdlib/panels.py`), and
`assert_single_panel()` raises rather than warns if a frame handed to a
cross-sectional aggregate spans the 2024-02-22 coverage break. PRIMARY
(2012-10-01…2024-02-20, full universe) is the primary panel; POSTBREAK
(2024-02-22…, ~88 symbols) is reported separately and never pooled with it.
