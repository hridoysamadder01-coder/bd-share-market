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

## STOPPED 2026-09-08 at the owner's request — the rule above is UNRESOLVED

Hridoy: *"sararat er oi check bondho koro or kono dorkar hoyto nai amader."* The watcher
was stopped, the scheduled post-open pass was cancelled, and no further passes will run.

Seven passes were taken, 2026-09-07 22:09 → 2026-09-08 04:10 Dhaka. Books were compared
by `book_sha256`, not by eye:

| Dhaka time | symbols | appeared | disappeared | hash changed | empty books |
|---|---|---|---|---|---|
| 2026-09-07 22:09 | 9 | (first pass) | — | — | 4 |
| 2026-09-07 22:10 | 9 | 0 | 0 | 0 | 4 |
| 2026-09-07 22:18 | 10 | 1 (joined watch list) | 0 | 0 | 5 |
| 2026-09-08 00:53 | 10 | 0 | 0 | 0 | 5 |
| 2026-09-08 01:57 | 10 | 0 | 0 | 0 | 5 |
| 2026-09-08 03:01 | 10 | 0 | 0 | 0 | 5 |
| 2026-09-08 04:10 | 10 | 0 | 0 | 0 | 5 |

Every book byte-identical across the night. Nothing appeared, nothing disappeared. The
lone `appeared = 1` is the tenth symbol joining the watch list at 22:18, not a book event.

**This does not satisfy the first branch of the decision rule, and must not be read as
satisfying it.** That branch requires the book to be empty or unchanged **at 09:55**. The
last pass was 04:10 Dhaka, so the final **5 h 45 m before the open — 04:10 → 09:55, the
window in which pre-open accumulation would most plausibly appear — was never sampled.**
The rule is therefore unresolved, not answered. Six hours of a flat closed book overnight
is a weak observation, and it is recorded as one.

There is a second, independent reason this watcher could not have settled the question
even if it had run to 09:55. Its sensor is the LankaBD public book, described above as
"the same book the broker terminal republishes". **That premise was later falsified**: a
third HAR showed AAMRATECH with a 10 × 8 ladder in the terminal against a 0 × 0 public
book one minute later. The correction is in `SCHEMA_MAP.md` (frozen close snapshot vs live
post-close). So a flat public book is consistent both with "nothing accumulated" and with
"accumulation happened where this sensor cannot see it", and the data cannot separate the
two. Answering the original claim needs a broker-side sensor, not more passes of this one.

Status: **STOPPED, RULE UNRESOLVED.** The mechanism (orders accepted while closed) stays
confirmed. Whether that accumulation is observable pre-open is still open, and no claim
either way is supported by what is in these files.

## Scope

This is exploratory observation. `micro/MICRO_PREREG.json`, the universe, thresholds,
splits and the sealed holdout are untouched and stay untouched.
