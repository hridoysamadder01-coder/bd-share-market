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
  bdlib/          config · io · qa · features · labels · panels · costs
  data/           raw/ (owner's DSE EOD CSVs, git-ignored) · synthetic/ (test fixture)
  qa/             run_qa.py · verify_detectors.py · EXCLUSIONS.csv
  features/       run_features.py · leakage_test.py
  prior_rounds/   Round 2 script + full output, preserved verbatim
  state_engine/   Phase 3 rungs 1–2 (run_states.py · verify_state_causality.py)
  experiments/    Phase 4 (phase4_precursors.py) · Phase 4.5 (phase45_footprints.py,
                  verify_footprint_causality.py) · rerun_saleability_killed.py
  results/        csv ledgers (committed) · parquet (regenerated, git-ignored)
  reports/        DATA_QA_REPORT · PHASE4_PRECURSOR_REPORT · PHASE45_DOORSTEP_REPORT
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

## Real DSE data (in place since 2026-09-01)

Owner-supplied end-of-day CSVs live in `data/raw/merged_eod/` (git-ignored — 36 MB
of the owner's own data; `data/raw/RAW_MANIFEST.json` carries a sha256 per file so
any result is traceable to the exact input).

```bash
python3 data/ingest_dse_eod.py                       # 424 CSVs → 392 equity symbols
python3 run_phase1_2.py --input data/raw/dse_eod.parquet --tag dse_eod \
                        --frequency DAILY --leak-symbols 25
```

**It is daily (EOD) data, not minute.** Every window in the feature layer is
therefore in days. 861,256 usable bars, 2012-10-01 → 2026-01-22.

To add a different dataset, drop CSV/parquet with columns
`symbol, ts, open, high, low, close, volume` (+ `turnover`, `trades` if the
exchange reports them — a derived `close×volume` turnover is marked as derived in
every report) and pass `--input`.

**Before any conclusion**, the unverified-convention flags in `bdlib/config.py`
(session hours, holiday calendar, corporate actions, tick rules, brokerage) must
be confirmed against official DSE sources. Every QA report prints them.

Phases 3–4.5 on the real data:

```bash
python3 state_engine/run_states.py --tag dse_eod          # Phase 3 rungs 1–2, per panel
python3 state_engine/verify_state_causality.py            # state-layer no-lookahead proof
python3 experiments/phase4_precursors.py --entry open     # Phase 4 (+ --entry close variant)
python3 experiments/phase45_footprints.py --tag dse_eod   # Phase 4.5 doorstep footprints (v2)
python3 experiments/verify_footprint_causality.py         # footprint/outcome causality proof
```

**The Phase 5 holdout (2019-01-01 → 2022-07-27) is sealed** — `phase45_footprints.py`
drops it at load and asserts. Do not run anything on it until Phase 5.

## Status

`RESEARCH_STATUS.md` — current phase, what is proved, what is not.
`REJECTED_CANDIDATES.md` — permanent, append-only (Round 2, Phase 4, Phase 4.5).
`SURVIVING_RESEARCH_LEADS.md` — what goes to Phase 5: **one negative-mean family
(Phase 4) and one door-probability footprint (Phase 4.5). Nothing tradeable.**
`DOORSTEP_FOOTPRINT_DESIGN.md` — the Phase 4.5 design, its amendments, and the
v2 corrections after adversarial review, all disclosed.

## What Round 2 already settled (do not re-propose without a reason)

Volume-spike + breakout: **never worked** (~11,000 trades, negative in every
period). Panic-day rebound: **not capturable** — it lives entirely in the
overnight gap. The best outside candidate was originally closed by an *assumed*
T+2 sell block; that assumption is withdrawn (saleability is UNKNOWN) and the
candidate was re-measured — still not promotable, now on evidence (t = 0.97, no
replication, contradicted post-break). Details in `REJECTED_CANDIDATES.md`.
