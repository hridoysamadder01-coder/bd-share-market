"""Market-mechanics engine: temporal state machines with evidence windows.

Every mechanism is a ``Mechanism`` subclass (see ``base.py``) registered in
``REGISTRY`` when its family module is imported. ``load_families()`` imports
every family module present; a family that is not (yet) present is listed in
``MISSING_FAMILIES`` rather than breaking the package import.
"""
from __future__ import annotations

import importlib
from typing import Dict, List

from .base import Mechanism, MechanismReading, REGISTRY, StateHistory, register  # noqa: F401

FAMILIES = ("queue_family", "sweep_family", "accumulation_family", "participation_family",
            "divergence_family", "ofi_shape_family", "session_family", "cross_family", "circuit_family")
MISSING_FAMILIES: Dict[str, str] = {}
_LOADED = False


def load_families() -> List[str]:
    global _LOADED
    if _LOADED:
        return [f for f in FAMILIES if f not in MISSING_FAMILIES]
    for fam in FAMILIES:
        try:
            importlib.import_module(f"{__name__}.{fam}")
        except ImportError as e:              # family not present yet
            MISSING_FAMILIES[fam] = str(e)
    _LOADED = True
    return [f for f in FAMILIES if f not in MISSING_FAMILIES]


def all_mechanisms() -> List[Mechanism]:
    load_families()
    return [cls() for cls in REGISTRY.values()]
