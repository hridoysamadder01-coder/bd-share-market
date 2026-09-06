"""Cross-language compatibility: the Go capture daemon (tower/ingest) must write
raw segments that the Python store (seeing.capture.raw_store) verifies and that
seeing.replay-style iteration decodes byte-exactly.

The test builds the daemon with ``go build``, runs it for ~6 s against a local
``python -m http.server`` fixture (two http_poll sources at 500 ms — one UTF-8
body, one binary body — one http_poll against a missing path to exercise the
GAP path, and one file_tail source fed during the run), sends SIGTERM, then
checks the output with the Python reader. Skips cleanly when ``go`` is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import zlib
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from seeing.capture.raw_store import decode_body, iter_segment, sha256_bytes, verify_store  # noqa: E402

INGEST_DIR = os.path.join(ROOT, "tower", "ingest")
GO = shutil.which("go")

TEXT_BODY = ("<html><body>DSE depth টাকা &amp; <b>bold</b>\r\n"
             + json.dumps({"symbol": "MALEKSPIN", "bids": [[45.5, 1200], [45.4, 800]], "asks": [[45.6, 500]]},
                          ensure_ascii=False) + "\n\ttab\x1bescape\n").encode("utf-8")
BINARY_BODY = bytes([0x00, 0x01, 0x02, 0xFF, 0xFE, 0x80]) + b"len16\x00\x05hello" + bytes(range(256))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 10.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("fixture http server did not come up")


@pytest.fixture(scope="module")
def ingest_binary(tmp_path_factory):
    if GO is None:
        pytest.skip("go toolchain not installed")
    out = str(tmp_path_factory.mktemp("bin") / "ingest")
    r = subprocess.run([GO, "build", "-o", out, "."], cwd=INGEST_DIR, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        pytest.fail(f"go build failed:\n{r.stdout}\n{r.stderr}")
    return out


def _run_daemon(binary: str, tmp_path, seconds: float = 6.0):
    www = tmp_path / "www"
    www.mkdir()
    (www / "depth.html").write_bytes(TEXT_BODY)
    (www / "blob.bin").write_bytes(BINARY_BODY)
    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                            "--directory", str(www)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tail_file = tmp_path / "feed.log"
    tail_file.write_bytes(b"first line\n")
    out = tmp_path / "capture"
    cfg = {
        "out": str(out), "capturer_id": "pytest", "software_version": "compat-test", "heartbeat_ms": 1000,
        "sources": [
            {"name": "fx_text", "type": "http_poll", "url": f"http://127.0.0.1:{port}/depth.html",
             "interval_ms": 500, "key": "MALEKSPIN", "headers": {"X-Test": "1"}},
            {"name": "fx_bin", "type": "http_poll", "url": f"http://127.0.0.1:{port}/blob.bin", "interval_ms": 500},
            {"name": "fx_missing", "type": "http_poll", "url": f"http://127.0.0.1:{port}/nope", "interval_ms": 1500},
            {"name": "fx_tail", "type": "file_tail", "path": str(tail_file), "framing": "line", "poll_ms": 50},
        ],
    }
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg))
    try:
        _wait_port(port)
        proc = subprocess.Popen([binary, "-config", str(cfg_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t0 = time.time()
        appended = []
        i = 0
        while time.time() - t0 < seconds:
            time.sleep(0.5)
            i += 1
            line = f"tail line {i} ৳\n".encode("utf-8")
            with open(tail_file, "ab") as fh:
                fh.write(line)
            appended.append(line)
        assert proc.poll() is None, f"daemon died early: {proc.stderr.read().decode()}"
        proc.send_signal(signal.SIGTERM)
        try:
            _, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("daemon did not exit on SIGTERM")
        assert proc.returncode == 0, err.decode()
    finally:
        srv.terminate()
        srv.wait(timeout=10)
    return str(out), appended, port


def test_realdata_go_daemon_output_verifies_with_python_store(ingest_binary, tmp_path):
    root, appended, _port = _run_daemon(ingest_binary, tmp_path)
    assert os.path.exists(os.path.join(root, "MANIFEST.json"))
    man = json.load(open(os.path.join(root, "MANIFEST.json")))
    assert man["schema_version"] == 1 and man["capturer_id"] == "pytest" and man["closed_utc"]
    sources = {s["source"] for s in man["segments"]}
    assert {"fx_text", "fx_bin", "fx_missing", "fx_tail", "heartbeat", "runner"} <= sources

    # 1. the Python store verifies every segment: trailer hash, CRC/sha per body, seq continuity, chain
    rep = verify_store(root)
    assert rep["all_ok"], rep
    assert rep["n"] == len(man["segments"]) >= 6

    # 2. file naming and manifest entries
    name_re = re.compile(r"^(?P<src>[a-z_]+)__pytest__(?P<ep>[0-9a-f]{8})__\d{8}T\d\d__\d{8}\.jsonl$")
    for s in man["segments"]:
        m = name_re.match(os.path.basename(s["path"]))
        assert m and m.group("src") == s["source"] and m.group("ep") == man["epoch"][:8], s["path"]
        path = os.path.join(root, s["path"])
        assert s["bytes"] == os.path.getsize(path)
        assert s["sha256"] == hashlib.sha256(open(path, "rb").read()).hexdigest()
        assert s["last_seq"] - s["first_seq"] + 1 == s["records"]

    # 3. replay-style iteration: bodies decode byte-exactly, envelopes carry what replay reads
    by_source: dict = {}
    for s in man["segments"]:
        recs = [r for r, ok in iter_segment(os.path.join(root, s["path"]))]
        assert all(ok for _, ok in iter_segment(os.path.join(root, s["path"])))
        assert recs[0]["kind"] == "META" and recs[-1]["kind"] == "TRAILER"
        assert recs[0]["prev_segment_sha256"] is None  # one segment per source in a 6 s run
        for r in recs:
            datetime.fromisoformat(r["t_recv_utc"])  # Python isoformat, +00:00
            assert r["t_recv_utc"].endswith("+00:00") and isinstance(r["t_recv_mono_ns"], int)
            assert r["epoch"] == man["epoch"] and r["capturer_id"] == "pytest"
        by_source.setdefault(s["source"], []).extend(recs)

    text = [r for r in by_source["fx_text"] if r["kind"] == "DATA"]
    assert len(text) >= 8, len(text)  # 6 s at 500 ms
    for r in text:
        body = decode_body(r)
        assert body == TEXT_BODY
        assert r["body_encoding"] == "utf8" and r["len"] == len(TEXT_BODY)
        assert r["crc32"] == zlib.crc32(TEXT_BODY) & 0xFFFFFFFF and r["body_sha256"] == sha256_bytes(TEXT_BODY)
        assert r["key"] == "MALEKSPIN" and r["src_seq"] is None
        http = r["http"]
        assert http["status"] == 200 and http["method"] == "GET" and http["request_headers"]["X-Test"] == "1"
        assert http["t_last_byte_utc"] >= http["t_first_byte_utc"] >= http["t_send_utc"]
        assert "Content-Type" in http["response_headers"] and http["elapsed_ms"] >= 0

    binary = [r for r in by_source["fx_bin"] if r["kind"] == "DATA"]
    assert len(binary) >= 8
    for r in binary:
        assert r["body_encoding"] == "b64" and decode_body(r) == BINARY_BODY

    # 4. a missing page is a GAP with the envelope and the body — never silent, never a DATA record
    missing = by_source["fx_missing"]
    assert not [r for r in missing if r["kind"] == "DATA"]
    gaps = [r for r in missing if r["kind"] == "GAP"]
    assert gaps and all(g["reason"] == "http" and g["http"]["status"] == 404 for g in gaps)
    assert all(decode_body(g) and g["body_sha256"] == sha256_bytes(decode_body(g)) for g in gaps)

    # 5. file_tail captured the seed line and every appended line, in order, wire-exact
    tail = [decode_body(r) for r in by_source["fx_tail"] if r["kind"] == "DATA"]
    assert tail[0] == b"first line\n"
    assert tail[1:] == appended[:len(tail) - 1] and len(tail) >= len(appended) - 1
    assert json.load(open(os.path.join(root, "state", "fx_tail.offset.json")))["offset"] == \
        len(b"first line\n") + sum(len(x) for x in appended[:len(tail) - 1])

    # 6. heartbeats every second with per-source counters and ages (null = never delivered)
    hbs = [r for r in by_source["heartbeat"] if r["kind"] == "HEARTBEAT"]
    assert len(hbs) >= 5
    last = hbs[-1]["status"]
    assert last["counts"]["fx_text"]["ok"] >= 8 and last["counts"]["fx_text"]["dropped"] == 0
    assert last["counts"]["fx_missing"]["err"] >= 1 and last["counts"]["fx_missing"]["ok"] == 0
    assert last["ages_s"]["fx_missing"] is None and 0 <= last["ages_s"]["fx_text"] < 5
    metas = [r for r in by_source["runner"] if r["kind"] == "META"]
    assert any(m.get("started") for m in metas) and any(m.get("finished") for m in metas)


def test_realdata_go_daemon_store_feeds_seeing_replay(ingest_binary, tmp_path):
    """seeing.replay.replay() consumes the Go store: no problems, heartbeats and gaps tabulated."""
    from seeing.replay import replay

    root, _appended, _port = _run_daemon(ingest_binary, tmp_path, seconds=3.0)
    tables = replay(root)
    assert tables["problems"] == []
    assert len(tables["heartbeats"]) >= 2 and "age_fx_text" in tables["heartbeats"].columns
    assert len(tables["gaps"]) >= 1 and set(tables["gaps"]["source"]) == {"fx_missing"}
    assert (tables["gaps"]["status"] == 404).all()
    assert set(tables["meta"]["source"]) >= {"runner"}
    assert tables["counts"]["fx_text"] >= 4 and tables["counts"]["fx_bin"] >= 4


def test_realdata_go_verify_command_agrees_with_python(ingest_binary, tmp_path):
    """The daemon's own -verify agrees with the Python verifier and detects tampering."""
    root, _appended, _port = _run_daemon(ingest_binary, tmp_path, seconds=2.0)
    r = subprocess.run([ingest_binary, "-verify", root], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "all_ok=true" in r.stdout, r.stdout + r.stderr
    man = json.load(open(os.path.join(root, "MANIFEST.json")))
    seg = next(s for s in man["segments"] if s["source"] == "fx_text")
    path = os.path.join(root, seg["path"])
    raw = open(path, "rb").read()
    tampered = raw.replace(b"MALEKSPIN", b"MALEKSPIM", 1)
    assert tampered != raw
    open(path, "wb").write(tampered)
    assert not verify_store(root)["all_ok"]
    r = subprocess.run([ingest_binary, "-verify", root], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "all_ok=false" in r.stdout
