# DATA_ACQUISITION_ARCHITECTURE — order-book / order-flow footprint on DSE

> **Architecture only.** Written 2026-09-02/03 after the minute-data QA; **v2** after four
> independent adversarial reviews (factual accuracy against the fetched evidence,
> microstructure logic, recorder losslessness and sizing, scope and QA consistency) —
> §11 lists what v1 got wrong. Nothing in this document is implemented; no signal,
> footprint, precursor or hypothesis was tested while writing it. It answers four
> questions the owner asked:
>
> 1. Which L2 / order-book / tick fields does the locked target need?
> 2. Which of them can a DSE retail investor obtain **live** from a broker / platform?
> 3. If no history exists, how is a **lossless** recorder built from today, on free infrastructure?
> 4. Which parts of the target can the existing trade-minute dataset validate, and which can it not?
>
> **Evidence grades** (every factual row carries one):
> **V** verified first-hand in this session (page, PDF, JSON endpoint or JS bundle fetched and read — by the author or by a reviewer whose fetch is on file);
> **S** search-result snippet only (source named, page not fetched — the DSE website itself was
> unreachable from this container: TLS reset / HTTP 503 on every attempt);
> **Q** computed here from our own data (minute QA, or `qa/minute_observability_sample.py`);
> **U** unverified — inference, or nothing found. **U rows are owner-verification items, not facts.**
> Numbered sources are in §10. Parameter values in §6 (5 s, hourly, ≤ 0.5 req/s …) are
> **illustrative defaults, not configuration**.

---

## 0. Verdict in ten lines (plain language first, detail in the sections)

1. **What the target needs.** Three things, all recorded continuously: the **live queue of buy and sell orders** for each stock (price and quantity at each visible level, ideally how many orders sit at each), **every trade with its time to the second**, and a **dated record of the rules in force** (price limits, floor prices, session hours, tick size). The list of every individual order and who placed it exists only inside the exchange; the design does not depend on it, and the one form of manipulation that needs it (proving that connected accounts traded with each other) is explicitly out of reach — only its shadow can be measured.
2. **Two quantities define the door on this market and were missing from v1.** Before a limit-up, what matters is how many shares are offered between the current price and the day's ceiling (the "shares to the door", readable from the depth screen), and what the 09:55–10:00 pre-opening auction shows, because a stock can open already locked at the limit. Both are now in the quantity list (§1, Q13–Q14).
3. **Live data of this kind exists on the retail side.** The DSE-FlexTP terminals issued through the investor's TREC broker — DSE-Mobile (2016 manual: best bid/ask "or Market Depth", last two transactions, last price stamped to the second) and DSE Investor / M-Invest (2023 guide: a Market Depth component, three **Time and Sales** screens, market statistics with total trades) [1][2, V]. The DSE website says registered DSE-Mobile / M-Invest users get "live top 10 buy and sell orders" [8, S]. Whether the depth screen shows the number of orders per level, whether depth is delivered for all stocks or only the one in focus, and what the Time and Sales rows contain is **not written in either manual** — that is owner action **D-13**.
4. **A public, login-free cross-check exists.** LankaBD's website returns the buy/sell queue for one stock at a time, with the day's trade count, volume and value, without login (one real response is saved under `evidence/`) [3, V]. It shows no order counts, serves one stock per request and pushed back when called in bursts; it is a cross-check and a fallback, not the main recorder (§4).
5. **No public historical order-book or tick dataset for DSE was found.** ICE lists a real-time Level 1 / Level 2 DSE equities feed commercially; whether DSE tick history is purchasable from it is not stated on the page [6, V]. Everything else public is daily or minute bars. **The order-book history starts on the day the recorder starts.** This is the single most important consequence of this document.
6. **The recorder is designed to lose nothing and to prove it.** It keeps the raw bytes exactly as received, stamps every record with two clocks, numbers records so a restart or a second machine can be reconciled, writes a heartbeat every few seconds and an explicit gap record whenever anything is missing, hashes every file and chains the hashes, runs on two independent machines, and parses only on replay. Nothing is deduplicated, corrected or filled at capture time — the same rule the QA already enforces (§6).
7. **Free infrastructure.** Oracle Cloud Always Free (Arm A1, 2 OCPU / 12 GB, 200 GB block storage) is the only free tier with enough disk [27, V], but its idle-reclamation rule (CPU, network and memory all below 20 % over 7 days) **matches a recorder that runs 4.5 hours a day** — the design must keep the machine deliberately non-idle or accept re-provisioning. Google Cloud's e2-micro fits but is tiny; GitHub Actions and Codespaces cannot cover ~100 hours a month; AWS is now credit-based. A home machine in Bangladesh is the natural second capturer.
8. **What the minute data can do.** It validates trade-tape behaviour and session structure only (prints per day, time-of-day profile, closing-window share, snapshot-row artefacts). Its pre-2020 rows are single-price on ≈ 99 % of minutes and its 2020+ rows on ≈ 75 % — read as **two capture regimes** (a hypothesis, §7). It cannot validate the book, order counts, cancellations or trade direction, and it **cannot validate the recorder**: it ends 2024-01-31, so there is no overlap.
9. **Two things only the owner can do, and nothing else starts until both are done.** **D-13:** on a trading day, open the broker's app (DSE-Mobile, or DSE Investor / M-Invest in a browser) and take screenshots of the *Market Depth* screen and the *Time and Sales* screen for one stock, including the pre-opening period if the app shows it; then say which broker and which app it is, and whether the app allows being logged in on two devices at once. **D-14:** ask the broker in writing whether you may record what your own app shows you, for personal research. If the answer is no, the plan falls back to a much coarser public source and most of the target is lost (§9).
   *(বাংলায়: D-13 — ট্রেডিং দিনে ব্রোকারের অ্যাপে "Market Depth" আর "Time and Sales" স্ক্রিনের স্ক্রিনশট দাও, আর বলো কোন ব্রোকার, কোন অ্যাপ, দুই ডিভাইসে একসাথে লগইন থাকে কিনা। D-14 — ব্রোকারকে লিখিতভাবে জিজ্ঞেস করো নিজের অ্যাপে যা দেখা যায় তা নিজের গবেষণার জন্য রেকর্ড করা যাবে কিনা।)*
10. **Nothing is implemented.** Recording, once permitted, should start before any research design, because the history clock starts only then.

---

## 1. The locked target, restated as observable quantities

The target (DOORSTEP_FOOTPRINT_DESIGN.md): the footprint of accumulation or manipulation
*before* the door — a limit-up or abnormal move in the next 1–10 sessions. On EOD data
the only footprints available were volume, range and close-location (F01–F18). Order-flow
data changes *what* can be observed, so the target is restated as quantities, each with the
documented DSE mechanic it is meant to catch. Functional forms below are **illustrative
names**, not definitions; definitions are pre-registered only once data exists.

**What the documented DSE mechanics look like** (BSEC enforcement, 2025, [18][19], V):
"a series of transactions … to create the false appearance of active trading", executed
"within their network … using multiple brokerage firms and several beneficiary owners (BO)
accounts", amounting to 11.27 % (Sonali Paper) and 37.84 % (Fortune Shoes) of total traded
volume in the investigation periods, with holdings above 10 % kept undeclared; an anonymous
brokerage managing director quoted by TBS adds that BO accounts are "opened in the names
of acquaintances" [19]. The 2010–11 crash probe found "massive manipulation" [24, V]. On the
price side, the EOD work's pre-run check showed that on days a stock gains ≥ 9.5 % (prev
close < Tk 200), 84 % close *on the high* (design doc, §Circuit band) — a descriptive fact
recorded there; whether it reflects a queue at the limit is exactly what Q1 / Q13 would measure.

**Two mechanics the market structure imposes.** In a price-time book a buy order at the
upper limit executes against every ask below it; a *resting* queue at the limit can exist
only once the ask ladder up to the limit is gone — so "queue at the limit" is a door-state
quantity, and the pre-door quantity is the **ask ladder between the touch and the limit**
(Q13). With a Tk 0.10 tick, ten visible levels span at most Tk 1.00: at Tk 10 that is the
whole 10 % band, at Tk 20 half of it, at Tk 200 one-twentieth — exact at the low-price end
where the BSEC cases and the spoofing literature's targets sit, a lower bound elsewhere.
And because a **pre-opening call auction** precedes the open (09:45–10:00 at launch, 09:55–10:00
since 2022-11-15 [9][11]), a stock can open already locked (Q14); 23 % of symbol-days in
the minute sample have a print by 10:05 (§7).

| Q | Observable quantity | Mechanic it targets | Minimum data | Level |
|---|---|---|---|---|
| Q1 | Door state and continuation: quantity (and, if shown, number of orders) resting at best bid once bid = upper limit; growth after lock; carry-over into the next pre-open (symmetric at lower limit / floor) | limit-up herding once the door is open | best level price/qty; order count per level if displayed | L1 (+ L2 detail) |
| Q2 | Depth imbalance over the top-k levels and its persistence over minutes (illustrative name: bid depth vs ask depth) | accumulation / absorption, pump preparation | top-5 … top-10 levels, price+qty | L2 |
| Q3 | Trade side (buyer- vs seller-initiated volume share) | absorption vs distribution; pump phase | trades + L1 at ≤ 1 s **with receipt ordering preserved**; side by the quote rule off the last pre-trade snapshot; **in a locked book (bid = ask = limit or floor) the side is known by construction** and no classifier is applied; an explicit side flag on Time and Sales — D-13 | trade + L1 |
| Q4 | Trade-size distribution: prints count vs volume, quantity **relative to the symbol's market lot**, share of one-share / odd-lot prints, repeated identical lot sizes (a sweep yields one print per level consumed, so this counts prints, not orders) | series trading, small-lot price setting | trade tape (time, price, qty); lot per symbol-date (F-REF); odd-lot prints separated if flagged (U) | trade + reference |
| Q5 | Order add / cancel rate, order lifetime, layering (size concentrated 2–5 ticks away, cancelled when touched) | spoofing / layering | order-by-order events (L3); *partial* from snapshot diffs minus prints | L3 (partial from L2) |
| Q6 | Matched-lot bursts: same quantity at the same price alternating sides within seconds; trade-count spikes with no price or net-volume change; the passive leg resting in the book before it is hit | wash / circular trading | trade tape + L1 (sign) + L2 (resting leg); **proof needs account or broker IDs** (regulator-only) | trade + L1 + L2 (+ IDs) |
| Q7 | Closing-window behaviour: prints in the last 30 minutes of the *continuous* session (13:50–14:20 under current hours) vs the day; the post-closing session's single-price prints (14:20–14:30); the *published* close vs the last print | marking the close — the close is the weighted average of those 30 minutes [13], the sole price of the post-closing session [12], and **tomorrow's limit reference** [9]: a marked close moves tomorrow's door. The minute QA could not reproduce the 30-minute rule on < 50 % of closes (QA §9) — D-10 stays open | trade tape with second timestamps; per-date continuous/closing boundary; post-closing prints kept separate; the published closing price | trade + session + reference |
| Q8 | Ask-side depletion / refill: large resting asks consumed without price moving; refill latency (reported as "≤ snapshot interval", never zero, because an instantaneous refill is indistinguishable from a disclosed-quantity order — U whether DSE has them) | absorption before a move | L2 top-k time series + trades | L2 + trade |
| Q9 | Time-of-day activity profile per symbol vs its own baseline | pump timing, opening-auction games | trade tape | trade |
| Q10 | Block / spot / buy-in board prints separated from public-board prints | transfers between related accounts | segment flag on prints (U), or the block-transaction list | trade / disclosure |
| Q11 | Dated disclosures: DSE "unusual price" query letters and replies, sponsor/director sale declarations, > 10 % holding declarations, circuit-breaker list | door timestamping, post-hoc labelling | daily disclosure scrape | disclosure |
| Q12 | Regime state per date: upper/lower limit tier, floor price, halt/suspension flags, spot-market periods around record dates, ex-date reference resets, session hours, tick per price band, lot | every Q above is conditional on it | dated rule journal | reference |
| **Q13** | **Shares to the door**: cumulative resting ask quantity from best ask up to and including the day's upper-limit price (symmetric: bid quantity down to the lower limit / floor); exact when the limit lies within the displayed k levels, otherwise a flagged lower bound | cost in shares of opening the door; thinning of the ask ladder before a limit-up | top-k depth (price, qty) + the date's limit (F-REF) | L2 + reference |
| **Q14** | **Pre-open state**: indicative price and matched volume per snapshot during the pre-opening session; unmatched surplus at the indicative; the pre-open book if displayed; opening prints at 10:00:00 (count, quantity, price vs limit); auction volume / day volume | open-at-limit; auction layering (enter / cancel before 10:00); queue carry-over | pre-open depth or indicative fields (F-AUC); trade tape. The DSE Investor guide describes a "Pre-Open" watchlist view with a mid-bid-ask (MBA) field [1] — D-13 | L2 (auction) + trade |
| Q15 *(gated)* | Own-order lifecycle: queue ahead at placement, time to fill, implied cancellations ahead (queue ahead at placement − prints at that price since placement) | queue turnover at the limit; cancellation ahead of the crowd | the account's own order events — requires placing one-lot passive probe orders, which the repository's "no BUY/SELL" rule forbids → new owner decision **D-18** | L3 (own orders only) |

**Measurable in principle from retail-obtainable data, for whichever symbols the terminal
delivers depth and prints for (D-13):** Q2, Q4, Q7, Q8, Q9, Q11, Q12, Q13. Q1 in quantity
only unless order counts are shown. Q14 only if a pre-open view is displayed. Q3 by
inference (exact in a locked book, noisy in fast minutes — §2.4). Q5 partial. Q6 proxies
only. Q10 depends on a segment flag. Q15 gated by D-18. **Nothing here is a result; every
line is an assessment of what the data could show.**

---

## 2. Field taxonomy and the fields × patterns matrix

### 2.1 Fields

| Code | Field | Notes |
|---|---|---|
| F-L1 | best bid / ask price and quantity, LTP, last qty, last time | the touchline; the DSE Investor guide calls bid/ask "indicative" [1] — a displayed quote, not a firm executable price for a POD click; for research it is the book top. The guide also describes a watchlist "Directions" arrow showing "the last trade direction" [1] — probably tick direction, not aggressor side (D-13) |
| F-L2 | depth by level: price, quantity, **number of orders** per level, k levels | k = 10 per the DSE site [8, S]; order count per level **unknown on every retail screen** (U); whether depth is streamed for all symbols or only the selected one is **unknown** (U, D-13) |
| F-L3 | order-by-order: add / modify / cancel with order IDs and timestamps | no evidence any of this leaves the exchange except to the trading engine and its surveillance (U); queue position is *derived*, not a field |
| F-TR | trade prints: time (to the second on retail screens; the engine stamps finer internally, U), price, quantity, and where available side, board / segment, trade ID | DSE Investor has three Time and Sales screens: "Main", "History", "Business Done" [1, V]; "Business Done" is likely a price × volume summary rather than a per-print tape (U, D-13); row fields are not documented |
| F-BRK | buyer / seller broker (TREC) code on each trade | no retail screen shows it; DSE publishes broker-level *totals* only (U) |
| F-ACC | BO account identity | regulator / CDBL only |
| F-AUC | pre-opening auction indicative price, matched volume, surplus, or the pre-open book; post-closing session prints | DSE Investor: "Pre-Open view displays the margin and the MBA (Mid Bid Ask)" [1, V]; whether the pre-open *book* is shown is U (D-13). The top-k depth during 09:55–10:00, if displayed, **is** the auction book |
| F-REF | price-limit tier by price band, lower-limit episodes, floor prices and per-stock exits, tick per band, lot, halts, corporate actions | must be a **dated journal**: BSEC changed these repeatedly and from July 2026 the bourses set them themselves [14, V] |
| F-DIS | query letters, declarations, block-trade list, circuit-breaker list, PSI | public daily; LankaBD's navigation carries "Announcements" with an Archive, "Block Transactions" and "Circuit Breaker" pages (links seen on fetched pages; content not fetched) [4] |
| F-SESS | session windows per date (Ramadan, special days, make-up Saturdays, the 2022 energy-saving schedule) | the minute QA showed 279 window-shift dates (121 of them 2016 snapshot noise) (Q); a per-date calendar is required |

### 2.2 Which fields each pattern needs

N = necessary, H = helpful, — = not needed. "N (label)" = needed to *label* an episode after
the fact, not to *measure* it. The last column is an assessment of measurability, **untested**.
Whether each field is obtainable for the whole universe or only for the symbol in focus is
unknown (D-13); an intraday "market quiet" condition (design doc) exists only in the former case.

| Pattern | F-L1 | F-L2 top-10 | F-L2 order counts | F-L3 | F-TR | F-TR side | F-AUC | F-BRK | F-ACC | F-REF / SESS | F-DIS | Measurable in principle with retail data? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Quiet accumulation / absorption | N | N | H | — | N | H | H | — | — | N | H | yes (Q2, Q8, Q4, Q13) |
| Pump by series trading | H | H | H | — | N | H | H | H | N (label) | N | N (label) / H (measure) | yes as footprint, not as proof |
| Limit-up door: approach and lock | N | **N** (approach: shares to the door, Q13) | H (many small orders vs one hand) | — | N | H (approach only; known by construction at lock) | **N** (open-at-limit, carry-over) | — | — | N | — | yes for quantities; order count only if shown (D-13) |
| Layering / spoofing | N | N | H | H (**N for attribution**) | N | — | H (auction-period enter / cancel, visible only if the pre-open book is shown) | — | — | N | — | footprint yes at 1–2 s — a layer must persist long enough to be read by the retail eyes it targets; attribution no; adds vs cancels of *different* orders at one level cannot be separated without order counts |
| Marking the close | H (end-of-day spread) | — | — | — | N | H | H (post-closing prints) | — | — | N (continuous / closing boundary per date + published close) | — | yes (Q7) — the 30-minute rule makes the window explicit |
| Wash / circular trading | H (sign) | H (resting leg) | H | — | N | H | — | **N** | **N** | H | H | **proxies only** (Q6) |
| Momentum ignition / opening games | N | H | — | H | N | H | **N** | — | — | N | — | partial; auction data if displayed (D-13) |

### 2.3 What the literature says the data must be (S unless noted)

- **Lee, Eom & Park (2013)**, *Journal of Financial Markets*: spoofing identified with complete order and trade data **with account identities** from the Korea Exchange; the spoofers exploited KRX's disclosure of aggregate depth — orders placed to be *seen*, away from the best quotes, cancelled later; targets were low-price, small-cap, high-volatility, low-transparency stocks [26]. Consequence: the targeted spoofing must persist at human-reading cadence, so 1–2 s snapshots capture the *footprint*; *attribution* needs L3 plus accounts.
- **Comerton-Forde & Putniņš (2011)**, *Journal of Financial Intermediation*, "Measuring closing price manipulation": a manipulation index built from prosecuted cases using trade and quote features — day-end return, next-day reversal, abnormal end-of-day spread, abnormal trading frequency. **Comerton-Forde & Putniņš (2014)**, *Review of Finance*, "Stock price manipulation: prevalence and determinants": roughly 1 % of closes manipulated, concentrated in low-liquidity, high-information-asymmetry names [26]. DSE's explicit 30-minute rule [13] and single-price post-closing session [12] make Q7 a clean measurement.
- **Cont, Kukanov & Stoikov (2014)**, *J. Financial Econometrics*: over short horizons price change is driven by **order-flow imbalance at the best quotes** — L1 changes suffice for price impact [26]. Caveat for snapshots: their OFI sums best-quote *events*; the difference between two snapshots equals that sum only if the best price did not change inside the interval — true of most 1–2 s intervals at a Tk 0.10 tick, not of fast minutes.
- **Cao, Hansch & Wang (2009)**, *J. Futures Markets* (ASX): the open book beyond the best quotes contributes ~22 % of price discovery [26] → top-10 is *helpful* for price discovery. For Q13 the deeper book *is* the quantity, so that criterion does not carry over.
- **Easley, López de Prado & O'Hara (2012)**, *RFS*: VPIN with bulk-volume classification needs only a trade tape (time, price, quantity) bucketed by volume — no quotes [26]. Whether it means anything in a limit-pinned market is a research question, not asked here.
- **Aggarwal & Wu (2006)**, *J. Business*: manipulated stocks show abnormal returns and volume during the pump, losses after — a trade/volume signature [26].
- **Cumming, Johan & Li (2011)**, *JFE*: indexes the specificity of exchange rules on manipulation, insider trading and broker–agency conflict across 42 exchanges and relates them to liquidity [26] — rules are a variable. The evidence that DSE's *limits* are a variable is the dated list in §3, not this paper.
- **Trade classification.** Lee & Ready (1991) give the quote rule with tick-rule fallback [26]. The evidence that classification accuracy degrades with coarse timestamps and in limit-order books is Holden & Jacobsen (2014, *J. Finance*) and the LOB studies of Ellis, Michaely & O'Hara (2000), Odders-White (2000) and Chakrabarty, Pascual & Shkilko (2015) — **named by a reviewer, not searched here (U)**.

### 2.4 Cadence conclusion — conditional, and what sampling loses per Q

**Working assumption (literature-based, S), not a finding:** for Q1, Q2, Q4, Q7, Q8, Q9, Q13 a
**1–2 s top-10 snapshot plus a complete trade tape** is sufficient, because queues at the
limit, the ask ladder and absorption are states that persist for minutes. The real cadence is
whatever the terminal delivers (D-13). Sufficiency is **conditional** on three things D-13
must establish: (a) depth and prints are delivered for the whole universe, not only the
symbol in focus (DSE Investor's Market Depth and Time and Sales components are per selected
symbol as documented; the DS30 ticker covers 30 names [1]); (b) the snapshot is a faithful
image at ≤ 1 s conflation, not a throttled redraw; (c) the tape is *complete* — prints per
second are expected to peak at the instant of lock (U, not measured here), exactly when a scrolling screen sampled at 1–2 s drops
rows. Tape completeness is testable per symbol-day by reconciling the sum of prints against
the day's totals (`noOfTrade`, `totalVolume`, `totalValueMN` from LankaBD [3], or the
terminal's market statistics [1]); a failed reconciliation is a GAP for Q4, Q6, Q7.

What a 1–2 s snapshot loses, per Q: **Q1** the lock instant to ± interval and cancel /
re-enter churn at the limit; **Q2** layers living less than an interval, the event-sum OFI in
fast intervals, and aliasing if a manipulator refreshes on a fixed cycle (keep source
sequence numbers and timestamps to detect it); **Q4** nothing if the tape is complete,
otherwise the highest-rate seconds — the lock bursts; **Q7** nothing, given the per-date
boundary; **Q8** refill latency resolved only to ± interval; **Q9** nothing; **Q13** nothing
while the limit lies within the displayed levels, a lower bound otherwise. For **Q5**
(layering) the data shows only what survives a snapshot interval; anything faster needs L3,
which is not available, and even for long-lived orders adds and cancels of *different*
orders at one level are not separable without order counts. Call-auction layering is
invisible unless the pre-open book is displayed. These are stated limits of the design,
not something to be patched later.

**Trade-side inference on DSE.** The dominant misclassification mechanisms are not
timestamp coarseness but: (i) **quote staleness inside the snapshot interval in a one-tick
book** — the ask at p is exhausted and a new bid posts at p within the interval, a seller
then hits p, and the print, sitting at the old ask, is signed as a buy; (ii) **within-second
ordering** — several prints and a quote change in one second cannot be ordered from
exchange stamps, only from receipt order (§6.1 requires it); (iii) **zero-tick dominance** —
85 % of minutes are single-price (QA §5), so the tick rule is near-useless and the quote rule
off the last pre-trade snapshot is the only classifier, exact in a visible book without
hidden orders whenever no quote change occurred inside the interval; (iv) **locked books**,
where the side is known by construction. Q3's minimum data is therefore "L1 at ≤ 1 s with
receipt ordering; tick rule only as fallback when the quote changed inside the interval".

---

## 3. What exists at the source (exchange level)

| Item | Finding | Grade / source |
|---|---|---|
| Matching engine | Nasdaq **X-stream INET**, live 11/12 December 2014; OMS **DSE-FlexTP** (Flextrade); retail apps are the "MOGO" client family (Android package `com.flextrade.dsemogo`). Quoted capacity 2,000 orders/s and 1,000 trades/s rests on a single secondary source | S [21][7] |
| Sessions since 2022-11-15 | "share trading will start at 10:00 am and will continue till 2:30 pm … There will be a 10-minute post-closing session … There will also be a pre-opening session from 9:55 am to 10:00" — i.e. pre-open **09:55–10:00**, continuous **10:00–14:20**, post-closing **14:20–14:30** | V [11] |
| Sessions 2022-08-24 → 2022-11-14 | 09:30–13:50, post-closing 13:50–14:00 ("Regular trading hours earlier used to be from 10am to 2:30pm") | V [10]; minute QA shows 09:30 first prints in 2022 (Q) |
| Pre-opening at launch | 09:45–10:00 from 2020-11-19; "The price, at which the maximum number of trades from the pre-opening session will be executed, will be the opening price"; "the circuit breaker … will be based on closing price at the DSE instead of the new opening price" | V [9] |
| Post-closing (2020 design) | 14:30–14:40 then; "Both bourses calculate the closing price based on the average price of trades executed over the last 30 minutes of the session"; "If there is no trade … the bourses take the average price of some latest trades" | V [9] |
| Post-closing session content | "In the post-closing session, investors can trade securities at the closing price for the day" | V [12] |
| Closing-price rule text | "weighted average price of all the trades in the last 30 (thirty) minutes before the closing session"; fallback: weighted average of up to 20 trades preceding; if no trade in the continuous session, the opening price | V [13] (broker blog quoting the rule; DSE original not reachable — U for the regulation reference). The minute QA could not reproduce it (QA §9) — D-10 |
| Ramadan hours (2024) | 09:30–13:30, post-closing 13:30–13:40 | V [12] |
| Pre-2020 hours | Wikipedia (undated): 10:00–14:30 Sun–Thu, Ramadan 10:00–14:00 [24]; the 2016 DSE-Mobile manual: "standard trading session is 10:30 am to 2:30 pm" [2]; the QA's first-print medians are 10:37–10:49 for 2015–2019 (Q). Open time per date is D-16 | V, conflicting |
| Price-limit tiers | ≤ Tk 200: 10 %; Tk 200–500: 8.75 %; "gradually declines … reaching 3.75 % for securities trading above Tk 5,000" (July 2026 description of the current framework). The intermediate tiers in `bdlib/config.py` (7.5 %, 6.25 %, 5 %) fit the pattern but were **not** quoted by the source | V [14] for the first and last tier; U for the middle |
| Lower-limit episodes | 2 % lower limit for the 66 companies released from the first floor on 2021-04-07 (S [30]); lower limit cut to 3 % for all securities on 2024-04-24, restored to 10 % on 2024-08-28 (V [16]) | mixed |
| Floor price regime 1 | imposed **2020-03-19**; "lifted floor prices in phases from April 2021" | V [16][17]; minute QA shows the market-wide positive jump on 2020-03-19 (Q) |
| Floor price regime 2 | imposed **2022-07-28** as the average of the preceding 5 days' prices; BSEC order of **2024-01-18** removed it for most stocks (35 kept) [15]; six remained until **2024-08-28**, after which only Beximco and Islami Bank kept a floor [16]; their withdrawal was announced for **June 2026** [17]. `config.FLOOR_ERA` ends 2024-01-31 — reconcile under D-11 / D-16 | V |
| Rule-setting authority | from July 2026 DSE and CSE "independently determine … circuit breakers, tick size, market lot, block size, order size, closing price calculations, market protection percentages and index calculation frequency" | V [14] |
| Tick size | Tk 0.10; **Tk 0.01 below Tk 1 from 2025-10-29** | S [22]; minute QA: 0 off-grid closes at Tk 0.10 through 2024-01 (Q) |
| Order types (retail terminal) | Limit, Market, Market-at-best; validity Day (others "if available"); single-leg limit resting orders; POD two-click trading on bid/ask | V [1] |
| Iceberg / hidden / disclosed-quantity orders | no evidence either way | U |
| Boards / segments | Normal (Public / Spot), Block (`.BL`) and Buy-In (`.BI`) boards [2]; ATB and SCP boards, DSMEX and ATBX indices [1]; odd-lot board U; segment flag on prints U | V for the names |
| Exchange market-data feed | a real-time feed is redistributed by ICE: "Level 1 and Level 2 … Level 2 market depth" for equities, real-time; the page's DSE tags read "Daily / End-of-Day" and DSE-specific tick history is not stated | V [6] |
| Market-data connection to retail terminals | DSE Investor's connection indicator distinguishes **OMS**, **FIX** and **market data** connections (green / amber / red) | V [1] |
| L3 to outsiders | no evidence | U → assume unavailable |
| Broker codes on public trades | no evidence on any screen or feed description | U → assume unavailable |
| Surveillance | vendor unknown | U |
| Daily scale | 2026-08-31: 149,199 trades, 177.5 M shares, Tk 4,772 M; 2026-08-30: 163,315 trades, 189.9 M shares (page has since rolled over; 2026-09-01: 150,064 trades; 09-02: 175,097) — order of magnitude **150–175 k trades/day** | S [23] |
| Public disclosures | DSE query letters on "unusual" price/volume rises and the standard reply "no undisclosed price-sensitive information" (Bangladesh Finance, Hami, S Alam CRS, Fine Foods; New Line Clothings did not reply) | S [20] |
| Exchange timestamps | the engine stamps at sub-second resolution internally (U, generic X-stream); retail displays show seconds [2] | U / V |

---

## 4. What a retail investor can obtain live — per channel

Legend for cells: **shown** = documented or observed; **?** = unknown (owner screenshot); **—** = not shown.

| Channel | Access | L1 bid/ask | Depth levels | Qty per level | Orders per level | Trade tape | Trade time precision | Day totals (trades, volume, value) | Delivery | Grade / source |
|---|---|---|---|---|---|---|---|---|---|---|
| **DSE-Mobile** (VIP / Trader / Biz Owner) | registration via the TREC broker [2]; since v2 (2023) the app is also the 2FA device for DSE Investor [1] | shown ("best bid and ask information or Market Depth") | "top 10 buy and sell orders" for registered users | shown | ? | "Last Transaction – last two transactions" | LTP example "12:02:04" → seconds | ? | native app; "real time" market updates [1][2]; delivery mechanism undocumented (the "RFS" in [1] is a trading feature, not the data transport); single-session-per-user rule ? (D-13) | V [2], S [8] for "top 10" |
| **DSE Investor / M-Invest** (web / desktop) | User-ID, password, 2FA via the app | shown ("indicative bid/ask"); "Directions" arrow = last trade direction | "Market Depth" component, per selected symbol as documented | ? | ? | **Time and Sales – Main / History / Business Done**; DS30 trade ticker | ? | Market statistics: index, turnover, value, **total trades**, ups/downs | browser terminal over separate OMS / FIX / market-data connections; "Pre-Open" watchlist view with MBA | V [1] (v1.1, July 2023) |
| Broker-branded FlexTP apps | broker account | same backend family | ? | ? | ? | ? | ? | ? | app | U (not examined individually) |
| **LankaBD** (LankaBangla Securities portal) | public, no login | LTP, O/H/L/C, YCP | buy/sell tables (2 buy rows visible after close; top-N during a session **?**) | shown (`Buy Price / Buy Volume`) | — | — (daily trade count only) | — | **noOfTrade, totalVolume, totalValueMN**, buy/sell percentage, totalBuyVolume / totalSellVolume, `closePrice` / `yClosePrice` (the published close) | one POST per symbol with the page's anti-forgery token; about a dozen requests in this session produced two connection resets on bursts — deliberate rate-limiting **U** (`evidence/README.md`) | V [3], sample in `evidence/` |
| LankaBD Data Matrix / Minute Chart Matrix / Price Archive / Price File | public | all symbols on one page: LTP, O/H/L/C, YCP, volume, value, fundamentals | — | — | — | — | — | (no trade-count column) | HTML page; minute charts; per-symbol and per-date archive forms (download format not verified) | V [4] |
| **AmarStock** | public + Premium (login) | real-time quote stream via a **SignalR hub** (`ReceiveQuote`; transport negotiated, WebSocket by default) | "Market Depth" and "Market Depth Monitor" pages exist | ? | ? | 1-Minute VPA (minute volume-price) | minute | site header: turnover, volume, trades | randomised login prompts, a free/premium plan flag and 401 handling in the page code; per-field gating of depth not established; **the hub URL in the page bundle decodes to a deliberately hostile path** — an explicit anti-automation stance | V [5] |
| StockNow | app / JS site | ? | ? | ? | ? | ? | ? | ? | unknown | U |
| Unofficial scrapers (e.g. `faysal515/bd-stock-api`) | GitHub | latest prices incl. per-symbol trade count, top-30, DSEX, a daily historical endpoint | — | — | — | — | — | — | crawls dsebd pages; no depth or tick data | V [28] |
| **DSE website** (dsebd.org / dse.com.bd) | public | latest share price pages; `mkt_depth_3.php` "Market Price"; close price; data archive; holidays & sessions | ? | ? | ? | — | — | — | HTML; **unreachable from this container** (TLS reset / 503) | U here — verify from Bangladesh |
| **ICE** (institutional) | commercial licence | L1 | L2 | yes | ? | real-time equities | ? | yes | ICE Consolidated Feed, APIs; DSE tick history not stated on the page | V [6] |
| LSEG / Bloomberg | commercial | indices confirmed (DSEX, DS30) | ? | ? | ? | ? | ? | ? | terminals / feeds | S; equities depth U |

**Reading of the matrix.** The only real-time, complete sources a retail investor can
legitimately hold are the **DSE-FlexTP terminals of their own broker** (DSE-Mobile and DSE
Investor / M-Invest). They carry F-L1, F-L2 ("top 10" per the DSE site) and F-TR (Time and
Sales). Whether the depth screen shows **number of orders per level**, whether depth and
prints arrive for **all symbols or only the selected one**, whether Time and Sales rows carry
a side or board flag, and whether **two sessions of one login can be open at once** is not
written in either manual — it is owner action **D-13**. LankaBD is a public, per-symbol
depth snapshot with day totals and no order counts: valuable as an **independent
cross-check** (and as the source of the published close), usable as a slow fallback sampler,
not as the primary book recorder. AmarStock's stream is technically the richest public
source, but its operators signal in the code that automated access is unwelcome: **not
recommended; excluded unless the owner obtains explicit permission (D-14)**.

If the owner's account is with LankaBangla (RESEARCH_STATUS D-6 mentions a LankaBangla /
DSE account), both the broker's FlexTP-based terminal and the public LankaBD portal apply.

---

## 5. Gap analysis — needed versus obtainable

| Field | Best retail source | Grade | Gap | Consequence for the target |
|---|---|---|---|---|
| F-L1 | DSE-Mobile / DSE Investor; LankaBD (coarse) | V | none for values; **delivery mechanism and symbol coverage unknown** | Q1, Q2, Q8, Q13 measurable in principle |
| F-L2 top-10 price + qty | DSE-Mobile / DSE Investor | S for "10 levels" | level count, refresh cadence and coverage unconfirmed | Q2, Q8, Q13 at snapshot cadence |
| F-L2 order counts | none confirmed | U | may not exist on any retail screen | Q1 loses "how many orders", keeps "how much quantity" |
| F-L3 | none | U → assume no | structural | Q5 partial by construction; spoofing attribution impossible; Q15 only via own probe orders (D-18) |
| F-TR (time, price, qty) | DSE Investor Time and Sales; DSE-Mobile last two | V | row fields unconfirmed; History depth unknown; completeness must be reconciled daily | Q3 (inferred), Q4, Q7, Q9 |
| F-TR side / board | none confirmed | U | must be inferred (§2.4) / from the block list | Q3 noisy in fast minutes; Q10 via disclosure list |
| F-AUC | DSE Investor "Pre-Open" view (MBA) | V for the view's existence; U for the book | — | Q14 partial or full depending on D-13 |
| F-BRK, F-ACC | none | U → no | regulator-only | Q6 proxies only |
| F-REF | press + BSEC orders + DSE circulars | V/S mixed | needs a dated table (D-16) | every Q is conditional on it |
| F-DIS | LankaBD announcements / block transactions / circuit-breaker pages, DSE news | link-level V | scraper cadence daily | Q11 measurable |
| F-SESS | per-date derivation from the tape itself + press | Q | — | Q7, Q9, Q14 need it |
| **History** | none public; ICE commercial (history not stated) | V | **no free history of the book** | recording must start now. How long a sample is needed is a Phase-5 / owner question (D-17); for scale, Phase 4.5 counted **3,328 fresh limit-up doors** on ~380 symbols over the six-year discovery window — roughly 1–2 fresh doors per symbol-year (PHASE45 report §5.5) |

---

## 6. Lossless recorder — reference architecture (design only)

All numbers in this section are **illustrative defaults, not configuration**; field lists are
**logical fields, not byte layouts**; "parquet" and "zstd" are recommendations, not decisions.

### 6.1 Principles (binding, same spirit as the QA rules)

1. **Raw before parsed.** Every source is stored as the bytes received (a WebSocket frame, an HTTP response body, a screen-state dump), with the parser applied only on replay. A parser bug must never lose data. If a channel is a screen or DOM dump rather than the frames, it is labelled *sampled*, with its cadence, never lossless.
2. **Two clocks, one time zone.** Each record carries `t_recv_mono_ns` from the monotonic clock (ordering and gap detection, comparable only within one process epoch) and `t_recv_utc` from a wall clock disciplined by chrony/NTP, with the offset logged once a minute. Receipt time is UTC. Source time strings are stored verbatim and interpreted on replay as Asia/Dhaka (UTC+06:00, no daylight saving) on the manifest's trading date; the trading date and the session window are Asia/Dhaka dates, segment rotation is on UTC hour boundaries. The distribution of (source time − receipt time) per source per day goes into the manifest, so exchange-clock skew is measured, never assumed.
3. **Identity that survives restarts and second machines.** Every record carries `capturer_id`, `epoch` (an identifier minted at every process start, never reused) and `seq` (monotonic within `(capturer_id, source, epoch)`, from 0). If the source carries its own sequence number, message id or trade id, it is promoted into the record header verbatim as `src_seq`, in addition to remaining inside the raw bytes.
4. **Heartbeat and explicit gaps.** A HEARTBEAT record is written every 5 s per source whether or not data arrived; silence is therefore distinguishable from "no trades". Reconnects, HTTP errors, rate-limit responses, clock steps, dropped frames and restarts are written as GAP records with reason and duration. Absence is information — never filled.
5. **Framing, integrity, versioning.** Segment header: magic, `schema_version`, `capturer_id`, `epoch`, `source`, `software_version`, sha256 of the previous segment (hash chain). Record header: `kind` (DATA, HEARTBEAT, GAP, CLOCK, META, HTTP_REQ, HTTP_RESP), `t_recv_mono_ns`, `t_recv_utc`, `seq`, `src_seq` (nullable), `len`, `crc32c` over length and bytes. Segment trailer: record count, first/last `seq`, sha256 of everything before the trailer, defined over the uncompressed byte stream. On replay a segment is salvaged record by record; only records failing their CRC are lost, and the other capturer's copy is used for exactly those.
6. **Append-only, restart-safe.** A restart never appends to an existing file: it opens a new segment under a new epoch. A segment without a close trailer is kept exactly as found, listed in the manifest as `unclosed`, hashed as-is, its trailing bytes after the last complete record retained and marked `partial` on replay. The interval between the last durable heartbeat of the old epoch and the first record of the new one is written as GAP(reason=restart) by the new epoch. Nothing is renamed or rewritten in place.
7. **Durability policy.** Appends are buffered; `fdatasync` is issued at every heartbeat (≤ 5 s), at every GAP / CLOCK record and at segment close, and the directory is fsynced after rotation. The maximum loss window on power loss is one heartbeat interval, and it is always visible afterwards as a heartbeat gap.
8. **Compression is a verified copy, not a rewrite.** After the close, each closed segment is compressed (zstd) to a new file, decompressed and compared against the segment sha256 before the uncompressed file is deleted; the manifest carries both hashes. A local uncompressed file is deleted only after the compressed file has been verified on the same disk; a local compressed file is pruned only after two remote copies have been hash-verified. Compression and upload never run inside the capture path or the session window.
9. **Two independent capturers**, on different networks (cloud VM and home machine). Cross-capturer reconciliation keys on `(source, sha256(payload), src_seq if present)` within a ± N s receipt window; capturer-local `seq` is never compared across machines. A merged canonical order is a replay *output* with a stated rule (by `src_seq` where present, else by the earliest `t_recv_utc`, ties broken by `capturer_id`); every record only one capturer saw, or whose order differs between them, is flagged. **Whether one login may be open on two devices at once is a D-13 question**; if not, the second capturer uses a different account or a different source.
10. **Replay is the only consumer.** Normalised tables (book snapshots, trades, disclosures, reference journal) are produced by a versioned parser from the raw segments and can be regenerated forever. Replay refuses segments without a META record.
11. **No dedup, no repair, no interpolation at capture.** Duplicate frames are kept and flagged on replay (informational; keyed on `src_seq` where present, otherwise on payload hash — an unchanged snapshot is not a duplicate). Source-side corrections, cancellations and resends are ordinary records; replay keeps both the original and the correction, linked by `src_seq` / trade id, and marks the corrected print — it never overwrites. A day-total field that decreases between polls is flagged, not repaired.
12. **Run metadata as data.** Every epoch starts with a META record: software version (git commit), configuration hash, user agent, rate-limit settings, symbol-list hash, `capturer_id`, OS/kernel, chrony sync state and offset, free disk. The symbol list is taken each morning from the source's own all-symbol page, stored raw, hashed, diffed against the previous day, and the diff written as META; S1 is never filtered by symbol — frames for symbols not on the list are kept and flagged. The reference state of the day (limit tiers, floor list, session hours) is captured daily so that replay never depends on today's rules.

### 6.2 Components

```
                 ┌────────────────────────────────────────────────────────────┐
  sources        │  S1  DSE-FlexTP terminal stream (owner's own login)        │  raw frames
                 │  S2  LankaBD depth endpoint, one symbol per request (slow) │  raw HTTP req + resp
                 │  S3  disclosures: LankaBD announcements / block / circuit, │  raw HTML, daily
                 │      DSE news pages                                        │
                 │  S4  reference journal: limits / floor / sessions / ticks  │  hand-entered, sourced
                 └───────────────┬────────────────────────────────────────────┘
                                 │  logical record: kind · capturer · epoch · seq · src_seq
                                 │                  t_mono · t_utc · len · crc · bytes
                 ┌───────────────▼────────────────┐   ┌──────────────────────┐
  capture (×2)   │  append-only segments, hourly  │──▶│ per-segment sha256,  │
                 │  heartbeat 5 s · GAP · CLOCK    │   │ hash chain, daily    │
                 │  META at every epoch start     │   │ manifest (JSON, git) │
                 └───────────────┬────────────────┘   └──────────────────────┘
                                 │ after close: verified zstd copy, then upload
                 ┌───────────────▼────────────────┐
  raw store      │  each capturer's own disk +    │  immutable; the second copy is the
                 │  the other capturer's disk     │  other capturer, not a 20 GB bucket
                 └───────────────┬────────────────┘
                                 │
                 ┌───────────────▼────────────────┐   ┌──────────────────────┐
  replay/parse   │  versioned parser → tables:    │──▶│ QA (detect & flag):  │
                 │  book_snapshots · trades ·      │   │ gaps, dupes, crossed │
                 │  disclosures · reference        │   │ book, out-of-session │
                 └────────────────────────────────┘   └──────────────────────┘
```

**S1 is the primary and the only real-time source.** How its frames are obtained is the
open design question: the FlexTP clients are proprietary; the DSE Investor web terminal runs
in a browser, so its market-data connection is observable to the account holder in their own
browser session — whether the terms permit *recording* it is D-14, and whether the frames are
decodable is unknown until one session is captured. Nothing here assumes reverse-engineering
a native app. Two S1 requirements follow from delta-style feeds: **at every connect and
reconnect the full initial state of all subscribed symbols is captured before deltas are
accepted**, and a resubscribe/refresh is forced at every segment rotation so that a lost
delta can corrupt a reconstructed book for at most one hour; the page HTML and JS bundle (or
app version) that define the frame format are captured daily as raw bytes, so a protocol
change stays decodable. If S1 delivers depth only for the selected symbol or a watchlist
(D-13), the recorder captures whatever is pushed, coverage becomes a stated subset listed in
the daily manifest, and the sizing in §6.4 collapses to the event-stream and trade-tape rows.

**S2 runs slowly by design.** Every poll writes an HTTP_REQ record (method, URL, request
headers including user agent and token id, body, `t_send`) and an HTTP_RESP record (status,
response headers verbatim including `Date` and `Content-Length`, `t_first_byte`,
`t_last_byte`, body bytes even when the status is not 200, `truncated` if the body ended
early). Nothing is discarded because it "looks like an error". At ≤ 0.5 requests/s the
~420-symbol cycle takes ~14 minutes — a coarse independent sample of L1/L2, the day totals
and the published close, used to cross-check S1 and to survive an S1 outage with
*something*, never as a book recorder.

**S3/S4 are small and daily**, and they are what turns a recorded book into a labelled
dataset later (query letters date the door from the exchange's side; the reference journal
says what "limit" meant that day; the expected session window is written next to the
observed one so deviations are flagged the same day).

### 6.3 Failure modes and their detectors

| Failure | Detector | Record |
|---|---|---|
| stream disconnect / reconnect | heartbeat gap > 10 s; reconnect event | GAP(reason=disconnect); full state re-captured on reconnect |
| **market-wide silence** (connected, nothing arrives) | no S1 message of any kind for > 30 s inside the continuous session (at ~9 trades/s market-wide, impossible if the feed is live); WebSocket ping/pong RTT logged each heartbeat; the source's own heartbeat, if any, captured as data | GAP(reason=silence) immediately |
| stale feed (S1 quiet while S2 shows changed LTP) | S2 cross-check | GAP(reason=stale, evidence=S2) |
| auth / session expiry, forced logout (2FA timeout, single-session rule, nightly logout) | stream stops while the socket may stay open; login page / auth frame observed; silence | GAP(reason=auth); the auth frame itself is kept |
| two capturers on one login evicting each other | correlated GAPs on both capturers within seconds | reconciliation report; a design violation until D-13 answers |
| capture-tool backpressure (browser / devtools buffer, in-process queue overflow) | frames-received counter vs records-written counter; `src_seq` gaps | GAP(reason=drop, count) — never a silent drop |
| browser tab throttling / page reload (if S1 is browser-captured) | visibility / lifecycle events; long heartbeat intervals with the process alive | CLOCK / GAP with reason |
| protocol or schema drift at the source (bundle update, JSON keys change) | daily bundle hash differs; parse-failure rate per segment on replay | META(bundle hash); replay QA flag — raw remains intact |
| exchange halt, session extension or early close | S1 silence while S2 also shows no LTP change; DSE / LankaBD notice page captured | GAP(reason=exchange, evidence) — distinct from stale |
| symbol-list drift (new listing, rename, suspension) | morning list diff; S1 frames for unknown symbols | META(list diff); flagged on replay |
| clock not disciplined at start (chrony unsynced after boot) / clock step | chrony state in META; offset beyond threshold; monotonic vs wall drift | CLOCK; `t_recv_utc` marked unsynced until lock; timestamps never rewritten |
| VM pause / live migration | monotonic vs wall discontinuity; heartbeat gap without reconnect | CLOCK + GAP(reason=pause) |
| rate limit / block (S2) | HTTP 4xx/5xx, connection reset | GAP(reason=http, code); the response kept |
| duplicate or replayed frames | same `src_seq`, or same payload hash within 1 s | kept; flagged on replay (informational) |
| out-of-session data (snapshot rows, holiday boards) | per-date session window from S4 and from the tape | flagged on replay — the QA's "snapshot row" rule at source (§8, item 1) |
| capturer death | daily manifest missing; the other capturer's manifest present | reconciliation report |
| write-loss window on power cut | first heartbeat of the new epoch vs last durable heartbeat | GAP(reason=restart) |
| silent bit rot / incomplete upload | hash verification at upload; periodic scrub of both copies | reconciliation report; re-copy from the other capturer |
| disk full | pre-session free-space check; segment write error | GAP + alert |

### 6.4 Sizing (assumptions stated; wire bytes are what is stored)

Assumptions: 420 symbols, a 4.5-hour session (16,200 s), top-10 depth on both sides, 245
sessions/year, zstd 10–30× on snapshots and 3–5× on events, ~0.4 KB per symbol-snapshot if the
wire is binary and ~1.2 KB if it is JSON (a web terminal is likely JSON), 150–165 k trades/day
[23] (§3 records one 175 k day; the upper bound moves the trade-tape and event rows by less
than 10 %), 3–10 book events per trade as a working assumption. Rows 1–3 assume S1 pushes
all-symbol depth (D-13); if it does not, only the event-stream and trade-tape rows apply.

| Mode | Per record (wire) | Records / day | Raw / day | zstd / day | Per year | Uncompressed days on 200 GB |
|---|---|---|---|---|---|---|
| Full snapshot every 1 s, all symbols — binary wire | 0.4 KB | 6.8 M | 2.7 GB | 90–270 MB | 22–67 GB | 74 |
| Full snapshot every 1 s, all symbols — JSON wire | 1.2 KB | 6.8 M | 8.2 GB | 270–820 MB | 67–200 GB | 25 |
| Full snapshot every 2 s — binary / JSON | 0.4 / 1.2 KB | 3.4 M | 1.4 / 4.1 GB | 45–140 / 140–410 MB | 11–33 / 33–100 GB | 147 / 49 |
| Event stream (book events only) | ~60 B | 0.45–1.65 M | 27–99 MB | 5–33 MB | 1.3–8.1 GB | — |
| Trade tape (add to any mode) | ~60–100 B | 150–165 k | 9–17 MB | 2–6 MB | 0.5–1.4 GB | — |
| S2 poller (in-session body with 10 + 10 rows, plus stored headers) | ~3.5 KB | ~8.1 k | ~28 MB | ~1–3 MB | ~0.2–0.7 GB | — |
| Heartbeats (4 sources × every 5 s) | ~40 B | ~13 k | < 1 MB | — | — | — |
| S3 / S4 | HTML | tens | < 5 MB | < 1 MB | < 0.3 GB | — |

Oracle's 200 GB (boot volume included; ~150 GB usable) holds 2.3–6.8 years of compressed
1-second binary snapshots, 0.8–2.3 years if the wire is JSON, or decades of an event stream —
and only 25–74 days of *uncompressed* raw, so post-close compression (§6.1 principle 8) is on
the storage-critical path. GCP's 30 GB (~20 GB net of the OS) holds 0.6–1.8 years of
compressed 2-second binary snapshots, 0.2–0.6 years if JSON. Oracle's 20 GB object storage
cannot be the second copy in snapshot mode; the second copy is the other capturer's disk.
Egress is irrelevant for capture (data flows in); uploads to the other capturer are bounded
by the compressed sizes.

### 6.5 Free / near-free infrastructure (limits quoted from provider pages)

| Option | What is free | Can it run a 4.5 h/day capture unattended? | Persistence | Caveats |
|---|---|---|---|---|
| **Oracle Cloud Always Free** [27, V] | 2 × VM.Standard.E2.1.Micro (1/8 OCPU, 1 GB); Arm A1: 1,500 OCPU-h + 9,000 GB-h per month ("equivalent to 2 OCPUs and 12 GB"); 200 GB block storage total (boot volumes included); 20 GB object storage; 10 TB egress/month; credit or debit card required ("we do not accept debit cards with a PIN or virtual, single-use, or prepaid cards") | **yes** | yes | "Idle Always Free compute instances may be reclaimed" when over any 7 days CPU p95 < 20 % **and** network < 20 % **and** memory < 20 % (A1). **A recorder busy 4.5 h × 5 days ≈ 13 % of the week, at a few per cent of one OCPU, meets all three — expect reclamation** unless the capturer is deliberately kept non-idle (e.g. holding > 20 % of memory in an in-memory ring buffer of recent frames, which the silence detector can use anyway). Upgrading the tenancy to Pay As You Go is commonly reported to stop reclamation while Always Free usage stays free — **not found on the fetched FAQ (U)**; the FAQ does say Always Free resources continue after a trial and that accounts idle for 30 days may be deemed abandoned. A reclaimed instance is stopped; only the other capturer's "peer manifest missing" alert would notice |
| **Google Cloud free tier** [27, V] | 1 e2-micro in us-west1 / us-central1 / us-east1; 30 GB standard persistent disk; 1 GB egress/month from North America; 5 GB Cloud Storage; billing account required | yes (tiny) | 30 GB only | egress 1 GB/month means the raw store cannot be copied out for free; fine for capture-in |
| **AWS Free Tier** [27, V/S] | credit-based: "up to $200 in credits over 6 months" (page fetched); "30+ AWS services are always free within monthly usage limits" | for ~6 months on credits | while credits last | the old 12-month 750 h EC2 offer is not on the current page |
| **GitHub Actions** [27, V] | 2,000 min/month (private repos); job limit 6 h; workflow 35 days; 20 concurrent jobs; 500 MB artifacts | a 4.5 h job fits the 6 h limit, but ~100 h/month ≫ 33 h included; schedule jitter (U, not from the cited page) | artifacts 500 MB | not designed for collectors; a public repo would publish the data. **Use only for the daily S3/S4 scrape and manifests** |
| **GitHub Codespaces** [27, V] | Free: 120 core-hours + 15 GB-month; Pro: 180 core-hours + 20 GB | 2-core → 60 h/month < ~100 h needed | 15 GB | an idle timeout stops the capture (S — not on the cited billing page) |
| Cloudflare Workers / cron, Render, Railway, Fly.io | not examined (U) | no long-running free processes assumed | — | verify before relying on any |
| **Home PC / Raspberry Pi in Bangladesh** | electricity + ISP | yes, with a UPS | local disk | power cuts and ISP drops are exactly the GAPs the design records; the natural **second capturer** |
| Android phone running the app itself | — | capture would require proxying the app's traffic on the device | — | out of scope unless D-14 permits and the owner wants it |

**Recommendation (not a decision):** primary capturer on Oracle A1 kept deliberately
non-idle (disk and hours fit), secondary at home; GitHub only for manifests and the daily
disclosure scrape; the DSE Investor web terminal as S1 if D-13 / D-14 confirm it; LankaBD as
S2 at a polite rate.

### 6.6 Terms and legality — owner items, not findings

- DSE-Mobile / DSE Investor terms of use were not reachable; the manuals only warn about financial risk [1][2]. Whether recording one's own market-data session for personal research is permitted is **D-14**.
- AmarStock's page code names its stream endpoint in a way that reads as a refusal of automated access [5, V]; not recommended, excluded unless permission is obtained.
- LankaBD's depth endpoint is public and its `robots.txt` returned an empty body on one fetch (session observation, not archived — `evidence/README.md`); polite use (identified user agent, ≤ 0.5 req/s, no parallelism) is a design constraint, and the owner should still ask LankaBangla if they are a client.
- Redistribution of exchange data is a different matter from private research use; the design stores raw data privately and publishes only derived research outputs.

---

## 7. What the trade-minute dataset can and cannot validate

The dataset (417 files, 43.1 M rows, 2015-10 → 2024-01) is irregular trade-minute data with
board-snapshot rows, 25 whole-market capture gaps and unknown provenance
(`reports/MINUTE_DATA_QA_REPORT.md`). `qa/minute_observability_sample.py` (seed 7, 40
symbols listed in `qa/MINUTE_OBSERVABILITY_SAMPLE.json`; rows inside 10:00–14:30 inclusive;
no footprint or predictive statistic computed) gives:

| Descriptive fact (Q) | Seed-7 sample | Independent 40-symbol sample (reviewer, different seed) |
|---|---|---|
| rows / symbol-days | 4,255,368 / 67,906 | 4,198,613 / 67,434 |
| minutes with more than one price (high > low) | **15.4 %** | 14.2 % |
| … by year 2015 → 2024 | 0.2 / 0.1 / 0.2 / 1.2 / 2.2 / **19.9 / 28.0 / 25.2 / 22.5 / 25.8 %** | 0.2 / 0.1 / 0.2 / 1.2 / 2.6 / 23.1 / 26.2 / 23.3 / 19.3 / 26.9 % |
| … pooled before 2020 / from 2020 | **0.9 % / 24.7 %** | 1.1 % / 23.5 % |
| minutes with open ≠ close | 13.5 % | 12.4 % |
| volume == 1 rows | 1.2 % | 1.3 % |
| share of prints in 10:00–10:05 / 14:20–14:30 | 1.1 % / 5.6 % | 1.0 % / 5.7 % |
| symbol-days whose last print falls in 14:20–14:30 | 71.5 % pooled; by year 77 / 68 / 84 / 82 / 86 / 75 / 72 / 60 / **48 (2023)** / 66 (Jan 2024) % | 71.4 % pooled |
| symbol-days with a first print by 10:05 | 23.0 % pooled (0–2 % before 2020; 27 % 2020; 42–58 % from 2021) | 22.6 % |

**Reading (a hypothesis, graded U for the interpretation, Q for the numbers).** Before 2020
the rows are single-price on ≈ 99 % of minutes (99.8–99.9 % in 2015–17, 97.8 % in 2019), which is
what a once-a-minute last-price sample produces, not what aggregated prints produce; from
2020 roughly a quarter of minutes carry a range, as true minute aggregates of several prints
would. So the dataset looks like **two capture regimes**, and only the 2020–2024 part says
anything about print-level behaviour. This sharpens owner action D-1 (provenance); until the
capture method is confirmed, pre-2020 rows are not used as a print-level baseline.

| Quantity | Can the minute data validate it? | How / why not |
|---|---|---|
| trade timing to the minute, prints per day, time-of-day profile | **yes** (2020+ for prints; all years for activity timing) | median 52 prints/day, p10 4.6 (QA §5); 48 % of 2023 symbol-days end in the last 10 minutes |
| per-minute volume | **yes, after excluding snapshot rows** | 15,846 + 30,742 snapshot rows carry whole-session volume (QA §4) |
| closing-window behaviour: one-price last-10-minute share by year | **yes** | 2019 7 % → 2020 13 % → 2021 22 % → 2022 32 % → 2023 19 % (QA §9) — a calibration expectation for Q7, not a prior |
| session windows per date | **yes** | `qa/MINUTE_QA_SESSION_BY_DATE.csv`; 279 shift dates (121 of them 2016 snapshot noise) |
| limit-hit timing within the day | **partial** | first print vs prior close against the (unverified) band; 1,470 beyond-band day gaps, 688 of them minute-only artefacts (QA §8) |
| intra-minute price range | **partial, 2020+ only** | 20–28 % of 2020+ minutes carry a range |
| Q3 trade side by tick rule on minute closes | **no** — would misclassify | 20–28 % of 2020+ minutes contain more than one price; a minute close cannot sign the trades inside it |
| Q1 / Q2 / Q8 / Q13 book quantities, order counts, imbalance, shares to the door | **no** | no bid/ask, no depth, no order counts in the data |
| Q14 pre-open state | **no** (prints at 10:00 only) | no auction fields |
| Q5 cancellations, order lifetime | **no** | no order data |
| Q6 wash-trade proxies (identical lots alternating) | **no** | per-minute aggregates hide individual prints |
| broker / account structure | **no** | absent |
| **the recorder itself** | **no** | the dataset ends 2024-01-31; there is no overlap with any future recording. It can only calibrate *expectations* (prints/day, time profile, closing-window share — using the 2023 figures, not the pooled ones) against which the recorder's first weeks are sanity-checked |

**Intraday analogues of the pre-registered footprints** (design doc F01–F18, v2 names), by
data requirement — an assessment of measurability, not a test:

| Footprint family | Members | Minute data | Needs order-book data |
|---|---|---|---|
| volume-based | F01, F02, F07, F09, F17, F18; reference **F15** (plain abnormal volume) | partial: minute volume profile with snapshot rows excluded and per-date session windows | no |
| dip recovered on volume (v2 name for F03 / F04) | F03, F04 | partial: intraday dip and recovery at minute resolution | Q8 (ask depletion) only with L2 |
| closing strength | F11; F12 (v2: volume-weighted closing strength, 10 sessions) | partial: close location at minute resolution; the published close is not the last print on 20 % of days (QA §9) | Q7 fully needs the tape + boundary |
| own-baseline departure | F05, F06 | partial: state rungs re-derived on minute features (windows in clock time, QA §14) | no |
| idiosyncratic move | F08; reference **F16** (already moved — not a doorstep footprint by construction) | yes (returns) | no |
| compression / turnover | F10, F14 | partial (turnover derived, not reported) | no |
| quiet-accumulation proxy | F13 | partial | Q2 imbalance needs L2 |
| **order-book quantities with no EOD analogue** | Q1, Q2, Q5, Q6, Q8, Q13, Q14 | **no** | **yes** |

---

## 8. Constraints the QA findings impose on any future validation

1. **Snapshot rows** (rows whose volume equals a whole session's volume) exist on holidays, weekends and inside sessions on specific 2019–2020 dates. The recorder's replay QA applies the same rule at source — a print whose quantity equals the day total is a board snapshot — **unless the source's own trade count for the day is 1** (the first trade of a day also satisfies the equality).
2. **Capture gaps are whole-market**: 25 days in the daily calendar have zero minute rows. The recorder's heartbeat / GAP records make this visible on the day, not two years later.
3. **Session windows shift within years** (279 dates, 121 of them 2016 snapshot noise). The recorder writes the expected window (S4) next to the observed first/last event per symbol per day, so the calendar is derived and deviations are flagged the same day.
4. **Two floor regimes**: 2020-03-19 → phased from April 2021; 2022-07-28 → 2024-01-18 for most, 2024-08-28 for four more, June 2026 for the last two. `config.FLOOR_ERA` ends 2024-01-31 — reconcile under D-11 / D-16. Floor pins the ask side at the floor and produces one-price closes — Q1 / Q7 / Q13 must be conditioned on the regime journal.
5. **The last print is not the official close** on 20 % of symbol-days (QA §9). The recorder captures the post-closing prints and the published closing price (LankaBD `closePrice`, the terminal's statistics) separately; the closing-price rule [13] is then testable rather than assumed (D-10).

---

## 9. Decision gates and owner actions (added to the ledger)

| # | Action | Why it gates |
|---|---|---|
| **D-13** | Name the broker and the platform(s) you hold (DSE-Mobile VIP / Trader, DSE Investor / M-Invest, broker app). On a trading day screenshot **Market Depth** (how many levels; price, quantity, number of orders?; is depth shown for all stocks or only the selected one?), **Time and Sales – Main / History / Business Done** (columns; timestamp precision; any side or board flag; is "Business Done" a per-print list or a price × volume summary?), the **Pre-Open** watchlist view during 09:55–10:00 (indicative price / MBA; is a pre-open book shown?), the **Directions** arrow, and state whether the app allows **two concurrent sessions of one login**. | Decides which Q quantities exist at all; every "?" in §4 and the sizing basis in §6.4 close here |
| **D-14** | Read the platform's terms of use for automated access / recording of your own session; ask the broker in writing if unclear. | S1 is the primary source. **Without permission the design falls back to LankaBD polling, and the target shrinks to Q11 / Q12 (Q10 only via the block-transaction list) plus ~14-minute samples of Q1 (quantity only), Q2, Q8, Q13 and day totals; Q4, Q7 and Q9 need a trade tape that LankaBD does not provide** |
| **D-15** | Choose the primary capturer (Oracle A1 needs a card and must be kept non-idle; a home machine needs a UPS) and whether a second capturer exists. | Redundancy is a principle, not an option |
| **D-16** (consolidates D-3, D-11 and the unnumbered limit-schedule row) | A dated rule table: all six limit tiers with sources; lower-limit episodes (2021-04-07 2 % for 66 names; 2024-04-24 3 % → 2024-08-28 10 %); both floor regimes with per-stock exits; session hours per date incl. pre-open start (09:45 from 2020-11-19, 09:55 from 2022-11-15) and the 2016 "10:30" open; tick and lot changes. The DSE circulars must be pulled from Bangladesh (site unreachable here). | F-REF is required by every Q |
| **D-17** | Decide whether commercial history is worth buying: ask ICE whether DSE tick / L2 history exists and at what cost (the page does not say); otherwise accept that the book history begins on the recorder's first day. How long a sample Phase 5 needs is an owner / Phase-5 decision — for scale, ~1–2 fresh limit-up doors per symbol-year in DISCOVERY. | Determines when order-flow research can start |
| **D-18** | Whether one-lot passive **probe orders** may ever be placed to measure queue turnover (Q15). The repository's "no BUY/SELL" rule forbids it today; the default is **no**. | Q15 exists only if yes |
| D-1 (sharpened) | Minute-data provenance: the sample suggests pre-2020 rows are once-a-minute price samples and 2020+ rows are print aggregates (§7, hypothesis). Confirm the capture method. | Only 2020+ is usable as a print-level baseline until then |

**Nothing starts before D-13 and D-14.** Recording, once permitted, should start before any
research design, because the history clock starts only then.

---

## 10. Sources

| # | Source | Grade | What was taken |
|---|---|---|---|
| 1 | *DSE Investor User Guide* v1.1, July 2023 (DSE-FlexTP), PDF hosted by HAC Securities — https://hacsecurities.com/download/DSE-Investor%20User%20Guide.pdf | V | "Real time market information and indicative bid/ask prices"; Market Depth component; "Time and Sales –Main / T&S- History / T&S- Business Done"; market statistics (total trades); connection indicator (OMS / FIX / market data); order types and validity; POD; "Pre-Open view displays the margin and the MBA (Mid Bid Ask)"; "Directions" arrow; ATB / SCP boards; the app as 2FA device; registration via TREC |
| 2 | *DSE-Mobile for Android (VIP & Trader) Manual*, Rev. 201603, PDF hosted by NRBC Bank Securities — https://www.nrbcbanksecurities.com/downloads/DSE-Mobile_User_Manual.pdf | V | "Bid-Ask – The best bid and ask information or Market Depth; Last Transaction – Last two transactions"; "LTP is 76.20 at 12:02:04"; VIP / Trader / Biz Owner; "standard trading session is 10:30 am to 2:30 pm"; Block (.BL) and Buy-In (.BI) boards |
| 3 | LankaBD Market Depth page and endpoint — https://lankabd.com/Home/MarketDepth , POST https://lankabd.com/Home/MarketDepthData | V | request shape `{Symbol, Exchange}` + `RequestVerificationToken`; response keys (see `evidence/`); `Buy Price / Buy Volume` tables; `closePrice`; session observations in `evidence/README.md` |
| 4 | LankaBD Data Matrix, Minute Chart Matrix, Price Archive, Price File; navigation links to Announcements (Archive), Block Transactions, Circuit Breaker — https://lankabd.com/Home/DataMatrix , /Home/MinuteChartMatrix , /Home/PriceArchive , /Home/PriceFile | V (pages); link-level for the announcement / block / circuit pages | all-symbol price grid columns; minute charts; archive forms |
| 5 | AmarStock site and bundles — https://www.amarstock.com/ , https://www.amarstock.com/market-depth-monitor/ , https://staticv2.amarstock.com/bundles/js/latestprice-onRealTime , …/bundles/MarketDepthMonitor , …/bundles/js/lsp-page | V | SignalR `HubConnectionBuilder` with `ReceiveQuote`; `apiv2.amarstock.com`, `premium.amarstock.com`; Market Depth / Market Depth Monitor / 1-Minute VPA pages; randomised login prompt, plan flag, 401 handling; hostile hub path |
| 6 | ICE Developer Portal, *Dhaka Stock Exchange (DSE)* — https://developer.ice.com/fixed-income-data-services/catalog/dhaka-stock-exchange-dse | V | "Level 1 and Level 2", "Level 2 market depth", real-time equities; DSE tags "Daily / End-of-Day"; history not DSE-specific |
| 7 | Google Play, *DSE Mobile* — https://play.google.com/store/apps/details?id=com.flextrade.dsemogo | S | package id; "real-time … bid/ask" |
| 8 | DSE, *DSE-Mobile Service* — https://dsebd.org/dse-mobile.php | S (site unreachable here) | "To get live top 10 buy and sell orders, investors are requested to register with DSE-Mobile app or M-invest"; service launched 2016-03-09 |
| 9 | The Business Standard, 2020-11-18, *DSE pre-opening, post-closing sessions begin tomorrow* — https://www.tbsnews.net/economy/stock/dse-pre-opening-post-closing-sessions-begin-tomorrow-159832 | V | 09:45–10:00 pre-opening from 2020-11-19; opening-price rule; post-closing 14:30–14:40; "average price of trades executed over the last 30 minutes"; circuit based on closing price |
| 10 | The Business Standard, 2022-08-23, *Stock exchanges cut trading hours by 10 mins* — https://www.tbsnews.net/economy/stocks/stock-exchanges-get-new-trading-hours-930am-150pm-482326 | V | 09:30–13:50, post-closing 13:50–14:00 from 2022-08-24; earlier hours 10:00–14:30 |
| 11 | The Financial Express, 2022-11-14, *Bourses change trading hours from Tuesday* — https://thefinancialexpress.com.bd/stock/bourses-change-trading-hours-from-tuesday-1668423289 (also UNB, *DSE share trading between 10am and 2:30pm from tomorrow*) | V | 10:00 start, all trading completed by 14:30, 10-minute post-closing, pre-opening 09:55–10:00 from 2022-11-15 |
| 12 | The Business Standard, 2024-03-10, *BSEC changes share trading schedule for Ramadan* — https://www.tbsnews.net/economy/stocks/bsec-changes-share-trading-schedule-ramadan-806866 | V | 09:30–13:30, post-closing 13:30–13:40 "instead of 2:20pm to 2:30pm"; "In the post-closing session, investors can trade securities at the closing price for the day" |
| 13 | Midway Securities blog, *How is the Closing Price of a Stock determined at the DSE* — https://www.midwaybd.com/blog/how-is-the-closing-price-of-a-stock-is-determined-at-the-dhaka-stock-exchange-plc | V (secondary) | closing-price rule text (30 minutes weighted average; 20-trade fallback; opening price fallback) |
| 14 | The Business Standard, 2026-07-01, *BSEC restores bourses' authority to set circuit breakers, trading rules* — https://www.tbsnews.net/economy/stocks/bsec-restores-bourses-authority-set-circuit-breakers-trading-rules-1477436 | V | tiers 10 % ≤ Tk 200, 8.75 % Tk 200–500, 3.75 % > Tk 5,000; list of rules the bourses now set |
| 15 | The Financial Express, 2024-01-18, *BSEC removes floor price from most stocks after 18 months* — https://thefinancialexpress.com.bd/stock/bsec-removes-floor-price-from-most-stocks-after-18-months | V | floor imposed 2022-07-28 as 5-day average; order of 2024-01-18; 35 companies kept |
| 16 | The Financial Express, 2024-08-28, *Regulator frees all but Beximco, Islami Bank of price restrictions* — https://thefinancialexpress.com.bd/economy/regulator-frees-all-but-beximco-islami-bank-of-price-restrictions | V | lower limit 3 % from 2024-04-24 restored to 10 %; six companies under floor until 2024-08-28; "lifted floor prices in phases from April 2021" |
| 17 | The Business Standard, 2026-06-08, *Beximco, Islami Bank set to exit floor price regime today* — https://www.tbsnews.net/economy/stocks/beximco-islami-bank-set-exit-floor-price-regime-today-1456691 | V | first floor 2020-03-19; phase-out from 2024-01-19 (the order of 2024-01-18, §3); all but two by August 2024; withdrawal of the last two announced |
| 18 | The Business Standard, 2025-03-19, *BSEC fines a dozen manipulators Tk80cr* — https://www.tbsnews.net/economy/stocks/bsec-fines-dozen-manipulators-tk80cr-1097071 | V | series transactions; BO-account networks across multiple brokerages; 11.27 % / 37.84 % of volume; undeclared > 10 % holdings |
| 19 | The Business Standard, 2025-06-22, *BSEC slaps record Tk1,100cr fines for share rigging* — https://www.tbsnews.net/economy/stocks/bsec-slaps-record-tk1100cr-fines-share-rigging-recovery-almost-zero-1170691 | V | fines = realised gain − 10 %; frozen BO accounts; "acquaintances" quote from an anonymous broker MD |
| 20 | The Business Standard, *DSE queries four firms over unusual share price surge* — https://www.tbsnews.net/economy/stocks/dse-queries-four-firms-over-unusual-share-price-surge-1236406 ; *Non-functional New Line Clothings fails to explain DSE query* | S | query-letter mechanism and standard reply |
| 21 | Nasdaq press release, Dec 2014, *Dhaka Stock Exchange Goes Live With New Trading Engine Powered by Nasdaq* — https://www.nasdaq.com/about/press-center/dhaka-stock-exchange-goes-live-new-trading-engine-powered-nasdaq ; The Daily Observer 2014-12-12 — https://www.observerbd.com/2014/12/12/60212.php ; ResearchGate, *Improving the Website and Automation of Dhaka Stock Exchange* — https://www.researchgate.net/publication/341193327 | S (Nasdaq page 503 here) | X-stream INET go-live; FlexTP; capacity figures (single secondary source); MOGO client apps |
| 22 | The Business Standard, *DSE lowers tick size to Tk0.01 for shares under Tk1* — https://www.tbsnews.net/economy/stocks/dse-lowers-tick-size-tk001-shares-under-tk1-1270021 ; The Daily Star, *DSE reduced tick size* — https://www.thedailystar.net/business/news/dse-reduced-tick-size-4019456 | S | Tk 0.10 tick; Tk 0.01 below Tk 1 from 2025-10-29 |
| 23 | StockSupporter, DSEX index page — https://www.stocksupporter.com/index-details/00DSEX | S | daily trades / volume / turnover (rolling page; counts not re-verifiable) |
| 24 | Wikipedia, *Dhaka Stock Exchange* — https://en.wikipedia.org/wiki/Dhaka_Stock_Exchange | V | hours 10:00–14:30 Sun–Thu; Ramadan 10:00–14:00; 2010–11 probe ("massive manipulation") |
| 25 | Muntasib-creator/DSE_dataset README (raw) — https://raw.githubusercontent.com/Muntasib-creator/DSE_dataset/main/README.md ; local clone commit `62403f3` (README, LICENSE, two data folders, no scraper code) | V | no capture method documented |
| 26 | Literature: Lee, Eom & Park, *Microstructure-based manipulation: strategic behavior and performance of spoofing traders*, J. Financial Markets 2013 (SSRN 1328899); Comerton-Forde & Putniņš, *Measuring closing price manipulation*, J. Financial Intermediation 2011 (SSRN 1009001) and *Stock price manipulation: prevalence and determinants*, Review of Finance 2014 (SSRN 1243042); Cont, Kukanov & Stoikov, *The price impact of order book events*, J. Financial Econometrics 2014; Easley, López de Prado & O'Hara, *Flow toxicity and liquidity in a high frequency world*, RFS 2012; Aggarwal & Wu, *Stock market manipulations*, J. Business 2006; Cumming, Johan & Li, *Exchange trading rules and stock market liquidity*, JFE 2011; Cao, Hansch & Wang, *The information content of an open limit-order book*, J. Futures Markets 2009; Lee & Ready, *Inferring trade direction from intraday data*, J. Finance 1991. Named by a reviewer, not searched: Holden & Jacobsen 2014; Ellis, Michaely & O'Hara 2000; Odders-White 2000; Chakrabarty, Pascual & Shkilko 2015 | S (existence and data used confirmed by search); U for the last four | data requirements per pattern |
| 27 | Provider pages: Oracle Always Free — https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm ; Oracle Free Tier FAQ — https://www.oracle.com/cloud/free/faq/ ; Google Cloud free tier — https://docs.cloud.google.com/free/docs/free-cloud-features ; AWS Free Tier — https://aws.amazon.com/free/ ; GitHub Actions limits — https://docs.github.com/en/actions/reference/limits ; Codespaces billing — https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-codespaces/about-billing-for-github-codespaces | V | quoted limits in §6.5 |
| 28 | faysal515/bd-stock-api — https://github.com/faysal515/bd-stock-api | V | unofficial dsebd crawler: latest (with trade count), top-30, DSEX, daily historical; no depth |
| 29 | This repository: `reports/MINUTE_DATA_QA_REPORT.md`, `qa/MINUTE_QA_ISSUES.json`, `qa/minute_observability_sample.py` → `qa/MINUTE_OBSERVABILITY_SAMPLE.json` (seed 7), and a reviewer's independent re-run with a different seed | Q | all Q figures |
| 30 | The Daily Star / New Age, April 2021, *BSEC sets price-fall circuit breaker for 66 cos at 2pc* — https://www.newagebd.net/article/135011/bsec-sets-price-fall-circuit-breaker-for-66-cos-at-2pc | S | 2 % lower limit on floor exit, 2021-04-07 |
| 31 | `evidence/README.md` in this repository | — | session observations that are not archived (request counts, resets, robots.txt, unreachable hosts) |

**Not reachable from this container** (record, not a finding): dsebd.org and dse.com.bd
(TLS reset / HTTP 503 on every page tried, including `mkt_depth_3.php`, `dse-mobile.php`,
`data_archive.php`), web.archive.org, scribd.com, slideshare.net, the rbsl.com.bd PDF
mirrors (404), thedailystar.net article pages (403 to the fetch tool; some reachable with a
browser user agent). Those must be verified from Bangladesh by the owner (D-13, D-16).

---

## 11. What v1 got wrong (all corrected above)

Four independent reviews of v1 found: the Phase 4.5 door count misquoted (a limit-*down*
hit count of one footprint was cited as the number of fresh limit-up doors; the real figure
is 3,328) · the floor-price end dates wrong by ~22 months (the last two exits are June 2026,
not August 2024) and unreconciled with `config.FLOOR_ERA` · the pre-opening session quoted
at its 2020 time (09:45) although it moved to 09:55 in November 2022 · "weighted" average
attributed to a source that says "average" · board names attributed to the wrong manual and
an odd-lot board asserted without evidence · "real-time push" and "top 10 levels" stated
beyond what the manuals say · pre-2020 single-price share quoted as 99.8 % when the pooled
figure is ≈ 99 % · the two-regime reading presented as fact rather than hypothesis · the
descriptive sample not reproducible from any committed artefact · the D-14 fallback claim
promising quantities (Q4, Q7, Q9) that the fallback source cannot deliver · Q1 rated
"fully measurable" while order counts are unknown · two door-defining quantities missing
(shares to the door; the pre-open auction state) · Comerton-Forde & Putniņš's prevalence
estimate attributed to the wrong paper; Cumming–Johan–Li and VPIN mischaracterised; the
trade-classification literature misattributed · the cadence conclusion stated
unconditionally, with no per-Q account of what sampling loses · the recorder's record
identity not restart-safe or comparable across capturers; framing, CRC, fsync, compression
and S2 request capture undefined; the single-login eviction and per-symbol-depth risks
unstated · sizing on a binary basis when the wire is probably JSON, and the Oracle
idle-reclamation risk read in the wrong direction (a 4.5 h/day recorder *meets* the idle
rule) · the snapshot-row rule at source with a first-trade false positive · D-16 duplicating
three existing ledger rows. The pattern is the same as in every earlier phase: **the
first-hand facts held; the readings needed hostile review.**
