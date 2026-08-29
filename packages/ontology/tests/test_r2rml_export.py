"""Tests for the R2RML mapping emitter."""

from __future__ import annotations

from mdl_ontology import export_r2rml, serialize
from mdl_ontology._common import RR
from rdflib import Graph, Literal, URIRef

from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    Domain,
    KeyGroup,
    LogicalEntity,
    Model,
    OntologyAlignment,
    PhysicalColumn,
    PhysicalTable,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
    TermMap,
)


def _model() -> Model:
    m = Model(ProjectConfig(name="sales", dbt_target="duckdb_dev"))
    m.domains["d1"] = Domain(id="d1", name="id_bigint", base_type="bigint")
    m.domains["d2"] = Domain(id="d2", name="text", base_type="string")

    # customer: keyed by customer_id (pk KeyGroup)
    m.logical_entities["cust"] = LogicalEntity(
        id="cust",
        name="customer",
        attributes=[
            Attribute(id="c_id", name="customer_id", domain="id_bigint", nullable=False),
            Attribute(id="c_name", name="name", domain="text", nullable=True),
        ],
    )
    m.key_groups["kc"] = KeyGroup(
        id="kc", entity="cust", name="pk_customer", type="pk", members=["c_id"]
    )
    # order: FK -> customer
    m.logical_entities["ord"] = LogicalEntity(
        id="ord",
        name="order",
        attributes=[
            Attribute(id="o_id", name="order_id", domain="id_bigint", nullable=False),
            Attribute(id="o_cust", name="customer_id", domain="id_bigint", nullable=True),
        ],
    )
    m.key_groups["ko"] = KeyGroup(
        id="ko", entity="ord", name="pk_order", type="pk", members=["o_id"]
    )
    m.relationships["r1"] = Relationship(
        id="rel_oc",
        name="order_customer",
        **{"from": RelationshipEnd(entity="ord", attributes=["o_cust"])},
        to=RelationshipEnd(entity="cust", attributes=["c_id"]),
        cardinality="many_to_one",
    )
    # physical table for order maps to a different warehouse name + columns
    m.physical_tables["pt_ord"] = PhysicalTable(
        id="pt_ord",
        target="duckdb_dev",
        realises="ord",
        name="FCT_ORDERS",
        columns=[
            PhysicalColumn(realises="o_id", name="order_key", data_type="BIGINT"),
            PhysicalColumn(realises="o_cust", name="customer_key", data_type="BIGINT"),
        ],
    )
    # an unmanaged entity must be excluded
    m.logical_entities["stg"] = LogicalEntity(
        id="stg", name="staging", attributes=[], unmanaged=True
    )
    return m


def _graph() -> Graph:
    return export_r2rml(_model(), target="duckdb_dev", allow_unmapped=True)


def test_triplesmap_per_managed_entity():
    g = _graph()
    tables = set(g.objects(predicate=RR.tableName))
    # customer falls back to entity name; order uses the physical table name (lowercased)
    assert Literal("customer") in tables
    assert Literal("fct_orders") in tables
    # unmanaged 'staging' excluded
    assert Literal("staging") not in tables


def test_subject_template_from_pk_on_project_base():
    # base derives from the project name ("sales") -> urn:sales:
    g = _graph()
    templates = {str(o) for o in g.objects(predicate=RR.template)}
    assert "urn:sales:cust/{customer_id}" in templates
    # order's PK column is the physical name from the PhysicalTable
    assert "urn:sales:ord/{order_key}" in templates


def test_class_iri_present():
    g = _graph()
    classes = {str(o) for o in g.objects(predicate=RR["class"])}
    assert "urn:sales:cust" in classes
    assert "urn:sales:ord" in classes


def test_attribute_predicate_object_maps_have_column_and_datatype():
    g = _graph()
    cols = {str(o) for o in g.objects(predicate=RR.column)}
    # physical column names for order, plain names for customer
    assert "order_key" in cols
    assert "customer_key" in cols
    assert "name" in cols
    datatypes = set(g.objects(predicate=RR.datatype))
    from rdflib.namespace import XSD

    assert XSD.long in datatypes  # bigint
    assert XSD.string in datatypes


def test_relationship_becomes_parent_triplesmap_join():
    g = _graph()
    # there is a joinCondition mapping order.customer_key -> customer.customer_id
    children = {str(o) for o in g.objects(predicate=RR.child)}
    parents = {str(o) for o in g.objects(predicate=RR.parent)}
    assert "customer_key" in children  # FK physical column on the child (order)
    assert "customer_id" in parents  # PK column on the parent (customer)
    # the parentTriplesMap points at the customer mapping node
    parent_tms = {str(o) for o in g.objects(predicate=RR.parentTriplesMap)}
    assert "urn:sales:mapping/cust" in parent_tms


def test_output_is_wellformed_rdf():
    g = _graph()
    ttl = serialize(g, "turtle")
    reparsed = Graph()
    reparsed.parse(data=ttl, format="turtle")
    assert len(reparsed) == len(g)


def test_keyless_entity_gets_blank_node_subject():
    m = _model()
    # add a keyless associative entity
    m.logical_entities["lnk"] = LogicalEntity(
        id="lnk",
        name="link",
        attributes=[Attribute(id="l_a", name="a", domain="text", nullable=True)],
    )
    g = export_r2rml(m, target="duckdb_dev", allow_unmapped=True)
    term_types = set(g.objects(predicate=RR.termType))
    assert RR.BlankNode in term_types


# --- customisable term-map ---------------------------------------------------


def _aligned_model() -> Model:
    """A model whose customer entity is aligned to a FIBO class."""
    m = Model(ProjectConfig(name="sales", dbt_target="duckdb_dev"))
    m.conceptual_entities["c1"] = ConceptualEntity(
        id="c1",
        name="Customer",
        ontology=OntologyAlignment(aligns_to="http://fibo/Customer", alignment="skos:exactMatch"),
    )
    m.logical_entities["cust"] = LogicalEntity(
        id="cust",
        name="customer",
        realises="c1",
        attributes=[Attribute(id="c_id", name="customer_id", nullable=False)],
    )
    m.key_groups["kc"] = KeyGroup(
        id="kc", entity="cust", name="pk", type="pk", members=["c_id"]
    )
    return m


def test_explicit_base_iri_overrides_default():
    m = _model()
    m.config.kg_base_iri = "https://acme.com/id/"
    ttl = serialize(export_r2rml(m, target="duckdb_dev", allow_unmapped=True))
    assert "https://acme.com/id/" in ttl
    # no minted IRI uses the derived default when an explicit base is set
    non_prefix_lines = [ln for ln in ttl.splitlines() if not ln.strip().startswith("@prefix")]
    assert not any("urn:sales:" in ln for ln in non_prefix_lines)


def test_aligned_ontology_iri_becomes_class_when_no_override():
    # the gap this feature fixes: alignment now flows into rr:class
    g = export_r2rml(_aligned_model(), allow_unmapped=True)
    classes = set(g.objects(predicate=RR["class"]))
    assert URIRef("http://fibo/Customer") in classes


def test_explicit_class_iri_overrides_alignment():
    m = _aligned_model()
    m.logical_entities["cust"].term_map = TermMap(class_iri="http://acme.com/Customer")
    g = export_r2rml(m, allow_unmapped=True)
    classes = {str(o) for o in g.objects(predicate=RR["class"])}
    assert "http://acme.com/Customer" in classes
    assert "http://fibo/Customer" not in classes


def test_subject_template_override_verbatim():
    m = _aligned_model()
    m.logical_entities["cust"].term_map = TermMap(
        subject_template="https://acme.com/id/customer/{customer_id}"
    )
    templates = {
        str(o) for o in export_r2rml(m, allow_unmapped=True).objects(predicate=RR.template)
    }
    assert "https://acme.com/id/customer/{customer_id}" in templates


def test_attribute_predicate_and_datatype_override():
    m = _aligned_model()
    m.logical_entities["cust"].attributes[0].term_map = TermMap(
        predicate_iri="http://acme.com/hasId",
        datatype="http://www.w3.org/2001/XMLSchema#string",
    )
    g = export_r2rml(m, allow_unmapped=True)
    preds = {str(o) for o in g.objects(predicate=RR.predicate)}
    dtypes = {str(o) for o in g.objects(predicate=RR.datatype)}
    assert "http://acme.com/hasId" in preds
    assert "http://www.w3.org/2001/XMLSchema#string" in dtypes


def test_default_base_derives_from_project_name():
    # with no explicit kg_base_iri, the base is a URN derived from the project name
    # ("sales" -> urn:sales:), and never carries a modelith.dev vendor host
    ttl = serialize(export_r2rml(_model(), target="duckdb_dev", allow_unmapped=True))
    non_prefix = [ln for ln in ttl.splitlines() if not ln.strip().startswith("@prefix")]
    assert any("urn:sales:" in ln for ln in non_prefix)
    assert not any("modelith.dev" in ln for ln in non_prefix)
