#!/usr/bin/env python3
"""Run BD Market Intelligence OS for this PC and phones on the same LAN.

Usage:
    python run_market_ui.py
    python run_market_ui.py --store path/to/replay_store

The default bind is 0.0.0.0:8765 so the same process is reachable from the PC
and, subject to the host firewall/router, other devices on the local network.
"""
from __future__ import annotations

import argparse
import socket
from typing import List, Optional


def lan_ipv4() -> List[str]:
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                out.add(ip)
    except OSError:
        pass
    return sorted(out)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run BD Market Intelligence OS on PC + phone")
    p.add_argument("--store", default=None, help="optional Observation Tower replay/live state-store directory")
    p.add_argument("--host", default="0.0.0.0", help="bind host; default 0.0.0.0 allows LAN devices")
    p.add_argument("--port", type=int, default=8765)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = parser().parse_args(argv)
    from tower.ui.server import create_app
    import uvicorn

    print(f"PC:    http://127.0.0.1:{args.port}")
    for ip in lan_ipv4():
        print(f"PHONE: http://{ip}:{args.port}  (same Wi-Fi/LAN, firewall permitting)")
    print("DATA:  http://127.0.0.1:%d/api/data/inventory" % args.port)
    uvicorn.run(create_app(args.store), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
