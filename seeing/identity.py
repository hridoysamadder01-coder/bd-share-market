"""Stage 0 — the reference and identity spine.

Eight wired sources name the same instrument differently, and until this module
existed "the same symbol" was an unstated assumption that every cross-source
number would have inherited silently. LankaBD carries `companyID` and
`mkistaT_INSTRUMENT_NUMBER`; StockNow keys by trading code with its own
`sector_id`; BullBD carries a MIC (`XDHA`) and its own sector string; CSE renders
its code one letter per node; EcoSoft OST prints `TradingCode` with a
`StockExchange`. Joining those on a bare string and hoping is how a DSE
instrument quietly acquires a CSE price.

## Two levels, deliberately

A **listing** is `(trading code, exchange)` — one order book, one price series.
A **company** is the issuer that may have several listings.

DSE `BRACBANK` and CSE `BRACBANK` are **two listings of one company**, not one
instrument. Merging them would manufacture a price series that no exchange
publishes; refusing to relate them at all would throw away the cross-exchange
check that is the whole reason CSE is wired. So they are held as distinct
listings and linked at the company level, and that link is INFERRED with its
rule stated — never OBSERVED, because no source we reach asserts it.

## What each truth class means here

* **OBSERVED** — a source literally printed this value for this listing. Two
  sources printing the same trading code is an observation by both, not an
  inference, so a same-exchange exact match is OBSERVED. The only normalisation
  applied before comparison is `NORMALISE_RULE` below — strip whitespace, decode
  HTML entities, upper-case — because CSE's per-letter rendering and an
  HTML-escaped ampersand are artifacts of how a page was produced, not different
  identifiers.
* **INFERRED** — derived by a stated rule. The cross-exchange company link is
  the main one. Every inferred mapping carries `rule` naming exactly which rule
  produced it.
* **NOT_OBSERVABLE** — no source carries it, *or* the sources that do carry it
  disagree. A disagreement does not average, vote, or prefer a source: the
  resolved value stays `None`, the conflict is recorded, and the listing is
  reported. That is the difference between a spine and a guess.

## Determinism

The map is a pure function of the evidence it reads. Every collection is sorted
before serialisation and no wall-clock value enters the hashed core, so
`spine_sha256` is reproducible: same evidence in, same hash out. `generated_utc`
sits outside the hashed core precisely so that it cannot break replay.

    python3 -m seeing.identity --out evidence/identity/2026-09-08
"""
from __future__ import annotations

import argparse
import glob
import gzip
import html
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .truth import Truth

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

NORMALISE_RULE = (
    "Remove every whitespace character, then decode HTML entities once, then upper-case. "
    "Each step exists for an observed defect, and nothing else is applied — no stripping of "
    "suffixes, no fuzzy matching, no repeated decoding. "
    "(1) Entity decoding: `symbols.csv` carries the same instrument twice, as `KAY&QUE` and "
    "as `KAY&AMP;QUE`, because one collector path HTML-escaped the ampersand. Undecoded, one "
    "real instrument becomes two identities and the coverage denominator is wrong. Decoding "
    "is applied exactly once: a code that genuinely contains the text `&amp;` must not be "
    "silently decoded twice into something else. "
    "(2) Whitespace removal runs FIRST, because CSE renders a trading code one letter per DOM "
    "node — an entity split across those nodes only becomes decodable once the gaps are gone. "
    "(3) Upper-casing runs last, after decoding, so that entity case (`&AMP;` vs `&amp;`) "
    "cannot change the outcome. A spelling HTML does not define, such as `&Amp;`, is left "
    "alone: decoding it would be a guess, not a decode.")

_WS = re.compile(r"\s+")

# Attributes are split three ways, because "the sources disagree" means three
# different things and collapsing them produced a spine that called 419 of 691
# DSE listings unresolved over a trailing full stop in a company name.
#
# IDENTITY — a disagreement here means we genuinely do not know which instrument
#   this is. The listing is UNRESOLVED and nothing downstream may use it.
# DESCRIPTIVE — a disagreement here is a labelling difference, not an identity
#   doubt: `A-MF` vs `A` is the same category at two granularities, and
#   "Aamra Networks Limited." vs "Aamra Networks Limited" is a full stop. The
#   conflict is still recorded and the resolved value is still withheld — the
#   listing simply stays resolved, because its *identity* was never in question.
# SOURCE_SCOPED — an id that only means something inside its own source's
#   namespace. LankaBD sector 10 and StockNow sector 25 are the same sector in
#   two unrelated numbering schemes; comparing them is a category error, so they
#   are kept per source and never reconciled against each other.
IDENTITY_ATTRIBUTES = ("exchange", "mic", "company_id", "instrument_number")
DESCRIPTIVE_ATTRIBUTES = ("company_name", "sector_name", "market_category", "instrument_type")
SOURCE_SCOPED_ATTRIBUTES = ("sector_id",)
ATTRIBUTES = IDENTITY_ATTRIBUTES + DESCRIPTIVE_ATTRIBUTES


def normalise_code(raw: Any) -> str:
    """The one normalisation, stated in NORMALISE_RULE. Never guesses.

    Order matters and is not incidental: strip whitespace, decode, then upper.
    Whitespace first, because CSE splits a code across DOM nodes and an entity
    split that way is not decodable until the gaps are closed. Upper last, so
    entity case (`&AMP;` vs `&amp;`) cannot change the outcome. Raw evidence is
    never rewritten — this is the canonical form used for joining, and the
    original string travels on every claim as `code_raw`.
    """
    s = _WS.sub("", str(raw or ""))            # CSE splits a code across DOM nodes
    return html.unescape(s).upper()            # exactly one pass, see NORMALISE_RULE


# --------------------------------------------------------------------------- claims
@dataclass(frozen=True)
class SourceClaim:
    """Exactly what one source said about one instrument, at one evidence date.

    Nothing here is resolved or reconciled — a claim is a quotation. The
    reconciliation happens in `build_spine`, where disagreements become visible.
    """

    source: str
    code_raw: str
    exchange: Optional[str]                      # None = the source did not name an exchange we can read
    as_of: str                                   # evidence date, YYYY-MM-DD
    evidence: str                                # path the claim was read from
    company_name: Optional[str] = None
    mic: Optional[str] = None
    company_id: Optional[str] = None
    instrument_number: Optional[str] = None
    sector_id: Optional[str] = None
    sector_name: Optional[str] = None
    market_category: Optional[str] = None
    instrument_type: Optional[str] = None
    # An opaque, source-private exchange enum (EcoSoft OST sends StockExchange=1).
    # It is kept verbatim and never read as an exchange name.
    source_exchange_code: Optional[str] = None

    @property
    def code(self) -> str:
        return normalise_code(self.code_raw)

    @property
    def listing_key(self) -> Optional[str]:
        return f"{self.exchange}:{self.code}" if self.exchange else None

    def attribute(self, name: str) -> Optional[str]:
        v = getattr(self, name, None)
        return None if v is None or str(v).strip() == "" else str(v).strip()


@dataclass
class Conflict:
    """Two sources disagreeing. Recorded, never resolved."""

    scope: str                                   # "listing_attribute" | "company_id_reuse" | "code_reuse"
    key: str
    attribute: Optional[str]
    values: Dict[str, str]                       # source -> value
    note: str

    def sort_key(self) -> Tuple[str, str, str]:
        return (self.scope, self.key, self.attribute or "")


@dataclass
class Resolution:
    """One resolved attribute of one listing, with the truth class it earned."""

    value: Optional[str]
    truth: str
    sources: List[str]
    rule: Optional[str] = None
    conflicting_values: Optional[Dict[str, str]] = None


@dataclass
class Listing:
    """One (code, exchange) — one order book."""

    key: str
    code: str
    exchange: str
    sources: List[str] = field(default_factory=list)
    attributes: Dict[str, Resolution] = field(default_factory=dict)
    source_scoped: Dict[str, Dict[str, str]] = field(default_factory=dict)
    conflicts: List[Conflict] = field(default_factory=list)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    absent_from_latest: List[str] = field(default_factory=list)
    exchange_truth: str = Truth.OBSERVED.value
    exchange_rule: Optional[str] = None

    @property
    def identity_conflicts(self) -> List[Conflict]:
        return [c for c in self.conflicts if c.attribute in IDENTITY_ATTRIBUTES]

    @property
    def resolved(self) -> bool:
        """Resolved = a source named it, and nothing about *which instrument it is*
        is in conflict. A descriptive disagreement is recorded but does not make
        the instrument unknown."""
        return bool(self.sources) and not self.identity_conflicts

    @property
    def truth(self) -> str:
        if not self.sources:
            return Truth.NOT_OBSERVABLE.value
        if self.identity_conflicts:
            return Truth.NOT_OBSERVABLE.value
        return self.exchange_truth


@dataclass
class Company:
    """An issuer, and the listings believed to belong to it."""

    key: str
    listings: List[str]
    truth: str
    rule: Optional[str] = None
    corroboration: Optional[str] = None


# --------------------------------------------------------------------------- loaders
def _parquet(path: str):
    import pandas as pd
    return pd.read_parquet(path)


def _s(v: Any) -> Optional[str]:
    """A scalar as a clean string, or None. NaN and empty are None, never '' or 'nan'."""
    if v is None:
        return None
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:                                            # noqa: BLE001
        pass
    s = str(v).strip()
    if s in ("", "nan", "None", "NaT"):
        return None
    # 236.0 -> 236 : an id read from a float column is still that id
    if re.fullmatch(r"-?\d+\.0", s):
        s = s[:-2]
    return s


def load_lankabd_watch(path: str, as_of: str) -> List[SourceClaim]:
    """LankaBD all-symbol watch — carries companyID and the instrument number."""
    if not os.path.exists(path):
        return []
    d = _parquet(path)
    out = []
    for r in d.to_dict("records"):
        code = _s(r.get("symbol"))
        if not code:
            continue
        out.append(SourceClaim(
            source="lankabd_watch", code_raw=code, exchange="DSE", as_of=as_of,
            evidence=os.path.relpath(path, ROOT),
            company_id=_s(r.get("company_id")), instrument_number=_s(r.get("instrument_number")),
            sector_id=_s(r.get("sector_id")), market_category=_s(r.get("market_category"))))
    return out


def load_lankabd_grid(path: str, as_of: str) -> List[SourceClaim]:
    """LankaBD grid — the same companyID plus the sector *name* and company name."""
    if not os.path.exists(path):
        return []
    d = _parquet(path)
    out = []
    for r in d.to_dict("records"):
        code = _s(r.get("symbol"))
        if not code:
            continue
        out.append(SourceClaim(
            source="lankabd_grid", code_raw=code, exchange="DSE", as_of=as_of,
            evidence=os.path.relpath(path, ROOT),
            company_name=_s(r.get("companyName")), company_id=_s(r.get("companyID")),
            sector_id=_s(r.get("sectorId")), sector_name=_s(r.get("sectorName")),
            market_category=_s(r.get("marketCategory"))))
    return out


def load_dse_fundamentals(path: str, as_of: str) -> List[SourceClaim]:
    """dsebd.org company page — the exchange's own sector and instrument type."""
    if not os.path.exists(path):
        return []
    d = _parquet(path)
    out = []
    for r in d.to_dict("records"):
        code = _s(r.get("symbol"))
        if not code:
            continue
        out.append(SourceClaim(
            source="dse_fundamentals", code_raw=code, exchange="DSE", as_of=as_of,
            evidence=os.path.relpath(path, ROOT),
            sector_name=_s(r.get("sector")), market_category=_s(r.get("market_category")),
            instrument_type=_s(r.get("instrument_type"))))
    return out


def _iter_raw_bodies(root: str, prefix: str):
    """DATA bodies from a raw-store segment, gzipped or not. Sorted for determinism."""
    from .capture.raw_store import decode_body, iter_segment
    pats = sorted(glob.glob(os.path.join(root, "segments", f"{prefix}*.jsonl")) +
                  glob.glob(os.path.join(root, "segments", f"{prefix}*.jsonl.gz")))
    for p in pats:
        for rec, ok in iter_segment(p):
            if ok and rec.get("kind") == "DATA":
                yield decode_body(rec), rec, p


def load_stocknow(root: str, as_of: str) -> List[SourceClaim]:
    """StockNow /api/v1/instruments — trading code, name, its own sector id, category."""
    out = []
    for body, _rec, path in _iter_raw_bodies(root, "stocknow_instruments"):
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        for sym, v in d.items():
            if not isinstance(v, dict):
                continue
            code = _s(v.get("code")) or _s(sym)
            if not code:
                continue
            out.append(SourceClaim(
                source="stocknow_instruments", code_raw=code, exchange="DSE", as_of=as_of,
                evidence=os.path.relpath(path, ROOT),
                company_name=_s(v.get("name")), sector_id=_s(v.get("sector_id")),
                market_category=_s(v.get("category"))))
        break                                    # one snapshot per evidence date is enough
    return out


def load_cse(root: str, as_of: str) -> List[SourceClaim]:
    """CSE current price — the other exchange. Code only; no name is published here."""
    from .capture.adapters.bd_public import CSECurrentPriceAdapter
    out = []
    for body, _rec, path in _iter_raw_bodies(root, "cse_current_price"):
        p = CSECurrentPriceAdapter(client=None).parse(body)
        for fr in p.frames:
            code = _s(fr.get("symbol"))
            if not code:
                continue
            out.append(SourceClaim(source="cse_current_price", code_raw=code, exchange="CSE",
                                   as_of=as_of, evidence=os.path.relpath(path, ROOT)))
        break
    return out


def load_bullbd(root: str, as_of: str) -> List[SourceClaim]:
    """BullBD detail — the only source that publishes a MIC."""
    from .capture.adapters.bd_public import BullBDDetailAdapter
    a = BullBDDetailAdapter(client=None)
    out = []
    for body, rec, path in _iter_raw_bodies(root, "bullbd_detail"):
        p = a.parse(body, rec.get("key"))
        for fr in p.frames:
            code = _s(fr.get("symbol"))
            if not code:
                continue
            out.append(SourceClaim(
                source="bullbd_detail", code_raw=code, exchange="DSE", as_of=as_of,
                evidence=os.path.relpath(path, ROOT),
                company_name=_s(fr.get("company_name")), mic=_s(fr.get("mic")),
                sector_name=_s(fr.get("sector")), market_category=_s(fr.get("market_category"))))
    return out


_ECOSOFT_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def load_ecosoft(directory: str, as_of: Optional[str] = None) -> List[SourceClaim]:
    """The owner's own broker terminal, from the committed HAR probes. TradingCode only.

    Each probe file is dated from its own filename rather than from one argument.
    The directory grows as the account holder records more sessions, and stamping a
    2026-09-08 recording with 2026-09-07 because that was the first one would put a
    false observation date on real evidence — the one thing an `as_of` exists to
    carry. `as_of` remains available as a fallback for a file whose name has no date.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "*.ndjson"))):
        m = _ECOSOFT_DATE.search(os.path.basename(path))
        file_as_of = m.group(1) if m else (as_of or "unknown")
        seen = set()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                p = rec.get("payload")
                if not isinstance(p, dict):
                    continue
                code = _s(p.get("TradingCode")) or _s((p.get("Depth") or {}).get("TradingCode")
                                                      if isinstance(p.get("Depth"), dict) else None)
                if not code or code in seen:
                    continue
                seen.add(code)
                # StockExchange arrives as the opaque enum "1", not an exchange
                # name. Reading it as one would be a guess, so the exchange is
                # left unknown here and resolved by corroboration in build_spine.
                out.append(SourceClaim(
                    source="ecosoft_ost", code_raw=code, exchange=None, as_of=file_as_of,
                    evidence=os.path.relpath(path, ROOT),
                    source_exchange_code=_s(p.get("StockExchange"))))
    return out


# --------------------------------------------------------------------------- build
@dataclass
class Spine:
    listings: Dict[str, Listing]
    companies: Dict[str, Company]
    conflicts: List[Conflict]
    unresolved: List[Dict[str, Any]]
    claims: int
    sources: List[str]
    as_of_dates: List[str]

    # ---------------------------------------------------------------- reporting
    def coverage(self, universe: Sequence[str], exchange: str = "DSE") -> Dict[str, Any]:
        """How much of a given symbol universe this spine resolves."""
        want = [normalise_code(s) for s in universe]
        resolved, unres, missing = [], [], []
        for c in want:
            lst = self.listings.get(f"{exchange}:{c}")
            if lst is None:
                missing.append(c)
            elif lst.resolved:
                resolved.append(c)
            else:
                unres.append(c)
        return {"universe_size": len(want), "exchange": exchange,
                "resolved": len(resolved), "unresolved": len(unres), "absent": len(missing),
                "resolved_pct": round(100.0 * len(resolved) / len(want), 2) if want else None,
                "unresolved_symbols": sorted(unres), "absent_symbols": sorted(missing)}

    def core(self) -> Dict[str, Any]:
        """The deterministic core — everything the hash covers. No wall clock."""
        return {
            "normalise_rule": NORMALISE_RULE,
            "sources": sorted(self.sources),
            "as_of_dates": sorted(self.as_of_dates),
            "claims": self.claims,
            "listings": [
                {
                    "key": k, "code": self.listings[k].code, "exchange": self.listings[k].exchange,
                    "truth": self.listings[k].truth,
                    "resolved": self.listings[k].resolved,
                    "exchange_truth": self.listings[k].exchange_truth,
                    "exchange_rule": self.listings[k].exchange_rule,
                    "sources": sorted(self.listings[k].sources),
                    "first_seen": self.listings[k].first_seen,
                    "last_seen": self.listings[k].last_seen,
                    "absent_from_latest": sorted(self.listings[k].absent_from_latest),
                    "attributes": {a: asdict(self.listings[k].attributes[a])
                                   for a in sorted(self.listings[k].attributes)},
                    "source_scoped": {a: self.listings[k].source_scoped[a]
                                      for a in sorted(self.listings[k].source_scoped)},
                    "conflicts": [asdict(c) for c in sorted(self.listings[k].conflicts,
                                                            key=lambda x: x.sort_key())],
                }
                for k in sorted(self.listings)
            ],
            "companies": [asdict(self.companies[k]) for k in sorted(self.companies)],
            "conflicts": [asdict(c) for c in sorted(self.conflicts, key=lambda x: x.sort_key())],
            "unresolved": sorted(self.unresolved, key=lambda x: (x["key"], x["reason"])),
        }

    def sha256(self) -> str:
        blob = json.dumps(self.core(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_json(self, generated_utc: Optional[str] = None) -> Dict[str, Any]:
        return {"schema": "identity_spine/1", "generated_utc": generated_utc,
                "spine_sha256": self.sha256(), "core": self.core()}


CROSS_EXCHANGE_RULE = (
    "INFERRED same-company link: a normalised trading code that appears on both DSE and CSE is "
    "taken to be the same issuer listed twice. It is NOT the same instrument — the two have "
    "separate books and separate prices, and they are never merged. No source we reach asserts "
    "this link, and CSE's current-price table publishes no company name, so there is no "
    "corroborating field; the link therefore stays INFERRED and is flagged uncorroborated.")


EXCHANGE_CORROBORATION_RULE = (
    "INFERRED exchange: the source published an opaque exchange enum rather than a name "
    "(EcoSoft OST sends StockExchange=1), so the exchange is taken from corroboration — the "
    "normalised trading code is listed on exactly one exchange by the other wired sources. "
    "If the code is listed on more than one exchange, or on none, the claim is NOT resolved "
    "to an exchange and is reported under UNKNOWN.")


def build_spine(claims: Sequence[SourceClaim]) -> Spine:
    """Group claims into listings, resolve attributes, and surface every disagreement."""
    by_listing: Dict[str, List[SourceClaim]] = {}
    floating: List[SourceClaim] = []
    for c in claims:
        if not c.code:                                            # an unreadable code is not a listing
            continue
        if c.exchange:
            by_listing.setdefault(c.listing_key, []).append(c)
        else:
            floating.append(c)

    # Claims whose source did not name a readable exchange are placed only where
    # the other sources leave exactly one possibility. Never by assumption.
    inferred_exchange: Dict[str, str] = {}
    unplaced: List[Dict[str, Any]] = []
    code_to_exchanges: Dict[str, set] = {}
    for k in by_listing:
        exch, _, code = k.partition(":")
        code_to_exchanges.setdefault(code, set()).add(exch)
    for c in floating:
        cands = sorted(code_to_exchanges.get(c.code, set()))
        if len(cands) == 1:
            key = f"{cands[0]}:{c.code}"
            by_listing.setdefault(key, []).append(c)
            inferred_exchange[key] = c.source
        else:
            unplaced.append({
                "key": f"UNKNOWN:{c.code}", "reason": "exchange not resolvable",
                "attributes": ["exchange"], "sources": [c.source],
                "detail": (f"source published the opaque exchange enum "
                           f"{c.source_exchange_code!r}; corroborating exchanges for this code: "
                           f"{cands or 'none'}")})

    all_dates = sorted({c.as_of for c in claims})
    latest = all_dates[-1] if all_dates else None
    conflicts: List[Conflict] = []
    listings: Dict[str, Listing] = {}

    for key, cs in by_listing.items():
        code, exchange = cs[0].code, cs[0].exchange
        lst = Listing(key=key, code=code, exchange=exchange,
                      sources=sorted({c.source for c in cs}),
                      first_seen=min(c.as_of for c in cs),
                      last_seen=max(c.as_of for c in cs))
        # a source that saw this listing earlier but not in the latest evidence
        if latest:
            seen_latest = {c.source for c in cs if c.as_of == latest}
            earlier = {c.source for c in cs if c.as_of != latest}
            sources_in_latest = {c.source for c in claims if c.as_of == latest}
            lst.absent_from_latest = sorted(
                (earlier - seen_latest) & sources_in_latest)

        if key in inferred_exchange:
            lst.exchange_truth = Truth.INFERRED.value
            lst.exchange_rule = EXCHANGE_CORROBORATION_RULE

        # Source-private id spaces are kept apart, never reconciled: LankaBD
        # sector 10 and StockNow sector 25 name the same sector in two unrelated
        # schemes, so "they disagree" would be a category error, not a finding.
        for attr in SOURCE_SCOPED_ATTRIBUTES:
            per: Dict[str, str] = {}
            for c in cs:
                v = c.attribute(attr)
                if v is not None:
                    per.setdefault(c.source, v)
            if per:
                lst.source_scoped[attr] = dict(sorted(per.items()))

        for attr in ATTRIBUTES:
            vals: Dict[str, str] = {}
            for c in cs:
                v = c.attribute(attr)
                if v is not None:
                    vals.setdefault(c.source, v)
            if not vals:
                lst.attributes[attr] = Resolution(None, Truth.NOT_OBSERVABLE.value, [],
                                                  rule="no wired source publishes this field "
                                                       "for this listing")
                continue
            distinct = sorted(set(vals.values()))
            if len(distinct) == 1:
                lst.attributes[attr] = Resolution(distinct[0], Truth.OBSERVED.value,
                                                  sorted(vals))
            else:
                is_identity = attr in IDENTITY_ATTRIBUTES
                con = Conflict(
                    scope="identity_attribute" if is_identity else "descriptive_attribute",
                    key=key, attribute=attr, values=dict(sorted(vals.items())),
                    note=(f"{len(distinct)} distinct values; not merged, not voted on. "
                          + ("IDENTITY-CRITICAL: which instrument this is cannot be settled, so "
                             "the listing is unresolved and nothing downstream may use it."
                             if is_identity else
                             "Descriptive: the sources label it differently but agree which "
                             "instrument it is, so the listing stays resolved and the value is "
                             "withheld rather than picked.")))
                conflicts.append(con)
                lst.conflicts.append(con)
                lst.attributes[attr] = Resolution(
                    None, Truth.NOT_OBSERVABLE.value, sorted(vals),
                    rule="sources disagree — the resolved value is withheld on purpose",
                    conflicting_values=dict(sorted(vals.items())))
        listings[key] = lst

    # ---- structural conflicts across listings -----------------------------
    # A company id that maps to two different codes on one exchange is either a
    # rename or a source error. Either way it is not silently accepted.
    cid_to_codes: Dict[Tuple[str, str], Dict[str, set]] = {}
    for key, lst in listings.items():
        res = lst.attributes.get("company_id")
        if res and res.value:
            cid_to_codes.setdefault((lst.exchange, res.value), {}).setdefault(
                "codes", set()).add(lst.code)
    for (exch, cid), d in sorted(cid_to_codes.items()):
        if len(d["codes"]) > 1:
            conflicts.append(Conflict(
                scope="company_id_reuse", key=f"{exch}:company_id={cid}", attribute="company_id",
                values={c: cid for c in sorted(d["codes"])},
                note="one company id appears under several trading codes — a rename or a source "
                     "error; the codes are kept apart until evidence says which"))

    # ---- companies: same code across exchanges ----------------------------
    companies: Dict[str, Company] = {}
    by_code: Dict[str, List[Listing]] = {}
    for lst in listings.values():
        by_code.setdefault(lst.code, []).append(lst)
    for code, group in sorted(by_code.items()):
        keys = sorted(g.key for g in group)
        exchanges = sorted({g.exchange for g in group})
        if len(exchanges) == 1:
            companies[code] = Company(key=code, listings=keys, truth=Truth.OBSERVED.value,
                                      rule="single exchange — the listing is the company here")
        else:
            names = {g.attributes["company_name"].value for g in group
                     if g.attributes.get("company_name") and g.attributes["company_name"].value}
            companies[code] = Company(
                key=code, listings=keys, truth=Truth.INFERRED.value, rule=CROSS_EXCHANGE_RULE,
                corroboration=("company names agree across the exchanges" if len(names) == 1 and names
                               else "uncorroborated — no shared field beyond the trading code"))

    # One entry per unresolvable INSTRUMENT, not one per file that mentions it.
    # The EcoSoft evidence directory grows a probe per recorded session, so a code
    # the opaque exchange enum cannot place appeared once per recording and made
    # the unresolved count climb with the evidence rather than with the problem.
    # Sources are merged so nothing about where the claim came from is lost.
    unresolved: List[Dict[str, Any]] = []
    _seen_unresolved: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for u in unplaced:
        k = (str(u.get("key")), str(u.get("reason")))
        prior = _seen_unresolved.get(k)
        if prior is None:
            _seen_unresolved[k] = dict(u)
            unresolved.append(_seen_unresolved[k])
        else:
            prior["sources"] = sorted(set(prior.get("sources") or []) | set(u.get("sources") or []))
    for key, lst in sorted(listings.items()):
        if not lst.resolved:
            unresolved.append({"key": key, "reason": "identity attribute conflict",
                               "attributes": sorted(c.attribute or ""
                                                    for c in lst.identity_conflicts),
                               "sources": sorted(lst.sources)})

    return Spine(listings=listings, companies=companies,
                 conflicts=sorted(conflicts, key=lambda c: c.sort_key()),
                 unresolved=unresolved, claims=len(claims),
                 sources=sorted({c.source for c in claims}),
                 as_of_dates=all_dates)


# --------------------------------------------------------------------------- assembly
DEFAULT_PUBLIC = os.path.join(ROOT, "evidence", "public", "2026-09-06", "normalized")
DEFAULT_ENGINE = os.path.join(ROOT, "evidence", "public_engine", "2026-09-08")
DEFAULT_ECOSOFT = os.path.join(ROOT, "evidence", "ecosoft_ost")


def collect_claims(public_dir: str = DEFAULT_PUBLIC, engine_dir: str = DEFAULT_ENGINE,
                   ecosoft_dir: str = DEFAULT_ECOSOFT) -> List[SourceClaim]:
    """Every claim from every committed evidence set, in a fixed order."""
    pub_date = os.path.basename(os.path.dirname(public_dir.rstrip("/"))) or "unknown"
    eng_date = os.path.basename(engine_dir.rstrip("/")) or "unknown"
    claims: List[SourceClaim] = []
    claims += load_lankabd_watch(os.path.join(public_dir, "market_watch.parquet"), pub_date)
    claims += load_lankabd_grid(os.path.join(public_dir, "market_grid.parquet"), pub_date)
    claims += load_dse_fundamentals(os.path.join(public_dir, "company_fundamentals.parquet"), pub_date)
    claims += load_stocknow(engine_dir, eng_date)
    claims += load_cse(engine_dir, eng_date)
    claims += load_bullbd(engine_dir, eng_date)
    claims += load_ecosoft(ecosoft_dir)
    claims.sort(key=lambda c: (c.source, c.exchange, c.code, c.as_of))
    return claims


def wired_dse_universe(public_dir: str = DEFAULT_PUBLIC) -> List[str]:
    """The DSE symbols the system currently wires — the coverage denominator."""
    p = os.path.join(public_dir, "symbols.csv")
    if not os.path.exists(p):
        return []
    import pandas as pd
    d = pd.read_csv(p)
    return sorted({normalise_code(s) for s in d["symbol"].astype(str)})


KNOWN_EXCHANGES = ("DSE", "CSE")


def stage0_gate(spine: Spine, universe: Sequence[str], rebuilt_sha: str) -> Dict[str, Any]:
    """The gate `ROADMAP.md` sets for Stage 0.

    *Every symbol used downstream resolves, or is explicitly listed as unresolved.*
    That is an **accounting** requirement, not a coverage threshold: the failure it
    exists to catch is a symbol going missing quietly. A low resolved percentage is
    reported, not punished — 68 absent instruments here are bonds and T-bills no
    wired source lists, and hiding them would be the actual defect.
    """
    cov = spine.coverage(universe, "DSE")
    exchanges = sorted({l.exchange for l in spine.listings.values()})
    checks = [
        {"check": "every wired symbol is accounted for",
         "pass": cov["resolved"] + cov["unresolved"] + cov["absent"] == cov["universe_size"],
         "detail": f"{cov['resolved']} resolved + {cov['unresolved']} unresolved + "
                   f"{cov['absent']} absent = {cov['universe_size']}"},
        {"check": "no wired symbol is unresolved without being named",
         "pass": len(cov["unresolved_symbols"]) == cov["unresolved"],
         "detail": f"{len(cov['unresolved_symbols'])} named"},
        {"check": "every absent symbol is named",
         "pass": len(cov["absent_symbols"]) == cov["absent"],
         "detail": f"{len(cov['absent_symbols'])} named"},
        {"check": "every unresolved listing states a reason and its sources",
         "pass": all(u.get("reason") and u.get("sources") for u in spine.unresolved),
         "detail": f"{len(spine.unresolved)} unresolved entries"},
        {"check": "every conflict keeps all source values and a note",
         "pass": all(c.values and c.note for c in spine.conflicts),
         "detail": f"{len(spine.conflicts)} conflicts, none collapsed"},
        {"check": "no opaque source enum leaked in as an exchange",
         "pass": all(e in KNOWN_EXCHANGES for e in exchanges),
         "detail": f"exchanges present: {exchanges}"},
        {"check": "deterministic rebuild reproduces the same spine hash",
         "pass": rebuilt_sha == spine.sha256(),
         "detail": f"{spine.sha256()[:16]}… vs {rebuilt_sha[:16]}…"},
    ]
    ok = all(c["pass"] for c in checks)
    return {"stage": 0, "name": "Reference & Identity Spine",
            "gate": "every symbol used downstream resolves, or is explicitly listed as unresolved",
            "result": "PASS" if ok else "BLOCKED", "checks": checks,
            "coverage_wired_dse": cov, "spine_sha256": spine.sha256(),
            "listings": len(spine.listings), "companies": len(spine.companies),
            "conflicts": len(spine.conflicts), "unresolved": len(spine.unresolved)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--public", default=DEFAULT_PUBLIC)
    ap.add_argument("--engine", default=DEFAULT_ENGINE)
    ap.add_argument("--ecosoft", default=DEFAULT_ECOSOFT)
    ap.add_argument("--out", default=os.path.join(ROOT, "evidence", "identity", "2026-09-08"))
    ap.add_argument("--no-timestamp", action="store_true",
                    help="omit generated_utc so two runs produce byte-identical files")
    a = ap.parse_args(argv)

    claims = collect_claims(a.public, a.engine, a.ecosoft)
    spine = build_spine(claims)
    universe = wired_dse_universe(a.public)
    cov = spine.coverage(universe, "DSE")

    os.makedirs(a.out, exist_ok=True)
    stamp = None if a.no_timestamp else datetime.now(timezone.utc).isoformat()
    payload = spine.to_json(stamp)
    payload["coverage_wired_dse"] = cov

    mp = os.path.join(a.out, "IDENTITY_MAP.json")
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False, sort_keys=True)

    rp = os.path.join(a.out, "UNRESOLVED_AND_CONFLICTS.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump({"spine_sha256": spine.sha256(),
                   "conflicts": [asdict(c) for c in spine.conflicts],
                   "unresolved": spine.unresolved,
                   "coverage_wired_dse": cov}, fh, indent=1, ensure_ascii=False, sort_keys=True)

    # An independent rebuild from the same evidence — the determinism check is
    # part of the gate, not a claim about it.
    rebuilt = build_spine(collect_claims(a.public, a.engine, a.ecosoft)).sha256()
    gate = stage0_gate(spine, universe, rebuilt)
    gp = os.path.join(a.out, "STAGE0_GATE.json")
    with open(gp, "w", encoding="utf-8") as fh:
        json.dump(gate, fh, indent=1, ensure_ascii=False, sort_keys=True)

    by_exch: Dict[str, int] = {}
    for lst in spine.listings.values():
        by_exch[lst.exchange] = by_exch.get(lst.exchange, 0) + 1
    print(f"claims          : {spine.claims:,} from {len(spine.sources)} sources "
          f"{spine.sources}")
    print(f"listings        : {len(spine.listings):,}  {by_exch}")
    print(f"companies       : {len(spine.companies):,}")
    print(f"resolved        : {sum(1 for l in spine.listings.values() if l.resolved):,}")
    print(f"unresolved      : {len(spine.unresolved):,}")
    print(f"conflicts       : {len(spine.conflicts):,}")
    print(f"wired DSE cover : {cov['resolved']}/{cov['universe_size']} "
          f"({cov['resolved_pct']}%), absent {cov['absent']}, unresolved {cov['unresolved']}")
    print(f"spine_sha256    : {spine.sha256()}")
    print(f"wrote {mp}\n      {rp}\n      {gp}")
    print()
    for c in gate["checks"]:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['check']}: {c['detail']}")
    print(f"\nSTAGE 0 GATE: {gate['result']}")
    return 0 if gate["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
