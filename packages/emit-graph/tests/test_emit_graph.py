"""Tests for the Neo4j / Cypher schema emitter."""

from __future__ import annotations

from mdl_emit_graph import emit_cypher

from mdl_core.ir import (
    Attribute,
    Domain,
    KeyGroup,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
)


def _model() -> Model:
    m = Model(ProjectConfig(name="sales"))
    m.domains["d1"] = Domain(id="d1", name="id_bigint", base_type="bigint")
    m.domains["d2"] = Domain(id="d2", name="text", base_type="string")
    m.logical_entities["e1"] = LogicalEntity(
        id="e1",
        name="customer",
        attributes=[
            Attribute(id="a1", name="customer_id", domain="id_bigint", nullable=False),
            Attribute(id="a2", name="email", domain="text", nullable=False),
        ],
    )
    m.logical_entities["e2"] = LogicalEntity(
        id="e2",
        name="order",
        attributes=[
            Attribute(id="b1", name="order_id", domain="id_bigint", nullable=False),
            Attribute(id="b2", name="customer_id", domain="id_bigint", nullable=True),
        ],
    )
    m.key_groups["k1"] = KeyGroup(
        id="k1", entity="e1", name="pk_customer", type="pk", members=["a1"]
    )
    m.key_groups["k2"] = KeyGroup(
        id="k2", entity="e1", name="ak_email", type="unique", members=["a2"]
    )
    m.relationships["r1"] = Relationship(
        id="r1",
        name="order_customer",
        **{"from": RelationshipEnd(entity="e2", attributes=["b2"])},
        to=RelationshipEnd(entity="e1", attributes=["a1"]),
        cardinality="many_to_one",
    )
    return m


def test_node_key_and_unique_constraints():
    cy = emit_cypher(_model())
    assert "FOR (n:Customer) REQUIRE n.customer_id IS NODE KEY;" in cy
    assert "FOR (n:Customer) REQUIRE n.email IS UNIQUE;" in cy


def test_existence_constraints_for_non_null():
    cy = emit_cypher(_model())
    assert "FOR (n:Customer) REQUIRE n.email IS NOT NULL;" in cy
    # nullable customer_id on order gets no existence constraint
    assert "REQUIRE n.customer_id IS NOT NULL" in cy  # customer's own id is non-null
    assert "n:Order) REQUIRE n.customer_id IS NOT NULL" not in cy


def test_relationship_type_emitted():
    cy = emit_cypher(_model())
    assert "(:Order)-[:ORDER_CUSTOMER]->(:Customer)" in cy
