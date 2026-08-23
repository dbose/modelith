"""Tests for the ODCS v3 data contract emitter."""

from __future__ import annotations

from mdl_emit_contract import emit_datacontract
from mdl_emit_contract.emitter import build_contract
from ruamel.yaml import YAML

from mdl_core.ir import (
    Attribute,
    CodeSet,
    CodeValue,
    ConceptualEntity,
    Domain,
    KeyGroup,
    LogicalEntity,
    Model,
    ProjectConfig,
    Stewardship,
)


def _model() -> Model:
    m = Model(ProjectConfig(name="sales"))
    m.domains["d1"] = Domain(id="d1", name="id_bigint", base_type="bigint")
    m.domains["d2"] = Domain(id="d2", name="text", base_type="string")
    m.domains["d3"] = Domain(id="d3", name="amount", base_type="decimal")
    m.domains["d4"] = Domain(
        id="d4", name="status_dom", base_type="string", value_set="order_status"
    )
    m.code_sets["cs1"] = CodeSet(
        id="cs1",
        name="order_status",
        values=[CodeValue(code="open"), CodeValue(code="closed")],
    )
    m.conceptual_entities["c1"] = ConceptualEntity(
        id="c1",
        name="Order",
        definition="A customer order.",
        stewardship=Stewardship(owner="Ada", steward="Grace"),
    )
    m.logical_entities["e1"] = LogicalEntity(
        id="e1",
        name="orders",
        realises="c1",
        attributes=[
            Attribute(id="a1", name="order_id", domain="id_bigint", nullable=False),
            Attribute(id="a2", name="total", domain="amount", nullable=True),
            Attribute(id="a3", name="status", domain="status_dom", nullable=False),
        ],
    )
    m.key_groups["k1"] = KeyGroup(id="k1", entity="e1", name="pk_orders", type="pk", members=["a1"])
    # An unmanaged entity must be excluded.
    m.logical_entities["e2"] = LogicalEntity(
        id="e2", name="staging", attributes=[], unmanaged=True
    )
    return m


def test_contract_structure():
    c = build_contract(_model())
    assert c["apiVersion"].startswith("v3")
    assert c["kind"] == "DataContract"
    assert c["id"] == "sales"
    names = [o["name"] for o in c["schema"]]
    assert names == ["orders"]  # unmanaged excluded


def test_property_types_and_keys():
    c = build_contract(_model())
    props = {p["name"]: p for p in c["schema"][0]["properties"]}
    assert props["order_id"]["logicalType"] == "integer"
    assert props["order_id"]["required"] is True
    assert props["order_id"]["primaryKey"] is True
    assert props["total"]["logicalType"] == "number"  # decimal -> number
    assert props["total"]["required"] is False


def test_valid_values_from_codeset():
    c = build_contract(_model())
    props = {p["name"]: p for p in c["schema"][0]["properties"]}
    q = props["status"]["quality"][0]
    assert q["rule"] == "validValues"
    assert set(q["validValues"]) == {"open", "closed"}


def test_roles_from_stewardship():
    c = build_contract(_model())
    roles = {(r["role"], r["description"]) for r in c.get("roles", [])}
    assert ("owner", "Ada") in roles
    assert ("steward", "Grace") in roles


def test_emit_is_valid_yaml():
    text = emit_datacontract(_model())
    parsed = YAML(typ="safe").load(text)
    assert parsed["kind"] == "DataContract"
    assert parsed["schema"][0]["name"] == "orders"
