"""MetricFlow emit + joinability/fan-out validation tests (spec §8)."""

from __future__ import annotations

from mdl_emit_semantic import emit_metricflow, validate_joinability

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
)
from mdl_core.yaml_io import load_str


def test_metricflow_projection(repo):
    doc = load_str(emit_metricflow(repo.model, "duckdb_dev"))
    sms = {sm["name"]: sm for sm in doc["semantic_models"]}
    assert {"counterparty", "trade"} <= set(sms)
    # primary entity = business key
    cpty = sms["counterparty"]
    prim = next(e for e in cpty["entities"] if e["type"] == "primary")
    assert prim["expr"] == "counterparty_id"
    # trade has a foreign entity to counterparty (from the relationship)
    trade = sms["trade"]
    assert any(e["type"] == "foreign" and e["name"] == "counterparty" for e in trade["entities"])


def test_measure_produces_metric():
    m = Model(ProjectConfig(name="t"))
    le = LogicalEntity(
        id=new_ulid(),
        name="sales",
        attributes=[
            Attribute(id=new_ulid(), name="sale_id", domain="bigint", role="business_key"),
            Attribute(id=new_ulid(), name="amount", domain="decimal", role="measure"),
        ],
    )
    m.add(le)
    doc = load_str(emit_metricflow(m, "duckdb_dev"))
    assert any(metric["name"] == "total_amount" for metric in doc.get("metrics", []))
    sm = doc["semantic_models"][0]
    assert any(meas["name"] == "amount" for meas in sm.get("measures", []))


def test_joinability_clean(repo):
    diags = validate_joinability(repo.model)
    from mdl_core.diagnostics import Severity

    assert not diags.has(Severity.error)


def test_many_to_many_flagged_as_fanout():
    m = Model(ProjectConfig(name="t"))
    a = LogicalEntity(id=new_ulid(), name="a", attributes=[Attribute(id=new_ulid(), name="a_id", role="business_key")])
    b = LogicalEntity(id=new_ulid(), name="b", attributes=[Attribute(id=new_ulid(), name="b_id", role="business_key")])
    m.add(a)
    m.add(b)
    m.add(
        Relationship(
            id=new_ulid(),
            name="a_b",
            **{"from": RelationshipEnd(entity=a.id)},
            to=RelationshipEnd(entity=b.id),
            cardinality="many_to_many",
        )
    )
    diags = validate_joinability(m)
    assert "MDL-E802" in {d.code for d in diags.items}


def test_dangling_relationship_endpoint_flagged():
    m = Model(ProjectConfig(name="t"))
    a = LogicalEntity(id=new_ulid(), name="a", attributes=[Attribute(id=new_ulid(), name="a_id", role="business_key")])
    m.add(a)
    m.add(
        Relationship(
            id=new_ulid(),
            name="a_ghost",
            **{"from": RelationshipEnd(entity=a.id)},
            to=RelationshipEnd(entity="01GHOST00000000000000000AA"),
            cardinality="many_to_one",
        )
    )
    diags = validate_joinability(m)
    assert "MDL-E801" in {d.code for d in diags.items}
