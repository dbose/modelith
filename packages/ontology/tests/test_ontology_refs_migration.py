"""ontology_refs migration + native-shape parity (spec §1).

Proves the legacy single-`ontology` alignment and the new `ontology_refs` list are
interchangeable for validation: they migrate to the same in-memory shape and fire the
same diagnostics. Also covers multi-ref behaviour the old single-object shape could
not express.
"""

from __future__ import annotations

from mdl_ontology.layers import check_layers, coverage_report

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    ConceptualEntity,
    Model,
    OntologyAlignment,
    OntologyRef,
    ProjectConfig,
    Term,
)


def _model(*objs) -> Model:
    m = Model(ProjectConfig(name="t"))
    for o in objs:
        m.add(o)
    return m


# --- legacy -> ontology_refs migration -------------------------------------


def test_legacy_alignment_migrates_to_refs():
    ce = ConceptualEntity(
        id=new_ulid(),
        name="Counterparty",
        ontology=OntologyAlignment(
            layer="core", aligns_to="fibo:PartyInRole", alignment="skos:exactMatch",
            status="accepted",
        ),
    )
    assert ce.ontology_layer == "core"
    assert len(ce.ontology_refs) == 1
    ref = ce.ontology_refs[0]
    assert ref.uri == "fibo:PartyInRole"
    assert ref.predicate == "skos:exactMatch"
    assert ref.layer == "core"
    assert ref.status == "accepted"
    # convenience accessors
    assert ce.aligned_uri == "fibo:PartyInRole"
    assert ce.primary_ref is ref


def test_legacy_exempt_migrates_object_level():
    ce = ConceptualEntity(
        id=new_ulid(),
        name="Widget",
        ontology=OntologyAlignment(
            layer="core", no_industry_equivalent=True, rationale="bespoke"
        ),
    )
    assert ce.ontology_layer == "core"
    assert ce.no_industry_equivalent is True
    assert ce.rationale == "bespoke"
    assert ce.ontology_refs == []


def test_native_and_legacy_fire_same_coverage_error():
    legacy = _model(
        ConceptualEntity(
            id=new_ulid(), name="A", ontology=OntologyAlignment(layer="core")
        )
    )
    native = _model(
        ConceptualEntity(id=new_ulid(), name="A", ontology_layer="core")
    )
    assert "MDL-E202" in {d.code for d in check_layers(legacy).items}
    assert "MDL-E202" in {d.code for d in check_layers(native).items}


def test_native_refs_satisfy_coverage():
    ce = ConceptualEntity(
        id=new_ulid(),
        name="Counterparty",
        ontology_layer="core",
        ontology_refs=[
            OntologyRef(
                predicate="skos:closeMatch",
                uri="fibo:PartyInRole",
                resolved_via="ols4",
                resolved_at="2026-08-29",
                layer="industry",
            )
        ],
    )
    diags = check_layers(_model(ce))
    assert "MDL-E202" not in {d.code for d in diags.items}


# --- multi-ref behaviour the single object could not express ---------------


def test_multiple_refs_all_checked_for_predicate():
    ce = ConceptualEntity(
        id=new_ulid(),
        name="Amount",
        ontology_layer="core",
        ontology_refs=[
            OntologyRef(predicate="skos:closeMatch", uri="fibo:MonetaryAmount"),
            OntologyRef(predicate="skos:broadMatch", uri="acme:Money"),
        ],
    )
    # both are valid SKOS predicates -> no MDL-E210
    diags = check_layers(_model(ce))
    assert "MDL-E210" not in {d.code for d in diags.items}
    # primary_ref prefers an accepted ref; here first-with-uri
    assert ce.primary_ref.uri == "fibo:MonetaryAmount"


def test_primary_ref_prefers_accepted():
    ce = ConceptualEntity(
        id=new_ulid(),
        name="X",
        ontology_layer="core",
        ontology_refs=[
            OntologyRef(uri="a:proposed", status="proposed"),
            OntologyRef(uri="a:accepted", status="accepted"),
        ],
    )
    assert ce.primary_ref.uri == "a:accepted"


def test_coverage_report_over_refs():
    m = _model(
        ConceptualEntity(
            id=new_ulid(), name="A", ontology_layer="core",
            ontology_refs=[OntologyRef(uri="fibo:A")],
        ),
        ConceptualEntity(
            id=new_ulid(), name="B", ontology_layer="core",
            no_industry_equivalent=True, rationale="x",
        ),
        ConceptualEntity(id=new_ulid(), name="C", ontology_layer="core"),  # uncovered
        Term(id=new_ulid(), name="T"),
    )
    rpt = coverage_report(m)
    assert rpt.total_core == 3
    assert rpt.core_with_industry == 1
    assert rpt.core_exempt == 1
    assert "C" in rpt.core_uncovered
