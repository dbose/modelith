"""Local-file vocabulary provider.

Loads declared RDF/OWL/Turtle files into one rdflib graph and searches/browses them.
This is the original `OntologyRegistry` graph behaviour, extracted behind the provider
contract and enriched to understand the annotations real ontologies actually use:
`rdfs:label` (FIBO/OWL) alongside `skos:prefLabel`, `skos:altLabel` synonyms,
`rdfs:comment` alongside `skos:definition`, and a class hierarchy drawn from BOTH
`skos:broader` and `rdfs:subClassOf`.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDFS, SKOS

from mdl_ontology.providers.base import (
    OntologyRef,
    ResolvedTerm,
    TermCard,
    local_name,
    score,
)

_FORMATS = {
    "turtle": "turtle",
    "ttl": "turtle",
    "xml": "xml",
    "owl": "xml",
    "rdfxml": "xml",
    "jsonld": "json-ld",
    "nt": "nt",
    "n3": "n3",
}

# Predicates that carry a human label, best first.
_LABEL_PREDS = (SKOS.prefLabel, RDFS.label)
# Predicates that carry a definition/description.
_DEF_PREDS = (SKOS.definition, RDFS.comment)
# Predicates that carry synonyms / alternate labels.
_SYNONYM_PREDS = (SKOS.altLabel,)
# Predicates that make one term broader than / a superclass of another (child -> parent).
_PARENT_PREDS = (SKOS.broader, RDFS.subClassOf)


class LocalFileProvider:
    """A vocabulary loaded from files on disk under the project root."""

    def __init__(self, name: str, layer: str, root: Path) -> None:
        self.name = name
        self.layer = layer
        self.root = Path(root)
        self.graph = Graph()
        self.prefixes: dict[str, str] = {}
        self._paths: list[tuple[str, str, list[str] | None]] = []  # (path, format, modules)

    def add_files(self, path: str | None, fmt: str, modules: list[str] | None) -> None:
        if path:
            self._paths.append((path, fmt, modules))

    def bind_prefixes(self, prefixes: dict[str, str]) -> None:
        for pfx, ns in prefixes.items():
            self.prefixes[pfx] = ns
            self.graph.bind(pfx.replace(":", "_"), ns, replace=True)

    def load(self) -> list[str]:
        loaded: list[str] = []
        for path, fmt, modules in self._paths:
            base = self.root / path
            for f in _files_for(base, modules):
                try:
                    self.graph.parse(str(f), format=_FORMATS.get(fmt.lower(), "turtle"))
                    loaded.append(str(f.relative_to(self.root)))
                except Exception:  # noqa: BLE001 - tolerate a bad file, report via loaded
                    continue
        return loaded

    # --- browse ------------------------------------------------------------

    def list_ontologies(self) -> list[OntologyRef]:
        """A local source exposes itself as one browsable vocabulary."""
        return [
            OntologyRef(
                id=self.name,
                name=self.name,
                namespace=next(iter(self.prefixes.values()), None),
                count=len(set(self.graph.subjects())) or None,
            )
        ]

    def search(
        self, query: str, *, within: str | None = None, limit: int = 20
    ) -> list[ResolvedTerm]:
        if within is not None and within != self.name:
            return []
        q = query.lower().strip()
        results: list[tuple[int, ResolvedTerm]] = []
        seen: set[str] = set()
        for subj in set(self.graph.subjects()):
            if not isinstance(subj, URIRef):
                continue
            iri = str(subj)
            if iri in seen:
                continue
            seen.add(iri)
            label = self._label(subj)
            definition = self._definition(subj)
            synonyms = self._synonyms(subj)
            hay = f"{label} {definition or ''} {' '.join(synonyms)}".lower()
            s = score(q, hay, label.lower())
            if s > 0:
                results.append(
                    (
                        s,
                        ResolvedTerm(
                            iri=iri,
                            prefixed=self._prefixed_for(iri),
                            label=label,
                            definition=definition,
                            source=self.name,
                        ),
                    )
                )
        results.sort(key=lambda t: (-t[0], t[1].label or ""))
        return [r for _, r in results[:limit]]

    def describe(self, ref: str) -> TermCard | None:
        iri = self.expand(ref) if not ref.startswith("http") else ref
        if iri is None:
            return None
        subj = URIRef(iri)
        if (subj, None, None) not in self.graph and (None, None, subj) not in self.graph:
            return None

        def _card(u: URIRef) -> dict:
            return {
                "iri": str(u),
                "prefixed": self._prefixed_for(str(u)),
                "label": self._label(u),
            }

        parents: dict[str, dict] = {}
        children: dict[str, dict] = {}
        for pred in _PARENT_PREDS:
            for o in self.graph.objects(subj, pred):
                if isinstance(o, URIRef):
                    parents[str(o)] = _card(o)
            for s in self.graph.subjects(pred, subj):
                if isinstance(s, URIRef):
                    children[str(s)] = _card(s)
        return TermCard(
            iri=iri,
            prefixed=self._prefixed_for(iri),
            label=self._label(subj),
            definition=self._definition(subj),
            source=self.name,
            synonyms=self._synonyms(subj),
            parents=sorted(parents.values(), key=lambda c: c["label"] or ""),
            children=sorted(children.values(), key=lambda c: c["label"] or ""),
        )

    # --- resolution --------------------------------------------------------

    def expand(self, prefixed: str) -> str | None:
        if ":" not in prefixed:
            return None
        pfx, tail = prefixed.split(":", 1)
        ns = self.prefixes.get(pfx)
        return ns + tail if ns is not None else None

    def resolves(self, prefixed: str) -> bool:
        iri = self.expand(prefixed)
        if iri is None:
            return False
        if len(self.graph) == 0:
            # No files loaded (offline / no bundle): accept a well-formed prefix so
            # validation degrades gracefully, matching the original behaviour.
            return True
        ref = URIRef(iri)
        return (ref, None, None) in self.graph or (None, None, ref) in self.graph

    # --- literal helpers ---------------------------------------------------

    def _first(self, subj, preds) -> str | None:
        for pred in preds:
            for o in self.graph.objects(subj, pred):
                return str(o)
        return None

    def _label(self, subj) -> str:
        return self._first(subj, _LABEL_PREDS) or local_name(str(subj))

    def _definition(self, subj) -> str | None:
        return self._first(subj, _DEF_PREDS)

    def _synonyms(self, subj) -> list[str]:
        out: list[str] = []
        for pred in _SYNONYM_PREDS:
            out.extend(str(o) for o in self.graph.objects(subj, pred))
        return sorted(set(out))

    def _prefixed_for(self, iri: str) -> str:
        for pfx, ns in self.prefixes.items():
            if iri.startswith(ns):
                return f"{pfx}:{iri[len(ns):]}"
        return iri


def _files_for(base: Path, modules: list[str] | None) -> list[Path]:
    if base.is_file():
        return [base]
    if not base.exists():
        return []
    exts = {".ttl", ".rdf", ".owl", ".xml", ".jsonld", ".nt", ".n3"}
    files = sorted(p for p in base.rglob("*") if p.suffix.lower() in exts)
    if modules:
        mods = {m.lower() for m in modules}
        files = [f for f in files if _matches_module(f, mods)]
    return files


def _matches_module(path: Path, mods: set[str]) -> bool:
    parts = {p.lower() for p in path.parts}
    stem = path.stem.lower()
    return any(m in stem or m in parts for m in mods)
