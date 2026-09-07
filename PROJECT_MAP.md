<!--
PROJECT_MAP — the one place that maps every decision: what, why, when, how, what broke, how it was fixed.
Documentation only. It does not change any code, market logic, seeing logic, tower logic, the 49 mechanisms,
the experiments or the adapters. When something in the codebase changes, add a dated row here — do not rewrite
history. Numbers cited here are copied from the committed evidence files named in each section; the evidence,
not this map, is the source of truth.
-->

# PROJECT MAP — bd-share-market

**A DSE (Dhaka Stock Exchange) participant-side market-observation research system.** It captures the
richest *lawful* market data reachable, reconstructs one synchronized market picture per symbol, exposes
order-book / order-flow / tape / queue / circuit / auction / resilience mechanics, and tests every claim
against simple baselines before believing it. It never invents a field, and it labels every value
OBSERVED / INFERRED / NOT_OBSERVABLE.

> **How to read this map.** Section 1 is the mental model. Section 2 is the map of the code. Section 3 is
> the decision log — every real decision with its date, reason, and outcome. Section 4 is the failure log —
> what broke and exactly how it was fixed. Section 5 is where each number lives (so nothing here is
> hand-typed folklore). Section 6 is what is blocked and on whom. Section 7 is the day's timeline.
> Keep it current by **appending dated rows**, never by editing old ones.

---

## 1. The mental model (why this exists)

The goal was never "find alpha this week". It is: **can a participant, from lawful data, reconstruct a
faithful, synchronized picture of the DSE order book and its mechanics, and can any state read off that
picture beat a trivial baseline?** Three honesty rules hold everywhere:

1. **Truth classes on every field.** `OBSERVED` = the source delivered it · `INFERRED` = we derived it from
   observations and say so · `NOT_OBSERVABLE` = no obtained source carries it, and we never fabricate it.
2. **Public book is only one sensor.** Per-trade prints, order counts and queue position are *not* public;
   they are blocked on a richer feed (broker export / HAR / FIX), and that block is recorded, not hidden.
3. **A claim must beat a baseline, pre-registered, or it dies.** The experiment rule (KEEP / KILL / BLOCKED)
   is fixed *before* the data is seen; a small denominator returns BLOCKED, not a hopeful KEEP.

---

## 2. Map of the code (what lives where)

| Area | Path | What it is |
|---|---|---|
| **Seeing engine** | `seeing/` | capture → normalization → book/tape/queue reconstruction → two-sensor fusion → one `MarketState` → features → state machine → pre-registered experiment → falsification → KEEP/KILL/BLOCKED |
| capture | `seeing/capture/` | polite HTTP client, hash-chained raw store, adapters (LankaBD, dsebd.org), session runner |
| richer-source adapters (blocked) | `seeing/capture/adapters/{har_import,broker_export,fix_md}.py` | HAR of own terminal · broker L2/T&S export · FIX 35=W/X — built and tested, waiting on the owner's data |
| experiment | `seeing/experiment/` | pre-registered design, run, falsification battery |
| **Observation Tower** | `tower/` | one continuously evolving `MarketState`, 49 mechanics, circuit/auction/resilience/cross engines, deterministic replay, live tailer, UI, Go ingest daemon, experiment tooling, completion gate |
| tower contracts | `tower/CONTRACTS.md`, `tower/mechanics/MECHANISMS.md` | the event/state contract and the mechanism catalogue |
| Go ingest daemon | `tower/ingest/` | zero-loss ingest: http-poll / websocket / tcp / FIX / ITCH / file-tail → seeing-compatible segments |
| **Public collector** | `collector/` | all-symbol public DSE acquisition (depth, tape, circuit, block, company fundamentals, official day-end pages, historical archive), raw-first, rerunnable |
| **Evidence** | `evidence/` | committed proof: raw capture, public dataset, the source-access ledger |
| **Research ledgers** | `RESEARCH_STATUS.md`, `REJECTED_CANDIDATES.md` | what is true today; every rejected idea (append-only, never deleted) |
| **Reports** | `reports/` | the seeing experiment report and the public-data acquisition report for 2026-09-06 |
| **Design docs** | `DATA_ACQUISITION_ARCHITECTURE.md`, `STATE_ENGINE_DESIGN.md`, `FEATURE_DICTIONARY.md`, `DOORSTEP_FOOTPRINT_DESIGN.md`, `SURVIVING_RESEARCH_LEADS.md` | the original designs and the surviving lead |
| **CI** | `.github/workflows/ci.yml` | runs the Python suite and the Go ingest tests on every push / PR |
| **Runtime (not committed)** | `runtime/` | the public collector's regenerated output tree — git-ignored; **never** put source here |

Two other live ledgers worth knowing: `tower/PROGRESS.md` (the tower build's running ledger) and
`evidence/SOURCE_ACCESS_LEDGER.md` (every source, whether it is verified or blocked and on what).

---

## 3. Decision log — what, why, when, outcome

Ordered oldest → newest. "Outcome" is the honest result, not the hope.

| # | Date | Decision | Why | Outcome |
|---|---|---|---|---|
| D-1 | 2026-09-01/02 | Keep every rejected research idea in `REJECTED_CANDIDATES.md`, append-only | deleting rejections is how a programme rediscovers its own dead ends | permanent ledger; prior rounds preserved verbatim |
| D-2 | 2026-09-05 | Build the seeing engine raw-first: store byte-exact payloads under a hash-chained log before any parsing | evidence must be reproducible and tamper-evident | `seeing/capture/raw_store.py`; verified `all_ok chain_ok` on the live capture |
| D-3 | 2026-09-05 | Truth classes (`seeing/truth.py`): a field is OBSERVED / INFERRED / NOT_OBSERVABLE, and `None` is never `0` | the whole project's honesty depends on never faking a value | enforced across adapters, engine, collector; multiple silent-zero bugs later caught against it |
| D-4 | 2026-09-05 | Pre-register the experiment (`seeing/experiment/design.py`): W=6, P=2, θ=0.2, horizons 2/4/8 frames, dev/val/holdout 40/30/30, n_min 30, verdict rule fixed before data | a hypothesis chosen after seeing the data is not a test | verdict on the live day came back BLOCKED honestly (D-14) |
| D-5 | 2026-09-06 03:50 UTC | Run the live capture through the whole DSE session (pre-open → close), 14 symbols, two book sensors + tape + watch + market + block + circuit | one real session of dynamic data, preserved | 72 hash-chain-verified segments (`evidence/capture/2026-09-06/`) |
| D-6 | 2026-09-06 | Build the Observation Tower as a **separate** PR from the seeing engine | the user asked to keep the big new build from tangling with the seeing PR | PR #3 (seeing) and PR #4 (tower), each self-contained |
| D-7 | 2026-09-06 | 49 mechanics with a lifecycle (building → active → confirmed → resolved/failed) and rolling windows, no permanent-zero placeholders | mechanics must be *computed*, not stubbed | 43/49 varied on the live day; the flat 6 need conditions that did not occur (circuit hits, close session, auction) |
| D-8 | 2026-09-06 | Verify **every** tower module twice by independent adversarial agents before trusting it | a plausible-but-wrong reading is worse than a missing one | 28 agent runs, 100+ real defects found and fixed with pinned tests (see §4) |
| D-9 | 2026-09-06 | Deterministic replay: same capture → byte-identical final state hashes | reproducibility is the difference between evidence and a demo | fixture replay identical twice; gate checks it every run |
| D-10 | 2026-09-06 | Completion gate (`tower/gate.py`): full suite + real-data replay + placeholder sweep + Go checks, one command | "done" needs a machine-checkable definition | GATE.json: 653 tests pass, full-day replay 0 reconstruction failures |
| D-11 | 2026-09-06 | Scout the richer sources **without any login attempt** (public surface only, robots.txt respected) | find exactly what a broker/terminal feed would add, lawfully | verified: public surface has no prints/order-counts; the exact owner inputs are named in the ledger |
| D-12 | 2026-09-06 | Build the all-symbol public collector, reusing the verified seeing adapters | maximize lawful coverage for every symbol, not just the 14 | 725 symbols, 400,297 normalized rows (`evidence/public/2026-09-06/`) |
| D-13 | 2026-09-06 | Merge both PRs into `main` | one branch to run from tomorrow, no divergence | main carries seeing + tower + collector + evidence; 57 seeing tests + 663 full suite pass on main |
| D-14 | 2026-09-06 | Accept the honest verdict: **BLOCKED**, not KEEP or KILL | one session's holdout had 1 composite episode (< 30 required) | recorded in `reports/SEEING_EXPERIMENT_REPORT_2026-09-06.md` and `RESEARCH_STATUS.md`; tower experiment KEEP 0 / KILL 3 / BLOCKED 46, FDR 0 of 3 |
| D-15 | 2026-09-06 | Repo cleanup (this task): aggregate multi-pass collector metadata, move runtime output out of `data/`, add a safety guard, add minimal CI | fix a metadata-overwrite bug and remove the accidental-deletion risk, without touching research logic | commit `7fce5c2`; 663 tests pass; guard verified live |
| D-16 | 2026-09-06 | Write this map (`PROJECT_MAP.md`) | keep every decision/failure/fix in one legible place, documentation-only | this file |

---

## 4. Failure log — what broke and how it was fixed

Every row is a real defect that a test, a verifier, or a live run exposed, plus the fix. This is the
"what norse, kmne thik korse" ledger. Grouped by area; each fix shipped with a test unless noted.

### Seeing engine / capture
| What broke | How it was fixed | Where |
|---|---|---|
| Pre-open watch returned all-zero day fields → empty universe | fall back to the last known ranking; test added | commit `7006e09` |
| dsebd.org TLS chain failed verification on every request | remember hosts whose chain failed once → one wire request per poll, recorded as a fallback | `61ac183` |
| A tape pull before the day's first trade carried the *previous* session's rows | engine skips rows whose exchange trading-date ≠ receipt trading-date; counted, never applied | `dc4b911` (I-017 in `REJECTED_CANDIDATES.md`) |
| A non-numeric tape row stamp made the adapter drop the whole pull | keep the pull, flag just the bad row (`t_source_utc` None) | `140e8cb` |
| FIX depth level without MDEntrySize stored size `0` | size is `None`, never `0`; `size_missing` reported | `140e8cb` (I-019) |
| Composite could never fire (transition and persistence required on the same frame) | transition = crossing within the last W frames; documented before the live run | design.py (I-015) |

### Tower — found by the two adversarial verification rounds (D-8)
| Class of defect | Example that was caught | Fix |
|---|---|---|
| **Silent zeros** | unsized tape prints and size-less FIX levels stored as `0`; watch `0.0` ltp reported as observed | `None` + explicit flags across tape/parsers/normalize; zero-sentinel fields excluded (`7cd3a3e`, `b2f9ed2`) |
| **Non-causal reads** | a cancel stamped 500 ns before its add sorted first and rebuilt a phantom book level | monotone frame clock for receipt-less streams; emitted events never rewritten by later input (`b2f9ed2`) |
| **Day-roll leaks** | previous session's ltp/open carried into the next day's first frames | day-roll resets quote memory and the tape/queue engines (`7cd3a3e`, `ce35bde`) |
| **False positives** | circuit hit/lock time accrued on a residual pre-open book; queue "pull" counted on a stable touch | session-phase gating; a pull requires a tape (`98c3ac5`, `fb4d3fd`) |
| **Contract deviations** | `normalize_store` documented to return a list but returns `(events, QAStats)`; a missing prior streak coerced to `0` | docs aligned; streak reported as `unknown` when no day history (`ce35bde`) |
| **Non-finite in the wire format** | `Infinity`/`NaN` could reach the JSON state store and the static page | `allow_nan=False` in the store; `NaN/inf → null`; the phone page is null-safe and self-contained (`ce35bde`) |
| **Cost / scale** | the full-day replay built and discarded 289k watch-only states, then timed out the gate | a light path feeds the cross engine a minimal state for watch-only symbols (`2d46692`) |

### Collector (this cleanup)
| What broke | How it was fixed | Where |
|---|---|---|
| A later small pass overwrote `validation.json` etc. → zeros for depth/tape/history although the files held the data | per-pass records under `metadata/passes/`; ledgers append-merged; the combined view recomputed from the files on disk (`--rebuild-metadata`); `raw_index.json` lists every payload with sha256 | `7fce5c2` |
| Collector wrote into `data/`, which holds tracked source — an accidental `rm -rf data` had happened | default output moved to git-ignored `runtime/public_data`; `assert_safe_out()` refuses repo root, home, system paths, and any dir holding tracked files | `7fce5c2` |
| Multi-megabyte archive page cut mid-transfer; older months served an empty page | retry the whole request, split the range down to a day, and stop after 12 consecutive empty months (the real archive horizon: 2024-09-08) | `da70a3a` |

### Process failures (kept because they are instructive)
- A background runner launched with `nohup … &` inside a tool call died; relaunched detached. → always launch long jobs detached.
- One adversarial verify round hit the session usage limit mid-run and was **resumed** from cache, not restarted. → workflows are resumable; don't re-run from zero.
- Several uncommitted checkpoints were swept into another commit by a broad `git add`. → commit narrow, commit often.

---

## 5. Where each headline number lives (source of truth)

Nothing in this map is hand-counted. Every figure is copied from a committed file; to re-derive, read that file.

| Claim | Value | File |
|---|---|---|
| Live capture segments, hash chain | 72 segments, `all_ok chain_ok` | `evidence/capture/2026-09-06/` + `python3 -m seeing verify …` |
| Seeing experiment verdict | **BLOCKED** (1 holdout composite episode < 30) | `results/seeing/2026-09-06/VERDICT.json`, `reports/SEEING_EXPERIMENT_REPORT_2026-09-06.md` |
| Synchronized frames / two-sensor agreement | 4,257 frames · 96.5 % book agreement | `results/seeing/2026-09-06/DENOMINATOR.json` |
| Tower full-day replay | 326,588 events → 17,429 states, 0 reconstruction failures, 43/49 mechanics vary | `results/tower/gate/GATE.json` |
| Tower experiment | KEEP 0 · KILL 3 · BLOCKED 46 · FDR 0 of 3 | `results/tower/2026-09-06/experiment/VERDICTS.json` |
| Tests (full suite) | 653–663 passed, 0 failed | `results/tower/gate/TESTS.json`; CI on every push |
| Public dataset | 725 symbols · 400,297 rows · tape 33,307 · interval flow 32,917 · history 310,895 · fundamentals 636 · P/E 12,570 · shareholding 1,220 | `evidence/public/2026-09-06/metadata/validation.json`, `reports/PUBLIC_DATA_ACQUISITION_2026-09-06.md` |
| Public archive horizon | 2024-09-08 → today (older served empty) | `reports/PUBLIC_DATA_ACQUISITION_2026-09-06.md` §"Verified limits" |

---

## 6. What is blocked, and on whom

The full, verified detail is in `evidence/SOURCE_ACCESS_LEDGER.md`. Summary:

**NOT_OBSERVABLE from any public source reached** — individual trade prints · trade side/aggressor ·
number of orders per depth level · order ids · queue position · intra-interval add/cancel netting ·
an exchange timestamp on the book itself.

**Exactly what would unblock it (owner input, no credentials ever handed over):**
1. Which broker holds the BO account (selects the DirectFN / XFL Trade / DSE-Mobile / EcoSoft OST route).
2. A HAR ("Save all as HAR with content", Chrome, DevTools → Network) of the owner's **own** logged-in
   terminal session with the Market Depth and Time & Sales screens open for 10–15 min during trading hours →
   `seeing/capture/adapters/har_import.py`.
3. A broker Level-II (with order counts) / Time & Sales CSV/JSON export → `broker_export.py`; or a FIX
   market-data entitlement (host, port, SenderCompID, TargetCompID, credentials, dictionary) → `fix_md.py`.

Owner's disclosed terminals scouted (public surface only): **EcoSoftBD OST** (`ost.ecosoftbd.com`) and
**LankaBangla TradeXpress** (`itrade.lbsbd.com`, which advertises Level-II by price and by order).

---

## 7. The 2026-09-06 timeline (one day, start to finish)

- **~02:50–03:50 UTC** — tower contracts and integration written; pre-open capture epoch verified.
- **03:50–08:20 UTC** — live DSE session captured (14 symbols, two book sensors, tape, watch, market,
  block, circuit); hourly manifests committed as evidence rotations.
- **04:00–08:00 UTC** — 14 tower module groups implemented and green; first adversarial verify round.
- **05:05 UTC** — interim seeing experiment (one hour of data): BLOCKED, as designed.
- **08:20 UTC** — capture ended, segments compressed and chain-verified.
- **08:26 UTC** — session-end seeing experiment: **BLOCKED** (1 holdout episode); report + ledgers.
- **~08:00–13:30 UTC** — second adversarial verify round (all 14 groups, 604→653 tests); cross-module
  fixes; final completion gate green; tower experiment run; phone artifact + zip.
- **~15:35–15:50 UTC** — three public-collector passes: 725 symbols, 400,297 rows.
- **~16:20 UTC** — PR #3 and PR #4 merged into `main`.
- **later** — repo cleanup (combined metadata, runtime path + safety guard, CI); this map.

---

## 8. How to keep this map alive

- When a decision is made, add a row to §3 with the date and the honest outcome.
- When something breaks and is fixed, add a row to §4 with the commit.
- When a number changes, update §5 to point at the file that proves it — never type the number here alone.
- Never edit or delete an old row; the value of this map is that it does not rewrite its own history.
- This file is documentation. It must never be the reason a piece of code, logic, mechanism, experiment or
  adapter changes.
