"""Local-file provider: enriched browse over rdfs:label / subClassOf / altLabel.

The original registry only understood SKOS (prefLabel/definition/broader), so a real
FIBO/OWL vocabulary browsed with blank labels and no hierarchy. These tests use an
OWL-style fixture (rdfs:label, rdfs:subClassOf, skos:altLabel) to prove the provider
now surfaces labels, synonyms, definitions, and a subClassOf hierarchy.
"""

from __future__ import annotations

from mdl_ontology import build_registry

# An OWL-flavoured vocabulary the way FIBO actually ships: rdfs:label + rdfs:subClassOf
# for the hierarchy, skos:altLabel for synonyms, rdfs:comment for the definition.
_OWL = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix acme: <https://acme.com/ont/core/> .

acme:Party a owl:Class ;
    rdfs:label "Party" ;
    rdfs:comment "A person or organization that can enter agreements." .

acme:Counterparty a owl:Class ;
    rdfs:label "Counterparty" ;
    rdfs:subClassOf acme:Party ;
    skos:altLabel "Trading Partner" ;
    rdfs:comment "A party to a financial contract." .
"""


def _registry(tmp_path):
    (tmp_path / "core.ttl").write_text(_OWL)
    reg = build_registry(
        tmp_path,
        [
            {
                "type": "local",
                "name": "acme_core",
                "layer": "core",
                "path": "core.ttl",
                "prefixes": {"acme": "https://acme.com/ont/core/"},
            }
        ],
    )
    reg.load()
    return reg


def test_search_finds_by_rdfs_label(tmp_path):
    reg = _registry(tmp_path)
    hits = {t.label for t in reg.search("counterparty")}
    assert "Counterparty" in hits  # rdfs:label surfaced, not a blank/local-name


def test_search_finds_by_synonym(tmp_path):
    # "trading partner" is a skos:altLabel on Counterparty; search should hit it
    reg = _registry(tmp_path)
    labels = {t.label for t in reg.search("trading partner")}
    assert "Counterparty" in labels


def test_describe_surfaces_definition_and_synonyms(tmp_path):
    reg = _registry(tmp_path)
    card = reg.describe("acme:Counterparty")
    assert card is not None
    assert card["label"] == "Counterparty"
    assert card["definition"] == "A party to a financial contract."
    assert "Trading Partner" in card["synonyms"]


def test_describe_hierarchy_from_subclassof(tmp_path):
    # Counterparty rdfs:subClassOf Party -> Party is a parent; Counterparty a child of Party
    reg = _registry(tmp_path)
    child = reg.describe("acme:Counterparty")
    assert [p["label"] for p in child["parents"]] == ["Party"]
    parent = reg.describe("acme:Party")
    assert "Counterparty" in [c["label"] for c in parent["children"]]


def test_list_ontologies(tmp_path):
    reg = _registry(tmp_path)
    refs = reg.list_ontologies()
    assert any(r.name == "acme_core" for r in refs)
    assert refs[0].count and refs[0].count >= 2


def test_no_config_still_resolves_gracefully(tmp_path):
    # no vocab files at all: a well-formed prefix is accepted (offline degradation)
    reg = build_registry(tmp_path, [])
    assert reg.resolves("fibo-fnd:Thing") is False  # no prefix known -> cannot expand
