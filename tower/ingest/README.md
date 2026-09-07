# tower/ingest — zero-loss capture daemon (Go 1.24)

A generic capture daemon that writes raw segments **byte-compatible with
`seeing/capture/raw_store.py`** (`RawStore`): the Python verifier
(`verify_store`) and `seeing.replay` read its output unchanged. Nothing is
parsed, de-duplicated or repaired at capture time — bytes in, bytes on disk,
with enough evidence to prove exactness later.

```
cd tower/ingest
go build ./... && ./ingest -config cfg.json     # run until SIGTERM/SIGINT
./ingest -verify <out>                          # re-hash every segment in MANIFEST.json
go vet ./... && go test ./...
```

The only dependency (`github.com/gorilla/websocket`) is vendored, so the build
is hermetic (`vendor/` is used automatically by `go build`).

## Output format (identical to the Python store)

* `<out>/segments/<source>__<capturer>__<epoch8>__<YYYYMMDDTHH>__<seq8>.jsonl`
  — one segment per (source, UTC hour), rotated hourly, never appended to
  after a restart (each process start mints a new epoch).
* Every line is a JSON record with `kind` (META, DATA, HEARTBEAT, GAP, TRAILER),
  `source`, `capturer_id`, `epoch`, `seq` (monotonic per source), `t_recv_utc`
  (Python `isoformat()` shape: `2026-09-06T01:06:50.988888+00:00`),
  `t_recv_mono_ns` (process-monotonic; never compared across restarts).
* DATA: `key`, `src_seq` (FIX tag 34 for SOH-framed streams, else `null`),
  an `http{...}` envelope (method, url, params, form, request_headers without
  cookies, t_send_utc, tls_verify, t_first_byte_utc, t_last_byte_utc, status,
  response_headers, final_url, elapsed_ms) or a `transport{...}` envelope
  (type, addr/url/path, framing, conn_id, frame_index, offset, opcode), then
  `len`, `crc32` (IEEE over the raw bytes), `body_sha256`, `body` and
  `body_encoding` — `utf8` when the bytes are valid UTF-8 (stored as a JSON
  string, no HTML escaping), else `b64`.
* First record of a segment: META with `prev_segment_sha256` — the sha256 of
  the previous closed segment of that source (hash chain; continued across
  restarts from the manifest; `null` for a source's first segment).
* Last record: TRAILER with `records` (lines before the trailer, META
  included), `first_seq`, `last_seq`, `sha256_before_trailer` (sha256 of every
  byte before the trailer line). The trailer consumes a seq number, exactly as
  the Python store does.
* `fdatasync` at every heartbeat, every GAP and every segment close.
* `MANIFEST.json` `{schema_version:1, capturer_id, epoch, software_version,
  opened_utc, segments:[{source,path,records,first_seq,last_seq,sha256,bytes,
  epoch,closed_utc}], previous_epochs, closed_utc}` rewritten (tmp + rename) on
  every segment close. Entries read from an existing manifest are kept
  verbatim, so the `gz_path`/`gz_sha256`/`gz_bytes` fields the Python
  `compress_and_verify` adds survive a Go restart, and `-verify` reads a
  segment through `gz_path` when the plain file was replaced (same rule as
  `raw_store.verify_store`). `status.json` mirrors the last heartbeat.
* The runner's final META carries `stopped_by` (`signal:<name>`, `deadline`
  for `-run-for`, or `cancel`) and `stopped_by_signal`.

## Loss accounting — nothing is ever silent

| event | record |
|---|---|
| writer queue full (streaming transports) | `GAP{reason:"drop", dropped:N}` written by the writer as soon as it can write again; `dropped` also in every heartbeat |
| established connection lost | `GAP{reason:"disconnect"}` then reconnect with backoff (1 s doubling to 60 s, reset after a frame arrives) |
| connection attempt failed | `GAP{reason:"connect_error"}` per attempt |
| framer skipped bytes to resynchronise | `GAP{reason:"resync"}` with the count |
| connection ended inside a frame (peer hang-up, reset, read timeout or daemon shutdown) | `GAP{reason:"partial_frame"}` with the bytes in `body` |
| HTTP non-2xx | `GAP{reason:"http"}` with the envelope and the body |
| HTTP transport error | `GAP{reason:"exception"}` with the envelope |
| anti-forgery token unavailable | `GAP{reason:"token"}` |
| tailed file truncated / rotated | `GAP{reason:"truncate"/"rotate"}`, restart at offset 0, unframed bytes kept in `body` (also at start-up when the file was replaced or shortened since the persisted offset) |
| tailed file read error | `GAP{reason:"exception"}`, then re-open at the consumed offset with fresh framing (the unframed tail is re-read, nothing lost or duplicated) |
| `from_end` skipped an existing prefix | `GAP{reason:"from_end"}` with the skipped byte count |

Polling/tailing transports (`http_poll`, `file_tail`) use a **blocking** put
(back-pressure instead of drops — the upstream can wait). Streaming transports
(`tcp`, `websocket`) use a non-blocking offer so a slow disk never stalls the
socket; the bounded queue (`queue_size`, default 4096 per source) makes the
drop explicit.

`HEARTBEAT` every `heartbeat_ms` (default 5000) to source `heartbeat` with
`status.ages_s` (seconds since the last DATA per source, `null` when a source
has never delivered), `status.counts` per source (`ok, err, unchanged, frames,
reconnects, enqueued, written, dropped, queue, bytes, state`).

SIGTERM/SIGINT: transports stop, writer queues drain, every segment gets its
TRAILER, the manifest gets `closed_utc`.

## Config

```json
{
  "out": "evidence/capture/2026-09-07",
  "capturer_id": "ccr-vm",
  "heartbeat_ms": 5000,
  "queue_size": 4096,
  "sources": [
    {"name": "lankabd_watch", "type": "http_poll",
     "url": "https://lankabd.com/api/datafeed/IndexLiveData/LiveStockWatchData",
     "method": "GET", "interval_ms": 2000,
     "headers": {"Referer": "https://lankabd.com/Home/MarketDepth", "X-Requested-With": "XMLHttpRequest",
                 "Accept": "application/json, text/javascript, */*; q=0.01"},
     "token_url": "https://lankabd.com/Home/MarketDepth",
     "token_regex": "name=\"__RequestVerificationToken\"[^>]*value=\"([^\"]+)\"",
     "token_header": "RequestVerificationToken", "token_ttl_s": 1500},
    {"name": "dsebd_depth", "type": "http_poll", "url": "https://www.dsebd.org/ajax/load-instrument.php",
     "method": "POST", "form": {"inst": "MALEKSPIN"}, "key": "MALEKSPIN", "interval_ms": 3000,
     "tls_insecure": true},
    {"name": "md_ws", "type": "websocket", "url": "wss://example/feed",
     "send_on_connect": ["{\"op\":\"subscribe\"}"], "ping_ms": 20000},
    {"name": "fix_md", "type": "tcp", "addr": "10.0.0.5:9876", "framing": "soh",
     "send_on_connect": ["8=FIX.4.49=..35=A...10=xxx"]},
    {"name": "itch", "type": "tcp", "addr": "10.0.0.6:26400", "framing": "len16"},
    {"name": "broker_log", "type": "file_tail", "path": "/var/log/broker/feed.log", "framing": "line"}
  ]
}
```

Transport fields:

* `http_poll`: `url`, `method` (GET; POST when `form` is set), `headers`,
  `form`, `params`, `interval_ms`, `key`, `timeout_ms`, `tls_insecure`,
  optional token flow `token_url` + `token_regex` (one capture group) +
  `token_header` (default `RequestVerificationToken`) + `token_ttl_s`. The
  token page's cookies are kept in a per-source jar and sent with every
  request; a 400/401/403/405 answer refreshes the token once and retries
  (LankaBD anti-forgery flow).
* `websocket`: `url`, `headers`, `send_on_connect` (text messages),
  `ping_ms`, `tls_insecure`. Each message is one DATA record (`opcode` text/binary).
* `tcp`: `addr`, `framing` (`line` with `delimiter`, default `"\n"`; `soh`
  FIX `8=…9=…10=xxx<SOH>`; `len16` 2-byte big-endian length prefix),
  `send_on_connect`, `read_timeout_ms`. Frames are **wire-exact**: delimiters,
  length prefixes and FIX trailers stay in the body, so concatenating a
  connection's frames reproduces the stream.
* `file_tail`: `path`, `framing` (`line` / `len16` / `soh`), `poll_ms`,
  `from_end` (first run only, no persisted offset: start at the current end
  of the file like `tail -f`; the skipped prefix is a `GAP{from_end}`;
  default false = capture the whole file). The consumed offset is persisted
  in `<out>/state/<source>.offset.json` (after each frame, saved at most once
  per second and at exit); a restart resumes there unless the file was
  replaced (inode) or shortened, which restarts at 0 with a GAP. A partial
  trailing frame is never emitted — it waits for its end.

## Tests

`go test ./...` covers: segment round trip through a tiny Go JSONL reader
(CRC/sha/trailer/seq/manifest), hourly rotation and the hash chain across a
restart, deterministic drop accounting, tamper detection, every framing
(including straddled reads, FIX resync and wrong BodyLength), an `httptest`
poll (envelope, token flow, 503 → GAP, connection refused → GAP), local TCP
servers with FIX and len16 framing (disconnect → GAP → reconnect), a
websocket server, file_tail (partial line, resume, truncation, `from_end`,
rotation between runs), partial frames at hang-up and at shutdown,
transient framer errors, manifest round trip with Python-added gz fields, and
a whole daemon run (stop cause). `tests/tower/test_ingest_go_compat.py` builds the daemon, runs it
against a local `python -m http.server`, and checks the output with
`seeing.capture.raw_store.verify_store` and `seeing.replay`.
