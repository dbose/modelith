"""Ontology term registry (spec §3, generalised).

FIBO is only *one example* of an industry-layer ontology. Terms come from two kinds
of source, resolved through one facade:

- **local file vocabularies** — any RDF/OWL/Turtle declared in `mdl-project.yaml`
  under `ontology_stack` (ACORD, FHIR, ISO 20022, a customer's own private ontology);
- **remote enterprise registries / catalogs** — OLS, OntoPortal, Collibra, queried
  over REST (added in later phases).

`OntologyRegistry` is a facade over these `SourceProvider`s: it fans `search` and
`list_ontologies` across them and merges the results, and routes `describe`/`expand`/
`resolves` to the owning provider. A caller never cares whether a term lives in the
repo or an enterprise catalog.

Declared in `mdl-project.yaml`, e.g.:

    ontology_stack:
      - type: local                       # default; a file vocabulary
        name: fibo
        layer: industry
        format: turtle
        path: ontologies/industry/fibo/2024.03
        modules: [fnd, fbc, sec]           # optional; load only these submodules
        prefixes:
          fibo-fnd-pty-pty: "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/"
      - type: ols                          # a remote registry (phase 2)
        name: ols
        layer: industry
        url: https://www.ebi.ac.uk/ols4/api
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Re-exported for backward compatibility with existing callers/imports.
from mdl_ontology.providers.base import OntologyRef, ResolvedTerm, SourceProvider, TermCard
from mdl_ontology.providers.local import LocalFileProvider

__all__ = [
    "VocabularySource",
    "ResolvedTerm",
    "OntologyRef",
    "TermCard",
    "SourceProvider",
    "OntologyRegistry",
    "build_registry",
]


@dataclass
class VocabularySource:
    """A declared ontology source. `type` selects the provider (default `local`);
    remote types (ols, ontoportal, collibra) carry `url`/auth fields used later."""

    name: str
    layer: str  # industry | core | domain | specialised
    type: str = "local"
    prefixes: dict[str, str] = field(default_factory=dict)
    path: str | None = None  # dir or file, relative to project root
    format: str = "turtle"
    modules: list[str] | None = None  # submodule filter (e.g. FIBO fnd/fbc); None = all
    # Remote-source fields (unused by the local provider; consumed in later phases).
    url: str | None = None
    apikey_env: str | None = None
    token_env: str | None = None
    ontologies: list[str] | None = None  # scope a remote source to these vocab ids
    domain_types: list[str] | None = None  # Collibra: which domain types are ontologies
    attributes: dict[str, str] | None = None  # Collibra: attr-name map (URI/Definition)

    @classmethod
    def from_config(cls, entry: dict) -> VocabularySource:
        return cls(
            name=str(entry["name"]),
            layer=str(entry.get("layer", "industry")),
            type=str(entry.get("type", "local")),
            prefixes=dict(entry.get("prefixes") or {}),
            path=entry.get("path"),
            format=str(entry.get("format", "turtle")),
            modules=list(entry["modules"]) if entry.get("modules") else None,
            url=entry.get("url"),
            apikey_env=entry.get("apikey_env"),
            token_env=entry.get("token_env"),
            ontologies=list(entry["ontologies"]) if entry.get("ontologies") else None,
            domain_types=list(entry["domain_types"]) if entry.get("domain_types") else None,
            attributes=dict(entry["attributes"]) if entry.get("attributes") else None,
        )


class OntologyRegistry:
    """Facade over the configured term-source providers."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.sources: dict[str, VocabularySource] = {}
        self.providers: list[SourceProvider] = []
        self._local: dict[str, LocalFileProvider] = {}
        # prefix -> namespace IRI, merged across local sources (for expand/prefixed).
        self.prefixes: dict[str, str] = {}

    def register(self, source: VocabularySource) -> None:
        self.sources[source.name] = source
        if source.type == "local":
            prov = self._local.get(source.name)
            if prov is None:
                prov = LocalFileProvider(source.name, source.layer, self.root)
                self._local[source.name] = prov
                self.providers.append(prov)
            prov.bind_prefixes(source.prefixes)
            prov.add_files(source.path, source.format, source.modules)
            for pfx, ns in source.prefixes.items():
                self.prefixes[pfx] = ns
        # Remote provider construction is wired in phase 2 (ols/ontoportal/collibra).

    def register_lock_layers(self, lock) -> None:
        """Register each lock-pinned ontology layer as a local provider over its
        fetched cache dir (`.mdl/ontology-cache/<layer>/`), so a lock-based layer
        browses/validates exactly like a committed one — offline, from the cache
        the lock pins (spec §3). Layers whose cache is not yet fetched simply
        contribute nothing until `mdl ontology fetch` runs."""
        from mdl_ontology.lock import CACHE_REL

        for layer_name, pin in getattr(lock, "ontology_layers", {}).items():
            # A committed `ontology_stack` entry of the same name wins — don't
            # double-register the same cache onto it (that double-loads the graph).
            if layer_name in self.sources:
                continue
            cache_sub = f"{CACHE_REL}/{layer_name}"
            if not (self.root / cache_sub).exists():
                continue
            src = VocabularySource(
                name=layer_name,
                layer=layer_name,
                type="local",
                path=cache_sub,
                format=pin.fmt,
                prefixes=dict(getattr(pin, "prefixes", {}) or {}),
            )
            self.register(src)

    def load(self) -> list[str]:
        """Materialise local vocabularies. Returns the loaded file paths. Remote
        providers have nothing to load (they query on demand)."""
        loaded: list[str] = []
        for prov in self._local.values():
            loaded.extend(prov.load())
        return loaded

    def loaded_term_count(self) -> int:
        """Number of distinct terms across local file vocabularies (browse stat)."""
        subjects: set[str] = set()
        for prov in self._local.values():
            subjects.update(str(s) for s in prov.graph.subjects())
        return len(subjects)

    # --- fan-out ------------------------------------------------------------

    def list_ontologies(self) -> list[OntologyRef]:
        refs: list[OntologyRef] = []
        for prov in self.providers:
            try:
                refs.extend(prov.list_ontologies())
            except Exception:  # noqa: BLE001 - one bad source must not break browse
                continue
        return refs

    def search(
        self, query: str, *, within: str | None = None, limit: int = 10
    ) -> list[ResolvedTerm]:
        """Merge ranked hits across providers. `within` scopes to one ontology id."""
        merged: list[ResolvedTerm] = []
        seen: set[str] = set()
        for prov in self.providers:
            try:
                hits = prov.search(query, within=within, limit=limit)
            except Exception:  # noqa: BLE001 - a failing remote source is skipped
                continue
            for t in hits:
                if t.iri in seen:
                    continue
                seen.add(t.iri)
                merged.append(t)
        # Providers already rank internally; keep first-seen order but cap.
        return merged[:limit]

    def describe(self, ref: str) -> dict | None:
        for prov in self.providers:
            try:
                card = prov.describe(ref)
            except Exception:  # noqa: BLE001
                continue
            if card is not None:
                return card.to_dict()
        return None

    def expand(self, prefixed: str) -> str | None:
        if ":" not in prefixed:
            return None
        pfx, tail = prefixed.split(":", 1)
        ns = self.prefixes.get(pfx)
        return ns + tail if ns is not None else None

    def resolves(self, prefixed: str) -> bool:
        # A source resolves it if any provider vouches for it.
        any_local = bool(self._local)
        for prov in self.providers:
            try:
                if prov.resolves(prefixed):
                    return True
            except Exception:  # noqa: BLE001
                continue
        # No provider resolved it. If there are no local files at all, degrade
        # gracefully for a well-formed prefix (matches the original empty-graph rule).
        if not any_local and self.expand(prefixed) is not None:
            return True
        return False


def build_registry(
    project_root: Path, ontology_stack: list[dict], lock=None
) -> OntologyRegistry:
    reg = OntologyRegistry(project_root)
    for entry in ontology_stack or []:
        if isinstance(entry, dict):
            reg.register(VocabularySource.from_config(entry))
    # Lock-pinned layers (spec §3) browse from the gitignored fetch cache.
    if lock is None:
        from mdl_ontology.lock import Lock

        try:
            lock = Lock.load(project_root)
        except Exception:  # noqa: BLE001 - a missing/broken lock must not break browse
            lock = None
    if lock is not None and getattr(lock, "ontology_layers", None):
        reg.register_lock_layers(lock)
    return reg
