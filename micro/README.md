# micro/ — DSE microstructure advantage: prospective evidence (DATA BRANCH ONLY)

This branch exists **only** to give the prospective microstructure test a durable home.
It never touches `main`, the research code, or any EOD research artifact.

- `MICRO_PREREG.json` — the FINAL frozen pre-registration (v2).
  SHA256 `169935bdb2ef772c6944da12d1afd691c0c2a1b685281ff16b01085d3c7ac01d`
  (v1 `2c8ed7b1d934e3e76fae480cda2960a87c5efdb5b702e65659e0a17dc6842c02`, superseded, kept verbatim
  with a full amendment record inside v2 — both amendments were made before any prospective frame existed).
- `engine/` — the frozen feature / baseline / model / evaluation code. Reused, never re-tuned.
- `sessions/<DATE>/` — per-session `SESSION.json` (acceptance record) + `features.parquet`.
- `raw/<DATE>.tar.gz` — raw capture evidence (hash-chain verified at capture time).

## Accumulation state

A session counts only if it passes the frozen acceptance criteria
(hash chain verifies, >= 2000 fused frames, >= 12 of 14 symbols, >= 3.0 h span,
<= 40% quality-gate failures). Split assignment is by ARRIVAL ORDER of ACCEPTED sessions:

| slot | sessions |
|---|---|
| DEV | first 8 accepted |
| VALIDATION | next 4 accepted |
| FINAL HOLDOUT | subsequent accepted, until >= 100 independent episodes AND >= 5 sessions |

`sessions/INDEX.json` is the authoritative counter. **FINAL HOLDOUT outcomes must not be read
until its denominator is complete.**

## Working on this branch

Raw is large; skip it when you only need to accumulate:

```bash
git sparse-checkout set micro/engine micro/sessions
```
