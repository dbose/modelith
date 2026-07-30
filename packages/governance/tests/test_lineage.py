"""OpenLineage emission tests (spec §9.6)."""

from __future__ import annotations

import json

from mdl_governance import emit_openlineage

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    LogicalEntity,
    Model,
    PhysicalColumn,
    PhysicalTable,
    ProjectConfig,
)


def _model_with_physical() -> Model:
    m = Model(ProjectConfig(name="lin"))
    le_id, a1 = new_ulid(), new_ulid()
    le = LogicalEntity(
        id=le_id,
        name="counterparty",
        attributes=[Attribute(id=a1, name="counterparty_id", domain="bigint", role="business_key")],
    )
    m.add(le)
    m.add(
        PhysicalTable(
            id=new_ulid(),
            target="snowflake_prod",
            realises=le_id,
            name="DIM_COUNTERPARTY",
            columns=[PhysicalColumn(realises=a1, name="COUNTERPARTY_ID", data_type="NUMBER(38,0)")],
        )
    )
    return m


def test_openlineage_payload_valid_json():
    doc = json.loads(emit_openlineage(_model_with_physical()))
    assert doc["eventType"] == "COMPLETE"
    assert doc["producer"] == "https://modelith.dev"
    outputs = doc["outputs"]
    assert len(outputs) == 1
    ds = outputs[0]
    assert ds["name"] == "DIM_COUNTERPARTY"
    # schema facet carries the logical fields
    fields = ds["facets"]["schema"]["fields"]
    assert any(f["name"] == "counterparty_id" for f in fields)
    # mdl facet carries ULID for correlation
    assert ds["facets"]["mdl"]["logical_entity"] == "counterparty"


def test_openlineage_empty_when_no_physical():
    m = Model(ProjectConfig(name="empty"))
    doc = json.loads(emit_openlineage(m))
    assert doc["outputs"] == []
