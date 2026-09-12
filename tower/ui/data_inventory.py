"""Read-only inventory of data artefacts available to the local UI.

The endpoint exposes metadata only: relative path, size and mtime. It never serves
file contents, credentials, cookies or HAR payloads. The purpose is to make data
presence/absence visible instead of silently hiding layers that are on disk.
"""
from __future__ import annotations

import os
import socket
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_ROOTS = ("data", "evidence", "results", "qa", "manifests", "reports")


def _inside_root(path: str) -> bool:
    ap = os.path.abspath(path)
    return ap == ROOT or ap.startswith(ROOT + os.sep)


def _walk() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for root_name in DATA_ROOTS:
        base = os.path.join(ROOT, root_name)
        if not os.path.isdir(base):
            continue
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for name in sorted(files):
                if name.startswith("."):
                    continue
                p = os.path.join(d, name)
                if not _inside_root(p):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
                ext = os.path.splitext(name)[1].lower() or "(none)"
                rows.append({
                    "path": rel,
                    "root": root_name,
                    "bytes": int(st.st_size),
                    "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                    "ext": ext,
                    "content_exposed": False,
                })
    return rows


def build_inventory(root: Optional[str] = None, q: Optional[str] = None, offset: int = 0, limit: int = 200) -> Dict[str, Any]:
    rows = _walk()
    total_all = len(rows)
    if root:
        rows = [r for r in rows if r["root"] == root]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r["path"].lower()]
    rows.sort(key=lambda r: r["path"])
    counts = Counter(r["root"] for r in _walk())
    bytes_by_root = Counter()
    for r in _walk():
        bytes_by_root[r["root"]] += r["bytes"]
    total = len(rows)
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    return {
        "truth": "OBSERVED",
        "roots": [
            {"root": r, "present": os.path.isdir(os.path.join(ROOT, r)), "files": counts.get(r, 0), "bytes": bytes_by_root.get(r, 0)}
            for r in DATA_ROOTS
        ],
        "total_files_on_disk": total_all,
        "filtered_files": total,
        "offset": offset,
        "limit": limit,
        "rows": rows[offset:offset + limit],
        "more": offset + limit < total,
        "note": "metadata only; raw file contents are not exposed by this endpoint",
    }


def _lan_addresses() -> List[str]:
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                out.add(ip)
    except OSError:
        pass
    return sorted(out)


def attach_data_inventory(app) -> None:
    from fastapi import Query

    @app.get("/api/data/inventory")
    def data_inventory(
        root: Optional[str] = Query(default=None),
        q: Optional[str] = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        return build_inventory(root=root, q=q, offset=offset, limit=limit)

    @app.get("/api/runtime/info")
    def runtime_info():
        return {
            "truth": "OBSERVED",
            "bind_for_pc_and_phone": "0.0.0.0",
            "default_port": 8765,
            "lan_ipv4": _lan_addresses(),
            "phone_url_examples": [f"http://{ip}:8765" for ip in _lan_addresses()],
            "security_note": "LAN binding exposes the UI to devices that can reach this computer; no raw file-content endpoint is enabled.",
        }
