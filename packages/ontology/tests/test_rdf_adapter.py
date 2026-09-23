"""The RDF backend adapter (`mdl_ontology._rdf`) and its containment boundary.

Two things are pinned here:

1. **Boundary guard** — the RDF backend (`pyoxigraph`, or `rdflib`) is imported in exactly
   ONE file, `_rdf.py`. Any other module importing it fails this test. This is what makes
   the backend swappable in a single file; it must not erode.
2. **Interface parity** — the small rdflib-shaped surface the rest of the package relies on
   (terms, namespaces, build, serialize, parse, query, typed literals, remote SPARQL)
   behaves as the consumers expect, independent of the backend.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from mdl_ontology import _rdf
from mdl_ontology._rdf import (
    IRI,
    OWL,
    RDF,
    RDFS,
    SH,
    SKOS,
    XSD,
    BlankNode,
    Graph,
    Lit,
    Namespace,
    is_blank,
    is_iri,
    is_literal,
    txt,
)

_ONTOLOGY_SRC = Path(_rdf.__file__).parent
_BACKENDS = ("pyoxigraph", "rdflib")


# --- 1. Boundary guard --------------------------------------------------------------


def _imports_backend(py_file: Path) -> bool:
    """True if the module imports a backend RDF library at any import statement."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in _BACKENDS:
                    return True
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in _BACKENDS:
                return True
    return False


def test_backend_imported_only_in_adapter():
    """`import pyoxigraph` / `import rdflib` must appear in _rdf.py ONLY — nowhere else in
    the ontology package. Enforces the one-seam design so a backend swap is a single-file
    change."""
    offenders = [
        str(p.relative_to(_ONTOLOGY_SRC))
        for p in _ONTOLOGY_SRC.rglob("*.py")
        if p.name != "_rdf.py" and _imports_backend(p)
    ]
    assert not offenders, (
        "RDF backend imported outside the _rdf.py adapter: "
        + ", ".join(offenders)
        + " — route it through mdl_ontology._rdf so the backend stays swappable."
    )


def test_adapter_is_the_seam():
    """Sanity: the adapter itself DOES import the backend (guards against the guard above
    silently passing because the backend moved out entirely)."""
    assert _imports_backend(_ONTOLOGY_SRC / "_rdf.py")


# --- 2. Interface parity ------------------------------------------------------------


def test_namespace_ergonomics():
    ns = Namespace("https://x.example/ns#")
    assert txt(ns.Term) == "https://x.example/ns#Term"
    assert txt(ns["Term"]) == "https://x.example/ns#Term"
    assert str(ns) == "https://x.example/ns#"  # str(NS) is the base, for concatenation


def test_prebuilt_namespaces_are_correct_iris():
    assert txt(RDF.type) == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    assert txt(RDFS.label) == "http://www.w3.org/2000/01/rdf-schema#label"
    assert txt(OWL.Class) == "http://www.w3.org/2002/07/owl#Class"
    assert txt(SKOS.prefLabel) == "http://www.w3.org/2004/02/skos/core#prefLabel"
    assert txt(SH.NodeShape) == "http://www.w3.org/ns/shacl#NodeShape"
    assert txt(XSD.string) == "http://www.w3.org/2001/XMLSchema#string"


def test_term_type_predicates():
    assert is_iri(IRI("https://x/1"))
    assert not is_iri(Lit("x"))
    assert is_literal(Lit("x"))
    assert not is_literal(IRI("https://x/1"))
    assert is_blank(BlankNode())
    assert not is_blank(IRI("https://x/1"))


def test_txt_returns_lexical_value_not_ntriples():
    # The trap this guards: pyoxigraph str() adds <...>/"..." decoration; txt() must not.
    assert txt(IRI("https://x/1")) == "https://x/1"
    assert txt(Lit("Party")) == "Party"


def test_typed_literals_match_rdflib_inference():
    """Lit infers xsd datatypes from Python types as rdflib does — load-bearing for SHACL
    minCount (xsd:integer) and boolean flags."""
    g = Graph()
    s = IRI("https://x/1")
    g.add((s, SH.minCount, Lit(1)))
    g.add((s, IRI("https://x/flag"), Lit(True)))
    nt = g.serialize("nt")
    assert '"1"^^<http://www.w3.org/2001/XMLSchema#integer>' in nt
    assert '"true"^^<http://www.w3.org/2001/XMLSchema#boolean>' in nt
    # a plain string stays untyped
    g2 = Graph()
    g2.add((s, RDFS.label, Lit("Party")))
    assert '"Party"' in g2.serialize("nt")
    assert "XMLSchema#string" not in g2.serialize("nt")  # not spuriously typed


@pytest.mark.parametrize("fmt", ["turtle", "ttl", "xml", "json-ld", "jsonld", "nt", "n3"])
def test_serialize_every_format(fmt):
    g = Graph()
    g.bind("skos", SKOS)
    g.add((IRI("https://x/1"), SKOS.prefLabel, Lit("Party")))
    out = g.serialize(fmt)
    assert isinstance(out, str) and out.strip()


def test_serialize_binds_prefixes():
    g = Graph()
    g.bind("skos", SKOS)
    g.add((IRI("https://x/1"), SKOS.prefLabel, Lit("Party")))
    assert "@prefix skos:" in g.serialize("turtle")


def test_parse_roundtrip_data_and_source(tmp_path):
    ttl = (
        '@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n'
        '<https://x/1> skos:prefLabel "Party" .\n'
    )
    # from inline data
    g = Graph().parse(data=ttl, format="turtle")
    assert len(g) == 1
    # from a file path (positional, rdflib style)
    p = tmp_path / "v.ttl"
    p.write_text(ttl, encoding="utf-8")
    g2 = Graph().parse(str(p), format="turtle")
    assert len(g2) == 1


def test_query_api():
    g = Graph()
    s1, s2 = IRI("https://x/1"), IRI("https://x/2")
    g.add((s1, RDF.type, OWL.Class))
    g.add((s1, SKOS.prefLabel, Lit("One")))
    g.add((s2, RDF.type, OWL.Class))
    # subjects(predicate, object)
    classes = {txt(x) for x in g.subjects(predicate=RDF.type, object=OWL.Class)}
    assert classes == {"https://x/1", "https://x/2"}
    # objects(subject, predicate)
    assert [txt(o) for o in g.objects(s1, SKOS.prefLabel)] == ["One"]
    # triples(pattern) and membership
    assert list(g.triples((s1, SKOS.prefLabel, None)))
    assert (s1, RDF.type, None) in g
    assert (IRI("https://x/nope"), None, None) not in g
    assert len(g) == 3


def test_sparql_construct_uses_httpx(monkeypatch):
    """Remote SPARQL is an httpx CONSTRUCT parsed back into a graph — no rdflib SPARQLStore.
    Mock the POST so no network is touched."""
    ttl = (
        '@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n'
        '<https://x/1> skos:prefLabel "Remote" .\n'
    )

    class _Resp:
        content = ttl.encode("utf-8")

        def raise_for_status(self):
            return None

    calls = {}

    def _fake_post(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)
    g = _rdf.sparql_construct("https://endpoint.example/sparql", timeout=5.0)
    assert len(g) == 1
    assert calls["url"] == "https://endpoint.example/sparql"
    assert "CONSTRUCT" in calls["kwargs"]["data"]["query"]
    assert calls["kwargs"]["headers"]["Accept"] == "text/turtle"
