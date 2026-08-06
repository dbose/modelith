"""Enumerated domains emit dbt accepted_values tests (P0-pre-b)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.ir import (
    Attribute,
    CodeSet,
    CodeValue,
    Domain,
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
    m.add(Domain(id="01DS", name="order_status", base_type="string",
                 allowed_values=["open", "shipped", "closed"]))
    m.add(CodeSet(id="01CS", name="currency",
                  values=[CodeValue(code="USD"), CodeValue(code="EUR")]))
    m.add(Domain(id="01DC", name="ccy_code", base_type="string", value_set="currency"))
    m.add(
        LogicalEntity(
            id="01LE",
            name="ord",
            attributes=[
                Attribute(id="01A1", name="status", domain="order_status", nullable=False),
                Attribute(id="01A2", name="ccy", domain="ccy_code", nullable=False),
                Attribute(id="01A3", name="note", domain="string"),  # not enumerated
            ],
        )
    )
    return m


def test_inline_enum_emits_accepted_values(tmp_path):
    sy = _schema(_model(), tmp_path)
    assert "accepted_values" in sy
    for v in ("open", "shipped", "closed"):
        assert v in sy


def test_code_set_enum_emits_accepted_values(tmp_path):
    sy = _schema(_model(), tmp_path)
    assert "USD" in sy and "EUR" in sy


def test_non_enum_column_has_no_accepted_values(tmp_path):
    sy = _schema(_model(), tmp_path)
    note_block = sy.split("name: note", 1)[1]
    assert "accepted_values" not in note_block
