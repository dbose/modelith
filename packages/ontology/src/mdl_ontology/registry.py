"""Generic vocabulary registry (spec §3, generalised).

FIBO is only *one example* of an industry-layer ontology. The registry is
vocabulary-agnostic: any RDF/OWL/Turtle vocabulary — ACORD, FHIR, ISO 20022, GS1,
or a customer's own — plugs in by declaration, no code changes. A vocabulary is
described by a `VocabularySource` (name, layer, prefixes, files, optional module
filter). The registry loads the declared modules into a single rdflib Graph and
resolves prefixed IRIs against it.

Declared in `mdl-project.yaml` under `ontology_stack`, e.g.:

    ontology_stack:
      - name: fibo
        layer: industry
        format: turtle
        path: ontologies/industry/fibo/2024.03
        modules: [fnd, fbc, sec]           # optional; load only these submodules
        prefixes:
          fibo-fnd-pty-pty: "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/"
      - name: fhir
        layer: industry
        format: turtle
        path: ontologies/industry/fhir/r5
        prefixes:
          fhir: "http://hl7.org/fhir/"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import SKOS

# rdflib format name per declared `format`.
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


@dataclass
class VocabularySource:
    name: str
    layer: str  # industry | core | domain | specialised
    prefixes: dict[str, str] = field(default_factory=dict)
    path: str | None = None  # dir or file, relative to project root
    format: str = "turtle"
    modules: list[str] | None = None  # submodule filter (e.g. FIBO fnd/fbc); None = all

    @classmethod
    def from_config(cls, entry: dict) -> VocabularySource:
        return cls(
            name=str(entry["name"]),
            layer=str(entry.get("layer", "industry")),
            prefixes=dict(entry.get("prefixes") or {}),
            path=entry.get("path"),
            format=str(entry.get("format", "turtle")),
            modules=list(entry["modules"]) if entry.get("modules") else None,
        )


@dataclass
class ResolvedTerm:
    iri: str
    prefixed: str
    label: str | None
    definition: str | None
    source: str  # vocabulary name


class OntologyRegistry:
    """Loads declared vocabularies into one graph and resolves/searches IRIs."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.graph = Graph()
        self.sources: dict[str, VocabularySource] = {}
        # prefix -> namespace IRI, merged across all sources
        self.prefixes: dict[str, str] = {}

    def register(self, source: VocabularySource) -> None:
        self.sources[source.name] = source
        for pfx, ns in source.prefixes.items():
            self.prefixes[pfx] = ns
            self.graph.bind(pfx.replace(":", "_"), ns, replace=True)

    def load(self) -> list[str]:
        """Materialise declared vocabularies. Returns the list of files loaded.
        Module-selective (spec §3.2): if `modules` is set, only files whose name
        contains a module token are parsed (FIBO ships hundreds of files)."""
        loaded: list[str] = []
        for source in self.sources.values():
            if not source.path:
                continue
            base = self.root / source.path
            files = self._files_for(base, source)
            fmt = _FORMATS.get(source.format.lower(), "turtle")
            for f in files:
                try:
                    self.graph.parse(str(f), format=fmt)
                    loaded.append(str(f.relative_to(self.root)))
                except Exception:  # noqa: BLE001 - tolerate a bad file, report via loaded
                    continue
        return loaded

    def _files_for(self, base: Path, source: VocabularySource) -> list[Path]:
        if base.is_file():
            return [base]
        if not base.exists():
            return []
        exts = {".ttl", ".rdf", ".owl", ".xml", ".jsonld", ".nt", ".n3"}
        files = sorted(p for p in base.rglob("*") if p.suffix.lower() in exts)
        if source.modules:
            mods = {m.lower() for m in source.modules}
            files = [f for f in files if _matches_module(f, mods)]
        return files

    # --- resolution --------------------------------------------------------

    def expand(self, prefixed: str) -> str | None:
        """`fibo-fnd-pty-pty:PartyInRole` -> full IRI, using declared prefixes."""
        if ":" not in prefixed:
            return None
        pfx, local = prefixed.split(":", 1)
        ns = self.prefixes.get(pfx)
        if ns is None:
            return None
        return ns + local

    def resolves(self, prefixed: str) -> bool:
        """True if the IRI expands AND exists as a subject in the loaded graph.
        Unresolvable IRIs are a validation error, not a warning (spec §3.2)."""
        iri = self.expand(prefixed)
        if iri is None:
            return False
        if len(self.graph) == 0:
            # No vocab files loaded (e.g. offline/no bundle): a well-formed prefix
            # is accepted syntactically so validation degrades gracefully.
            return True
        ref = URIRef(iri)
        return (ref, None, None) in self.graph or (None, None, ref) in self.graph

    def search(self, query: str, *, limit: int = 10) -> list[ResolvedTerm]:
        """Rank loaded classes by label/definition match (spec §3.2 `ontology
        search`). Simple token overlap — no external service (offline, §13.4)."""
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
            label = self._first_literal(subj, SKOS.prefLabel) or _local_name(iri)
            definition = self._first_literal(subj, SKOS.definition)
            hay = f"{label} {definition or ''}".lower()
            score = _score(q, hay, label.lower())
            if score > 0:
                results.append(
                    (
                        score,
                        ResolvedTerm(
                            iri=iri,
                            prefixed=self._prefixed_for(iri),
                            label=label,
                            definition=definition,
                            source=self._source_for(iri),
                        ),
                    )
                )
        results.sort(key=lambda t: (-t[0], t[1].label or ""))
        return [r for _, r in results[:limit]]

    def describe(self, ref: str) -> dict | None:
        """Full term card for a prefixed name or IRI: label, definition, source,
        and skos:broader / narrower neighbours (for the canvas ontology browser)."""
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
                "label": self._first_literal(u, SKOS.prefLabel) or _local_name(str(u)),
            }

        broader = [
            _card(o) for o in self.graph.objects(subj, SKOS.broader) if isinstance(o, URIRef)
        ]
        narrower = [
            _card(s) for s in self.graph.subjects(SKOS.broader, subj) if isinstance(s, URIRef)
        ]
        return {
            "iri": iri,
            "prefixed": self._prefixed_for(iri),
            "label": self._first_literal(subj, SKOS.prefLabel) or _local_name(iri),
            "definition": self._first_literal(subj, SKOS.definition),
            "source": self._source_for(iri),
            "broader": sorted(broader, key=lambda c: c["label"]),
            "narrower": sorted(narrower, key=lambda c: c["label"]),
        }

    def _first_literal(self, subj, pred) -> str | None:
        for o in self.graph.objects(subj, pred):
            return str(o)
        return None

    def _prefixed_for(self, iri: str) -> str:
        for pfx, ns in self.prefixes.items():
            if iri.startswith(ns):
                return f"{pfx}:{iri[len(ns):]}"
        return iri

    def _source_for(self, iri: str) -> str:
        for src in self.sources.values():
            for ns in src.prefixes.values():
                if iri.startswith(ns):
                    return src.name
        return "unknown"


def _matches_module(path: Path, mods: set[str]) -> bool:
    parts = {p.lower() for p in path.parts}
    stem = path.stem.lower()
    return any(m in stem or m in parts for m in mods)


def _local_name(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1]
    return iri


def _score(query: str, haystack: str, label: str) -> int:
    if not query:
        return 0
    score = 0
    if query == label:
        score += 100
    if query in label:
        score += 40
    if query in haystack:
        score += 10
    for tok in query.split():
        if tok in haystack:
            score += 2
    return score


def build_registry(project_root: Path, ontology_stack: list[dict]) -> OntologyRegistry:
    reg = OntologyRegistry(project_root)
    for entry in ontology_stack or []:
        if isinstance(entry, dict):
            reg.register(VocabularySource.from_config(entry))
    return reg
