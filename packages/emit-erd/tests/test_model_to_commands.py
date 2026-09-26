"""model_to_commands: a rich IR Model -> command list that apply_command reconstructs.

This is the bridge that lets the erwin importer (which produces a full Model, not a thin
table view) ride the canvas/CLI apply path without flattening subject areas, categories,
domains, or keys away.
"""

from __future__ import annotations

from pathlib import Path

from mdl_emit_erd.imports.model import model_to_commands

from mdl_core.commands import apply_command
from mdl_core.diagnostics import Severity
from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    Category,
    ConceptualEntity,
    Domain,
    KeyGroup,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
    SubjectArea,
)
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate


def _source_model() -> Model:
    m = Model(ProjectConfig(name="src", dbt_target="duckdb"))
    m.add(Domain(id=new_ulid(), name="id_type", base_type="bigint"))
    ce_c = ConceptualEntity(id=new_ulid(), name="Customer")
    ce_o = ConceptualEntity(id=new_ulid(), name="Order")
    m.add(ce_c)
    m.add(ce_o)
    a_cid = Attribute(id=new_ulid(), name="customer_id", role="business_key", domain="id_type")
    cust = LogicalEntity(id=new_ulid(), name="customer", realises=ce_c.id, attributes=[a_cid])
    a_oid = Attribute(id=new_ulid(), name="order_id", role="business_key", domain="id_type")
    a_ofk = Attribute(id=new_ulid(), name="customer_id", domain="id_type")
    order = LogicalEntity(id=new_ulid(), name="order", realises=ce_o.id, attributes=[a_oid, a_ofk])
    m.add(cust)
    m.add(order)
    m.add(KeyGroup(id=new_ulid(), entity=cust.id, name="pk_customer", type="pk", members=[a_cid.id]))
    m.add(KeyGroup(id=new_ulid(), entity=order.id, name="pk_order", type="pk", members=[a_oid.id]))
    m.add(
        Relationship(
            id=new_ulid(), name="order_placed_by_customer",
            **{"from": RelationshipEnd(entity=order.id, attributes=[a_ofk.id])},
            to=RelationshipEnd(entity=cust.id, attributes=[a_cid.id]),
            verb_phrase="places",
        )
    )
    m.add(SubjectArea(id=new_ulid(), name="Sales", members=[ce_c.id, ce_o.id]))
    return m


def _apply(model: Model, tmp_path: Path) -> Model:
    (tmp_path / "mdl-project.yaml").write_text("name: t\ndbt_target: duckdb\n")
    for c in model_to_commands(model):
        apply_command(tmp_path, c["op"], c["payload"])
    return ModelRepo.load(tmp_path).model


def test_round_trip_reconstructs_structure(tmp_path):
    out = _apply(_source_model(), tmp_path)
    assert {le.name for le in out.logical_entities.values()} == {"customer", "order"}
    assert sum(1 for k in out.key_groups.values() if k.type == "pk") == 2
    assert len(out.relationships) == 1
    assert any(d.name == "id_type" for d in out.domains.values())
    sa = next(iter(out.subject_areas.values()))
    assert sa.name == "Sales" and len(sa.members) == 2


def test_reconstructed_model_validates(tmp_path):
    out = _apply(_source_model(), tmp_path)
    diags = validate(out)
    assert not diags.has(Severity.error), [
        d.message for d in diags.items if d.severity == Severity.error
    ]


def test_category_with_discriminator_round_trips(tmp_path):
    m = Model(ProjectConfig(name="src", dbt_target="duckdb"))
    ce = ConceptualEntity(id=new_ulid(), name="Party")
    ce_p = ConceptualEntity(id=new_ulid(), name="Person")
    m.add(ce)
    m.add(ce_p)
    disc = Attribute(id=new_ulid(), name="party_type", role="attribute", domain="string")
    pk = Attribute(id=new_ulid(), name="party_id", role="business_key", domain="string")
    party = LogicalEntity(id=new_ulid(), name="party", realises=ce.id, attributes=[pk, disc])
    person = LogicalEntity(id=new_ulid(), name="person", realises=ce_p.id,
                           attributes=[Attribute(id=new_ulid(), name="party_id", role="business_key",
                                                 domain="string")])
    m.add(party)
    m.add(person)
    m.add(KeyGroup(id=new_ulid(), entity=party.id, name="pk_party", type="pk", members=[pk.id]))
    m.add(Category(id=new_ulid(), name="party_category", supertype=party.id,
                   subtypes=[person.id], discriminator=disc.id, materialization="single_table"))
    out = _apply(m, tmp_path)
    cat = next(iter(out.categories.values()))
    assert cat.discriminator is not None
    assert not validate(out).has(Severity.error)
