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
