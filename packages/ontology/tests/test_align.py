"""Alignment pass (spec §2) + R2RML fail-loud precondition (spec §5)."""

from __future__ import annotations

import pytest
from mdl_ontology import (
    R2RMLCoverage,
    UnmappedError,
    align_model,
    confidence_band,
    export_r2rml,
    r2rml_coverage,
)
from mdl_ontology.align import LexicalMatcher, normalize_name
from mdl_ontology.providers.base import ResolvedTerm

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    LogicalEntity,
    Model,
    OntologyRef,
    ProjectConfig,
)


class _StubRegistry:
    """A registry whose search returns a fixed term set — stands in for the merged
    closure of every configured source (the pass never sees individual providers)."""

    def __init__(self, terms):
        self._terms = terms

    def search(self, query, *, within=None, limit=20):
        q = query.lower()
        hits = [t for t in self._terms if q in (t.label or "").lower() or q in t.iri.lower()]
        return hits[:limit]

    def expand(self, prefixed):
        return None


_TERMS = [
    ResolvedTerm("https://x/Portfolio", "fibo:Portfolio", "Portfolio",
                 "A collection of assets.", "fibo-ols"),
    ResolvedTerm("https://x/Position", "fibo:Position", "Position",
                 "A holding.", "fibo-ols"),
    ResolvedTerm("https://x/Party", "fibo:Party", "Party", "A legal person.", "fibo-ols"),
]


def _model(*entities) -> Model:
    m = Model(ProjectConfig(name="t"))
    for e in entities:
        m.add(e)
    return m


# --- alignment pass --------------------------------------------------------


def test_align_proposes_ranked_candidates():
    ce = ConceptualEntity(id=new_ulid(), name="Portfolio")
    reg = _StubRegistry(_TERMS)
    props = align_model(_model(ce), reg, include_attributes=False)
    assert len(props) == 1
    p = props[0]
    assert p.object_name == "Portfolio"
    assert p.best.uri == "https://x/Portfolio"
    assert p.best.confidence >= 0.75  # exact label match ranks high


def test_align_skips_already_aligned():
    ce = ConceptualEntity(
        id=new_ulid(), name="Portfolio",
        ontology_refs=[OntologyRef(uri="fibo:Portfolio")],
    )
    reg = _StubRegistry(_TERMS)
    assert align_model(_model(ce), reg, include_attributes=False) == []


def test_align_threshold_filters_weak_matches():
    ce = ConceptualEntity(id=new_ulid(), name="Zxqw")  # matches nothing
    reg = _StubRegistry(_TERMS)
    assert align_model(_model(ce), reg, include_attributes=False) == []


def test_align_returns_pure_proposals_no_mutation():
    ce = ConceptualEntity(id=new_ulid(), name="Position")
    reg = _StubRegistry(_TERMS)
    align_model(_model(ce), reg, include_attributes=False)
    # nothing written back onto the object
    assert ce.ontology_refs == []


def test_confidence_band():
    assert confidence_band(0.9) == "high"
    assert confidence_band(0.6) == "medium-high"
    assert confidence_band(0.4) == "medium"
    assert confidence_band(0.1) == "low"


def test_lexical_matcher_scores_exact_label_highest():
    m = LexicalMatcher()
    exact = m.score("portfolio", _TERMS[0])
    weak = m.score("portfolio", _TERMS[2])  # "Party"
    assert exact > weak
    assert 0.0 <= exact <= 1.0


# --- reversed-name normalization (F1 friction fix) -------------------------


def test_normalize_name_strips_layer_prefix_and_singularizes():
    assert normalize_name("dim_customers") == "customer"
    assert normalize_name("fct_order_items") == "order item"
    assert normalize_name("stg_orders") == "order"
    assert normalize_name("MartDailyRevenue") == "daily revenue"
    assert normalize_name("Parties") == "party"
    # an all-layer-words name keeps its tokens rather than emptying out
    assert normalize_name("mart") in ("mart", "mart")


def test_align_matches_reversed_dim_names():
    """The F1 case: reversed names like `Customers` (plural, ex-`dim_`) must still
    match a `Customer` ontology term. Before the normalization fix this returned
    nothing."""
    terms = [
        ResolvedTerm("u:Customer", "fibo:Customer", "Customer", "A buyer.", "fibo"),
        ResolvedTerm("u:Order", "fibo:Order", "Order", "A purchase request.", "fibo"),
    ]
    reg = _StubRegistry(terms)
    ce_customers = ConceptualEntity(id=new_ulid(), name="Customers")
    ce_orders = ConceptualEntity(id=new_ulid(), name="Orders")
    props = align_model(_model(ce_customers, ce_orders), reg, include_attributes=False)
    by_name = {p.object_name: p.best.prefixed for p in props}
    assert by_name.get("Customers") == "fibo:Customer"
    assert by_name.get("Orders") == "fibo:Order"


# --- R2RML fail-loud -------------------------------------------------------


def _mapped_model() -> Model:
    ce = ConceptualEntity(
        id="01J000000000000000000000CE", name="Portfolio",
        ontology_refs=[OntologyRef(uri="https://x/Portfolio", predicate="skos:exactMatch")],
    )
    attr = Attribute(
        id="01J000000000000000000000AT", name="code",
        ontology_refs=[OntologyRef(uri="https://x/hasCode")],
    )
    le = LogicalEntity(
        id="01J000000000000000000000LE", name="portfolio",
        realises=ce.id, attributes=[attr],
    )
    return _model(ce, le)


def _unmapped_model() -> Model:
    ce = ConceptualEntity(id=new_ulid(), name="Portfolio")
    attr = Attribute(id=new_ulid(), name="code")
    le = LogicalEntity(id=new_ulid(), name="portfolio", realises=ce.id, attributes=[attr])
    return _model(ce, le)


def test_r2rml_coverage_flags_unmapped():
    cov = r2rml_coverage(_unmapped_model())
    assert not cov.ok
    assert "portfolio" in cov.unmapped_entities
    assert "portfolio.code" in cov.unmapped_attributes


def test_r2rml_coverage_clean_when_mapped():
    cov = r2rml_coverage(_mapped_model())
    assert cov.ok
    assert isinstance(cov, R2RMLCoverage)


def test_export_r2rml_fails_loud_on_unmapped():
    with pytest.raises(UnmappedError) as ei:
        export_r2rml(_unmapped_model())
    assert "portfolio" in ei.value.report.unmapped_entities


def test_export_r2rml_allow_unmapped_mints():
    g = export_r2rml(_unmapped_model(), allow_unmapped=True)
    assert len(g) > 0  # mapping emitted with minted fallback IRIs


def test_export_r2rml_ok_when_mapped():
    g = export_r2rml(_mapped_model())  # no allow_unmapped needed
    assert len(g) > 0
