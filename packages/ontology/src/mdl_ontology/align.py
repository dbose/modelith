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

from dataclasses import dataclass
from typing import Protocol

from mdl_core.ir import Attribute, ConceptualEntity, LogicalEntity, Model, Term
from mdl_ontology.providers.base import ResolvedTerm, score


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
    """Build the match query from the object's own text. Name carries the most
    signal; definition/synonyms broaden recall."""
    parts = [name]
    parts.extend(synonyms or [])
    return " ".join(parts).strip()


def _rank(
    query: str,
    registry,
    matcher: Matcher,
    *,
    limit: int,
    threshold: float,
) -> list[Candidate]:
    try:
        hits = registry.search(query, limit=max(limit * 3, 10))
    except Exception:  # noqa: BLE001 - a failing resolver yields no candidates
        return []
    scored: list[Candidate] = []
    seen: set[str] = set()
    for h in hits:
        if h.iri in seen:
            continue
        seen.add(h.iri)
        conf = matcher.score(query, h)
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
        query = _query_for(obj.name, defn, synonyms)
        cands = _rank(query, registry, matcher, limit=limit, threshold=threshold)
        if not cands:
            return
        matched = "name+synonyms" if synonyms else "name"
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
]


# Silence "imported but unused" for the type-only imports that document intent.
_ = (Attribute, ConceptualEntity, LogicalEntity, Term)
