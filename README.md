# bd_research — Bangladesh (DSE) market-structure research workspace

## SCOPE LOCK

This workspace is **isolated from the OYSHE HFT system**. Nothing here imports
from, writes to, or is imported by `/hft`, `/tests`, `/tools`, `/docs` or any
root-level OYSHE module. The international HFT track stays exactly where it is,
paused at its external-access gate (CME CERT, `KNOWN_GAPS.md` B-1/B-2). This is a
different problem, in a different language, on a different market.

## What this is trying to do

Detect **market structure while it is forming** — not match fixed patterns.

The question is never *"does this look like a chart shape that worked before?"*
It is:

1. What changed?
2. Where did it change?
3. How unusual is it **relative to that instrument's own recent state**?
4. Is the change isolated, or is the whole market doing it?
5. Is it persistent or transient?
6. What historically happened after **similar states** — not identical shapes?
7. **How many similar states failed?**
8. Does anything survive unseen data and real costs?

Explicitly not being built: "volume spike + breakout = buy", "RSI < X = buy", or
any fixed rule. No indicator library appears in the feature layer.

## Method rules (binding)

- **Do not assume a profitable pattern exists.** Build the falsification
  machinery first; a signal is a conclusion, never a starting assumption.
- **Never silently repair data.** Detection and exclusion are recorded with
  reason codes; uncertain values are excluded or flagged, never invented.
- **Every occurrence counts.** If a state appeared 1,000 times and 20 preceded a
  move, all 1,000 are in the denominator. Never start from good outcomes and
  search backwards.
- **Causality is proved, not asserted.** `features/leakage_test.py` corrupts the
  future and requires every earlier feature value to be unchanged — with a
  deliberately leaky positive control that the test must catch.
- **Degenerate is NaN, not zero.** No dispersion in a baseline means "unknown",
  never "normal".

## Layout

```
bd_research/
  bdlib/          config · io · qa · features · labels   (shared library)
  data/           raw/ (owner drops real DSE data) · synthetic/ (test fixture)
  qa/             run_qa.py · verify_detectors.py · EXCLUSIONS.csv
  features/       run_features.py · leakage_test.py
  state_engine/   Phase 3 — designed, not built (STATE_ENGINE_DESIGN.md)
  experiments/    Phase 4–5 — not built
  results/        parquet/csv outputs
  reports/        DATA_QA_REPORT.md
  manifests/      one JSON per run: inputs (sha256), params, environment, outputs
```

## Run it

```bash
cd bd_research
python3 data/make_synthetic.py                       # test fixture with planted defects
python3 qa/verify_detectors.py                       # proves the QA detectors fire
python3 qa/run_qa.py --input data/synthetic/synthetic_minute_bars.parquet --tag synthetic
python3 features/run_features.py --input results/synthetic_bars_annotated.parquet --tag synthetic
python3 features/leakage_test.py                     # proves no lookahead
```

Requires `numpy`, `pandas`, `pyarrow`.

## With real DSE data

Drop minute bars in `data/raw/` as CSV or parquet with columns
`symbol, ts, open, high, low, close, volume` (+ `turnover`, `trades` if the
exchange reports them — a derived `close×volume` turnover is marked as derived in
every report). Then run the same commands with `--input data/raw/<file>`.

**Before any conclusion**, the unverified-convention flags in `bdlib/config.py`
(session hours, holiday calendar, corporate actions, tick rules, brokerage) must
be confirmed against official DSE sources. Every QA report prints them.

## Status

`RESEARCH_STATUS.md` — current phase, what is proved, what is not.
`REJECTED_CANDIDATES.md` — permanent, append-only.
`SURVIVING_RESEARCH_LEADS.md` — currently, and honestly, empty.
