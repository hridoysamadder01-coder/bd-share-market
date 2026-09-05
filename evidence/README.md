# evidence/ — manual verification artefacts for DATA_ACQUISITION_ARCHITECTURE.md

Nothing in this folder is recorder output. No recorder, poller or scraper was run.
Each file is a single manual verification fetch made while writing the
architecture document, kept so a reviewer can see exactly what a claim rests on.

| File | What it is | How it was obtained |
|---|---|---|
| `lankabd_marketdepthdata_BRACBANK_2026-09-02_after-close.json` | The JSON body returned by LankaBD's public market-depth endpoint for one symbol, after the 2026-09-02 session | One `GET https://lankabd.com/Home/MarketDepth` to obtain the page's anti-forgery token and cookie, then one `POST https://lankabd.com/Home/MarketDepthData` with form fields `Symbol=BRACBANK`, `Exchange=DSE`, the `RequestVerificationToken` header and a browser user agent (2026-09-02, ~22:00 UTC) |

**Session observations that are not archived** (stated here so they are graded honestly):

- Before the successful call above, about a dozen requests were made to the same
  endpoint over roughly ten minutes while working out the request shape (GET
  without a token → HTTP 405; POST without the token → HTTP 400; three attempts
  in quick succession → connection reset by peer, twice). Whether the resets are
  deliberate rate-limiting is **unverified**; the document treats the endpoint as
  rate-limited on that basis and designs any future use at a polite rate.
- `https://lankabd.com/robots.txt` returned an empty body on one fetch (not saved).
- The DSE website (`dsebd.org`, `dse.com.bd`) could not be reached from the research
  container at all (TLS connection reset with the session CA bundle; HTTP 503 through
  the fetch tool), so nothing from it is graded V in the document.
- The two DSE-FlexTP manuals (DSE-Mobile Android manual Rev. 201603; DSE Investor
  User Guide v1.1, July 2023) and the AmarStock page bundles were downloaded and
  read; they are third-party documents and are **not** committed. The document
  quotes them with their public URLs.
