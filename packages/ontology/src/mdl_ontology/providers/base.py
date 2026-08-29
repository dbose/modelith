"""The provider contract and shared helpers.

Every source (local file vocabulary or remote registry) implements `SourceProvider`.
The two-phase browse is first-class: `list_ontologies()` enumerates the vocabularies
a source indexes, and `search(query, within=<ontology id>)` scopes term search to one
of them (bounded, so a catalog with millions of terms stays usable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ResolvedTerm:
    """A search hit. `source` is the provider name; `source_kind` distinguishes a
    taxonomy class from a catalog glossary term."""

    iri: str
    prefixed: str
    label: str | None
    definition: str | None
    source: str
    source_kind: str = "ontology-class"  # or "glossary-term"


@dataclass
class OntologyRef:
    """One indexed vocabulary a source exposes (the first browse phase)."""

    id: str  # provider-scoped id (a local name, an OLS ontology id, a Collibra domain id)
    name: str
    description: str | None = None
    namespace: str | None = None
    count: int | None = None  # term count, when the source reports it


@dataclass
class TermCard:
    """A full term detail card (the describe() result)."""

    iri: str
    prefixed: str
    label: str | None
    definition: str | None
    source: str
    synonyms: list[str] = field(default_factory=list)
    parents: list[dict] = field(default_factory=list)  # [{iri, prefixed, label}]
    children: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "iri": self.iri,
            "prefixed": self.prefixed,
            "label": self.label,
            "definition": self.definition,
            "source": self.source,
            "synonyms": list(self.synonyms),
            "parents": list(self.parents),
            "children": list(self.children),
            # legacy keys the canvas already reads (broader/narrower == parents/children)
            "broader": list(self.parents),
            "narrower": list(self.children),
        }


@runtime_checkable
class SourceProvider(Protocol):
    """One ontology term source. Local and remote both satisfy this."""

    name: str
    layer: str

    def list_ontologies(self) -> list[OntologyRef]:
        """The vocabularies this source indexes (browse phase one)."""
        ...

    def search(
        self, query: str, *, within: str | None = None, limit: int = 20
    ) -> list[ResolvedTerm]:
        """Ranked term search, optionally scoped to one ontology id."""
        ...

    def describe(self, ref: str) -> TermCard | None:
        """Full card for a prefixed name or IRI, with hierarchy neighbours."""
        ...

    def expand(self, prefixed: str) -> str | None:
        """Prefixed IRI -> absolute IRI, or None if the prefix is unknown here."""
        ...

    def resolves(self, prefixed: str) -> bool:
        """True if this source can vouch for the IRI."""
        ...


# --- shared scoring / naming (used by the local provider and the lexical matcher) ---


def local_name(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1]
    return iri


def score(query: str, haystack: str, label: str) -> int:
    """Token-overlap relevance. Kept identical to the original registry scorer so
    ranking is unchanged, and reused by the reverse-time lexical matcher."""
    if not query:
        return 0
    s = 0
    if query == label:
        s += 100
    if query in label:
        s += 40
    if query in haystack:
        s += 10
    for tok in query.split():
        if tok in haystack:
            s += 2
    return s
