"""The dbt emitter must fail loudly when two entities map to the same model name.

A dbt model name is the file stem AND the ref() key. Two entities mapping to the same
name (or one that only differs in case — the same file on a Windows/macOS filesystem)
would silently overwrite each other's .sql and break ref(). The emitter raises instead.
"""

from __future__ import annotations

import pytest

from mdl_core.ir import LogicalEntity, Model, PhysicalTable, ProjectConfig
from mdl_emit_dbt.emitter import DbtEmitter


def _model_with(names: list[str], *, physical: bool) -> Model:
    m = Model(ProjectConfig(name="m", dbt_target="duckdb_dev"))
    for i, nm in enumerate(names):
        le = LogicalEntity(id=f"01J00000000000000000000L{i}", name=f"e{i}", realises=None)
        m.add(le)
        if physical:
            m.add(
                PhysicalTable(
                    id=f"01J00000000000000000000P{i}",
                    target="duckdb_dev",
                    realises=le.id,
                    name=nm,
                    materialization="table",
                )
            )
    return m


def test_case_only_collision_raises(tmp_path):
    # two physical tables Foo / FOO -> same file on a case-insensitive filesystem
    m = _model_with(["Foo", "FOO"], physical=True)
    with pytest.raises(ValueError, match="same dbt model name"):
        DbtEmitter(m, "duckdb_dev").generate(tmp_path, write=False)


def test_exact_collision_raises(tmp_path):
    m = _model_with(["orders", "orders"], physical=True)
    with pytest.raises(ValueError, match="same dbt model name"):
        DbtEmitter(m, "duckdb_dev").generate(tmp_path, write=False)


def test_distinct_names_do_not_raise(tmp_path):
    m = _model_with(["orders", "customers"], physical=True)
    # should plan cleanly (write=False, no disk touch)
    DbtEmitter(m, "duckdb_dev").generate(tmp_path, write=False)
