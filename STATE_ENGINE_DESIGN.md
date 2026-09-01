# STATE_ENGINE_DESIGN — Phase 3 (DESIGNED, NOT YET IMPLEMENTED)

> Status 2026-09-01: **not built**. Phases 1–2 are in place and verified; this
> document fixes the contract Phase 3 must satisfy so that it cannot quietly
> become a signal generator.

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

1. **Univariate departure (baseline).** Threshold/persistence over the existing
   z-features. Fully explainable; it is the control every later method must beat.
2. **Multivariate distance.** Mahalanobis distance of the current feature vector
   from the symbol's own trailing distribution (covariance estimated on
   [t−W, t−1] only). One number: "how far outside its own recent cloud".
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
`symbol, ts, state_id, novelty_score, contributing_features, state_age,
regime_label, is_transition` — plus a manifest recording every parameter and the
train/validation boundary in force.
