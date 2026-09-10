# Free-test round — 2026-09-10 (post evidence-lock)

**Context.** Hridoy explicitly asked ("freely test strategies again") for me to
drop the ceremonial "discovery is closed" I had put in place, and get back to
testing new candidates on the daily panel. His own hard safety rules stay:
read-only market data, no BUY/SELL execution, no auth bypass, no fabrication.

**Candidate tested:** upper-circuit continuation.

**Signal (day t):**

    ret_1d >= 0.08  AND  close == high  AND  turnover >= 5,000,000 TK

**Panel:** 2012-10-01 → 2026-01-22, sealed holdout 2019-01-01→2022-07-27 and
reserved-from-2026-01-25 dropped. n_signal_rows = 3,279 across 365 symbols and
1,285 dates. Independent episodes (>=10-session gap): 2,207 (1.49x overlap).

## Result at close-to-close accounting (naïve entry — day-t close → day-t+h close)

Matched controls (5 per signal, same date + liquidity quintile + vol quintile):

| h  | signal   | control | diff    | t   |
|----|----------|---------|---------|-----|
| 1  | +2.55 %  | +0.01 % | +2.54 pp| 22.3|
| 3  | +2.96 %  | +0.17 % | +2.78 pp| 12.8|
| 5  | +3.13 %  | +0.20 % | +2.93 pp| 10.5|
| 10 | +2.96 %  | +0.13 % | +2.83 pp|  7.3|

Year consistency at h=3: 12/12 years positive, mostly t >= 3.

Median MFE over next 5 sessions: **+9.55 %**. Median MAE: **-4.14 %**.

## Realistic-entry result (the killer)

You cannot fill at day-t close: the stock closed AT its high. Realistically the
earliest entry is day-t+1 open. The signal-day → next-open gap distribution:

- mean **+3.25 %**, median **+2.67 %**, p25 +1.31 %, p75 +4.63 %
- P(gap > 0) = **90.8 %**

So on average, **90 % of the "predicted continuation" is priced in overnight,
before you can buy**. Realistic returns (t+1 open → t+h close), matched:

| h  | signal   | control | diff    | t   | net after 1 % cost | P(net > 0) |
|----|----------|---------|---------|-----|--------------------|------------|
| 1  | -0.44 %  | -0.78 % | +0.34 pp| 4.8 | **-1.44 %**        | 0.36       |
| 3  | +0.28 %  | -0.61 % | +0.88 pp| 4.6 | **-0.73 %**        | 0.42       |
| 5  | +0.57 %  | -0.64 % | +1.18 pp| 5.3 | **-0.46 %**        | 0.41       |
| 10 | +0.44 %  | -0.63 % | +1.06 pp| 4.5 | **-0.56 %**        | 0.36       |

Every horizon nets negative after the 1 % round trip.

## Verdict — the honest one

The signal is **real and statistically strong** in close-to-close terms. It is
**dead as a taker** because the overnight gap arbitrage prices the continuation
before entry is possible. Realistic edge left after gap: +0.3 to +1.2 pp before
cost, which does not clear ~1 % round-trip.

Same shape as the micro touch-locality lock (V-014): a real information asymmetry
that the market prices before you can act on it.

The only direction this candidate could still trade is **maker-style** — a
resting limit order below the day-t close, hoping to be filled on an intraday
pullback the next morning before the run resumes. That requires a fill model
this data cannot provide, and getting filled at a lower price by definition
selects for cases where the signal is weaker.

**Status: WEAK (real edge, priced in the gap; ECONOMICALLY_UNUSABLE as taker).**

## What this proves about the harness

- The daily panel, PIT-safe, sealed holdout preserved.
- The matched-control approach works and isolates real signal.
- The realistic-entry check (gap analysis) is the killer test that any
  daily-signal candidate must survive from here on.
- 1 % round-trip is the bar. Anything netting negative after 1 % is a
  descriptive finding, not a trade.

Do NOT re-run "upper circuit continuation" in any parameterisation without
first accounting for the overnight gap. If you strip the gap out of any
signal you find, you strip out the trade.
