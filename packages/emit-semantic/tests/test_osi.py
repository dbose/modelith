"""OSI v0.1.1 emit/import tests (spec §4)."""

from __future__ import annotations

from mdl_emit_semantic import emit_osi, import_osi
from mdl_emit_semantic.osi import DEFAULT_VERSION, get_osi

from mdl_core.yaml_io import load_str


def test_osi_version_pinned():
    assert DEFAULT_VERSION == "0.1.1"
    assert get_osi("0.1.1").OSI_VERSION == "0.1.1"


def test_unsupported_version_rejected():
    import pytest

    with pytest.raises(KeyError):
        get_osi("0.2.0")


def test_emit_osi_structure(repo):
    text = emit_osi(repo.model, targets=["duckdb_dev"])
    doc = load_str(text)
    assert doc["osi_version"] == "0.1.1"
    sm = doc["semantic_model"]
    assert sm["name"] == "testmodel"
    names = {ds["name"] for ds in sm["datasets"]}
    assert {"counterparty", "trade"} <= names
    # primary_key present
    cpty = next(ds for ds in sm["datasets"] if ds["name"] == "counterparty")
    assert cpty["primary_key"] == ["counterparty_id"]
    # multi-dialect: ANSI + duckdb
    dialects = {d["dialect"] for d in cpty["fields"][0]["expression"]["dialects"]}
    assert "ANSI_SQL" in dialects and "DUCKDB" in dialects
    # ontology IRI carried into custom_extensions
    assert cpty["custom_extensions"]["vendor_name"] == "MODELITH"
    assert cpty["custom_extensions"]["ontology_iri"] == "fibo-fnd-pty-pty:PartyInRole"
    # relationship on the many side (trade -> counterparty)
    assert any(r["from"]["dataset"] == "trade" for r in sm.get("relationships", []))


def test_osi_roundtrip_emit_import(repo):
    text = emit_osi(repo.model, targets=["duckdb_dev"])
    imported = import_osi(text)
    names = {le.name for le in imported.logical_entities.values()}
    assert {"counterparty", "trade"} <= names
    # ULID identity recovered from custom_extensions.mdl_ulid
    src_ulids = {le.name: le.id for le in repo.model.logical_entities.values()}
    imp_ulids = {le.name: le.id for le in imported.logical_entities.values()}
    assert src_ulids == imp_ulids
    # relationship recovered
    assert len(imported.relationships) == 1
    # business key preserved
    cpty = next(le for le in imported.logical_entities.values() if le.name == "counterparty")
    assert any(a.role == "business_key" and a.name == "counterparty_id" for a in cpty.attributes)


def test_time_dimension_flagged():
    from mdl_core.ids import new_ulid
    from mdl_core.ir import Attribute, LogicalEntity, Model, ProjectConfig

    m = Model(ProjectConfig(name="t", platform_targets=["duckdb_dev"]))
    le = LogicalEntity(
        id=new_ulid(),
        name="event",
        attributes=[
            Attribute(id=new_ulid(), name="event_id", domain="bigint", role="business_key"),
            Attribute(id=new_ulid(), name="occurred_at", domain="timestamp"),
        ],
    )
    m.add(le)
    doc = load_str(emit_osi(m))
    ds = doc["semantic_model"]["datasets"][0]
    time_field = next(f for f in ds["fields"] if f["name"] == "occurred_at")
    assert time_field["dimension"]["is_time"] is True
