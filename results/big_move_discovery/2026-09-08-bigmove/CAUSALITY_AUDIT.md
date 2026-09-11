# Causality audit — big-move discovery, 2026-09-08

## 1. The leak that was found, and what it invalidated

`research/edge_discovery/families.py:96` and `research/edge_discovery/run.py:199`
both computed cross-sectional market state like this:

```python
f["_tbin"] = f["t_frame"].dt.floor("60s")
f["market_pressure"] = f.groupby("_tbin")["P"].transform("mean")
f["xs_rank"] = f.groupby("_tbin")["P"].rank(pct=True) - 0.5
```

A bucket is not an instant. Every row in the 10:00:00–10:00:59 bucket saw every
other row in that bucket, including rows 50 seconds in its own future. A decision
at 10:00:05 was therefore computed partly from an observation at 10:00:55.

**`CTX_xs_rank_top` was reported at +21.75 pp using this construction.** That
number is withdrawn. It is not "probably still fine" — it was measured with
information the decision could not have had, and no claim resting on it survives
until it is rebuilt.

## 2. What replaces it

`research/bigmove/pit.py::asof_cross_section`. For a decision row (entity i,
time t), the market state uses, for every other entity j, **only j's latest
observation with `t_j <= t`**.

* **Same-instant batches.** Rows sharing a timestamp all enter the state, then all
  read it. This is the only ordering-independent choice. It is not leakage: for a
  daily panel every symbol's close occurs at the same instant, and the decision is
  taken after that instant, not during it. `strict_before=True` tightens to
  `t_j < t` and is available as the adversarial setting.
* **Leave-one-out, always.** Entity i is excluded from its own market, so
  `share_resid_asof` cannot be diluted by the share being measured.
* **Staleness is exposed, never hidden.** An entity that stops printing keeps its
  last value and carries its age; `age_self_s`, `age_median_s`, `age_max_s` and
  `n_stale_dropped` travel with every row. `max_age_s` drops stale entities, and
  no cutoff is tuned — a freshness threshold picked to maximise a result is a
  fitted parameter wearing a hygiene costume.

## 3. Adversarial tests (`tests/test_bigmove_pit.py`, 13 passed)

| test | requirement | result |
|---|---|---|
| **A** | append an extreme observation later in the same minute; every earlier row's output must be bit-identical | PASS |
| **A′** | the *replaced* bucket implementation must FAIL the same perturbation | PASS — it moves, proving the test bites |
| **B** | rows sharing a timestamp give identical results under any input order (4 shuffles) | PASS |
| **B′** | a same-instant batch sees the whole batch, never a running prefix | PASS |
| **C** | an entity whose first print is in the future never enters an earlier snapshot | PASS |
| **C′** | an entity that stops printing persists with a visible age; a cutoff drops it and counts it | PASS |
| **D** | the same raw data replays to a bit-identical hash, including from shuffled input | PASS |

## 4. Daily-panel causality (`tests/test_bigmove_panel.py`, 14 passed)

* **No feature reads the future.** Every row past a cut point is corrupted to NaN;
  all ten tested features before the cut must be bit-identical. PASS.
* **The relative baseline excludes today.** A 1e9-volume day must not dilute its
  own denominator, and must not have touched yesterday's. PASS.
* **A trailing window that jumps a reservation hole is NOT_OBSERVABLE.** Dropping
  the sealed holdout leaves a 3.7-year gap in every symbol; a 20-day mean across
  it would silently mix 2018 with 2022. Features on such a window are NaN, not
  wrong. PASS.
* **Outcomes never cross a symbol boundary.** PASS.
* **No candidate may reference an outcome column.** The candidate source is
  inspected for `fwd_ret`, `mfe_`, `mae_`, `hit_p`, `clean_p`, `days_to_p`. PASS.
* **Deterministic replay.** Identical input, identical feature hash. PASS.

## 5. Reservations, asserted rather than filtered

| window | status |
|---|---|
| 2019-01-01 … 2022-07-27 | sealed Phase-5 holdout — dropped at load, absence asserted |
| 2026-01-25 onward | reserved slice, never seen by any phase — dropped at load, absence asserted |

Panel after reservations: 587,474 rows, 392 symbols, 2012-10-01 … 2026-01-22.

## 6. Cross-sectional statistics respect the coverage break

The universe drops from 381 symbols to 88 at 2024-02-22. `add_causal_xs` computes
the as-of cross-section **per panel** (PRIMARY / POSTBREAK), because a rank that
spans the break compares a share against a market that changed size rather than
against the market.

## 7. What remains uncontrolled

* **Overlapping horizons.** A 30-day outcome measured daily shares 29 days with
  its neighbour. Significance is computed on a per-date difference series with
  Newey-West lags set to the horizon — never on the row count. The row count is
  reported but is not the inferential unit.
* **Multiple comparisons.** 36 candidate families × 8 outcomes = 288 tests, plus
  42 response-curve cells. No family-wise correction is applied in this run; a
  candidate clearing t ≥ 2.5 on one cell out of 288 is not evidence, and the
  ranking must be read with that in mind. Survivors are judged on year
  consistency and bootstrap CI as well as t, which is a weaker control than an
  explicit FDR and is stated here rather than implied.
