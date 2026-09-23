"""connect.py — the live-database subprocess boundary. No warehouse or dbt in CI: the
dbt calls are mocked. The macro's real SQL is validated separately against DuckDB
(manual acceptance); here we pin the Python orchestration, arg-building, profile scaffold,
and output parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdl_core.yaml_io import load_file
from mdl_reverse import connect

# --- scaffold_profile --------------------------------------------------------


def test_scaffold_snowflake_uses_env_var_for_secrets(tmp_path: Path):
    res = connect.scaffold_profile(
        "snowflake", tmp_path, profile_name="warehouse", target="prod",
        fields={"account": "acme", "database": "DB", "schema": "PUBLIC",
                "warehouse": "WH", "role": "RO", "user": "svc"},
    )
    data = load_file(res.path)["warehouse"]["outputs"]["prod"]
    # non-secret fields are literal
    assert data["account"] == "acme"
    assert data["database"] == "DB"
    # the password is an env_var placeholder, never a literal
    assert "env_var('MDL_SNOWFLAKE_PASSWORD')" in data["password"]
    assert res.env_vars == ["MDL_SNOWFLAKE_PASSWORD"]


def test_scaffold_duckdb_has_no_secrets(tmp_path: Path):
    res = connect.scaffold_profile("duckdb", tmp_path, fields={"path": "w.duckdb"})
    assert res.env_vars == []
    assert load_file(res.path)["warehouse"]["outputs"]["dev"]["path"] == "w.duckdb"


def test_scaffold_unknown_adapter_raises(tmp_path: Path):
    with pytest.raises(connect.ConnectError, match="unknown adapter"):
        connect.scaffold_profile("oracle", tmp_path)


# --- probe_dbt (mock `dbt --version`) ----------------------------------------


def _mock_run(monkeypatch, mapping):
    """Patch connect._run so a substring of the argv picks a canned RunResult."""
    def fake(argv, cwd=None, timeout=connect.DEFAULT_TIMEOUT):
        joined = " ".join(argv)
        for needle, result in mapping.items():
            if needle in joined:
                return result
        return connect.RunResult(1, "", f"unmatched: {joined}")
    monkeypatch.setattr(connect, "_run", fake)


def test_probe_all_present(monkeypatch):
    _mock_run(monkeypatch, {
        "--version": connect.RunResult(0, "Core: installed: 1.8\nPlugins:\n  - snowflake: 1.8", ""),
    })
    p = connect.probe_dbt("snowflake")
    assert p.dbt_found and p.adapter_found and p.ready
    assert p.install_cmd is None


def test_probe_missing_adapter_gives_pip_command(monkeypatch):
    _mock_run(monkeypatch, {
        "--version": connect.RunResult(0, "Core: installed: 1.8\nPlugins:\n  - duckdb: 1.8", ""),
    })
    p = connect.probe_dbt("snowflake")
    assert p.dbt_found and not p.adapter_found and not p.ready
    assert p.install_cmd == "pip install dbt-snowflake"


def test_probe_no_dbt(monkeypatch):
    _mock_run(monkeypatch, {"--version": connect.RunResult(127, "", "command not found: dbt")})
    p = connect.probe_dbt("postgres")
    assert not p.dbt_found
    assert "dbt-core" in p.install_cmd and "dbt-postgres" in p.install_cmd


# --- dbt_debug ---------------------------------------------------------------


def _profiles(tmp_path: Path) -> Path:
    (tmp_path / "profiles.yml").write_text(
        "warehouse:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: w.duckdb\n",
        encoding="utf-8",
    )
    return tmp_path


def test_dbt_debug_success(monkeypatch, tmp_path):
    _mock_run(monkeypatch, {"debug": connect.RunResult(0, "All checks passed!", "")})
    r = connect.dbt_debug(_profiles(tmp_path))
    assert r.ok and "All checks passed" in r.message


def test_dbt_debug_failure_surfaces_message(monkeypatch, tmp_path):
    _mock_run(monkeypatch, {
        "debug": connect.RunResult(1, "Connection test: [ERROR]\nCould not connect", ""),
    })
    r = connect.dbt_debug(_profiles(tmp_path))
    assert not r.ok and "Could not connect" in r.message


# --- introspect_constraints (mock the run-operation) -------------------------

_PAYLOAD = (
    'MDL_CONSTRAINTS_JSON {"customer": {"columns": '
    '{"customer_id": {"type": "BIGINT", "nullable": false}}, '
    '"primary_key": ["customer_id"], "unique": [], "foreign_keys": []}}'
)


def test_introspect_constraints_parses_payload(monkeypatch, tmp_path):
    _mock_run(monkeypatch, {
        "run-operation": connect.RunResult(0, f"12:00 Running\n{_PAYLOAD}\n12:00 Done", ""),
    })
    out = connect.introspect_constraints(_profiles(tmp_path), schema="main")
    assert out["customer"]["primary_key"] == ["customer_id"]
    assert out["customer"]["columns"]["customer_id"]["type"] == "BIGINT"


def test_introspect_constraints_failure_raises(monkeypatch, tmp_path):
    _mock_run(monkeypatch, {
        "run-operation": connect.RunResult(1, "", "Database Error: schema does not exist"),
    })
    with pytest.raises(connect.ConnectError, match="introspection failed"):
        connect.introspect_constraints(_profiles(tmp_path), schema="nope")


def test_constraints_args_only_macro_kwargs():
    # our macro takes `schema`/`database`, NOT generate_source's schema_name/database_name
    args = connect._constraints_args("main", "prod")
    assert args == {"schema": "main", "database": "prod"}


def test_select_filters_tables_client_side(monkeypatch, tmp_path):
    # payload carries customer + orders; --select keeps only the named ones
    payload = (
        'MDL_CONSTRAINTS_JSON {"customer": {"columns": {}, "primary_key": [], '
        '"unique": [], "foreign_keys": []}, "orders": {"columns": {}, '
        '"primary_key": [], "unique": [], "foreign_keys": []}}'
    )
    _mock_run(monkeypatch, {"run-operation": connect.RunResult(0, payload, "")})
    out = connect.introspect_constraints(_profiles(tmp_path), schema="main", select="customer")
    assert set(out) == {"customer"}  # orders filtered out


# --- the throwaway project ---------------------------------------------------


def test_throwaway_project_has_macro_and_matching_profile(tmp_path):
    prof = _profiles(tmp_path)
    with connect._throwaway_project(prof) as proj:
        # the macro is copied in
        assert (proj / "macros" / "mdl_get_constraints.sql").exists()
        cfg = load_file(proj / "dbt_project.yml")
        # profile: matches the user's profiles.yml top-level name
        assert cfg["profile"] == "warehouse"
        assert cfg["macro-paths"] == ["macros"]
        # no packages.yml (macro is self-contained, no dbt-codegen)
        assert not (proj / "packages.yml").exists()
    # cleaned up after
    assert not proj.exists()
