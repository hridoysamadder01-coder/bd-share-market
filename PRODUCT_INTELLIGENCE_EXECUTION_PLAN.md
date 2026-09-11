# PRODUCT INTELLIGENCE EXECUTION PLAN

> **Companion document. Authored by Hridoy Samadder, 2026-09-11.**
> `ROADMAP.md` is an append-only locked contract and is NOT rewritten by this
> file. This plan sits on top of it and says how the product is built outward
> from the research truth the roadmap already governs.
> `AGENTS.md` and `HRIDOY_RAW_EVIDENCE_LOCK.md` continue to apply unchanged.

---

## 0. Why this document exists — the failure that triggered it

On 2026-09-11 Hridoy found that the shipped UI had **invented research in the
frontend**. It defined seven "attention rules" in `tower/ui/static/labels.js`
and presented their output as findings, with confident causal sentences.

Audited against the repo's own committed ledger:

| UI rule as shipped | weight | What the ledger actually says |
|---|---|---|
| `Quiet accumulation` — `rel_volume_z ≥ 2` + calm price | **9 — highest, lead item** | **P45-1 ❌ SUBTRACTS INFORMATION.** lift 1.59 vs 1.76 for *plain* abnormal volume; the calm condition *lowers* the hit rate, paired t −2.6 and −6.8. Named in `SURVIVING_RESEARCH_LEADS.md` under "**What is explicitly NOT carried**". |
| `Heavy selling` | 8 | Every v1 down-door candidate was a reversal artefact after an already-open limit-up; rejected. |
| `Activity without follow-through` | 7 | No ledger entry at all — **invented**. |
| `Thin and fragile` | 6 | No ledger entry at all — **invented**. |
| `Range squeezed` — "the stock is coiling" | 5 | **P45-7 ❌ UNMEASURABLE** (213 occurrences). |
| `Abnormal for days` | 4 | **P45-5 ❌ DIES AT THE INCREMENTAL GATE.** Named under "explicitly NOT carried". |
| `Abnormal volume` `≥ 2.5` | 3 | Nearest to the only carried lead (F07/F15), but the threshold was made up and F07's defining market-quiet condition was stripped out. |

Six of seven were wrong. The ranking was **inverted**: the most thoroughly
killed idea carried the highest weight and led the product.

The ledger's own words, which the UI contradicted:

> Everything in `REJECTED_CANDIDATES.md` §Phase 4.5: quiet accumulation,
> absorption / dip-recovered, closing strength, persistence, idiosyncratic
> moves, every v1 down-door candidate, and every limit-up footprint.
> — `SURVIVING_RESEARCH_LEADS.md`, "What is explicitly NOT carried"

> not accumulation (price is not calm — **the calm variant is weaker**)
> — `SURVIVING_RESEARCH_LEADS.md`, Lead B

**Correction shipped 2026-09-11.** The invented layer is gone. See §1.

---

## 1. TRUTH BOUNDARY — the law that comes before every other phase

**The UI consumes engine and ledger output. It does not produce research.**

Enforced, not merely stated:

1. `labels.js` exports `OBSERVATIONS`, not rules. Each entry restates **one
   measured number**. No mechanism, no intent, no forward claim.
2. Every observation carries a `verdict`, rendered on every row:
   `OBSERVED` · `UNVALIDATED` · `NOT_TESTED` · `REJECTED`.
3. `labels.js` exports `FORBIDDEN_CLAIMS` — a greppable list of shapes the UI
   may never present as a finding, so a rejected idea cannot return under a new
   name.
4. A standing banner sits above every measurement list:
   *"Research verdict: 0 tradeable candidates. Nothing below is a signal."*
5. `#/research` renders the ledger verbatim: what is carried, what was killed,
   and why. The screen **reports** verdicts; it does not compute them.
6. Feature labels that asserted a mechanism were renamed to what they measure
   (`accumulation_proxy` → "Volume weighted toward up-closes vs down-closes").

**Status: DONE.**

---

## 2. The target

BD Market Intelligence OS does not produce BUY/SELL. It answers, in order:

> What is forming in the market now → is it market-wide, sector-specific or
> share-specific → which share is exceptional against the others → how unusual
> is its current state against its **own** full history → what happened after
> every past occurrence of the same measurable state → does the live book/tape
> confirm or contradict it → and how trustworthy is the evidence.

---

## 3. Phases

Each phase is executed only after the one before it is verified. No phase may
introduce an interpretation the engine or ledger does not produce.

### Phase 1 — Truth boundary ✅ DONE (2026-09-11)
As §1.

### Phase 2 — Historical intelligence spine
`/api/stock/{sym}` currently returns `history: null`. The foundation is not
thin: **917,206 extended-EOD rows, 408 symbols, 2012-10-01 → 2026-09-07**, plus
a minute dataset of **43.1M irregular trade-minute rows, 411 series,
2015-10 → 2024-01**. None of it reaches the UI.

**Architectural change, decided by Hridoy 2026-09-11:** the "do not touch the
API" lock is lifted **for read-only additions only**. Engine logic, research
code, frozen artefacts and existing response shapes stay untouched. New
read-only endpoints may be added.

Missing periods are never interpolated. Coverage breaks, late listings, the
floor era and corporate-action-suspect rows stay visible as gaps.

### Phase 3 — Mission Control = "what is happening now?"
First five seconds: market state → sector state → exceptional shares → what was
measured. Raw evidence does not sit on Home.

### Phase 4 — Market → Sector → Share decomposition
A share at +4% is not "strong" until it is split into its market component, its
sector component and its share-specific residual. The Exceptional Share list is
built from the residual, not the headline change.

### Phase 5 — Stock Command Center = stock time machine
Opens with what is happening now, then the **full available history** — price,
volume, market-relative and sector-relative behaviour, circuit and floor
regimes, and a company timeline. The reader sees where today's activity sits in
that stock's own percentile, not just today's number.

### Phase 6 — Similar-state engine
Not pattern-shape matching. **Measurable-state** search: every historical
occurrence of today's state, winners *and* failures, then forward return, MFE,
MAE, time-to-target and adverse excursion at 3/5/10/20/30 days.
**Point-in-time construction is mandatory** — no count may be produced with
outcome leakage.

### Phase 7 — Live market radar
When the market is open, the live layer sits on the historical one: L1/L2
depth, touch pressure, spread, replenishment, tape flow, circuit proximity,
source disagreement, freshness. The surviving microstructure observation is the
**touch-locality of displayed imbalance** — shown as research evidence, never
as a signal, and the ledger itself says the next unit of work is **capture, not
analysis**.

### Phase 8 — Pre-move / buildup, in its correct place
The daily-bar version of this question is exhausted and contradicted. It may
only be re-opened with genuinely **new** information: intraday state,
market/sector/share decomposition, circuit context, corporate-action-clean
population. If new evidence survives, a buildup state exists. If not, it is
KILLED and stays killed.

### Phase 9 — Research verdict terminal ✅ DONE (2026-09-11)
Every hypothesis shows: what is tested · population · evidence ·
counter-evidence · sample size · historical failures · costs considered ·
verdict. The reader never sees "the AI says"; they see what the system stands on.

### Phase 10 — Decision support, last
No BUY/SELL, no position sizing, no target, no probability-of-profit enters the
production UI until Stage 10 (position logic), Stage 10.5 (cost and liquidity)
and Stage 11 (falsification) have passed. Stage 12 is the final transformation:
`OBSERVATION → CONTEXT → EVIDENCE → VALIDATED POLICY → DECISION SUPPORT`.

---

## 4. Navigation

```
Mission Control | Radar | Sectors | Stocks | Research | Data Trust
Stock:  Now | History | State | Relative | Live | Company | Evidence
```
Mobile keeps the same mental model — it is not a shrunk desktop.

---

## 5. Acceptance law

Within **5 seconds** of opening the app a reader can say:
*what the market is doing, which sector is behaving differently, which share is
exceptional, and why.*

Within **15 seconds** of opening a stock:
*how unusual today's behaviour is against that stock's own history, what
happened after the same state before, and how far the evidence can be trusted.*

---

## 6. The standing instruction

> Build the product outward from the research truth, the historical data, and
> the locked roadmap. Do not build a dashboard, and do not create a second
> research engine in the UI. The UI must expose the existing market
> intelligence, full historical context, state comparisons, live observation,
> research verdicts, and provenance. **Any interpretation not produced or
> supported by the engine or the ledger must not appear as a fact or a signal.**
