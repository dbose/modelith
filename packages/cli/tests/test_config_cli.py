"""CLI: `mdl config get/set` — the general mdl-project.yaml config doorway."""

from __future__ import annotations

from typer.testing import CliRunner

from mdl_cli.main import app

runner = CliRunner()


def _init(tmp_path):
    d = tmp_path / "proj"
    assert runner.invoke(app, ["init", str(d)]).exit_code == 0
    return d


def test_config_set_get_scaffold_dir(tmp_path):
    d = _init(tmp_path)
    r = runner.invoke(
        app, ["config", "set", "scaffold.dbt_project_dir", "transform/warehouse", "-m", str(d)]
    )
    assert r.exit_code == 0, r.output
    g = runner.invoke(app, ["config", "get", "scaffold.dbt_project_dir", "-m", str(d)])
    assert g.exit_code == 0, g.output
    assert g.output.strip() == "transform/warehouse"


def test_config_set_scalar_and_list(tmp_path):
    d = _init(tmp_path)
    assert runner.invoke(app, ["config", "set", "dbt_target", "snowflake_prod", "-m", str(d)]).exit_code == 0
    assert (
        runner.invoke(
            app, ["config", "set", "platform_targets", "duckdb_dev,snowflake_prod", "-m", str(d)]
        ).exit_code
        == 0
    )
    from mdl_core.yaml_io import load_file

    cfg = load_file(d / "mdl-project.yaml")
    assert cfg["dbt_target"] == "snowflake_prod"
    assert list(cfg["platform_targets"]) == ["duckdb_dev", "snowflake_prod"]


def test_config_set_rejects_invalid_type(tmp_path):
    d = _init(tmp_path)
    # platform_targets must be a list; force a scalar via a key the coercion leaves as string
    # by targeting a field ProjectConfig types as a list — a bad type is rejected.
    r = runner.invoke(app, ["config", "set", "ontology_stack", "not-a-list", "-m", str(d)])
    assert r.exit_code == 1
    assert "invalid" in r.output.lower()


def test_config_set_preserves_comments(tmp_path):
    d = _init(tmp_path)
    before = (d / "mdl-project.yaml").read_text()
    assert "#" in before  # the scaffolded config is richly commented
    runner.invoke(app, ["config", "set", "dbt_target", "bigquery_dev", "-m", str(d)])
    after = (d / "mdl-project.yaml").read_text()
    assert "#" in after  # comments survive the comment-preserving write
    assert "bigquery_dev" in after


def test_config_get_whole_doc(tmp_path):
    d = _init(tmp_path)
    r = runner.invoke(app, ["config", "get", "-m", str(d)])
    assert r.exit_code == 0
    assert "name:" in r.output


def test_config_clear_scaffold_dir(tmp_path):
    d = _init(tmp_path)
    runner.invoke(app, ["config", "set", "scaffold.dbt_project_dir", "x", "-m", str(d)])
    runner.invoke(app, ["config", "set", "scaffold.dbt_project_dir", "", "-m", str(d)])
    g = runner.invoke(app, ["config", "get", "scaffold.dbt_project_dir", "-m", str(d)])
    assert "not set" in g.output
