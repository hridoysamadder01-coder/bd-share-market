"""Append-only, hash-chained raw capture store.

Design (DATA_ACQUISITION_ARCHITECTURE.md §6, now implemented):

* Every record is one JSON line: ``kind`` (META, DATA, HEARTBEAT, GAP, CLOCK,
  TRAILER), ``capturer_id``, ``epoch`` (minted per process start), ``seq``
  (monotonic per (capturer, source, epoch)), optional ``src_seq``,
  ``t_recv_utc`` + ``t_recv_mono_ns`` (two clocks), and for DATA the HTTP
  envelope plus the body **exactly as received**: UTF-8 bodies are stored as
  JSON strings (lossless), anything else as base64; ``body_sha256`` and
  ``crc32`` are over the raw bytes so replay can prove exactness.
* One segment file per (source, UTC hour). A segment opens with a META record
  that carries the sha256 of the previous closed segment of that source (hash
  chain) and closes with a TRAILER (record count, first/last seq, sha256 of
  everything before the trailer).
* A restart never appends to an existing file: new epoch, new segment.
* ``fdatasync`` at every heartbeat, GAP and segment close.
* After the run, ``compress_and_verify`` gzips each closed segment, decompresses
  it back, compares the sha256, and only then removes the uncompressed file.
  The manifest carries both hashes.
* Nothing is deduplicated, corrected or filled at capture time.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..clock import mono_ns, now_utc

SCHEMA_VERSION = 1
KINDS = ("META", "DATA", "HEARTBEAT", "GAP", "CLOCK", "TRAILER")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def encode_body(body: bytes) -> Dict[str, Any]:
    """Lossless body encoding: UTF-8 text as a JSON string, else base64."""
    try:
        text = body.decode("utf-8")
        if text.encode("utf-8") == body:
            return {"body": text, "body_encoding": "utf8"}
    except UnicodeDecodeError:
        pass
    return {"body": base64.b64encode(body).decode("ascii"), "body_encoding": "b64"}


def decode_body(rec: Dict[str, Any]) -> bytes:
    enc = rec.get("body_encoding")
    if enc == "utf8":
        return rec["body"].encode("utf-8")
    if enc == "b64":
        return base64.b64decode(rec["body"])
    raise ValueError("record has no body")


@dataclass
class _Segment:
    source: str
    path: str
    fh: Any
    hasher: Any
    hour_key: str
    records: int = 0
    first_seq: Optional[int] = None
    last_seq: Optional[int] = None
    closed: bool = False


@dataclass
class RawStore:
    root: str
    capturer_id: str
    epoch: str = field(default_factory=lambda: uuid.uuid4().hex)
    software_version: str = "unknown"
    _segments: Dict[str, _Segment] = field(default_factory=dict)
    _seq: Dict[str, int] = field(default_factory=dict)
    _prev_sha: Dict[str, Optional[str]] = field(default_factory=dict)
    _manifest: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        os.makedirs(os.path.join(self.root, "segments"), exist_ok=True)
        self._manifest = {
            "schema_version": SCHEMA_VERSION,
            "capturer_id": self.capturer_id,
            "epoch": self.epoch,
            "software_version": self.software_version,
            "opened_utc": now_utc().isoformat(),
            "segments": [],
        }
        # Continue the per-source hash chain from an existing manifest, if any.
        mpath = os.path.join(self.root, "MANIFEST.json")
        if os.path.exists(mpath):
            try:
                old = json.load(open(mpath))
                for s in old.get("segments", []):
                    self._prev_sha[s["source"]] = s.get("sha256")
                self._manifest["segments"] = old.get("segments", [])
                self._manifest["previous_epochs"] = old.get("previous_epochs", []) + [
                    {"epoch": old.get("epoch"), "opened_utc": old.get("opened_utc")}]
            except Exception:  # noqa: BLE001 — a corrupt manifest must not stop capture
                pass

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _hour_key(ts: datetime) -> str:
        return ts.strftime("%Y%m%dT%H")

    def _open_segment(self, source: str, ts: datetime) -> _Segment:
        hk = self._hour_key(ts)
        seq0 = self._seq.get(source, 0)
        name = f"{source}__{self.capturer_id}__{self.epoch[:8]}__{hk}__{seq0:08d}.jsonl"
        path = os.path.join(self.root, "segments", name)
        fh = open(path, "ab")
        seg = _Segment(source=source, path=path, fh=fh, hasher=hashlib.sha256(), hour_key=hk)
        self._segments[source] = seg
        meta = {
            "kind": "META", "schema_version": SCHEMA_VERSION, "source": source,
            "capturer_id": self.capturer_id, "epoch": self.epoch,
            "software_version": self.software_version,
            "prev_segment_sha256": self._prev_sha.get(source),
            "segment_hour_utc": hk,
        }
        self._append(seg, meta, source)
        return seg

    def _append(self, seg: _Segment, rec: Dict[str, Any], source: str) -> Dict[str, Any]:
        seq = self._seq.get(source, 0)
        rec = dict(rec)
        rec.setdefault("kind", "DATA")
        rec["source"] = source
        rec["capturer_id"] = self.capturer_id
        rec["epoch"] = self.epoch
        rec["seq"] = seq
        rec.setdefault("t_recv_utc", now_utc().isoformat())
        rec.setdefault("t_recv_mono_ns", mono_ns())
        line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        seg.fh.write(line)
        seg.hasher.update(line)
        seg.records += 1
        seg.first_seq = seq if seg.first_seq is None else seg.first_seq
        seg.last_seq = seq
        self._seq[source] = seq + 1
        return rec

    def _segment_for(self, source: str, ts: datetime) -> _Segment:
        seg = self._segments.get(source)
        if seg is None or seg.closed:
            return self._open_segment(source, ts)
        if seg.hour_key != self._hour_key(ts):
            self._close_segment(seg)
            return self._open_segment(source, ts)
        return seg

    def _close_segment(self, seg: _Segment) -> None:
        if seg.closed:
            return
        trailer = {
            "kind": "TRAILER", "records": seg.records, "first_seq": seg.first_seq,
            "last_seq": seg.last_seq, "sha256_before_trailer": seg.hasher.hexdigest(),
        }
        self._append(seg, trailer, seg.source)
        seg.fh.flush()
        os.fsync(seg.fh.fileno())
        seg.fh.close()
        seg.closed = True
        digest = sha256_file(seg.path)
        self._prev_sha[seg.source] = digest
        self._manifest["segments"].append({
            "source": seg.source, "path": os.path.relpath(seg.path, self.root),
            "records": seg.records, "first_seq": seg.first_seq, "last_seq": seg.last_seq,
            "sha256": digest, "bytes": os.path.getsize(seg.path),
            "epoch": self.epoch, "closed_utc": now_utc().isoformat(),
        })
        self.write_manifest()

    # ------------------------------------------------------------------ public
    def write(self, source: str, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Append one logical record to the source's stream. Returns the record as written."""
        ts = now_utc()
        rec = dict(rec)
        rec.setdefault("t_recv_utc", ts.isoformat())
        rec.setdefault("t_recv_mono_ns", mono_ns())
        seg = self._segment_for(source, ts)
        return self._append(seg, rec, source)

    def write_data(self, source: str, *, key: Optional[str], body: bytes,
                   http: Dict[str, Any], src_seq: Optional[Any] = None,
                   t_recv_utc: Optional[str] = None) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "kind": "DATA", "key": key, "src_seq": src_seq, "http": http,
            "len": len(body), "crc32": zlib.crc32(body) & 0xFFFFFFFF,
            "body_sha256": sha256_bytes(body),
        }
        if t_recv_utc:
            rec["t_recv_utc"] = t_recv_utc
        rec.update(encode_body(body))
        return self.write(source, rec)

    def write_gap(self, source: str, reason: str, detail: str = "", key: Optional[str] = None,
                  http: Optional[Dict[str, Any]] = None, body: Optional[bytes] = None) -> Dict[str, Any]:
        rec: Dict[str, Any] = {"kind": "GAP", "reason": reason, "detail": detail[:4000], "key": key}
        if http:
            rec["http"] = http
        if body is not None:
            rec["len"] = len(body)
            rec["body_sha256"] = sha256_bytes(body)
            rec.update(encode_body(body))
        out = self.write(source, rec)
        self.sync(source)
        return out

    def write_heartbeat(self, status: Dict[str, Any]) -> Dict[str, Any]:
        out = self.write("heartbeat", {"kind": "HEARTBEAT", "status": status})
        self.sync_all()
        return out

    def write_meta(self, source: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        out = self.write(source, {"kind": "META", **payload})
        self.sync(source)
        return out

    def sync(self, source: str) -> None:
        seg = self._segments.get(source)
        if seg and not seg.closed:
            seg.fh.flush()
            os.fsync(seg.fh.fileno())

    def sync_all(self) -> None:
        for s in list(self._segments):
            self.sync(s)

    def close(self) -> None:
        for seg in list(self._segments.values()):
            self._close_segment(seg)
        self._manifest["closed_utc"] = now_utc().isoformat()
        self.write_manifest()

    def write_manifest(self) -> str:
        path = os.path.join(self.root, "MANIFEST.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self._manifest, fh, indent=1, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path

    # ------------------------------------------------------------------ post-run
    def compress_and_verify(self) -> Dict[str, Any]:
        """gzip every closed segment; verify by round trip; only then delete the original."""
        report = {"compressed": 0, "verified": 0, "failed": []}
        for s in self._manifest["segments"]:
            path = os.path.join(self.root, s["path"])
            if not os.path.exists(path) or path.endswith(".gz"):
                continue
            gz = path + ".gz"
            with open(path, "rb") as src, gzip.open(gz, "wb", compresslevel=9) as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    dst.write(chunk)
            report["compressed"] += 1
            h = hashlib.sha256()
            with gzip.open(gz, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() == s["sha256"]:
                s["gz_path"] = os.path.relpath(gz, self.root)
                s["gz_sha256"] = sha256_file(gz)
                s["gz_bytes"] = os.path.getsize(gz)
                os.remove(path)
                report["verified"] += 1
            else:
                os.remove(gz)
                report["failed"].append(s["path"])
        self.write_manifest()
        return report


# ---------------------------------------------------------------------- reading
def iter_segment(path: str):
    """Yield (record, ok) from a segment (.jsonl or .jsonl.gz); a line that fails
    to parse is yielded as (raw_line, False) — salvage record by record."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        for line in fh:
            try:
                yield json.loads(line), True
            except Exception:  # noqa: BLE001
                yield line, False


def verify_segment(path: str) -> Dict[str, Any]:
    """Re-hash a segment and check its TRAILER, record CRCs and seq continuity."""
    h = hashlib.sha256()
    recs = 0
    bad_crc = 0
    seqs = []
    trailer = None
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                bad_crc += 1
                continue
            if rec.get("kind") == "TRAILER":
                trailer = rec
                break
            h.update(line)
            recs += 1
            seqs.append(rec.get("seq"))
            if rec.get("kind") in ("DATA", "GAP") and "body" in rec:
                body = decode_body(rec)
                if rec.get("crc32") is not None and (zlib.crc32(body) & 0xFFFFFFFF) != rec["crc32"]:
                    bad_crc += 1
                if rec.get("body_sha256") and sha256_bytes(body) != rec["body_sha256"]:
                    bad_crc += 1
    contiguous = all(b == a + 1 for a, b in zip(seqs, seqs[1:]))
    ok = trailer is not None and trailer.get("records") == recs and \
        trailer.get("sha256_before_trailer") == h.hexdigest() and bad_crc == 0 and contiguous
    return {"path": path, "records": recs, "bad_records": bad_crc, "has_trailer": trailer is not None,
            "trailer_matches": trailer is not None and trailer.get("sha256_before_trailer") == h.hexdigest(),
            "seq_contiguous": contiguous, "ok": ok}


def verify_store(root: str) -> Dict[str, Any]:
    """Verify every segment listed in the manifest and the per-source hash chain."""
    manifest = json.load(open(os.path.join(root, "MANIFEST.json")))
    results = []
    chain_ok = True
    prev: Dict[str, Optional[str]] = {}
    for s in manifest["segments"]:
        path = os.path.join(root, s.get("gz_path") or s["path"])
        r = verify_segment(path)
        # chain: META.prev_segment_sha256 must equal the previous segment's sha256
        first = next(iter_segment(path))[0]
        expected_prev = prev.get(s["source"])
        if s["source"] in prev and first.get("prev_segment_sha256") != expected_prev:
            chain_ok = False
            r["chain_ok"] = False
        else:
            r["chain_ok"] = True
        prev[s["source"]] = s["sha256"]
        results.append(r)
    return {"segments": results, "all_ok": all(r["ok"] for r in results) and chain_ok,
            "chain_ok": chain_ok, "n": len(results)}
