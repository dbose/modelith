"""User-defined properties (UDPs) flow into dbt meta (P4)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    LogicalEntity,
    Model,
    ProjectConfig,
)
from mdl_emit_dbt.emitter import DbtEmitter


def _schema(model: Model, tmp_path: Path) -> str:
    return DbtEmitter(model, "duckdb_dev").generate(tmp_path, write=False).files[
        "models/schema.yml"
    ]


def _model():
    m = Model(ProjectConfig(name="t", dbt_target="duckdb_dev"))
    m.add(ConceptualEntity(id="01CE", name="Order",
                           udp={"sox_scope": True, "data_class": "confidential"}))
    m.add(
        LogicalEntity(
            id="01LE", name="ord", realises="01CE", udp={"refresh_sla_hrs": 4},
            attributes=[
                Attribute(id="01A1", name="order_id", domain="bigint", nullable=False,
                          udp={"source_system": "ERP", "pii": False}),
                Attribute(id="01A2", name="total", domain="decimal"),
            ],
        )
    )
    return m


def test_entity_udp_flows_to_model_meta(tmp_path):
    sy = _schema(_model(), tmp_path)
    assert "sox_scope: true" in sy  # from conceptual entity
    assert "data_class: confidential" in sy
    assert "refresh_sla_hrs: 4" in sy  # from logical entity


def test_attribute_udp_flows_to_column_meta(tmp_path):
    sy = _schema(_model(), tmp_path)
    assert "source_system: ERP" in sy
    assert "pii: false" in sy


def test_no_udp_no_extra_meta(tmp_path):
    sy = _schema(_model(), tmp_path)
    # the `total` column has no udp: its meta block only has mdl_ulid
    total_block = sy.split("name: total", 1)[1].split("name:")[0]
    assert "source_system" not in total_block
