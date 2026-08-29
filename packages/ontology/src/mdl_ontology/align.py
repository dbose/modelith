"""Alignment pass — Pass 2 of reverse (spec §2).

Reverse Pass 1 lifts a dbt project into an LDM. This pass runs *separately* and is
non-blocking: it proposes ontology alignments for the reversed entities/attributes, but
never commits them. Each proposal lands in the `DecisionLedger` as a
`kind="ontology_alignment"` decision with a confidence and the ranked candidate list as
evidence; a human accepts it through the SME-web-app -> PR flow, which is what writes
`resolved_via` / `resolved_by` / `confidence` / `approved_at` onto the entity YAML (the
audit trail Collibra governance needs).

**Merged closure (the §2 critical note).** Candidate matching runs through
`registry.search()`, which fans across *every* configured source — the public ontology,
the enterprise Collibra/OLS registry, and any local enterprise extension files — and
merges the hits. So searching the registry *is* searching the merged closure (public +
enterprise imports/subclass/restriction); we never match the raw public ontology alone.

The scorer is behind a `Matcher` interface. `LexicalMatcher` (token overlap, the same
scorer the registry ranks with) ships now; an embeddings matcher plugs in later without
touching the pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from mdl_core.ir import Attribute, ConceptualEntity, LogicalEntity, Model, Term
from mdl_ontology.providers.base import ResolvedTerm, score

# dbt layer / modeling prefixes that carry no business meaning — stripped before
# matching so `dim_customers` / `fct_orders` / `mart_kpi` match the concept, not the
# layer. Order matters only for readability; matching is set membership.
_LAYER_TOKENS = frozenset(
    {"dim", "fct", "fact", "stg", "stage", "staging", "int", "intermediate",
     "mart", "marts", "rpt", "report", "reporting", "agg", "snap", "snapshot",
     "base", "ref", "raw", "src", "source", "tmp", "wrk", "work"}
)


def _singularize(word: str) -> str:
    """Cheap English singularizer — enough to turn Customers -> Customer,
    Parties -> Party, Addresses -> Address. Not linguistically complete."""
    w = word
    if len(w) > 4 and w.lower().endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.lower().endswith(("ses", "xes", "zes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.lower().endswith("s") and not w.lower().endswith("ss"):
        return w[:-1]
    return w


def _tokenize(name: str) -> list[str]:
    """Split a snake_case / camelCase / PascalCase name into lowercase word tokens."""
    # snake / kebab first, then camel/pascal within each part
    parts = re.split(r"[_\-\s]+", name)
    tokens: list[str] = []
    for p in parts:
        tokens.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", p) or [p])
    return [t.lower() for t in tokens if t]


def normalize_name(name: str) -> str:
    """Strip modeling-layer prefixes, singularize, and rejoin — the business term a
    reversed entity/attribute name actually denotes. `dim_customers` -> `customer`,
    `fct_order_items` -> `order item`, `MartDailyRevenue` -> `daily revenue`."""
    toks = [t for t in _tokenize(name) if t not in _LAYER_TOKENS]
    if not toks:  # name was ALL layer words (e.g. "mart") — keep the original tokens
        toks = _tokenize(name)
    toks = [_singularize(t) for t in toks]
    return " ".join(toks)


class Matcher(Protocol):
    """Scores how well a modelled object's text matches an ontology term (0..1)."""

    def score(self, query: str, term: ResolvedTerm) -> float: ...


@dataclass
class LexicalMatcher:
    """Token-overlap matcher — the offline default. Reuses the registry's scorer so a
    candidate ranks here the same way it ranks in search, then normalises to 0..1."""

    def score(self, query: str, term: ResolvedTerm) -> float:
        hay = f"{term.label or ''} {term.definition or ''}".lower()
        raw = score(query.lower(), hay, (term.label or "").lower())
        # score() maxes near ~150 (exact label + contains + tokens); clamp to 0..1.
        return min(raw / 150.0, 1.0)


@dataclass
class Candidate:
    uri: str
    prefixed: str
    label: str | None
    definition: str | None
    source: str
    confidence: float

    def to_evidence(self) -> dict:
        return {
            "uri": self.prefixed or self.uri,
            "label": self.label,
            "source": self.source,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class AlignmentProposal:
    """One object's ranked alignment candidates (not committed)."""

    object_id: str
    object_kind: str  # conceptual_entity | term | attribute
    object_name: str
    matched_field: str  # what we matched on: name | name+definition
    candidates: list[Candidate]

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


def _confidence_band(c: float) -> str:
    if c >= 0.75:
        return "high"
    if c >= 0.55:
        return "medium-high"
    if c >= 0.35:
        return "medium"
    return "low"


def _query_for(name: str, definition: str | None, synonyms: list[str]) -> str:
    """The scoring query: the normalized business name plus synonyms and definition
    keywords, so a term whose label differs from the raw name still matches."""
    parts = [normalize_name(name)]
    parts.extend(synonyms or [])
    if definition:
        parts.append(definition)
    return " ".join(p for p in parts if p).strip()


def _search_terms(name: str, synonyms: list[str]) -> list[str]:
    """The retrieval queries sent to the registry — the normalized name and any
    synonyms, tried independently so a resolver's substring search finds candidates
    it would miss on the raw `dim_*` name."""
    terms: list[str] = []
    for t in [normalize_name(name), name, *(synonyms or [])]:
        t = (t or "").strip()
        if t and t not in terms:
            terms.append(t)
    return terms


def _rank(
    name: str,
    definition: str | None,
    synonyms: list[str],
    registry,
    matcher: Matcher,
    *,
    limit: int,
    threshold: float,
) -> list[Candidate]:
    hits = []
    seen_iri: set[str] = set()
    for q in _search_terms(name, synonyms):
        try:
            for h in registry.search(q, limit=max(limit * 3, 10)):
                if h.iri not in seen_iri:
                    seen_iri.add(h.iri)
                    hits.append(h)
        except Exception:  # noqa: BLE001 - a failing resolver yields no candidates
            continue
    norm = normalize_name(name)
    query = _query_for(name, definition, synonyms)
    scored: list[Candidate] = []
    for h in hits:
        # score on the normalized name (captures an exact/near label match) and on the
        # fuller query (recall via synonyms/definition); keep the stronger signal.
        conf = max(matcher.score(norm, h), matcher.score(query, h) * 0.9)
        if conf < threshold:
            continue
        scored.append(
            Candidate(
                uri=h.iri,
                prefixed=h.prefixed,
                label=h.label,
                definition=h.definition,
                source=h.source,
                confidence=conf,
            )
        )
    scored.sort(key=lambda c: -c.confidence)
    return scored[:limit]


def _has_alignment(obj) -> bool:
    return any(r.uri for r in getattr(obj, "ontology_refs", []) or [])


def align_model(
    model: Model,
    registry,
    *,
    matcher: Matcher | None = None,
    threshold: float = 0.3,
    limit: int = 5,
    include_attributes: bool = True,
    skip_aligned: bool = True,
) -> list[AlignmentProposal]:
    """Propose ontology alignments for a model's objects (spec §2). Returns the ranked
    proposals only — nothing is written. Recording them into the decision ledger is a
    caller concern (the `mdl ontology align` CLI command does it), which keeps this
    package free of any dependency on the reverse ledger.
    """
    matcher = matcher or LexicalMatcher()
    proposals: list[AlignmentProposal] = []

    def _consider(obj, kind: str, defn: str | None, synonyms: list[str]) -> None:
        if skip_aligned and _has_alignment(obj):
            return
        cands = _rank(
            obj.name, defn, synonyms, registry, matcher,
            limit=limit, threshold=threshold,
        )
        if not cands:
            return
        matched = "name+definition" if defn else ("name+synonyms" if synonyms else "name")
        proposals.append(AlignmentProposal(obj.id, kind, obj.name, matched, cands))

    for ce in model.conceptual_entities.values():
        _consider(ce, "conceptual_entity", ce.definition, list(ce.synonyms))
    for term in model.terms.values():
        _consider(term, "term", term.definition, list(term.synonyms))
    if include_attributes:
        for le in model.logical_entities.values():
            for attr in le.attributes:
                _consider(attr, "attribute", None, [])

    return proposals


def confidence_band(c: float) -> str:
    """Public: map a 0..1 confidence to the ledger's Confidence band name."""
    return _confidence_band(c)


__all__ = [
    "Matcher",
    "LexicalMatcher",
    "Candidate",
    "AlignmentProposal",
    "align_model",
    "confidence_band",
    "normalize_name",
]


# Silence "imported but unused" for the type-only imports that document intent.
_ = (Attribute, ConceptualEntity, LogicalEntity, Term)
