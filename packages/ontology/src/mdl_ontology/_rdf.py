"""The RDF backend adapter — the ONE seam between mdl_ontology and its RDF library.

Modelith's ontology stack builds, serializes, parses and queries RDF graphs. It used to
call rdflib directly, everywhere. That coupled the whole package to the `rdflib` PyPI
coordinate, which an enterprise package firewall may quarantine (by name — OSV maps an old
Debian CVE to it). The backend is now **pyoxigraph** (Rust/PyO3, zero Python transitive
deps, CVE-clean, a coordinate a name-matching scanner does not flag).

This module is the only place in the repo that imports the backend. Everything else in
`mdl_ontology` depends solely on the small, rdflib-shaped interface exported here:

    Terms:       IRI(str), BlankNode(), Lit(value, *, datatype=None, lang=None)
    Namespaces:  Namespace(base) with `.Term` / `["Term"]` -> IRI(base + term)
                 prebuilt: OWL RDF RDFS SH SKOS XSD  (fixed W3C IRIs, no backend needed)
    Graph:       g.add((s, p, o)); g.bind(prefix, ns); g.serialize(fmt) -> str;
                 g.parse(data=..., source=..., format=...) -> Graph;
                 g.subjects(pred=None, obj=None); g.objects(subj, pred);
                 g.triples((s, p, o)); iteration over (s, p, o) triples
    Remote:      sparql_construct(endpoint, query=None, *, timeout=120.0) -> Graph

Names are deliberately rdflib-shaped so the call sites read the same, but they resolve to
this adapter, not rdflib — so a future backend swap is a rewrite of THIS file alone. A
boundary guard test asserts `import pyoxigraph` / `import rdflib` appears here only.

Serialized byte-shape (prefix order, blank-node labels) may differ from rdflib; the graph
is semantically identical (same triple set). Tests assert semantic equivalence, not bytes.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import pyoxigraph as _ox

# --- Terms --------------------------------------------------------------------------

# The adapter's term types ARE pyoxigraph's — they already behave as value objects with a
# `.value` attribute and hashable identity, which is all the consumers need. Aliasing
# (rather than wrapping) keeps `g.add((s, p, o))` zero-overhead and lets Quad accept them
# directly. Consumers must not rely on any rdflib-only method; the interface above is the
# whole contract.
_NamedNode = _ox.NamedNode
_BlankNode = _ox.BlankNode
_Literal = _ox.Literal
_Quad = _ox.Quad

Term = _NamedNode | _BlankNode | _Literal


def IRI(value: str) -> _NamedNode:
    """An absolute IRI term (rdflib's URIRef)."""
    return _NamedNode(str(value))


def BlankNode() -> _BlankNode:
    """A fresh blank node (rdflib's BNode)."""
    return _BlankNode()


# XSD IRIs for Python-native literal typing, matching rdflib's automatic datatype
# inference (Literal(1) -> xsd:integer, Literal(True) -> xsd:boolean, Literal(1.5) ->
# xsd:double). SHACL minCount/maxCount rely on the integer type being present, so this
# parity is behavioral, not cosmetic.
_XSD = "http://www.w3.org/2001/XMLSchema#"
_XSD_INTEGER = _ox.NamedNode(_XSD + "integer")
_XSD_BOOLEAN = _ox.NamedNode(_XSD + "boolean")
_XSD_DOUBLE = _ox.NamedNode(_XSD + "double")


def Lit(value, *, datatype=None, lang: str | None = None) -> _Literal:
    """A literal (rdflib's Literal). An explicit `datatype` IRI or language `lang` is
    carried through; otherwise the datatype is inferred from the Python type exactly as
    rdflib does — bool -> xsd:boolean, int -> xsd:integer, float -> xsd:double — so a
    typed literal like SHACL's `sh:minCount 1` stays `xsd:integer`, not a plain string.
    str and everything else become a plain (untyped) literal, as rdflib's Literal("x")."""
    if lang is not None:
        return _Literal(str(value), language=lang)
    if datatype is not None:
        return _Literal(str(value), datatype=_as_named(datatype))
    # bool is a subclass of int — check it first, and lower-case for xsd:boolean lexicals.
    if isinstance(value, bool):
        return _Literal("true" if value else "false", datatype=_XSD_BOOLEAN)
    if isinstance(value, int):
        return _Literal(str(value), datatype=_XSD_INTEGER)
    if isinstance(value, float):
        return _Literal(repr(value), datatype=_XSD_DOUBLE)
    return _Literal(str(value))


def _as_named(x) -> _NamedNode:
    return x if isinstance(x, _NamedNode) else _NamedNode(str(x))


def txt(term) -> str:
    """The lexical value of a term (rdflib's `str(term)` semantics): the IRI for a
    NamedNode, the label for a BlankNode, the lexical string for a Literal — WITHOUT the
    N-Triples decoration (`<...>` / `"..."`) that pyoxigraph's own `str()` adds. Consumers
    read term text through this, so the backend's stringification stays hidden."""
    v = getattr(term, "value", None)
    return v if v is not None else str(term)


def is_iri(term) -> bool:
    """True if `term` is an IRI/named-node (rdflib's `isinstance(x, URIRef)`). Consumers
    use this instead of isinstance against a backend type, so the backend stays hidden."""
    return isinstance(term, _NamedNode)


def is_literal(term) -> bool:
    """True if `term` is a literal (rdflib's `isinstance(x, Literal)`)."""
    return isinstance(term, _Literal)


def is_blank(term) -> bool:
    """True if `term` is a blank node (rdflib's `isinstance(x, BNode)`)."""
    return isinstance(term, _BlankNode)


# --- Namespaces ---------------------------------------------------------------------


class Namespace:
    """A prefix namespace: `NS.Term` or `NS["Term"]` -> IRI(base + "Term").

    Reproduces rdflib.Namespace ergonomics. `str(NS)` is the base IRI, so existing code
    like `str(MDL) + f"shape/{id}"` keeps working unchanged.
    """

    __slots__ = ("_base",)

    def __init__(self, base: str) -> None:
        self._base = str(base)

    def __getattr__(self, name: str) -> _NamedNode:
        if name.startswith("_"):
            raise AttributeError(name)
        return _NamedNode(self._base + name)

    def __getitem__(self, name: str) -> _NamedNode:
        return _NamedNode(self._base + name)

    def __str__(self) -> str:
        return self._base

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Namespace({self._base!r})"


# Prebuilt standard namespaces — fixed W3C IRIs, defined without touching the backend.
RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
SH = Namespace("http://www.w3.org/ns/shacl#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")


# --- Formats ------------------------------------------------------------------------

# rdflib-style format strings (as the call sites pass them) -> pyoxigraph RdfFormat.
_FORMATS = {
    "turtle": _ox.RdfFormat.TURTLE,
    "ttl": _ox.RdfFormat.TURTLE,
    "xml": _ox.RdfFormat.RDF_XML,
    "rdfxml": _ox.RdfFormat.RDF_XML,
    "owl": _ox.RdfFormat.RDF_XML,
    "json-ld": _ox.RdfFormat.JSON_LD,
    "jsonld": _ox.RdfFormat.JSON_LD,
    "nt": _ox.RdfFormat.N_TRIPLES,
    "ntriples": _ox.RdfFormat.N_TRIPLES,
    "n3": _ox.RdfFormat.N3,
    "nquads": _ox.RdfFormat.N_QUADS,
    "trig": _ox.RdfFormat.TRIG,
}


def _fmt(name: str) -> _ox.RdfFormat:
    key = (name or "turtle").lower()
    if key not in _FORMATS:
        raise ValueError(f"unsupported RDF format {name!r}")
    return _FORMATS[key]


# --- Graph --------------------------------------------------------------------------


class Graph:
    """An in-memory RDF graph with the small slice of rdflib's Graph API the ontology
    stack uses, backed by a pyoxigraph Store (default graph only).

    `.add`, `.bind`, `.serialize`, `.parse`, `.subjects`, `.objects`, `.triples`, and
    iteration. Not a general rdflib Graph — just what mdl_ontology needs.
    """

    def __init__(self) -> None:
        self._store = _ox.Store()
        self._prefixes: dict[str, str] = {}

    # -- build --
    def add(self, triple: tuple[Term, Term, Term]) -> None:
        s, p, o = triple
        self._store.add(_Quad(s, p, o))

    def bind(self, prefix: str, namespace, *, replace: bool = False) -> None:
        # rdflib's bind(prefix, ns[, replace]) — record the prefix for serialization.
        self._prefixes[str(prefix)] = str(namespace)

    # -- serialize / parse --
    def serialize(self, fmt: str = "turtle", *, format: str | None = None) -> str:
        # Accept both the positional `fmt` and rdflib's `format=` keyword, so existing
        # call sites (g.serialize(format="turtle")) work unchanged.
        out = _ox.serialize(
            self._store,
            format=_fmt(format if format is not None else fmt),
            prefixes=self._prefixes or None,
        )
        return out.decode("utf-8") if isinstance(out, (bytes, bytearray)) else out

    def parse(
        self,
        source: str | None = None,
        *,
        data: str | bytes | None = None,
        format: str = "turtle",
    ) -> Graph:
        """Load RDF into this graph. Accepts a file PATH as the first positional arg
        (rdflib's `g.parse(str(path), format=...)`) or inline `data=`
        (rdflib's `g.parse(data=content, format=...)`). Returns self."""
        rfmt = _fmt(format)
        if data is not None:
            self._store.load(data, format=rfmt)
        elif source is not None:
            with open(source, "rb") as fh:
                self._store.load(fh.read(), format=rfmt)
        else:
            raise ValueError("parse() needs either a source path or data=")
        return self

    # -- query (the provider/ingest read API) --
    def __iter__(self) -> Iterator[tuple[Term, Term, Term]]:
        for q in self._store.quads_for_pattern(None, None, None, None):
            yield (q.subject, q.predicate, q.object)

    def __len__(self) -> int:
        return sum(1 for _ in self._store.quads_for_pattern(None, None, None, None))

    def __contains__(self, pattern: tuple[Term | None, Term | None, Term | None]) -> bool:
        # rdflib supports `(s, p, o) in graph` with None as a wildcard — used by the
        # providers to test "does this subject appear anywhere?".
        s, p, o = pattern
        for _ in self._store.quads_for_pattern(s, p, o, None):
            return True
        return False

    # Query methods use rdflib's exact parameter names (subject/predicate/object) so call
    # sites — including `g.subjects(predicate=..., object=...)` — read identically.
    def triples(
        self, pattern: tuple[Term | None, Term | None, Term | None]
    ) -> Iterator[tuple[Term, Term, Term]]:
        s, p, o = pattern
        for q in self._store.quads_for_pattern(s, p, o, None):
            yield (q.subject, q.predicate, q.object)

    def subjects(
        self, predicate: Term | None = None, object: Term | None = None
    ) -> Iterator[Term]:
        for q in self._store.quads_for_pattern(None, predicate, object, None):
            yield q.subject

    def objects(
        self, subject: Term | None = None, predicate: Term | None = None
    ) -> Iterator[Term]:
        for q in self._store.quads_for_pattern(subject, predicate, None, None):
            yield q.object

    def predicates(
        self, subject: Term | None = None, object: Term | None = None
    ) -> Iterator[Term]:
        for q in self._store.quads_for_pattern(subject, None, object, None):
            yield q.predicate

    def add_all(self, triples: Iterable[tuple[Term, Term, Term]]) -> None:
        for t in triples:
            self.add(t)


# --- Remote SPARQL ------------------------------------------------------------------

_DEFAULT_CONSTRUCT = "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"


def sparql_construct(
    endpoint: str, query: str | None = None, *, timeout: float = 120.0
) -> Graph:
    """CONSTRUCT the full graph from a remote SPARQL endpoint into an adapter Graph.

    Replaces rdflib's SPARQLStore remote client (pyoxigraph's store is local-only): POST
    the query to the endpoint asking for Turtle, then parse the response. Kept inside the
    adapter so no transport detail leaks into fetch.py — a backend swap changes only here.
    """
    import httpx

    q = query or _DEFAULT_CONSTRUCT
    resp = httpx.post(
        endpoint,
        data={"query": q},
        headers={"Accept": "text/turtle"},
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    g = Graph()
    g.parse(data=resp.content, format="turtle")
    return g
