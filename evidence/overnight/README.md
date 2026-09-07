# Overnight book watch — does the closed book accumulate orders before the open?

## The claim being tested

Hridoy, 2026-09-07: *the exchange chart cannot move while the market is closed, but
advance orders keep accumulating behind it, and that accumulation is what moves the
chart the next morning.*

The mechanism is not in doubt: the EcoSoft OST terminal reports `Accept Order: Yes`
with `Market Closed` on screen, and `GET /core/api/v1/Order/AcceptOrder/4` returns
`{"Success":true,"Data":"Yes"}` — orders are accepted while the market is shut. The
open question is only whether that accumulation is **observable** before the open.

## What was already observed (2026-09-07, market closed since 14:10 Dhaka)

The closed book is **not** frozen. Two independent facts, one sensor each:

| symbol | time (Dhaka) | book | sensor |
|---|---|---|---|
| CITYGENINS | 20:22–20:31 | 10 bid levels + 7 ask levels | broker terminal HAR |
| CITYGENINS | 22:08 | **0 + 0 (emptied)**, re-polled ×3 | LankaBD public |
| AAMRANET | 21:41 → 22:08 | 1 bid 16.80 × 3000, unchanged | terminal HAR, then LankaBD |
| 1STPRIMFMF | 21:59 → 22:08 | 1 ask 21.90 × 650, unchanged | screenshot, then LankaBD |

Cross-section at 22:08 Dhaka: some symbols hold residual orders while closed
(AAMRANET 1/0, 1STPRIMFMF 0/1, BRACBANK 3/0, BEXIMCO 4/4, ROBI 1/1) and some are empty
(CITYGENINS, GP, SQURPHARMA, WALTONHIL). So "market closed means nothing moves" is
false as stated: orders **disappear** while closed.

What has **not** been observed is the opposite direction — a level *appearing* or
growing while the market is closed. That is exactly what accumulation would look like,
and it is what this watcher exists to catch.

## The decision rule (fixed before the data arrives)

Between the evening and the 10:00 Dhaka open, per symbol:

* **levels only ever disappear, and the book is empty or unchanged at 09:55** →
  broker-held advance orders do not reach the exchange book before the open.
  The claim may still be true inside the broker, but it is NOT_OBSERVABLE from any
  sensor reached so far, and it is not a tradable signal for us.
* **levels appear or grow before the open** → the accumulation IS visible, and the
  pre-open book is foreknowledge of opening pressure. That is a real candidate signal.
  It would then need its own pre-registration before any edge claim, exactly like the
  frozen micro experiment — this file is an observation log, not a result.

## What the watcher does

`micro/engine/overnight_book_watch.py` appends one JSON line per pass to
`<trading date>.jsonl`: per symbol the full bid/ask ladder, level counts, best prices,
total quantities, a book hash, and the LTP/day totals. Each pass prints what
**appeared** and what **disappeared** since the previous line. A failed symbol is
recorded as a gap so a missing row is never read as an empty book.

It reads one public sensor (LankaBD `/Home/MarketDepthData`, the same book the broker
terminal republishes) at a polite interval, hourly. It changes nothing and decides
nothing.

## Scope

This is exploratory observation. `micro/MICRO_PREREG.json`, the universe, thresholds,
splits and the sealed holdout are untouched and stay untouched.
