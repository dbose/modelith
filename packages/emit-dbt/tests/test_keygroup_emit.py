"""KeyGroup emission (P0-pre-a): named/composite PK + unique/alternate keys.

A `pk` KeyGroup is the authoritative primary key (fixes the old per-column bug for
composite PKs); `unique`/`alternate` KeyGroups become column `unique` constraints /
dbt tests (single-col) or a `dbt_utils.unique_combination_of_columns` model test
(composite). Models without KeyGroups keep the legacy `role: business_key` path.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.ir import Attribute, KeyGroup, LogicalEntity, Model, ProjectConfig
from mdl_emit_dbt.emitter import DbtEmitter


def _schema(model: Model, tmp_path: Path) -> str:
    out = DbtEmitter(model, "duckdb_dev").generate(tmp_path, write=False)
    return out.files["models/schema.yml"]


def _order_line_model(*, with_keygroups: bool) -> Model:
    m = Model(ProjectConfig(name="t", dbt_target="duckdb_dev"))
    a = {
        "oid": Attribute(id="01ATOID", name="order_id", domain="bigint", nullable=False),
        "ln": Attribute(id="01ATLN", name="line_no", domain="integer", nullable=False),
        "sku": Attribute(id="01ATSKU", name="sku", domain="string", nullable=False),
        "eml": Attribute(id="01ATEML", name="email", domain="string", nullable=False),
    }
    m.add(LogicalEntity(id="01LE", name="order_line", attributes=list(a.values())))
    if with_keygroups:
        m.add(KeyGroup(id="01KPK", entity="01LE", name="pk_order_line", type="pk",
                       members=["01ATOID", "01ATLN"]))
        m.add(KeyGroup(id="01KUQ", entity="01LE", name="uq_email", type="unique",
                       members=["01ATEML"]))
        m.add(KeyGroup(id="01KALT", entity="01LE", name="ak_order_sku", type="alternate",
                       members=["01ATOID", "01ATSKU"]))
    return m


def test_composite_pk_from_keygroup(tmp_path):
    sy = _schema(_order_line_model(with_keygroups=True), tmp_path)
    # both PK members carry a primary_key constraint (one composite key)
    assert sy.count("type: primary_key") == 2


def test_single_unique_key_becomes_constraint(tmp_path):
    sy = _schema(_order_line_model(with_keygroups=True), tmp_path)
    # duckdb supports unique -> column constraint on email
    assert "type: unique" in sy


def test_composite_unique_becomes_model_test(tmp_path):
    sy = _schema(_order_line_model(with_keygroups=True), tmp_path)
    assert "dbt_utils.unique_combination_of_columns" in sy
    assert "order_id" in sy and "sku" in sy


def test_backward_compatible_business_key(tmp_path):
    # no KeyGroups: legacy role=business_key still emits a single primary_key
    m = Model(ProjectConfig(name="t", dbt_target="duckdb_dev"))
    m.add(
        LogicalEntity(
            id="01LE2",
            name="customer",
            attributes=[
                Attribute(id="01BK", name="customer_id", domain="bigint",
                          role="business_key", nullable=False),
                Attribute(id="01NM", name="name", domain="string"),
            ],
        )
    )
    sy = _schema(m, tmp_path)
    assert sy.count("type: primary_key") == 1
    assert "dbt_utils" not in sy


def test_keygroup_pk_wins_over_business_key_role(tmp_path):
    # if both a pk KeyGroup and stray business_key roles exist, the KeyGroup decides
    m = Model(ProjectConfig(name="t", dbt_target="duckdb_dev"))
    m.add(
        LogicalEntity(
            id="01LE3",
            name="thing",
            attributes=[
                Attribute(id="01P", name="pk_col", domain="bigint", nullable=False),
                # a stray business_key role that should be IGNORED for PK emission
                Attribute(id="01STRAY", name="other", domain="string",
                          role="business_key", nullable=False),
            ],
        )
    )
    m.add(KeyGroup(id="01K", entity="01LE3", name="pk_thing", type="pk", members=["01P"]))
    sy = _schema(m, tmp_path)
    # exactly one primary_key, on the KeyGroup member — not the stray role
    assert sy.count("type: primary_key") == 1
    # the pk constraint sits under pk_col, not under other
    pk_col_block = sy.split("name: pk_col", 1)[1].split("name: other")[0]
    assert "type: primary_key" in pk_col_block
