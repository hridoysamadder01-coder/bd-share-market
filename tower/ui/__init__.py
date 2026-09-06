"""Observation Tower UI: a FastAPI server over a state store (live or replay)
plus a build-free single-page front end (``static/``).

    python3 -m tower.ui.server --store DIR --port 8765 [--host 127.0.0.1]

The server never computes market quantities: every number it serves is read
from the store written by ``tower.replay`` / ``tower.live`` (see
``tower/CONTRACTS.md``, "State store format"). Files are tailed, so a store
that is still growing and a finished replay store are served the same way.
"""
from .server import StoreReader, create_app  # noqa: F401

__all__ = ["StoreReader", "create_app"]
