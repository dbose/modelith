"""CLI: `mdl generate` output-dir resolution — explicit -o wins, else
scaffold.dbt_project_dir, else target/dbt."""

from __future__ import annotations

from typer.testing import CliRunner

from mdl_cli.main import app

runner = CliRunner()


def _init(tmp_path):
    d = tmp_path / "proj"
    assert runner.invoke(app, ["init", str(d)]).exit_code == 0
    return d


def test_generate_defaults_to_scaffold_dbt_project_dir(tmp_path):
    d = _init(tmp_path)
    runner.invoke(app, ["config", "set", "scaffold.dbt_project_dir", "transform/warehouse", "-m", str(d)])
    r = runner.invoke(app, ["generate", "-m", str(d)])
    assert r.exit_code == 0, r.output
    # models were written under the configured dir, not target/dbt
    assert (d / "transform" / "warehouse" / "models").is_dir()
    assert not (d / "target" / "dbt").exists()


def test_generate_falls_back_to_target_dbt(tmp_path, monkeypatch):
    d = _init(tmp_path)  # no scaffold.dbt_project_dir set
    # the historical fallback is target/dbt relative to CWD; run from the project dir
    monkeypatch.chdir(d)
    r = runner.invoke(app, ["generate", "-m", str(d)])
    assert r.exit_code == 0, r.output
    assert (d / "target" / "dbt" / "models").is_dir()


def test_generate_explicit_out_overrides(tmp_path):
    d = _init(tmp_path)
    runner.invoke(app, ["config", "set", "scaffold.dbt_project_dir", "transform/warehouse", "-m", str(d)])
    out = d / "custom"
    r = runner.invoke(app, ["generate", "-m", str(d), "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert (out / "models").is_dir()
    assert not (d / "transform" / "warehouse").exists()
