"""Cache-on-align (spec §4).

When a modeller picks a term from a *remote* resolver and writes it into
`ontology_refs`, the term only lives in the catalog — offline validation
(MDL-E213) and R2RML export would have nothing to resolve against. So on accept we
snapshot the resolved card into the local cache as a tiny SKOS Turtle file. The URI is
still pinned via `ontology.lock` exactly like a vendored term (spec §4 invariant); this
cache is purely so the repo can validate/export without hitting the network.

Cards land under the gitignored cache in a dedicated `resolved/` bucket so they never
collide with a fetched layer:  `.mdl/ontology-cache/resolved/<slug>.ttl`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDFS, SKOS

from mdl_ontology.lock import CACHE_REL

_RESOLVED_SUB = "resolved"


def _slug(iri: str) -> str:
    return hashlib.sha1(iri.encode("utf-8")).hexdigest()[:16]  # noqa: S324 - not security


def resolved_cache_dir(root: Path) -> Path:
    return Path(root) / CACHE_REL / _RESOLVED_SUB


def cache_resolved_term(
    root: Path,
    iri: str,
    *,
    label: str | None = None,
    definition: str | None = None,
    synonyms: list[str] | None = None,
    source: str | None = None,
) -> Path:
    """Write a resolved term as a one-concept SKOS Turtle file into the local cache
    and return its path. Idempotent: re-caching the same IRI overwrites in place."""
    if not iri.startswith("http"):
        raise ValueError(f"cache_resolved_term needs an absolute IRI, got {iri!r}")
    g = Graph()
    g.bind("skos", SKOS)
    g.bind("rdfs", RDFS)
    subj = URIRef(iri)
    g.add((subj, SKOS.prefLabel, Literal(label or iri.rsplit("/", 1)[-1])))
    g.add((subj, RDFS.label, Literal(label or iri.rsplit("/", 1)[-1])))
    if definition:
        g.add((subj, SKOS.definition, Literal(definition)))
    for syn in synonyms or []:
        g.add((subj, SKOS.altLabel, Literal(syn)))
    if source:
        g.add((subj, RDFS.isDefinedBy, Literal(source)))
    dest = resolved_cache_dir(root) / f"{_slug(iri)}.ttl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(g.serialize(format="turtle"), encoding="utf-8")
    return dest


def cache_from_registry(root: Path, registry, ref: str) -> Path | None:
    """Resolve `ref` through the registry (across all providers, including remote)
    and cache the resulting card. Returns the cache path, or None if unresolved.

    This is the align-time hook: after `set_alignment` records a remote URI, call
    this so the term is available offline. Prefers a full `describe` card; falls back
    to a `search` hit when the resolver has no term-detail endpoint (search already
    carries label + definition, which is enough to validate/export offline)."""
    card = registry.describe(ref)
    if card and card.get("iri", "").startswith("http"):
        return cache_resolved_term(
            root,
            card["iri"],
            label=card.get("label"),
            definition=card.get("definition"),
            synonyms=card.get("synonyms"),
            source=card.get("source"),
        )
    # fallback: find the term among search hits by exact iri/prefixed match
    tail = ref.split(":", 1)[-1]
    for hit in registry.search(tail, limit=25):
        if hit.iri == ref or hit.prefixed == ref or (
            hit.iri.startswith("http") and hit.iri.rsplit("/", 1)[-1].endswith(tail)
        ):
            if hit.iri.startswith("http"):
                return cache_resolved_term(
                    root,
                    hit.iri,
                    label=hit.label,
                    definition=hit.definition,
                    source=hit.source,
                )
    return None
