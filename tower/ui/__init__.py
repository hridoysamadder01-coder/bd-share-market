"""Observation Tower UI: a FastAPI server over a state store (live or replay)
plus a build-free single-page front end (``static/``).

    python3 -m tower.ui.server --store DIR --port 8765 [--host 127.0.0.1]

The server never computes market quantities: every number it serves is read
from the store written by ``tower.replay`` / ``tower.live`` (see
``tower/CONTRACTS.md``, "State store format"). Files are tailed, so a store
that is still growing and a finished replay store are served the same way.
Import ``tower.ui.server`` for ``create_app`` / ``StoreReader`` (kept out of
this package ``__init__`` so ``python3 -m tower.ui.server`` runs cleanly).
"""
