"""Cost accounting that keeps VERIFIED and ESTIMATED apart.

A single "net of 1% costs" number hides which part of a result rests on evidence
and which rests on a guess. Here every figure is reported in three layers:

  gross                      what the price series did
  net_verified               gross − verified brokerage (0.8% round trip)
  net_with_estimate(+x)      further ESTIMATED costs, shown as a band, labelled

Never sum an estimate into the verified figure. If a candidate survives
`net_verified` but dies at +0.2% estimated, that is the finding — not a rounding
detail to bury.

Explicitly NOT modelled (and therefore not silently assumed to be zero):
capital-gains tax (UNKNOWN), slippage / market impact (UNKNOWN, no model),
saleability constraints (UNKNOWN — see config.EARLIEST_SALEABILITY_DAYS).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config as C


@dataclass(frozen=True)
class CostLayers:
    gross: float
    net_verified: float
    net_by_estimate: dict          # additional-cost → net
    n: int
    t_gross: float
    t_net_verified: float
    hit_gross: float
    hit_net_verified: float

    def as_row(self, label: str) -> dict:
        row = {"cohort": label, "n": self.n,
               "gross": self.gross, "t_gross": self.t_gross,
               "hit_gross": self.hit_gross,
               "net_verified_0.8%": self.net_verified,
               "t_net_verified": self.t_net_verified,
               "hit_net_verified": self.hit_net_verified}
        for add, v in self.net_by_estimate.items():
            row[f"net_est_+{add:.1%}"] = v
        return row


def _t(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return float("nan")
    sd = x.std(ddof=1)
    return float("nan") if sd == 0 else float(x.mean() / (sd / np.sqrt(len(x))))


def layered(returns: pd.Series | np.ndarray) -> CostLayers:
    """Cost layers for a set of per-trade (or per-event) gross returns."""
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    b = C.BROKERAGE_ROUND_TRIP_VERIFIED
    nv = r - b
    return CostLayers(
        gross=float(r.mean()) if len(r) else float("nan"),
        net_verified=float(nv.mean()) if len(r) else float("nan"),
        net_by_estimate={add: (float(nv.mean() - add) if len(r) else float("nan"))
                         for add in C.ESTIMATED_ADDITIONAL_COSTS if add > 0},
        n=int(len(r)),
        t_gross=_t(r),
        t_net_verified=_t(nv),
        hit_gross=float((r > 0).mean()) if len(r) else float("nan"),
        hit_net_verified=float((nv > 0).mean()) if len(r) else float("nan"),
    )


def header() -> str:
    parts = [
        f"verified brokerage (round trip): {C.BROKERAGE_ROUND_TRIP_VERIFIED:.1%} "
        f"[VERIFIED={C.BROKERAGE_VERIFIED}]",
        "additional costs shown as an ESTIMATE band: "
        + ", ".join(f"+{a:.1%}" for a in C.ESTIMATED_ADDITIONAL_COSTS if a > 0),
        "NOT modelled: capital-gains tax (UNKNOWN), slippage/impact (UNKNOWN), "
        "saleability constraint (UNKNOWN)",
    ]
    return "\n".join("  · " + p for p in parts)
