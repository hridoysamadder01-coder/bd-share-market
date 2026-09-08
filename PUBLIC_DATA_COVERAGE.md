# PUBLIC DATA COVERAGE — every target field, and which sensor actually delivers it

One row per field in the target schema. `TRUTH` is the strongest class any wired source
achieves for that field; `SOURCE(S)` names the sensors that carry it. A field with two or
more independent sources can be cross-checked by `seeing/consensus.py`, which reports
disagreement and never resolves it.

`FRESHNESS` is how far behind the observation is when it reaches us, not how often we ask.
`COVERAGE` is the share of the DSE equity universe the field is available for.

## IDENTITY

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| source, exchange | every adapter | OBSERVED | — | 100 % |
| symbol | LankaBD ×4, dsebd ×2, StockNow, CSE, BullBD | OBSERVED | — | 473 DSE / 388 CSE |
| company_id | LankaBD watch (`companyID`), StockNow (`sector_id`) | OBSERVED | — | ~100 % |
| instrument_id | LankaBD watch (`mkistaT_INSTRUMENT_NUMBER`) | OBSERVED | — | ~100 % |
| mic | BullBD (`XDHA`) | OBSERVED | 1 session | per symbol polled |

## TIME

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| t_recv | raw store, every record | OBSERVED | 0 | 100 % |
| t_source | LankaBD watch (exchange stamp, to the second), LankaBD tape (epoch ms), StockNow (`updated_at`), BullBD (ISO-8601 UTC) | OBSERVED | 0–60 s live; EOD after close | 4 of 9 live sources |
| trading_date, session_phase | `seeing/clock.py` | INFERRED | 0 | 100 % |
| freshness_ms | `t_recv − t_source`, `seeing/consensus.py` | INFERRED | — | where t_source exists |
| delayed_flag | `SourceSpec.delayed` | OBSERVED | — | declared per source |

## BOOK

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| bid_levels, ask_levels | LankaBD depth, dsebd depth | OBSERVED | live, on demand | polled symbols only |
| best_bid, best_ask, spread | derived from the ladders | INFERRED | as above | polled symbols |
| n_bid_levels, n_ask_levels | both depth sources | OBSERVED | live | polled symbols |
| total_buy_volume, total_sell_volume, buy_percentage, sell_percentage | LankaBD depth | OBSERVED | live | polled symbols |
| **bid_orders_per_level, ask_orders_per_level** | — | **NOT_OBSERVABLE** | — | 0 % |
| **queue_position, order_events** | — | **NOT_OBSERVABLE** | — | 0 % |

Two independent book sensors, so a book disagreement is detectable. Neither publishes
order counts; that ceiling is unchanged by everything added here.

## PRICE / ACTIVITY

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| ltp, open, high, low, close_published, yclose | LankaBD ×4, dsebd ×2, StockNow, CSE, BullBD | OBSERVED | live → EOD | **5 independent sensors** |
| day_trades, day_volume, day_value | same five | OBSERVED | live → EOD | ~100 % |
| interval_trades / volume / value / vwap | LankaBD tape, differenced | INFERRED (cumulative differencing) | ~1 min | polled symbols |
| **trade_prints, trade_side** | — | **NOT_OBSERVABLE** (side is INFERRED by the quote rule only) | — | 0 % |

## MARKET

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| market_trades, market_volume, market_value | LankaBD market stats | OBSERVED | live | market-wide |
| advancing, declining, unchanged | LankaBD market stats | OBSERVED | live | market-wide |
| index_value | LankaBD, EcoSoft `Analysis/IndexData` (owner HAR) | OBSERVED | live | DSEX |

## REFERENCE

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| upper_limit, lower_limit, tick_size, breaker_pct | LankaBD circuit (635), dsebd `cbul.php` (636) | OBSERVED | daily | **two independent sources**, 635–636 instruments |
| market_category | LankaBD watch, StockNow, BullBD | OBSERVED | daily | ~100 % |
| sector | StockNow (`sector_id`), BullBD (name) | OBSERVED | daily | ~100 % |
| session_rules | dsebd `hts.php` | OBSERVED | on change | market-wide |
| floor flag | **StockNow only** | OBSERVED | EOD | 473 |
| 7d/15d/30d/90d/180d/365d reference price, yearly high/low | **StockNow only** | OBSERVED | EOD | 473 |

## BLOCK

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| block_trades, block_quantity, block_value, block_max_price, block_min_price | LankaBD block board | OBSERVED | daily list | symbols with block prints |
| intraday block timing | — | NOT_OBSERVABLE | — | 0 % |

## FUNDAMENTALS

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| eps, nav, pe, price_to_nav | **BullBD only** | OBSERVED | quarterly-ish | per symbol polled |
| paid_up_capital | **BullBD only** | OBSERVED | quarterly | per symbol polled |
| **total_shares (outstanding)** | **BullBD only** | OBSERVED | on change | per symbol polled |
| market_cap | `total_shares × ltp` | INFERRED | as inputs | per symbol polled |

`total_shares` is what converts a holding *percentage* into a share *count*. No other free
source in this repository publishes it, which is why BullBD is registered despite giving
no book.

## OWNERSHIP

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| sponsor_director_pct, government_pct, institution_pct, foreign_pct, public_pct | `collector/dse_public_collector.py` → `displayCompany.php` | OBSERVED | monthly as-on | 419 symbols, 1,220 rows |
| free_float | — | NOT_OBSERVABLE | — | 0 % |

**Usable history is the binding constraint, not access:** DSE exposes only three as-on
dates per company with no archive, so 162 of 208 usable observations share one month.
See `evidence/platform_survey/README.md`. Monthly collection from now on is the only fix.

## REGIME / MACRO

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| fx_rate | Bangladesh Bank exchangerate page | OBSERVED (raw kept, parser pending) | daily | market-wide |
| policy_rate, repo_rate, tbill_yield | Bangladesh Bank bill page | OBSERVED (raw kept, parser pending) | daily–weekly | market-wide |
| bo_accounts, cds_market_value, shares_in_cds | CDBL site (index captured; statistics pages not yet located) | NOT_OBSERVABLE **yet** | — | 0 % |

## EVENTS / REGULATORY

| FIELD | SOURCE(S) | TRUTH | FRESHNESS | COVERAGE |
|---|---|---|---|---|
| corporate_event | BullBD `shareDetail.corporateEvent` (null for the symbol checked) | OBSERVED when present | — | unmeasured |
| announcement_*, record_date, agm_date, rights_ratio | — | NOT_OBSERVABLE **yet** | — | 0 % |
| enforcement_case, investigation_period, violation_type, regulator_decision_date | BSEC site (index captured; orders are PDF) | NOT_OBSERVABLE **yet** | — | 0 % |

## Summary

| | fields |
|---|---|
| OBSERVED by ≥ 2 independent sources | price/activity (5 sensors), circuit limits (2), book (2) |
| OBSERVED by exactly 1 source | fundamentals, outstanding shares, horizon prices, floor flag, ownership, macro |
| INFERRED | interval tape, market cap, freshness, session phase, trade side |
| NOT_OBSERVABLE, structural | order counts, prints, queue position, order events, free float |
| NOT_OBSERVABLE, pending work | announcements, BSEC enforcement, CDBL participation |
