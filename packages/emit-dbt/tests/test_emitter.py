from pathlib import Path

from mdl_core.repo import ModelRepo
from mdl_emit_dbt.emitter import DbtEmitter


def _gen(model_dir: Path, out: Path, inline: bool = False):
    repo = ModelRepo.load(model_dir)
    return DbtEmitter(repo.model, "duckdb_dev", inline=inline).generate(out, write=True)


def test_emits_working_sql_not_stub(model_dir: Path, tmp_path: Path):
    _gen(model_dir, tmp_path / "dbt")
    sql = (tmp_path / "dbt" / "models" / "counterparty.sql").read_text()
    assert "select 1" not in sql.replace("select 1 as", "")  # no bare stub
    assert "ref('stg_counterparty')" in sql
    assert "counterparty_id" in sql


def test_schema_yml_has_contract_and_meta(model_dir: Path, tmp_path: Path):
    _gen(model_dir, tmp_path / "dbt")
    sy = (tmp_path / "dbt" / "models" / "schema.yml").read_text()
    assert "contract:" in sy and "enforced: true" in sy
    assert "mdl_ulid:" in sy
    assert "ontology_iri: fibo-fnd-pty-pty:PartyInRole" in sy
    assert "owner: risk" in sy


def test_scd2_uses_macro_by_default(scd2_model_dir: Path, tmp_path: Path):
    _gen(scd2_model_dir, tmp_path / "dbt")
    sql = (tmp_path / "dbt" / "models" / "counterparty.sql").read_text()
    assert "mdl_scd2(" in sql
    # macro package shipped alongside
    assert (tmp_path / "dbt" / "macros" / "mdl_scd2.sql").exists()


def test_scd2_inline_escape_hatch(scd2_model_dir: Path, tmp_path: Path):
    _gen(scd2_model_dir, tmp_path / "dbt", inline=True)
    sql = (tmp_path / "dbt" / "models" / "counterparty.sql").read_text()
    assert "mdl_scd2(" not in sql
    assert "is_current" in sql and "valid_from" in sql


def test_fk_compensated_by_dbt_test(model_dir: Path, tmp_path: Path):
    # duckdb adapter declares FK informational -> emitter records relationship for a test.
    res = _gen(model_dir, tmp_path / "dbt")
    # generation state should include the relationship ULID under schema.yml
    schema_state = res.state.get("models/schema.yml")
    assert schema_state is not None
    assert len(schema_state.ulids) >= 1
