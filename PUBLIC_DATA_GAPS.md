# PUBLIC DATA GAPS — only what is genuinely unavailable

A field belongs here only if no wired source delivers it **and** the reason was checked
rather than assumed. Anything obtainable with more work is in the second table, with the
work named. Fields that are merely un-parsed (BB rate tables, BSEC orders, CDBL
statistics) are **not** gaps — the bytes are being captured; see `PUBLIC_SOURCE_MATRIX.md`.

## 1. Structurally unavailable from any free public Bangladesh source

| FIELD | WHY | WHAT WOULD LIFT IT |
|---|---|---|
| **bid_orders_per_level / ask_orders_per_level** | Every public sensor publishes aggregated price+quantity per level. Five independent sources checked (dsebd, LankaBD, StockNow, CSE, the owner's EcoSoft terminal) — all hit the same ceiling. The broker terminal republishes the same aggregation. | FIX 35=W with tag 346 (`NumberOfOrders`), i.e. a DSE MDS entitlement. Parser already built and tested: `seeing/capture/adapters/fix_md.py` |
| **trade_prints (per-trade tape) and trade_side** | No public route returns individual executions. The finest public granularity is LankaBD's ~1-per-minute cumulative rows. Side can only be inferred by the quote rule. | Broker Level-II / Time & Sales export, or FIX 35=X. Both adapters exist: `broker_export.py`, `fix_md.py` |
| **queue_position, order_events, cancel_vs_trade_split** | Requires order-by-order data. Snapshot sources cannot express it, and differencing snapshots conflates a cancel with a trade. | Order-by-order feed (not offered publicly by DSE) |
| **intraday block-trade timing** | The block board publishes a daily list per symbol, with no timestamps. | — none identified |
| **free_float** | `displayCompany.php` publishes the five holding percentages but not a float figure. Free float could be derived from `100 − sponsor_director_pct` **only** under an assumption about lock-ins that is not stated anywhere, so it is left NOT_OBSERVABLE rather than computed. | An exchange or regulator publication that states float directly |

## 2. Available in principle, blocked by a named dependency

| FIELD | BLOCKER | STATE |
|---|---|---|
| AmarStock depth monitor, 1-minute VPA, VPA archive | `robots.txt` disallows `ClaudeBot` / `anthropic-ai` / `Claude-Web`. A policy blocker, not a technical one. | **Will not be collected.** Registered blocked in the engine so the gap stays visible. |
| BullBD depth / minute series / circuit | Socket-filled (`global.socketData`); the server-rendered slots are empty or hollow. | Would need the socket protocol; not attempted. |
| Announcements, record dates, AGM dates, rights ratios **per symbol** | No structured public route located on dsebd.org or BullBD. BullBD exposes `corporateEvent` per symbol but it was `null` for the symbol checked, so its fill rate is **unmeasured**. BSEC publications (now parsed) are market-wide regulatory documents, not per-symbol corporate actions. | Stage 6. Measure the BullBD `corporateEvent` fill rate first; if it is near zero the stage returns BLOCKED. |
| BSEC **enforcement detail** — investigation period, violation type, penalty amount, the symbol involved | The publication *index* is now parsed (35 publications, 30 dated, including Orders on floor-price withdrawal and market control parameters), but each document is a **PDF this parser does not open**. | A PDF extraction pass. Recording that a publication exists, when and of what type is done; reading its contents is not, and is not guessed. |
| Bangladesh Bank **FX rate** | The page answers **HTTP 200 with a CAPTCHA**, not data — "this question is for testing whether you are a human visitor", a support ID and an image code, reproduced on every attempt 2026-09-08. A bot challenge, not an outage: retrying cannot fix it, and solving it is not something this system does. | **BLOCKED and registered as such.** `fx_rate` stays NOT_OBSERVABLE. Detected as `bot_challenge` — before that code existed the page counted as a WORKING source. |

## 3. The gap that dominates everything else

**Ownership history.** The five holding percentages are OBSERVED for 419 symbols, but DSE
publishes only **three as-on dates per company and keeps no archive**, so 162 of 208 usable
observations share the single month 2026-06. The join is not statistically usable today,
and no amount of price data fixes it — the constraint is on the ownership side.

The only remedy is time: run `collector/dse_public_collector.py` monthly from now on. Each
run adds one as-on date per symbol; a year gives ~400 symbols × 12 dates spread over 12
independent months. Nothing computed before that has meaning.
See `evidence/platform_survey/README.md` for the counts.

## Not gaps

* **DSE `latest_share_price_scroll_l.php` and `hts.php` connection resets.** Intermittent
  host flakiness (2 of 4 attempts from this container; both return 200 with retry). Recorded
  as `connect_error` GAPs so the hole is in the record. The data is available.
* **Trading-phase sources showing UNTRIED after hours.** Correct behaviour: book and tape
  specs are gated to trading phases.
