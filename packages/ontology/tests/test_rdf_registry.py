"""RDF/SHACL export + generic vocabulary registry tests (spec §3.2, §3.3)."""

from __future__ import annotations

from pathlib import Path

from mdl_ontology import build_registry, export_rdf, export_shacl, serialize
from mdl_ontology.registry import VocabularySource
from rdflib import Graph
from rdflib.namespace import SH, SKOS

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    LogicalEntity,
    Model,
    OntologyAlignment,
    ProjectConfig,
)

_A_VOCAB = """\
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix ex: <http://example.org/vocab/> .

ex:PartyInRole a skos:Concept ;
    skos:prefLabel "Party In Role" ;
    skos:definition "A party acting in some capacity." .
ex:Account a skos:Concept ;
    skos:prefLabel "Account" ;
    skos:definition "A financial account held by a party." .
"""


def _demo_model() -> Model:
    ce_id, le_id, a1, a2 = new_ulid(), new_ulid(), new_ulid(), new_ulid()
    m = Model(ProjectConfig(name="t"))
    m.add(
        ConceptualEntity(
            id=ce_id,
            name="Counterparty",
            definition="A legal person.",
            synonyms=["CPTY"],
            ontology=OntologyAlignment(
                layer="core", aligns_to="ex:PartyInRole", alignment="skos:exactMatch"
            ),
        )
    )
    m.add(
        LogicalEntity(
            id=le_id,
            name="counterparty",
            realises=ce_id,
            attributes=[
                Attribute(id=a1, name="counterparty_id", domain="bigint", role="business_key", nullable=False),
                Attribute(id=a2, name="opened_on", domain="date", nullable=True),
            ],
        )
    )
    return m


def test_rdf_export_has_skos_alignment(tmp_path: Path):
    # Prefixed IRIs need a registry to expand; declare the `ex` prefix.
    reg = build_registry(
        tmp_path, [{"name": "ex", "layer": "industry", "prefixes": {"ex": "http://example.org/vocab/"}}]
    )
    g = export_rdf(_demo_model(), layer="all", registry=reg)
    matches = list(g.triples((None, SKOS.exactMatch, None)))
    assert matches, "expected a skos:exactMatch triple"
    ttl = serialize(g, "turtle")
    assert "exactMatch" in ttl


def test_shacl_export_shapes():
    g = export_shacl(_demo_model())
    shapes = list(g.subjects(predicate=None, object=SH.NodeShape))
    assert len(shapes) == 1
    # non-null business key -> minCount 1 present somewhere
    mincounts = list(g.triples((None, SH.minCount, None)))
    assert mincounts
    # the shape graph is valid turtle
    Graph().parse(data=serialize(g, "turtle"), format="turtle")


def test_registry_loads_arbitrary_vocab(tmp_path: Path):
    # ANY vocabulary plugs in by declaration — FIBO is just one example.
    vocab_dir = tmp_path / "ontologies" / "industry" / "example"
    vocab_dir.mkdir(parents=True)
    (vocab_dir / "vocab.ttl").write_text(_A_VOCAB)

    reg = build_registry(
        tmp_path,
        [
            {
                "name": "example",
                "layer": "industry",
                "format": "turtle",
                "path": "ontologies/industry/example",
                "prefixes": {"ex": "http://example.org/vocab/"},
            }
        ],
    )
    loaded = reg.load()
    assert loaded, "vocab file should load"
    assert reg.resolves("ex:PartyInRole")
    assert not reg.resolves("ex:DoesNotExist")

    hits = reg.search("account")
    assert any(h.prefixed == "ex:Account" for h in hits)


def test_registry_from_config_shape():
    src = VocabularySource.from_config(
        {"name": "fhir", "layer": "industry", "prefixes": {"fhir": "http://hl7.org/fhir/"}}
    )
    assert src.name == "fhir" and src.layer == "industry"
    assert src.prefixes["fhir"] == "http://hl7.org/fhir/"
