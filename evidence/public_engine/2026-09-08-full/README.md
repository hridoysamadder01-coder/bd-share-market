# Whole-market public pull — DSE, 2026-09-08 (market CLOSED)

First sweep of the **entire DSE listing universe** rather than a hand-picked
symbol list. 691 listings, taken from `evidence/identity/2026-09-08/IDENTITY_MAP.json`
(every listing the Stage 0 identity spine attributes to DSE), against every public
source that publishes anything while the market is shut.

Run at 09:44–10:03 UTC = **15:44–16:03 Dhaka**, i.e. after the 14:10 close. That is
the whole point of the timing and also its limit: what a closed market publishes is
the day's outcome and its reference data, never its order flow. **No depth, tape,
watch, market or block feed appears here** — those sources are `TRADING_PHASES`
only. This run is the reference and outcome layer; the intraday layer needs a
session.

## What came back

| source | table | rows | symbols | of 691 | missing | gaps |
|---|---|---:|---:|---:|---:|---|
| `dsebd_latest` | `latest` | 395 | 395 | 395 | 296 | 1 connect_error |
| `lankabd_circuit` | `circuit` | 1270 | 635 | 635 | 56 | — |
| `stocknow_instruments` | `instruments` | 946 | 473 | 460 | 231 | — |
| `bullbd_detail` | `fundamentals` | 718 | 472 | 472 | 219 | 254 not_found |
| `dse_ownership` | `ownership` | 1220 | 419 | 419 | 272 | 272 parse_error |
| `cse_current_price` | `cse` | 774 | 387 | 386 | 305 | — |
| `bsec_publications` | `regulatory` | 35 | — | — | — | — |
| `bb_bill_rate`, `cdbl_stats` | `macro` | 5 | — | — | — | — |
| `dsebd_hts` | `hts` | 1 | — | — | — | 1 connect_error |

### A second reading of the same bytes

`dse_ownership` fetches `displayCompany.php` once per symbol for its shareholding
block. The rest of that page was captured and never read. Parsing it on replay —
**no extra request, and retroactive to this store** — adds `extract/company_profile.csv`:

| | symbols |
|---|---:|
| scrip code | 636 |
| listing year, market category, year end, loans, reserves | 419 |
| **cash-dividend history** | **371 symbols, 2,984 (symbol, year, percent) rows** |
| latest dividend % and its year | 217 |
| issuer disclosure URLs (financials, price-sensitive info) | 309 |

The deepest records run to 38 years (APEXTANRY), 34 (HEIDELBCEM, MONNOCERA). The
dividend string is kept verbatim beside the parsed pairs, because when the two
later disagree the raw text is what settles it.

The page also prints the company secretary's name, personal mobile and personal
e-mail, plus factory address, phone and fax. **None of it is extracted** — those
are people and premises, not market data — and a test asserts no field carries
them.

**The "missing" column is not a failure count.** 691 is every DSE *listing*, and
roughly 230 of those are treasury bills and bonds (`TB2Y…`, `TB20Y…`), which have
no shareholding block, no EPS and no trades. That is why the numbers cluster where
they do:

* **395** symbols actually traded on 2026-09-08 (`dsebd_latest`) — the real
  tradeable universe for the day.
* **635** carry circuit limits, the widest per-symbol coverage of any source.
* **472** have fundamentals on BullBD; the 254 `not_found` are the instruments it
  does not list.
* **419** have a shareholding declaration; the 272 `parse_error` are pages that
  exist but carry no such block — recorded as a distinct reason from `not_found`
  precisely because it means something different.

`dsebd_latest` and `dsebd_hts` show one `connect_error` each: dsebd.org reset the
connection at 09:45. Both had already been captured successfully at 09:30 in the
first epoch of this same store, so the tables are complete and the gap record is
kept rather than swept away.

## Layout

```
MANIFEST.json      every segment: source, record count, sha256, hash-chain link
SOURCE_STATUS.json per-source health, attempts, and every failure code
extract/*.csv      the parsed tables — 1.9 MB, this is what analysis reads
extract/COVERAGE.json  the table above, machine-readable, plus store verification
segments/          the raw bytes: ~300 MB, deliberately NOT in git (.gitignore)
```

The raw store stays on the machine that pulled it. `MANIFEST.json` carries the
sha256 of every record and the per-source hash chain, so any row in a CSV can be
traced back to the exact bytes it was parsed from, and `verify_store()` reports
`all_ok: true, chain_ok: true` across all 22 segments.

Regenerate the tables from the raw store with:

```
python3 -m seeing.extract --store evidence/public_engine/2026-09-08-full \
                          --out   evidence/public_engine/2026-09-08-full/extract \
                          --universe "<the 691 DSE codes>"
```

## Three defects this run exposed

The sweep is also the first thing that ever ran the engine at market width, and it
found three bugs that a 14-symbol capture could not:

1. **Nothing was due.** `time.monotonic()` counts from boot; the engine used `0.0`
   for "never polled". On a container with 23 minutes of uptime, no source with an
   hourly-or-slower closed cadence was due, and the engine ran six minutes writing
   only heartbeats. Fixed with `NEVER_POLLED = -inf`.
2. **A 404 was treated as a rate problem.** Sweeping 691 listings against a source
   that lists only equities produced hundreds of consecutive 404s, each escalating
   the exponential backoff to its 120 s cap — 12 misses cost 726 s of waiting. 404
   and 410 are now neutral; 429, 403 and 5xx still back off.
3. **List-order polling starved the registry.** The first due item in registry
   order meant a wide per-symbol source took every slot. Simulated at the real
   0.4 s gap over 30 minutes: depth 1500 polls, tape 0, market 0. The scheduler now
   picks the most overdue work measured in cadences.

Only (1) and (2) affected this run. (3) would have hit tomorrow's open session,
where depth and tape compete for the same budget.
