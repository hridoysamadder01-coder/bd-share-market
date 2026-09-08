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
| Announcements, record dates, AGM dates, rights ratios | No structured public route located on dsebd.org or BullBD in this pass. BullBD exposes `corporateEvent` per symbol but it was `null` for the symbol checked, so its population rate is **unmeasured**. | Next pass: sample `corporateEvent` across many symbols to measure the fill rate before building on it. |
| BSEC enforcement cases, investigation periods, violation types, decision dates | The site is reachable (385 KB index, `robots.txt` allows everything) but orders are published as PDFs with no structured listing found. | Bytes captured daily from now. Needs a PDF-index parser, then OCR/extraction. |
| CDBL BO accounts, CDS market value, shares in CDS | The site is reachable (82 KB) but the statistics pages were not located behind the index in this pass. | Bytes captured daily. Needs a navigation pass to find the statistics routes. |
| Bangladesh Bank FX / policy / bill rates as **numbers** | Pages captured (46 KB, 49 KB) but BB re-lays-out its tables between publications, so no column mapping is committed. | Raw-first: parse on replay once the layouts are studied. Not a data gap. |

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
