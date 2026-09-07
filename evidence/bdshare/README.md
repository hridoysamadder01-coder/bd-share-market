# bdshare probe — a third independent read of the DSE book

`bdshare` 1.2.5 (`get_market_depth_data`), which fetches `dsebd.org/mkt_depth_3.php`
directly. Run 2026-09-08 02:47 Dhaka (market closed) over the ten symbols the overnight
watcher tracks. Raw result: `order_book_2026-09-08T0247Z.json`.

**It fails out of the box here.** `dsebd.org` serves a broken certificate chain, so every
call raises `SSLCertVerificationError`. The library builds its own `requests.Session`, so
`set_session()` does not help; the fallback has to be forced at the `Session.request`
level for that host only. This repo's own client already does verified-then-fallback for
exactly this host (`seeing/capture/http_client.py`, `TLS_FALLBACK_HOSTS`).

## What it returns

Four columns, and only four, across every symbol:
`buy_price`, `buy_volume`, `sell_price`, `sell_volume`.

| symbol | bids | asks | bid qty | ask qty | imbalance |
|---|---|---|---|---|---|
| BEXIMCO | 4 | 4 | 3,625 | 55,364 | −0.8771 |
| ROBI | 1 | 1 | 50,000 | 5,000 | +0.8182 |
| AAMRANET | 1 | 0 | 3,000 | 0 | +1.0000 |
| BRACBANK | 3 | 0 | 20,500 | 0 | +1.0000 |
| 1STPRIMFMF | 0 | 1 | 0 | 650 | −1.0000 |
| AAMRATECH, GP, CITYGENINS, SQURPHARMA, WALTONHIL | 0 | 0 | 0 | 0 | — |

These match the LankaBD sensor's numbers for the same symbols at the same minute, so the
two independent paths agree on the live closed-market book.

## Why it matters

No order count, no queue position, no pending-order field, no trade prints, no side —
the same ceiling already recorded for the broker terminal (row 20) and the public portals
(rows 1 and 4). `dse-stock-mcp` (`github:SambasBoyyyy/dse-stock-mcp`) was also queried over
MCP stdio: five tools (`get_current_price`, `get_historical_data`, `list_symbols`,
`resolve_symbol`, `get_company_news`), none of them a depth or book tool.

Four independent free paths, one ceiling. Order-level data is a paid product: DSE sells
Real Time Level-2 with ITCH (order-by-order) and BBO10, contact `imdsp@dse.com.bd`,
PABX +88-02-41040189-200 ext. 1547/1541 — from DSE's own
`assets/pdf/Introduction of DSE Data Sale Services.pdf` and `assets/pdf/iMDS.pdf`.
Published fees there: iMDS real-time option 1 initial BDT 50,000 plus BDT 32,799/month;
EOD BDT 60,000/year; academic/research initial BDT 50,000 plus minimum BDT 10,000/month.
Whether ITCH/Level-2 is included at the academic rate is not stated in the PDF.
