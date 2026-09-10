# DSE Merged EOD Dataset
Built: 2026-09-01 | For: DSE-AI-TRADER backtesting

## Sources (both GitHub, academic/research use)
1. TashreefMuhammad/Dhaka-Stock-Exchange-EoD-Dataset-Metadata — 165 instruments, Oct 2012 → Jan 2026 (paper submitted to Financial Innovation/Springer; cite if published)
2. Muntasib-creator/DSE_dataset — 421 instruments daily + 419 minute-level, Oct 2012 → Feb 2024 (cite repo per its README)

Merge rule: source 1 (newer) wins on overlapping dates. Rows failing sanity (high<low, close<=0) dropped. Instruments with <100 days dropped.

## Coverage [Artifact — verify before trusting]
- 423 instruments total
- 100 extend into Jan 2026 (includes GP, BATBC, ROBI, SQURPHARMA, WALTONHIL)
- 321 end ~Feb 2024
- GAP: Feb 2026 → today missing for ALL instruments. Fill via DSE website / bdshare from a normal machine.

## Known caveats (do NOT skip)
1. UNADJUSTED prices — dividends/splits/rights NOT adjusted. Multi-day return calcs will show false jumps on ex-dates. Either restrict holding windows, detect >20% overnight gaps as suspect, or source adjustment factors.
2. Includes bonds, mutual funds, treasuries — filter to equities via symbol list before backtesting.
3. No category (A/B/Z) column — needs separate mapping from DSE site.
4. Floor-price era (mid-2022 → early 2024): many stocks pinned at floor, zero real liquidity despite printed prices. EXCLUDE or flag this window in backtests, else results are fiction.
5. Third-party data — spot-check 5-10 symbols against DSE official records before believing any backtest.

## Evidence class: Artifact (cross-checked between two independent sources on overlap, NOT verified against DSE official)
