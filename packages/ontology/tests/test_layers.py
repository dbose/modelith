"""Four-layer ontology validation tests (spec §3.1)."""

from __future__ import annotations

from mdl_ontology.layers import check_layers, coverage_report

from mdl_core.ir import ConceptualEntity, Model, OntologyAlignment, ProjectConfig, Term


def _model(*objs) -> Model:
    m = Model(ProjectConfig(name="t"))
    for o in objs:
        m.add(o)
    return m


def _ce(name, layer=None, aligns_to=None, alignment=None, no_ind=False, rationale=None, defn=None):
    ont = None
    if layer or aligns_to:
        ont = OntologyAlignment(
            layer=layer,
            aligns_to=aligns_to,
            alignment=alignment,
            no_industry_equivalent=no_ind,
            rationale=rationale,
        )
    from mdl_core.ids import new_ulid

    return ConceptualEntity(id=new_ulid(), name=name, ontology=ont, definition=defn)


def test_core_without_industry_alignment_errors():
    m = _model(_ce("Counterparty", layer="core"))
    diags = check_layers(m)
    assert "MDL-E202" in {d.code for d in diags.items}


def test_core_with_alignment_ok():
    m = _model(_ce("Counterparty", layer="core", aligns_to="fibo:PartyInRole", alignment="skos:exactMatch"))
    diags = check_layers(m)
    assert "MDL-E202" not in {d.code for d in diags.items}


def test_core_exempt_needs_rationale():
    m = _model(_ce("Widget", layer="core", no_ind=True))  # no rationale
    diags = check_layers(m)
    assert "MDL-W204" in {d.code for d in diags.items}
    # with rationale -> clean
    m2 = _model(_ce("Widget", layer="core", no_ind=True, rationale="bespoke internal concept"))
    assert "MDL-W204" not in {d.code for d in check_layers(m2).items}


def test_non_skos_predicate_rejected_at_schema():
    # The IR's Alignment Literal rejects non-SKOS predicates at construction, so an
    # invalid predicate never reaches the model — a stronger guard than MDL-E210
    # (which remains as defense-in-depth for objects built outside pydantic).
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OntologyAlignment(layer="core", aligns_to="fibo:Foo", alignment="owl:sameAs")


def test_downward_alignment_errors():
    # a core term aligning to a specialised term is downward -> error
    from mdl_core.ids import new_ulid

    spec = ConceptualEntity(
        id=new_ulid(), name="StagedObligor", ontology=OntologyAlignment(layer="specialised")
    )
    core = ConceptualEntity(
        id=new_ulid(),
        name="Obligor",
        ontology=OntologyAlignment(layer="core", aligns_to="x:StagedObligor", alignment="skos:closeMatch"),
    )
    m = _model(spec, core)
    diags = check_layers(m)
    assert "MDL-E211" in {d.code for d in diags.items}


def test_exactmatch_cycle_detected():
    from mdl_core.ids import new_ulid

    a_id, b_id = new_ulid(), new_ulid()
    a = ConceptualEntity(id=a_id, name="A", ontology=OntologyAlignment(layer="core", aligns_to="x:B", alignment="skos:exactMatch"))
    b = ConceptualEntity(id=b_id, name="B", ontology=OntologyAlignment(layer="core", aligns_to="x:A", alignment="skos:exactMatch"))
    m = _model(a, b)
    diags = check_layers(m)
    assert "MDL-E212" in {d.code for d in diags.items}


def test_duplicate_term_similarity_warns():
    dom = _ce(
        "Obligor",
        layer="domain",
        aligns_to="core:Counterparty",
        defn="A party that owes a financial obligation to the firm under a contract",
    )
    spec = _ce(
        "StagedObligor",
        layer="specialised",
        aligns_to="domain:Obligor",
        defn="A party that owes a financial obligation to the firm under a contract stage",
    )
    m = _model(dom, spec)
    diags = check_layers(m)
    assert "MDL-W205" in {d.code for d in diags.items}


def test_coverage_report():
    m = _model(
        _ce("A", layer="core", aligns_to="fibo:A"),
        _ce("B", layer="core", no_ind=True, rationale="x"),
        _ce("C", layer="core"),  # uncovered
        Term(id=__import__("mdl_core.ids", fromlist=["new_ulid"]).new_ulid(), name="T"),
    )
    rpt = coverage_report(m)
    assert rpt.total_core == 3
    assert rpt.core_with_industry == 1
    assert rpt.core_exempt == 1
    assert "C" in rpt.core_uncovered
    assert rpt.coverage_pct == round(100 * 2 / 3, 1)
