<!--
ROADMAP — the locked build sequence. Append-only, exactly like PROJECT_MAP.md.
Documentation only. This file has never been, and must never become, the reason a piece of
code, mechanism, threshold, experiment, adapter or frozen artefact changes. It records what
is to be built and in what order; it does not build anything. When a stage's state changes,
append a dated row to §5 — never edit or delete a row above it.
-->

# ROADMAP — bd-share-market

**Locked 2026-09-08.** Twelve stages, in order, plus four connective items that the twelve
cannot be joined without. Each stage names what already exists (with paths), what it must
deliver, its truth-class obligation, and the gate it must pass before the next stage may
rely on it.

> **Read this with `PROJECT_MAP.md`, not instead of it.** That file is the decision and
> failure log of what *has* happened; this file is the ordered list of what is *to* happen.
> `AGENTS.md` (LOCKED) outranks both. Where this roadmap and a frozen research artefact
> appear to conflict, the frozen artefact wins and this file is wrong.

---

## 1. The rules this roadmap inherits

These are not restated aspirations; they are the constraints every stage below is written
against, and a stage that violates one is not "done" whatever it produces.

1. **RAW evidence first.** Bytes are stored before anything parses them. Parsing happens on
   replay, so a parser fixed next month can re-read everything captured today.
   (`seeing/capture/raw_store.py`, AGENTS.md §8)
2. **OBSERVED / INFERRED / NOT_OBSERVABLE on every field.** Missing is never zero; inference
   is never observation. (`seeing/truth.py`, AGENTS.md §21)
3. **No invented data, no fake success.** `VERIFIED` or `NOT VERIFIED`, never "should work".
   (AGENTS.md §2, §26)
4. **Current/live state change is the primary input.** The system's subject is what the
   market is doing now, not what a static table says it did.
5. **Market, sector and share states stay separated.** They are three different objects with
   three different denominators, and pooling them is how a market-wide move gets read as a
   share-specific signal.
6. **"Manipulation" and "accumulation" name a measurable footprint or state, never a proven
   intent.** The engine may report that a book shape is consistent with accumulation. It may
   never report that someone is accumulating. (`seeing/features/hidden_pressure.py` already
   holds this line: it returns a credibility, not a verdict.)
7. **Every prediction stays a testable hypothesis.** No guaranteed-profit assumption, ever,
   at any stage, including stage 12.
8. **Existing architecture is reused, not replaced.** New work extends `seeing/capture/adapters/`,
   `tower/`, and the existing store and contracts.
9. **Research history is never rewritten.** `REJECTED_CANDIDATES.md` is append-only; a
   rejected idea returning under a new name is still rejected until new evidence says otherwise.
10. **New mathematics is allowed and expected.** Conventional indicators are not mandatory.
    What is mandatory is that whatever is invented is eventually falsifiable against data.

---

## 2. Where the twelve stages stand today

Honest state as of 2026-09-08. "EXISTS" means committed, tested code — not that it is
validated. Nothing in this repository is a validated edge; `SURVIVING_RESEARCH_LEADS.md`
still reads **Tradeable candidates: NONE**.

| # | Stage | State | What already exists |
|---|---|---|---|
| 0 | **Reference & Identity Spine** ★ | **MISSING** | symbol strings per source; no resolution layer |
| 1 | **Public Data Engine** | **PARTIAL** | `seeing/capture/engine.py`, `adapters/bd_public.py`: 8 sources live, 4 raw-only, 2 blocked. `PUBLIC_SOURCE_MATRIX.md` |
| 2 | **Unified Market State** | **PARTIAL** | `tower/state.py`, `tower/fusion.py`, `tower/normalize.py`, `seeing/consensus.py` — built for 2 book sensors, not yet for the 8-source set |
| 3 | **Current-State Mathematics** | **PARTIAL** | `seeing/features/micro.py`, `tower/mechanics/` (49 mechanisms), `seeing/features/hidden_pressure.py` (unfitted) |
| 3.5 | **Regime & Panel Separation** ★ | **MISSING** | the breaks are *documented* in `RESEARCH_STATUS.md`; no enforcing layer |
| 4 | **Market → Sector → Share hierarchy** | **MISSING** | `tower/cross.py` does cross-sectional work; there is no sector tier |
| 5 | **Circuit / Halt Engine** | **PARTIAL** | `tower/circuit.py`, `tower/mechanics/circuit_family.py`; limits collected twice (635 + 636 rows) |
| 6 | **Corporate-Action Engine** | **MISSING** | corporate actions are currently only *suspected*, via the beyond-band rule (I-008) |
| 7 | **Pre-Move / Buildup Engine** | **PARTIAL, CONSTRAINED** | `tower/pressure.py`, `mechanics/accumulation_family.py`, `hidden_pressure.py`. **P45-8 already rejected the daily-bar form of this question** |
| 8 | **Exceptional Share / Relative-Strength** | **PARTIAL** | `market_relative_ret`, `xs_*` features; `tower/cross.py` |
| 9 | **Dynamic Move-Range Engine** | **MISSING** | the inputs exist (per-symbol bands, OBSERVED, two sources); no engine |
| 10 | **Position Logic** | **MISSING** | nothing. No entry, sizing, scaling or exit logic exists anywhere |
| 10.5 | **Cost & Liquidity Model** ★ | **MISSING** | named as the reason F07 is not tradeable; not built |
| 11 | **Falsification / KEEP-KILL-BLOCKED** | **EXISTS** | `seeing/experiment/falsify.py`, `tower/experiment.py` — the most mature part of the system |
| 12 | **Live decision-support output** | **PARTIAL** | `tower/live.py` + UI observe; they do not decide |
| — | **Point-in-Time Discipline** ★ | **MISSING as a contract** | the rule is understood; it has been violated twice (see §4) |

★ = added by this lock as a connective requirement, not new product scope. Justified in §3.

---

## 3. The locked sequence

Each stage may begin only when its dependency has passed its gate. A stage that cannot pass
its gate returns **BLOCKED** and the sequence stops there — it does not skip ahead.

### Stage 0 — Reference & Identity Spine ★ *(added)*

**Why it is required, not decoration.** Stage 2 must merge DSE, CSE, StockNow and BullBD
observations of the same company. They do not agree on the key: CSE renders its code one
letter per node, LankaBD carries `companyID` and `mkistaT_INSTRUMENT_NUMBER`, StockNow keys
by trading code with its own `sector_id`, BullBD carries a MIC (`XDHA`) and its own sector
string. Without one resolved identity, "the same symbol" is an assumption, and every
cross-source number in stages 2, 4 and 8 inherits that assumption silently.

**Delivers.** A per-instrument identity record: trading code, exchange, MIC, company id per
source, sector id per source, listing state, and the mapping's own provenance. Conflicts are
reported, never auto-merged.
**Truth.** Every mapping is OBSERVED (both sources named it) or INFERRED (matched by rule,
rule stated). A guessed match is NOT_OBSERVABLE and stays unmatched.
**Gate.** Every symbol used downstream resolves, or is explicitly listed as unresolved.

### Stage 1 — Public Data Engine

**State: PARTIAL, running.** 8 sources live (`./run_public_market_engine`), verified
2026-09-08 on a closed market: 8 WORKING, 2 DEGRADED, 2 BLOCKED, hash chain verified.

**Remaining.** Parsers for the four raw-only sources (Bangladesh Bank FX and bill rates,
BSEC, CDBL); a trading-hours run to exercise the book/tape specs that correctly sat out
while closed; the ownership snapshot on a monthly cadence.
**Truth.** Already enforced per adapter. Blocked sources stay registered with their reason
(AmarStock: robots.txt; BullBD depth: socket-only) so the gap cannot vanish from the report.
**Gate.** A full trading-session run with `SOURCE_STATUS.json` showing no unexplained
FAILING source, and `verify_store()` `all_ok`.

### Stage 2 — Unified Market State

**Depends on 0 and 1.** Extend the existing fusion — do not write a second one.

**Delivers.** One synchronized state per instrument per instant, carrying every source's
view rather than a chosen one. `seeing/consensus.py` already implements the rule: `consensus`
is filled only where reporting sources agree; disagreement keeps every value and reports the
spread; book truncation is counted apart from contradiction; age is kept apart from freshness.
**Truth.** Field-level, inherited from the contributing sources; a field with no reporting
source stays NOT_OBSERVABLE and is never interpolated.
**Gate.** Deterministic replay: the same raw store reproduces byte-identical state hashes
(the existing `tower/gate.py` discipline), now with the wider source set.

### Stage 3 — Current-State Mathematics

**Depends on 2.** This is where new mathematics is explicitly permitted. Conventional
indicators are not required and are not privileged.

**Delivers.** Quantities describing *what the state is now and how it is changing*: pressure,
imbalance and its rate, depth persistence and replenishment, spread stability, activity
acceleration, participation concentration, and whatever else proves measurable. Each carries
its own definition and its own truth class.
**Truth.** A derived quantity is INFERRED and names its rule. `hidden_pressure.py` is the
model to follow: it publishes `fitted=False` on its own weights and returns a credibility
that collapses on a cliff shape rather than asserting a hidden queue.
**Gate.** Every quantity is computable on real captured data, varies (a quantity that is
constant across a real session is not a measurement), and is documented in
`FEATURE_DICTIONARY.md`.

### Stage 3.5 — Regime & Panel Separation ★ *(added)*

**Why it is required.** Stage 4 and stage 8 are cross-sectional by construction, and this
dataset has documented breaks that make cross-sectional aggregates incomparable across them:
reporting symbols **381 → 88 on 2024-02-22**, **327 → 243 on 2020-03-25**, and a floor-price
era **2022-07-28 → 2024-01-31** covering 119,520 bars (13.9 %). `RESEARCH_STATUS.md` already
records these; what does not exist is a layer that *enforces* them. Without it, stage 4's
first sector aggregate silently spans a basis change, and stage 8's relative strength is
measured against a market whose membership changed underneath it.

**Delivers.** A regime/panel registry: named periods with their boundaries and cause, the
rule that no cross-sectional aggregate may span a boundary, and a check that refuses rather
than warns.
**Truth.** Boundaries are OBSERVED (they are in the data and dated); their *cause* is
INFERRED where it is a reading rather than an announcement.
**Gate.** A deliberate attempt to compute a cross-sectional aggregate across 2024-02-22 is
refused by the layer, with a test that pins the refusal.

### Stage 4 — Market → Sector → Share hierarchy

**Depends on 0, 2, 3, 3.5.** Three tiers, three denominators, never pooled.

**Delivers.** A market-tier state, a sector-tier state, and a share-tier state, each computed
on its own membership, plus the decomposition that says how much of a share's move is market,
how much is sector, and how much is specific to it.
**Truth.** Sector membership is OBSERVED (StockNow `sector_id`, BullBD sector, LankaBD
`sectorID`) — and where those three disagree, stage 0's conflict report governs; the
decomposition is INFERRED.
**Gate.** The three tiers reconcile: a share's decomposition sums to its observed move within
a stated tolerance, on real data, across a whole session.

### Stage 5 — Circuit / Halt Engine

**Depends on 0, 2, 3.5.** Extends `tower/circuit.py`, which already exists.

**Delivers.** Per-symbol band state (distance to the upper and lower limit in ticks and in
percent), band-touch and band-lock detection, halt detection, and the history of both.
**Truth.** Bands are OBSERVED from two independent sources (LankaBD 635 rows, dsebd 636). A
touch or lock is INFERRED from price against band — and, per **I-008**, must use the
**at-band** definition: a move *beyond* the band is a corporate-action reference reset, not a
lock. Measured contamination on the UP side is 6.6 %; it was 27–50 % on the DOWN side.
**Gate.** The at-band rule reproduces the measured base rate (8,973 locks in 916,798 bars,
0.979 %; `evidence/circuit_proposal_review/circuit_lock_base_rate.py`), and the beyond-band
population is excluded and counted, not silently dropped.

### Stage 6 — Corporate-Action Engine

**Depends on 1 and 5.** This stage exists largely *because* of stage 5: today a corporate
action is only ever a *suspicion* raised by the beyond-band rule.

**Delivers.** Public disclosures — dividend, rights, bonus, record date, AGM, ex-date —
captured before their effective date, with the effective date itself, so that a reference
reset is a **known** event rather than an inferred anomaly.
**Truth.** A captured disclosure is OBSERVED. A reset inferred only from a beyond-band move
stays INFERRED and is labelled a *suspect*, never a confirmed action.
**Gate.** For a sample of beyond-band days, the engine can say which had a disclosed action
and which did not — and the count of "unexplained beyond-band" days is published, not hidden.
**Known blocker.** No structured public announcements route has been located
(`PUBLIC_DATA_GAPS.md` §2). BullBD exposes `corporateEvent` per symbol but its fill rate is
**unmeasured**. Measuring that fill rate is the first task of this stage; if it is near zero,
the stage returns BLOCKED and says so.

### Stage 7 — Pre-Move / Buildup Engine

**Depends on 3, 3.5, 4, 5, 6, and the Point-in-Time contract.** This stage carries the
heaviest prior constraint in the repository and must open by acknowledging it.

**What is already settled.** **P45-8** ran "any footprint → limit_up" on daily bars and
rejected it: best lift 1.84 (F08u), 1.72 (F07), 1.55 (F15), and limit-up doors are *not*
anticipated beyond what today's activity and today's move already say. **P45-4** shows the
idiosyncratic-move footprint collapses 1.84 → 1.29 once today's own move enters the match.
This stage therefore may **not** re-ask the daily-bar question. It is licensed only on inputs
P45 did not have: intraday state (stage 3), the tier decomposition (stage 4), band proximity
(stage 5), and disclosure-cleaned populations (stage 6).
**Delivers.** A buildup state — measurable, current, named — and a stated hypothesis about
what it precedes.
**Truth.** The state is INFERRED. Any language about accumulation describes the footprint,
never an actor's intent (principle §1.6).
**Gate.** Stage 11, in full, on a population that P45 did not exhaust. A result that merely
reproduces P45-8 is a KILL, recorded in `REJECTED_CANDIDATES.md`.

### Stage 8 — Exceptional Share / Relative-Strength Engine

**Depends on 4 and 3.5.** Answers "why can a share rise while the index is weak" as a
measurement, not a story.

**Delivers.** A relative-strength state per share against both its sector and the market,
using stage 4's decomposition, and a characterisation of shares whose specific component
dominates while the market component is negative.
**Truth.** INFERRED throughout.
**Gate.** The population is defined before it is measured, and the measurement survives
stage 11. `xs_*` features already exist and are already regime-sensitive — 3.5 governs them.

### Stage 9 — Dynamic Move-Range Engine

**Depends on 5, 4, 3.** "How much room is left" — the one genuinely new question the circuit
review surfaced as answerable from data already in hand.

**Delivers.** An estimate of remaining move room from current structure: distance to band,
depth ahead of price, and the tier context — expressed as a distribution, never a point
target.
**Truth.** INFERRED. Band distance itself is OBSERVED.
**Gate.** Stage 11. And explicitly: **distance-to-band is the only input here that P45 did
not test**, so this stage must report its increment over band distance alone. If band
distance explains it, that is the finding.

### Stage 10 — Position Logic

**Depends on every stage above, and on 10.5.** Seven states, each driven by a measurable
state change rather than a price target: initial entry · partial buy · pyramiding · hold ·
partial profit · reduce · full exit.

**Delivers.** An explicit state machine whose transitions are functions of stage 2–9 states,
with every transition condition written before it is measured.
**Truth.** Each transition names the states it reads and their truth classes. A transition
that depends on a NOT_OBSERVABLE field cannot be built and must be recorded as such.
**Gate.** Stage 11 on the *whole policy*, not on its parts: a policy assembled from
individually-surviving components is a new object and needs its own verdict.
**Standing constraint.** No short selling exists on this market (`SURVIVING_RESEARCH_LEADS.md`
Lead A: "avoidance / exclusion filter only"). Reduce and exit are the only downside actions.

### Stage 10.5 — Cost & Liquidity Model ★ *(added)*

**Why it is required.** `SURVIVING_RESEARCH_LEADS.md` names three gates, and the third is
"realistic costs". It also states plainly why F07 is not tradeable: "no cost layer; fails 94 %
of the time". Stage 10 produces actions; without a cost and fillability model, stage 11
cannot return a meaningful verdict on them, because a policy that trades often can look
profitable gross and be negative net. Round 2 already used a cost bracket
(0.8 % / 1.0 % / 1.2 % round trip) — that bracket is the starting point, not a new invention.

**Delivers.** Round-trip cost, and a fillability estimate from observed depth: whether the
intended size could have been done at the price the policy assumed.
**Truth.** Costs are a stated assumption (INFERRED, parameterised, reported with the result).
Fillability is INFERRED from OBSERVED depth.
**Gate.** Every stage-10 result is reported gross **and** net, at all three cost brackets.

### Stage 11 — Falsification / KEEP-KILL-BLOCKED

**EXISTS.** `seeing/experiment/falsify.py`, `tower/experiment.py`. This stage is not to be
rebuilt; it is to be *applied*, unchanged, to stages 7–10.

**The rules that already hold and do not soften.** Pre-registration before data. Date
permutation, side flip, anchor shift, LOSO, liquidity split, block bootstrap. A small
denominator returns **BLOCKED**, not a hopeful KEEP (D-14 accepted exactly this). Distinct
events counted, not occurrences (I-012). Bootstrap lower bound > 1 required (I-013).
**Holdout discipline.** The sealed daily holdout **2019-01-01 → 2022-07-27** is spoken for by
Phase 5 and may not be spent on anything in this roadmap. The one genuinely unseen daily
slice is **2026-01-25 → 2026-09-07** (55,133 bars, fetched 2026-09-07, no phase has seen it);
it is single-use, and features do not yet exist for it. Intraday work has its own frozen
prereg (`micro/MICRO_PREREG.json`, sha256 `169935bd…`), which stays frozen.

### Stage 12 — Live decision-support output

**Depends on 11 returning something other than KILL.**

**Delivers.** A live view showing current market / sector / share state, band proximity,
remaining-room distribution, buildup state, and — only for whatever survived stage 11 — the
position state and what would change it. Every number carries its truth class, its age and
its freshness.
**Truth.** The output is decision *support*. It states what is observed, what is inferred and
what is unknown.
**Hard limit.** If stage 11 returns KILL or BLOCKED for a component, stage 12 shows that
component's **state** and shows no action from it. A blocked component is displayed as
blocked. There is no fallback to "show it anyway".

---

## 4. Cross-cutting: Point-in-Time Discipline ★ *(added)*

**Why it is required.** This is not theoretical. The repository has been bitten twice, and
both are recorded:

* `fwd_mae` is a **forward label**. `results/dse_eod_labels.parquet` holds the `fwd_*`
  columns and `results/dse_eod_features.parquet` holds **none** — the separation is
  deliberate. An outside proposal to score today's stock with `fwd_mae` was rejected on
  2026-09-08 (`evidence/circuit_proposal_review/`).
* `flag_locked_run` and `flag_stale_run` **look ahead by construction**. `bdlib/qa.py:40`
  stamps the whole run's length onto every day of the run, so day 1 of a 10-day stale run is
  already flagged. They are input **gates**, never predictors.

**The contract.** Before stages 7–10 consume any feature, that feature must be shown to be
computable from information available at the decision instant. A feature that cannot be is
either recomputed causally or excluded. The check is mechanical and belongs beside the
feature definitions, not in a reviewer's memory.

**Gate.** A test that fails if any feature reaching a stage-7-and-later model can be shown to
depend on future information.

---

## 5. Preserved research questions — explicit future targets

Carried verbatim in intent, each bound to the stage that can answer it and to what would make
the answer trustworthy. None of these is answered today.

| # | Question | Stage | What would make an answer trustworthy |
|---|---|---|---|
| Q1 | Can pressure / liquidity / float / activity buildup precede a share move? | 7 | Must beat P45-8's ceiling on inputs P45 did not have; a result reproducing P45-8 is a KILL |
| Q2 | Why can some shares outperform while the index is weak? | 8 | Tier decomposition (4) with regime separation (3.5); population defined before measurement |
| Q3 | Can repeated circuit/halt behaviour and pre-halt buildup be characterized? | 5 → 7 | At-band definition (I-008); base rate 0.979 %; distinct events, not occurrences |
| Q4 | Can current structure estimate remaining move room? | 9 | Must report its increment over band distance alone |
| Q5 | Can sector strength improve share selection? | 4 → 8 | No cross-sectional aggregate spanning 2024-02-22 or the floor era |
| Q6 | Can public corporate-action disclosures be incorporated before effective dates? | 6 | First measure BullBD `corporateEvent` fill rate; BLOCKED is an acceptable answer |
| Q7 | Can measurable state changes drive partial entry, pyramiding, profit-taking and exit? | 10 | Whole-policy verdict, gross **and** net at all three cost brackets |

---

## 6. Stage state log — append only

| Date | Stage | State | Note |
|---|---|---|---|
| 2026-09-08 | all | **LOCKED** | This roadmap written. No code, architecture or behaviour changed by it. |
| 2026-09-08 | 1 | PARTIAL | 8 sources live, verified on a closed market; 4 raw-only, 2 blocked (`PUBLIC_SOURCE_MATRIX.md`) |
| 2026-09-08 | 11 | EXISTS | Unchanged and not to be rebuilt; to be applied to 7–10 |
| 2026-09-08 | 0, 3.5, 6, 9, 10, 10.5 | MISSING | Not started |
| 2026-09-08 | **0** | **PASS (hardened)** | Canonical normalisation now decodes HTML entities: `symbols.csv` carried the same instrument twice, as `KAY&QUE` and `KAY&AMP;QUE`, so one real instrument was two identities. Order is strip-whitespace → decode-once → upper. Universe 725 → **724**, absent 68 → **67**, coverage **657/724 (90.75 %)**, 0 unresolved. New `spine_sha256 c516aed2…`, byte-identical on rebuild, gate 7/7 PASS |
| 2026-09-08 | **1** | **PASS** | Parsers completed for Bangladesh Bank bills, BSEC publications, CDBL statistics and DSE ownership; ownership wired at a 30-day per-symbol cadence. Trading-hours run exercised book and tape during CONTINUOUS. `bb_exchange_rate` moved to **BLOCKED**: it answers HTTP 200 with a CAPTCHA, which previously counted as a WORKING source — the new `bot_challenge` code catches it |
| 2026-09-08 | 1 | finding | Two defects the live run exposed, both fixed: **(a)** the engine never fetched LankaBD's company-id map, so every `lankabd_tape` poll failed with "no company id for <symbol>" and a *configuration* gap was being reported as a `connect_error`. Specs can now declare a one-time `bootstrap`; the tape spec loads the 715-symbol map. **(b)** a 200 carrying a bot challenge was classified healthy — `bot_challenge` was added, with only unambiguous markers so an SPA's `<noscript>` "enable JavaScript" block cannot false-positive (verified against all 16 captured payloads) |
| 2026-09-08 | **0** | **PASS** | `seeing/identity.py` built and gated. 2,562 claims from 7 sources → 1,078 listings (691 DSE + 387 CSE), 692 companies, 657/725 wired DSE symbols resolved (90.62 %), 0 unresolved, 68 absent and named. Deterministic: `spine_sha256 c3c89cbc…`, byte-identical on rebuild. Gate `evidence/identity/2026-09-08/STAGE0_GATE.json`, 7/7 checks PASS |
| 2026-09-08 | 0 | note | Three design corrections the real evidence forced, each recorded because each would otherwise have been a silent error: **(a)** LankaBD `sector_id` 10 and StockNow `sector_id` 25 are the same sector in unrelated numbering schemes — comparing them was a category error, so source-private ids are now kept per source and never reconciled; **(b)** a disagreement over a company name's trailing full stop, or `A-MF` vs `A`, is a labelling difference, not a doubt about which instrument it is — identity-critical and descriptive attributes are now separated, and only the former can make a listing unresolved; **(c)** EcoSoft OST publishes `StockExchange=1`, an opaque enum, and reading it as an exchange name put a listing under an exchange literally called `"1"`. Exchange is now inferred by corroboration or left unresolved — three EcoSoft symbols (AAMRANET, AAMRATECH, CITYGENINS) are listed on **both** DSE and CSE, so the enum settles nothing and they stay unresolved rather than being assumed onto DSE |
| 2026-09-08 | 0 | finding | `evidence/public/2026-09-06/normalized/symbols.csv` contains `KAY&AMP;QUE` — an HTML entity that was double-escaped upstream of that committed table (the instrument is `KAY&QUE`). Recorded, **not repaired**: raw evidence is immutable (`AGENTS.md` §8), and it is one of the 68 named absent symbols, so nothing is silently lost. A future collector pass is where it gets fixed, not here |
| 2026-09-08 | **2** | **PASS** | `seeing/fusion/unified.py` + `seeing/fusion/gate.py`. 4,257 states over 14 listings from `evidence/capture/2026-09-06`, keyed by the Stage 0 spine (`c516aed2…`), carrying per-field cross-source consensus. Two independent replays **from raw bytes** reproduce the run hash `87e00d2a…` and all 4,257 state hashes positionally. Gate 6/6, `evidence/unified/2026-09-06/STAGE2_GATE.json` |
| 2026-09-08 | **2** | **defect removed** | `fuse.py` fell back to the day's FIRST circuit row when no poll preceded a frame and recorded it as `ref_from_future=True`. A flagged look-ahead is still a look-ahead: `shares_to_door`, `bid_at_upper_limit` and every band distance on those frames were computed from a reference the market had not yet published. Removed — such frames keep NaN limits and `ref_status = NOT_YET_OBSERVED`, and `assert_no_future_reference` fails the gate if the column returns. A second instance was found alongside it: the book scaler took `tick_size.last()`, handing 08:00's tick to a 04:00 book; it now takes the first observed tick |
| 2026-09-08 | 2 | finding | The depth page's own cumulative day totals **lag the all-symbol watch poll**. `day_volume` disagrees on 54.3 % of states, watch ahead in 2,056 of 2,312 (median shortfall 0.23 % of the day; at 04:01 the depth page said `day_volume = 0` while the watch said 135,167). Reference fields agree — `yclose` 99.9 %, OHL ~99 % — and `ltp` disagreements are symmetric with a median gap of exactly one tick, i.e. two sensors a moment apart rather than a contradiction. The consensus layer reports all of it and resolves none of it |
| 2026-09-08 | **3** | **PASS (gate)** | `seeing/features/geometry.py`: TLPI(λ) family, E1 reproduced against its preregistered θ=0.20, per-second velocity/acceleration, touch-vs-deep geometry, ladder mismatch, risk veto. Gate 5/5 — every quantity computable on real data, every quantity varies, every quantity documented, **plus** the two structural identities: TLPI(0) ≡ `imb_all` (max abs diff 0.0) and TLPI(8) → E1 (r = 0.9996), both on 3,758 real states |
| 2026-09-08 | 3 | finding | The TLPI response curve **peaks in the interior**: λ = 0 → 0.539, λ = 2 → **0.692**, λ = 8 → 0.683 (AUC, h=180 s, 1,997 identical eligible rows). Both endpoints — the existing all-levels and L1 imbalances — are worse than the interior. The scale that carries it is **ticks, not percent**: the relative-to-mid variants are flat at 0.539–0.544 across the whole grid |
| 2026-09-08 | 3 | finding | The distance idea's contribution appears **exactly where the algebra permits it and nowhere else**. Against the identical decay on level RANK (control C7): on mirror-symmetric ladders +0.0005 (n=1,179) — zero, as required, since the weights cancel level by level; on gapped books **+0.0479, 95 % CI [+0.0262, +0.0806]** (n=717). 40 % of frames are gapped. Pooling the two dilutes a real effect with rows that cannot speak to it |
| 2026-09-08 | 3 | negative | **Rates of change carry almost nothing** at 60/180/600 s. Acceleration 0.500–0.510 for every λ and for E1; velocity peaks at 0.557. The level of book imbalance is informative, its derivative is not. `geom_deep_imb` (the book behind the touch, alone) is 0.525 — consistent with the λ curve |
| 2026-09-08 | 3 | **INSUFFICIENT_SAMPLE** | All 132 evaluations. `micro/sessions/INDEX.json` reports **0 accepted sessions**, so the micro DEV set is empty and the prereg's block bootstrap (block = one whole session) cannot run on one session. The ranking is on the 2026-09-06 **calibration** session, which the prereg marks already-seen and never countable as DEV/VAL/HOLDOUT. **Not a kill** — nothing was looked for and found absent. Nothing beats E1 with an established margin: TLPI(2) is +0.0106 [−0.0066, +0.0274] |
| 2026-09-08 | **3** | **E1 REPRODUCED** | `imb_l1 > 0.20` on the calibration session against `tower/experiment.py`'s own matched control (symbol, tod bucket, spread bucket), frame horizons: h2 **+11.09 ± 0.78 pp**, h4 **+15.78 ± 0.94 pp**, h8 **+12.88 ± 1.01 pp** (handoff reference ~+12.6 / +16.5 / +11.9). Median cadence **43.67 s**, the documented 43.7 s. Same shape — h4 is the peak |
| 2026-09-08 | 3 | **method correction** | A matched lift is computed from a **drawn** control, so a single-seed value is one sample of a random quantity. Across 20 seeds E1 ranges **+13.76 to +17.28 pp** (sd 0.94); the first version of this run reported +17.28 pp, the top of that range, as the estimate. Every matched lift is now the mean over 20 independent draws with its sd beside it, and differences below ~3 pp between candidates are not resolvable from this sample |
| 2026-09-08 | 3 | **TOUCH LOCALITY CONFIRMED** | Same θ at different depths, mean over h2/h4/h8: `imb_l1` **+13.25 pp**, `imb_top3` +6.85, `imb_top5` **+7.95** (reference +7.97), `imb_weighted` **+7.20** (reference +6.84), `imb_all` **−2.68**. Adding levels 2–5 halves the lift; adding the whole book inverts its sign. Two independent confirmations: `GEO_deep_only_bid` (deep bid pressure, no touch pressure) scores **−11.89 pp** on P(up), and `DYN_G` (top5 up, L1 down) scores **+15.99 pp on P(down)** — when touch and depth disagree, the touch wins |
| 2026-09-08 | 3 | **incremental gate: NOTHING beats E1** | Symbol-block bootstrap on the matched-lift difference: best margin `CTX_xs_rank_top` **+7.35 pp, CI [−0.51, +15.14]** — misses zero by 0.51 and is the closest. Two results ARE established and both are negative: `DYN_C_level_vel` **−5.69 pp [−10.62, −0.44]** and `VETO_E1_no_risk` **−7.17 pp [−7.56, −0.59]**. Requiring positive velocity alongside E1, and vetoing E1 on the risk states, each do measurable HARM |
| 2026-09-08 | 3 | finding | Pooled, only **two** candidates exceed E1 by more than one combined sd: `CTX_xs_rank_top` **+21.75 ± 1.74** (margin +5.97, 3.0× combined sd, 14 symbols, best MAE −0.184 vs E1's −0.287) and `CTX_sector_permission` +18.14 ± 1.42 (margin +2.37, 1.4×, **8 symbols only** — not comparable to a 14-symbol candidate). Everything else — the geometry, cross and λ candidates at +15.1 to +16.2 — sits inside one sd of E1 and is **not distinguishable from it** |
| 2026-09-08 | 3 | finding | **TLPI's advantage over E1 is confined to gapped books, on both metrics.** By matched lift (h4): symmetric E1 +14.66 vs `TLPI_l1` +14.47 (indistinguishable, as the algebra requires); gapped E1 +13.80 vs `TLPI_l1` **+18.37** (+4.57 in TLPI's favour). This independently confirms the AUC ablation (+0.0479 [+0.0262, +0.0806] gapped, +0.0005 symmetric) |
| 2026-09-08 | 3 | finding | **The adverse excursion falls monotonically with λ**: MAE −0.511 ticks at λ=0 against **−0.295 at λ=8**, a 42 % reduction. Localization improves the path, not only the direction — the one result here that speaks to position risk. Best λ region is **[1, 8]** and the curve is FLAT across it (matched lift 14.1–15.4 pp at h4, AUC 0.675–0.694), so the parameter is not finely tuned |
| 2026-09-08 | 3 | negative | **No risk state predicts a fall** on this session, scored on P(down): depletion −1.44 pp, bid retreat −3.29, any −3.54, spread widening **−8.14 (KILLED)**. The risk-veto family as defined fails. Its real job is the cost of being wrong (MAE, fillability), which is Stage 10.5 and does not exist yet |
| 2026-09-08 | 3 | finding | **E1 is weak in the opening half hour**, not negative: matched lift **+4.04 pp** in OPEN_EARLY (390 rows) against **+17.34** MID and **+17.27** LATE. `TLPI_l1` is the reverse at **+19.07 pp** early — its best phase is E1's weakest. The opposite of a 'morning edge': a morning weakness the λ family may cover. One session, 390 early rows, so a lead |
| 2026-09-08 | 3 | **correction** | The single-draw version of the phase table reported E1 at **−2.10 pp** early. Averaging over 20 control draws moves it to **+4.04 pp**: one draw was enough to invent a negative morning effect that is not there. The row above supersedes it, and it is the concrete reason the draw-averaged estimator was adopted rather than a precaution |
| 2026-09-08 | 3 | **correction** | `REF_all` / `TLPI_l0` (whole-book imbalance) was recorded KILLED at −5.50 pp from one draw. Averaged it is **−4.68 ± 1.18 pp**, inside the WEAK band. It is bad and it is **not** decisively killed; the KILLED list for this run is `GEO_deep_only_bid` (−12.21 ± 1.49) and `RISK_spread_widening` (−6.04 ± 3.14, marginal on its own sd) |
| 2026-09-08 | 3 | negative | **Signal strength does not improve on cleaner evidence.** On the high-quality subset (911 of 2,622 rows) E1 falls **+16.98 → +10.61 pp** and `VETO_E1_no_risk` collapses **+14.62 → +2.97**, while `TLPI_l1` (+15.90 → +14.88), `GEO_touch_dominant_bid` (+16.47 → +15.70) and `CTX_market_permission` (+7.45 → +7.35) hold within 1 pp. How much a candidate leans on low-quality frames is itself a discriminator, and the touch-geometry and λ candidates are the robust ones |
| 2026-09-08 | 3 | **F07 REPRODUCED (exact)** | `results/DOORSTEP_FOOTPRINT_ANALYSIS.csv`, DISCOVERY / `fresh_both` / k=3: hit rate **5.5995 %** vs vol-matched base **2.5924 %**, lift **2.1599×**, **145** distinct doors, 101 symbols — the handoff's 5.60 / 2.59 / 2.16× / 145 exactly. Within-parent t **0.708**: the 'quiet market' condition's own margin is still not established. Stands as DAILY POSITIVE CONTEXT |
| 2026-09-08 | 3 | **F17 REPRODUCED (exact)** | Same source, abn_down k=3: hit rate **1.8754 %** vs **0.7626 %**, vol-matched lift **2.4591×**, shock-matched **2.0290×**, **62** distinct doors — the handoff's 1.88 / 0.76 / 2.46× / 2.03× / 62 exactly. Within-parent t 1.32, the incremental gate it failed in Phase 4.5, unchanged. Stands as DOWNSIDE RISK CONTEXT |
| 2026-09-08 | 3 | **DEPARTURE partially reproduced** | Rebuilt with `state_engine/run_states.py`'s own rule on the DEV panel, 10-session excess vs same-date same-trailing-volatility-quintile control: any DEPARTURE/EXTREME **−0.25 %** (t_date −5.5), persistent ≥2 **−0.55 %** (t_date −6.4), ≥3 −0.59 %, ≥5 **−0.78 %**. The handoff cites −1.3 % to −1.6 %, t −8 to −10: **direction robust and monotone in persistence, magnitude about half, t weaker**. Reported as measured. DEV panel only (sealed window absent) and the date-clustered t is the honest one — overlapping 10-session windows make the raw t too generous. AVOID / DOWNSIDE CONTEXT, not short logic |
| 2026-09-08 | 3 | finding | **`tlpi_1` has the cleanest decile response curve** — P(up) 0.102 → 0.393 with one inversion in nine steps, against three for `imb_l1` and four for `imb_top5`, which turns *down* in its top decile. Monotonicity points at λ ≈ 1 even though E1 wins on the threshold rule |
| 2026-09-08 | 3 | NOT_OBSERVABLE | **Six of the fourteen captured symbols are the only member of their sector** (Bank, Financial Institutions, Food & Allied, Fuel & Power, Miscellaneous, Telecommunication), so their sector tier would be their own pressure renamed. Only Textile (5) and Pharmaceuticals (3) support a real sector tier — `CTX_sector_permission` covers 8 symbols, not 14, and its lift is not comparable to a 14-symbol candidate's |
| 2026-09-08 | 3 | **negative (families A…J)** | **Every elaboration of the pressure level is worse than the level itself.** h4 matched lift: A (plain level, = E1) **+15.78**, H persistent≥3 +12.70, J reversal +12.23, C level+velocity +10.05, D level+velocity+acceleration +9.98, B crossing +7.60, E touch-only +4.83, I exhaustion +1.10, F L1-pos-top5-neg −3.48. Adding velocity costs 5.7 pp, acceleration a further 0.1, a crossing requirement 8.2. The proposed sequence *touch pressure → persistence → velocity → acceleration* does not survive: every step after the first subtracts. G (deep bullish, touch bearish) scores **+9.34 pp on P(down)** — touch locality from the other side |
| 2026-09-08 | **4** | **finding (Stage 8 question answered)** | **Relative strength carries the information; aligned strength does not.** h4 matched lift: `CTX_market_permission` (E1 **and** market up) **+8.05** — 7.7 pp WORSE than E1 alone (+15.78); `CTX_market_weak_share_strong` (E1 while the market tier is **negative**) **+16.15**; `CTX_share_resid` (share minus market) +15.14; and `CTX_xs_rank_top` (pure cross-sectional rank, no threshold on the share's own pressure) **+21.75 ± 1.74**, the best candidate in the run. A share pushing up while the market is not beats a share pushing up with it — the Stage 8 hypothesis measured rather than narrated |
| 2026-09-08 | 3 | negative | **Cross-timescale combinations subtract, with one exception.** Only `X_E1_and_TLPI1` (+16.18 ± 0.60) holds level with E1. Every added condition costs: touch-dominant + no veto +13.50, persistence + no veto +10.28, TLPI + velocity +8.47, TLPI + velocity + acceleration +8.02, E1 + market permission + no veto +6.23 |
| 2026-09-08 | **3** | **finding (matters for Stage 10.5)** | **The probability edge and the expected move are not the same horizon.** E1: h2 lift +11.09 pp with mean **+0.102** ticks and MAE **−0.059** (adverse excursion 0.58× the gain); h4 lift **+15.78** with mean +0.135 and MAE −0.287 (2.1×); h8 lift +12.88 but mean **−0.013** — at h8 the up moves are more frequent and the down moves are bigger, so the direction edge does not survive into expectation. Reporting P(up) alone would have hidden this entirely. h4 maximises the probability lift, **h2 has by far the best path** |
| 2026-09-08 | 3 | finding | **Response curves: `tlpi_1` is the most monotone candidate** — Spearman ρ over ten deciles 0.988 for P(up) (one inversion in nine steps) against 0.948 for `imb_l1` and **0.717** for `imb_top5`, which turns *down* in its top decile. Touch locality visible in the shape of the curve, not just in a number. And **ρ(MAE) is strongly positive for every score** (0.59–0.95): a higher reading monotonically reduces the adverse excursion, so the score predicts the path and not only the direction |
| 2026-09-08 | **3** | **FIRST THING THAT BEATS E1** | **Quote and tape are different information.** Every quantity invented from the displayed book (time-to-consume, walk-the-book cost, Herfindahl concentration, pressure-per-cost) correlates with E1 at ρ **0.58–0.66** — different algebra on the same source is still the same source; the one genuinely uncorrelated one (`hhi_asym`, ρ 0.04) carries no signal. The independent information is the **tape**: `qt_flow` = signed interval volume / top-5 liquidity, ρ with E1 **+0.08**. Causal rank-fusion `QT = causal_rank(E1) + causal_rank(qt_flow)`: **h2 AUC 0.7058 → 0.7770, delta +0.0712, 95 % CI [+0.0280, +0.1067], ESTABLISHED**, improving in **9 of 10** symbols. h4 +0.0188 and h8 +0.0113 both span zero. This is the preregistration's own primary question — does *fused* book+flow beat the strongest standalone baseline — answered yes at the shortest horizon on one session |
| 2026-09-08 | 3 | **look-ahead caught and removed** | The first version of `qt_fusion` combined its inputs with `rank(pct=True)` over the whole frame — a percentile depending on every row after t, so the value could not be computed live. It **inflated h4 from not-established to established** (+0.0348 CI [+0.0122, +0.0553] leaky, against +0.0188 CI [−0.0116, +0.0465] causal). Replaced with `causal_rank`: percentile among that symbol's own strictly-earlier values, NaN until 20 observations exist. `tests/test_invented.py::test_no_feature_reads_the_future` corrupts every frame past a cut and requires bit-identical values before it — that test is what caught it |
| 2026-09-08 | 3 | finding | The **single frame beats the accumulation**: 4-frame cumulative signed volume *hurts* (−0.021 AUC) where the current frame's flow helps. Same shape as level-beats-derivative — it is flow arriving NOW, not flow already in the price. And the gain is largest at the **shortest** horizon (h2 ≈ 87 s), which is also where E1's adverse excursion is smallest relative to its gain (0.58× vs 2.1× at h4): edge and path point the same way for once |
| 2026-09-08 | 3 | measured (Stage 10.5 input) | Walking the displayed book gives a slippage model for free: median round-trip cost **1.00 tick at 500 shares**, **1.75 at 2,000**, **2.43 at 10,000**; buying costs more than selling at size (`slip_buy_10000` 1.43 vs `slip_sell_10000` 1.06 median). These are displayed-book estimates, not realised fills, but they are the first cost numbers in the repository that come from data rather than assumption |
| 2026-09-08 | **10.5** | **ECONOMIC TEST — every rule LOSES TO COST** | Against the book's own quoted round trip (1.00 tick at 500 shares): E1 alone nets **−0.90 / −0.87 / −1.01** ticks at h2/h4/h8; the QT rule nets **−0.66 / −0.60 / −0.74**. Even a **perfect exit at the maximum favourable excursion** — unachievable, it needs the future — tops out at **+0.061 ticks at h8**, zero within noise. **As a taker strategy this is dead by a factor of ~2.5, not by a hair**; no threshold tuning closes a 0.6-tick gap when the whole signal is worth 0.4 ticks |
| 2026-09-08 | 3 | finding | The QT rule does everything a better signal should: **triples E1's expected move** (+0.102 → +0.341 ticks at h2), turns the mean adverse excursion **positive** (+0.139 — on average the position never goes against you inside the window), and improves MAE/gain from 0.58× to 0.41×. It is a genuinely better signal that is still not a tradeable one |
| 2026-09-08 | **10.5** | **direction this sets** | The cost layer did not qualify the finding, it **inverted what to build next**. Every number assumes crossing the spread. A limit order resting at the touch pays no spread — it earns part of it, and the same +0.34 ticks becomes a different proposition. What that needs is a **fill model**: posting at the bid, you often are NOT filled when the signal is right (price leaves without you) and always filled when it is wrong. That adverse-selection asymmetry is measurable from this same data and is the next thing to build |
| 2026-09-08 | **1** | **EcoSoft tz question RESOLVED** | A third owner-recorded HAR (2026-09-08 08:57 UTC, AAMRANET, 7 polls @ 11.0 s, 10 records kept, 33 sensitive keys stripped, **0 leaked**) settles what `SCHEMA_MAP.md` left open: **`Depth.DateTime` is Dhaka wall-clock carrying a spurious `Z`, and it is the book's LAST-MODIFICATION time, not the response time.** Two independent proofs — read as UTC it would be 20:11 Dhaka, in the future; and the sibling `Company.UpdateDateTime` carries no `Z` at all and sits 10 min before the poll. The `Z` must be stripped or every EcoSoft frame lands 6 hours ahead |
| 2026-09-08 | **1** | **finding — explains the session rejection** | **The public LankaBD depth payload carries no last-modified field at all** (checked against the raw record, not assumed: only `openPrice`, `lastTradePrice`, `yesterdayClosePrice`, two HTML tables; the sole date is the HTTP `Date` response header). EcoSoft publishes a staleness stamp; the public source does not. Polling a stamp-less source at 10.3 s when it refreshes every ~43 s is why 85 % of payloads were byte-identical and today's session failed its quality gate — there was nothing to poll *on* |
| 2026-09-08 | 1 | note | 10-level book confirmed on a **second, unlike** symbol (AAMRANET, Z-category at 17.70; the first recording was CITYGENINS), so the 10-row cap is the terminal's rather than one symbol's accident. In the recorded AAMRANET book the largest single quantity — a 22,500 bid — sits at level 3, which a public 5-level view shows but a 2-level one would miss |
| 2026-09-08 | 1 | **STILL OPEN** | All 7 polls byte-identical (`distinct body_sha256 = 1`): recorded 47 min after the 14:10 close, so a frozen book proves nothing. **Whether EcoSoft's book updates faster than the public ~43 s during an OPEN market remains UNKNOWN.** Only a recording inside 10:00–14:00 Dhaka answers it. Raw HAR deliberately NOT committed — it carries request cookies and authorization the probe never reads |
| 2026-09-08 | 3.5 | EXISTS | `bdlib/panels.assert_single_panel` refuses (raises) rather than warns on a cross-sectional aggregate spanning the 2024-02-22 coverage break; pinned by `tests/test_edge_families.py::test_a_cross_sectional_aggregate_spanning_the_coverage_break_is_refused` |

---

## 7. What this lock explicitly does not do

* It does not re-open any question closed in `REJECTED_CANDIDATES.md`. P45-1 … P45-8 and
  I-001 … I-017 stand.
* It does not alter `micro/MICRO_PREREG.json`, the universe, thresholds, splits or the sealed
  holdout.
* It does not promise that any stage will succeed. Stages 6, 7 and 10 each have a realistic
  path to **BLOCKED**, and BLOCKED is a valid outcome (AGENTS.md §27).
* It does not add product scope. The four ★ items exist only because stages 2, 4, 8, 10 and
  11 cannot be connected without them.
