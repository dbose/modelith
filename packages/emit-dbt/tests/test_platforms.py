"""Platform adapter tests (spec §7.2)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.repo import ModelRepo
from mdl_emit_dbt.emitter import DbtEmitter
from mdl_emit_dbt.platforms import (
    IcebergAdapter,
    RedshiftAdapter,
    SnowflakeAdapter,
    TrinoAdapter,
    available_adapters,
    get_adapter,
)


def test_all_adapters_registered():
    assert set(available_adapters()) == {"duckdb", "snowflake", "redshift", "iceberg", "trino"}


def test_snowflake_type_map_and_caps():
    a = SnowflakeAdapter()
    assert a.map_base_type("bigint") == "NUMBER(38,0)"
    assert a.map_base_type("timestamp") == "TIMESTAMP_NTZ"
    caps = a.constraint_support()
    assert not caps.foreign_key and not caps.primary_key  # informational only
    assert a.dialect() == "snowflake"


def test_redshift_dist_sort_options():
    from mdl_core.ir import Attribute, LogicalEntity

    le = LogicalEntity(
        id="01J8ZR000000000000000000AA",
        name="dim_customer",
        attributes=[Attribute(id="01J8ZS000000000000000000AA", name="customer_id", role="business_key")],
    )
    opts = RedshiftAdapter().physical_options(le)
    assert opts["dist"] == "customer_id"
    assert opts["sort"] == ["customer_id"]


def test_iceberg_partition_options():
    from mdl_core.ir import Attribute, LogicalEntity

    le = LogicalEntity(
        id="01J8ZR000000000000000000AB",
        name="fct_sales",
        attributes=[Attribute(id="01J8ZS000000000000000000AB", name="sale_id", role="business_key")],
    )
    opts = IcebergAdapter().physical_options(le)
    assert opts["table_format"] == "iceberg"
    assert opts["partition_by"] == ["sale_id"]


def test_trino_dialect():
    assert TrinoAdapter().dialect() == "trino"


def test_unknown_adapter_raises():
    import pytest

    with pytest.raises(KeyError):
        get_adapter("bigquery")


def test_emitter_uses_snowflake_types_and_clustering(model_dir: Path, tmp_path: Path):
    repo = ModelRepo.load(model_dir)
    DbtEmitter(repo.model, "snowflake_prod").generate(tmp_path / "dbt", write=True)
    sy = (tmp_path / "dbt" / "models" / "schema.yml").read_text()
    assert "NUMBER(38,0)" in sy  # snowflake bigint mapping
    sql = (tmp_path / "dbt" / "models" / "counterparty.sql").read_text()
    assert "cluster_by=['counterparty_id']" in sql and "transient=False" in sql


def test_emitter_idempotent_per_platform(model_dir: Path, tmp_path: Path):
    for tgt in ("snowflake_prod", "redshift_dw", "iceberg_lake", "trino_cat"):
        repo = ModelRepo.load(model_dir)
        out = tmp_path / tgt
        DbtEmitter(repo.model, tgt).generate(out, write=True)
        first = {p.name: p.read_text() for p in out.rglob("*") if p.is_file()}
        DbtEmitter(ModelRepo.load(model_dir).model, tgt).generate(out, write=True)
        second = {p.name: p.read_text() for p in out.rglob("*") if p.is_file()}
        assert first == second, f"{tgt} not idempotent"
