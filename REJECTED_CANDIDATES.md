# REJECTED_CANDIDATES

> Permanent ledger. A rejected idea is evidence and is never deleted — deleting
> rejections is how a research programme fools itself into rediscovering the same
> dead end. Append only.

## Prior rounds (Round 1 / Round 2)

**No Round 1 / Round 2 artifacts exist in this repository.** A search of the tree
(`bd_research/`, any `*dse*`, `*bd_*`, `*bangladesh*` path) returned nothing before
this workspace was created on 2026-09-01. Whatever was explored in earlier chat
sessions was never committed, so it cannot be preserved from disk.

**Owner action:** paste or upload any earlier findings — especially the things that
DID NOT work — and they will be entered below verbatim with their original dates.
Until then this section is honestly empty rather than reconstructed from memory.

## Rejected research candidates

| # | Date | Candidate | Why rejected | Evidence |
|---|---|---|---|---|
| — | — | *(none yet — no research claim has been made)* | — | — |

## Rejected implementation choices (kept so they are not retried)

| # | Date | Choice | Why rejected | Evidence |
|---|---|---|---|---|
| I-001 | 2026-09-01 | Robust z with denominator `1.4826·MAD + eps` | Locked / zero-volume stretches give MAD = 0, so z reached 4·10¹³ and would have poisoned every downstream mean, threshold and model | Observed on the synthetic fixture; replaced by a 1% relative scale floor plus NaN when the baseline is degenerate |
| I-002 | 2026-09-01 | Amihud impact as `\|ret\| / (turnover + eps)` | Zero-turnover bars produced 1.7·10¹⁷ | Caught by the numeric-sanity gate in `features/run_features.py`; now NaN when turnover = 0 |
| I-003 | 2026-09-01 | Fixture that set a stale close without re-bracketing high/low | Planted a second, unintended defect and corrupted the planted-vs-detected accounting (39 detected vs 5 planted) | `qa/verify_detectors.py` now reconciles counts exactly |
