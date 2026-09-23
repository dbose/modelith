"""`mdl reverse --connect` (live datastore) and `--init-profile` CLI wiring.

No warehouse or dbt in CI: the connect.py boundary is mocked. These pin the CLI
orchestration — source validation, the dbt-debug gate, constraint introspection feeding
the shared reverse engine, --no-constraints, and the profile scaffold.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.main import app
from mdl_reverse.connect import ConnectResult

runner = CliRunner()

# A mdl_get_constraints payload: two governed tables + a staging table, a *_sk surrogate,
# a declared PK and FK — the shape connect.introspect_constraints returns.
_CONSTRAINTS = {
    "customer": {
        "columns": {
            "customer_id": {"type": "BIGINT", "nullable": False},
            "customer_sk": {"type": "VARCHAR", "nullable": False},
            "region_id": {"type": "BIGINT", "nullable": True},
            "legal_name": {"type": "VARCHAR", "nullable": False},
        },
        "primary_key": ["customer_id"],
        "unique": [],
        "foreign_keys": [
            {"columns": ["region_id"], "ref_table": "region", "ref_columns": ["region_id"]}
        ],
    },
    "region": {
        "columns": {"region_id": {"type": "BIGINT", "nullable": False}},
        "primary_key": ["region_id"], "unique": [], "foreign_keys": [],
    },
    "stg_raw": {
        "columns": {"id": {"type": "BIGINT", "nullable": True}},
        "primary_key": [], "unique": [], "foreign_keys": [],
    },
}


def _patch_connect(monkeypatch, *, debug_ok=True, constraints=None):
    import mdl_reverse.connect as c

    monkeypatch.setattr(c, "dbt_debug", lambda *a, **k: ConnectResult(debug_ok, "msg"))
    monkeypatch.setattr(
        c, "introspect_constraints",
        lambda *a, **k: (constraints if constraints is not None else _CONSTRAINTS),
    )


def _profiles(tmp_path: Path) -> Path:
    (tmp_path / "profiles.yml").write_text(
        "warehouse:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: w.duckdb\n",
        encoding="utf-8",
    )
    return tmp_path


# --- --init-profile ----------------------------------------------------------


def test_init_profile_writes_env_var_template(tmp_path):
    r = runner.invoke(app, ["reverse", "--init-profile", "snowflake", "--profiles-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    text = (tmp_path / "profiles.yml").read_text()
    assert "env_var('MDL_SNOWFLAKE_PASSWORD')" in text
    assert "export MDL_SNOWFLAKE_PASSWORD" in r.output
    assert "pip install dbt-snowflake" in r.output


def test_init_profile_unknown_adapter_errors(tmp_path):
    r = runner.invoke(app, ["reverse", "--init-profile", "oracle", "--profiles-dir", str(tmp_path)])
    assert r.exit_code == 1
    assert "unknown adapter" in r.output


# --- --connect ---------------------------------------------------------------


def test_connect_reverses_with_declared_keys(tmp_path, monkeypatch):
    _patch_connect(monkeypatch)
    out = tmp_path / "model"
    r = runner.invoke(app, [
        "reverse", "--connect", "--schema", "main",
        "--profiles-dir", str(_profiles(tmp_path)), "--out", str(out), "--no-review",
    ])
    assert r.exit_code == 0, r.output
    assert "connection ok" in r.output
    assert "3 table(s), 1 declared foreign key(s)" in r.output
    # the declared FK is auto-accepted (high confidence), surrogate stripped, staging out
    assert "high] customer.region_id -> region" in r.output
    assert "strip surrogate_key customer.customer_sk" in r.output
    assert (out / "mdl-project.yaml").exists()


def test_connect_requires_schema(tmp_path, monkeypatch):
    _patch_connect(monkeypatch)
    r = runner.invoke(app, [
        "reverse", "--connect", "--profiles-dir", str(_profiles(tmp_path)), "--out", str(tmp_path / "m"),
    ])
    assert r.exit_code == 1
    assert "--schema" in r.output


def test_connect_debug_failure_aborts(tmp_path, monkeypatch):
    _patch_connect(monkeypatch, debug_ok=False)
    r = runner.invoke(app, [
        "reverse", "--connect", "--schema", "main",
        "--profiles-dir", str(_profiles(tmp_path)), "--out", str(tmp_path / "m"),
    ])
    assert r.exit_code == 4
    assert "connection test failed" in r.output


def test_connect_no_constraints_infers_keys(tmp_path, monkeypatch):
    _patch_connect(monkeypatch)
    out = tmp_path / "model"
    r = runner.invoke(app, [
        "reverse", "--connect", "--schema", "main", "--no-constraints",
        "--profiles-dir", str(_profiles(tmp_path)), "--out", str(out), "--no-review",
    ])
    assert r.exit_code == 0, r.output
    assert "columns/types only, keys will be inferred" in r.output
    # with keys dropped, the FK is now a MEDIUM name/type proposal, not a high-conf accept
    assert "0 declared foreign key(s)" in r.output
    assert "medium] customer.region_id -> region" in r.output


def test_connect_empty_schema_warns(tmp_path, monkeypatch):
    _patch_connect(monkeypatch, constraints={})
    r = runner.invoke(app, [
        "reverse", "--connect", "--schema", "nope",
        "--profiles-dir", str(_profiles(tmp_path)), "--out", str(tmp_path / "m"), "--no-review",
    ])
    assert "no tables found" in r.output


# --- source validation -------------------------------------------------------


def test_connect_and_ddl_are_mutually_exclusive(tmp_path):
    r = runner.invoke(app, ["reverse", "--connect", "--ddl", "x.sql", "--out", str(tmp_path / "m")])
    assert r.exit_code == 1
    assert "exactly one source" in r.output
