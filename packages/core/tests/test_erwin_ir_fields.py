"""The erwin-facing IR additions: physical names + relationship verb phrases.

These are optional, additive fields that erwin import populates and a re-export should
round-trip. A model that doesn't use them behaves exactly as before.
"""

from __future__ import annotations

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
)
from mdl_core.repo import ModelRepo
from mdl_reverse.writer import write_model


def test_physical_names_and_verb_phrases_round_trip(tmp_path):
    m = Model(ProjectConfig(name="p", dbt_target="duckdb"))
    ce_c = ConceptualEntity(id=new_ulid(), name="Customer")
    ce_o = ConceptualEntity(id=new_ulid(), name="Order")
    m.add(ce_c)
    m.add(ce_o)
    cust = LogicalEntity(
        id=new_ulid(), name="customer", physical_name="CUSTOMER", realises=ce_c.id,
        attributes=[Attribute(id=new_ulid(), name="customer_id",
                              physical_name="CUSTOMER_ID", role="business_key")],
    )
    order = LogicalEntity(id=new_ulid(), name="order", physical_name="ORDER", realises=ce_o.id,
                          attributes=[Attribute(id=new_ulid(), name="customer_id")])
    m.add(cust)
    m.add(order)
    m.add(
        Relationship(
            id=new_ulid(), name="order_placed_by_customer",
            **{"from": RelationshipEnd(entity=order.id, attributes=[order.attributes[0].id])},
            to=RelationshipEnd(entity=cust.id, attributes=[cust.attributes[0].id]),
            verb_phrase="places", inverse_verb_phrase="is placed by",
        )
    )
    write_model(m, tmp_path)

    reloaded = ModelRepo.load(tmp_path).model
    le = next(e for e in reloaded.logical_entities.values() if e.name == "customer")
    assert le.physical_name == "CUSTOMER"
    assert le.attributes[0].physical_name == "CUSTOMER_ID"
    rel = next(iter(reloaded.relationships.values()))
    assert rel.verb_phrase == "places"
    assert rel.inverse_verb_phrase == "is placed by"


def test_absent_fields_stay_clean(tmp_path):
    # a model that uses none of the new fields writes YAML without them (no noise)
    m = Model(ProjectConfig(name="p", dbt_target="duckdb"))
    ce = ConceptualEntity(id=new_ulid(), name="Thing")
    m.add(ce)
    m.add(LogicalEntity(id=new_ulid(), name="thing", realises=ce.id))
    write_model(m, tmp_path)
    text = (tmp_path / "logical" / "entities" / "thing.yaml").read_text()
    assert "physical_name" not in text
