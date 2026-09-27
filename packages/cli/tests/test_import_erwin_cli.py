"""CLI: `mdl import erwin` — write-a-model default + --apply into an existing model."""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.main import app

runner = CliRunner()

# the real-shaped erwin fixture (reuse the reader's test fixture)
_FIXTURE = (
    Path(__file__).resolve().parents[2] / "reverse" / "tests" / "test_erwin.py"
)
sys.path.insert(0, str(_FIXTURE.parent))


def _erwin_xml() -> str:
    text = _FIXTURE.read_text()
    return text.split('_ERWIN = """')[1].split('"""')[0]


def test_import_erwin_writes_a_model(tmp_path):
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "model"
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert (out / "logical" / "entities" / "counterparty.yaml").exists()
    # warnings surface as notes
    assert "note:" in r.output
    # and the written model validates
    v = runner.invoke(app, ["validate", "-m", str(out)])
    assert v.exit_code == 0, v.output


def test_import_erwin_apply_into_existing(tmp_path):
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "mdl-project.yaml").write_text("name: existing\ndbt_target: duckdb\n")
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(existing), "--apply"])
    assert r.exit_code == 0, r.output
    assert "merged" in r.output
    assert (existing / "logical" / "entities" / "trade.yaml").exists()
    v = runner.invoke(app, ["validate", "-m", str(existing)])
    assert v.exit_code == 0, v.output


def test_import_erwin_diverts_on_existing_model(tmp_path):
    # a second write into a dir that already holds a model diverts to a sibling
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "model"
    assert runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out)]).exit_code == 0
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out)])
    assert r.exit_code == 0, r.output
    # the original is untouched; a -reversed-v1 sibling was written
    assert (tmp_path / "model-reversed-v1" / "mdl-project.yaml").exists()


def test_import_erwin_empty_folder_bootstraps_model(tmp_path):
    # empty folder + default --scaffold model → a runnable project skeleton + objects
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "proj"
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert (out / ".mdl" / "lock.yaml").exists()
    assert (out / ".mdl" / "state").is_dir()
    assert (out / ".gitignore").exists()
    assert (out / "logical" / "entities" / "counterparty.yaml").exists()
    # the layout + dbt target persist in the config
    from mdl_core.yaml_io import load_file

    cfg = load_file(out / "mdl-project.yaml")
    assert dict(cfg["scaffold"])["layout"] == "model"
    assert cfg["dbt_target"] == "duckdb_dev"
    assert runner.invoke(app, ["validate", "-m", str(out)]).exit_code == 0


def test_import_erwin_workspace_layout(tmp_path):
    # --scaffold workspace → objects under model/, plus the transform/warehouse project
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "repo"
    r = runner.invoke(
        app, ["import", "erwin", str(xml), "-o", str(out), "--scaffold", "workspace"]
    )
    assert r.exit_code == 0, r.output
    assert (out / "model" / "logical" / "entities" / "counterparty.yaml").exists()
    assert (out / "model" / ".mdl" / "lock.yaml").exists()
    assert (out / "transform" / "warehouse" / "dbt_project.yml").exists()
    assert (out / "transform" / "warehouse" / "profiles.yml").exists()
    assert (out / ".github" / "CODEOWNERS").exists()
    from mdl_core.yaml_io import load_file

    cfg = load_file(out / "model" / "mdl-project.yaml")
    assert dict(cfg["scaffold"])["dbt_project_dir"] == "transform/warehouse"
    assert runner.invoke(app, ["validate", "-m", str(out / "model")]).exit_code == 0


def test_import_erwin_workspace_reuses_existing_dbt_project(tmp_path):
    # a workspace that already has a dbt project must NOT get a second dumb DuckDB one
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "repo"
    out.mkdir()
    # the user's real dbt project lives at dbt/ with a Snowflake-ish profile
    (out / "dbt").mkdir()
    (out / "dbt" / "dbt_project.yml").write_text(
        "name: analytics\nversion: '1.0.0'\nprofile: analytics\n"
    )
    r = runner.invoke(
        app, ["import", "erwin", str(xml), "-o", str(out), "--scaffold", "workspace"]
    )
    assert r.exit_code == 0, r.output
    # NO throwaway transform/warehouse project/profiles was scaffolded
    assert not (out / "transform" / "warehouse" / "dbt_project.yml").exists()
    assert not (out / "transform" / "warehouse" / "profiles.yml").exists()
    # the model is wired to the real dbt project, not a phantom DuckDB one
    from mdl_core.yaml_io import load_file

    cfg = load_file(out / "model" / "mdl-project.yaml")
    assert dict(cfg["scaffold"])["dbt_project_dir"] == "dbt"
    assert "existing dbt project" in r.output
    assert runner.invoke(app, ["validate", "-m", str(out / "model")]).exit_code == 0


def test_import_erwin_none_layout_no_skeleton(tmp_path):
    # --scaffold none → model objects only, no .mdl skeleton (pre-scaffold behaviour)
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "bare"
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out), "--scaffold", "none"])
    assert r.exit_code == 0, r.output
    assert (out / "logical" / "entities" / "counterparty.yaml").exists()
    assert not (out / ".mdl" / "lock.yaml").exists()


def test_import_erwin_respects_existing_structure(tmp_path):
    # a dir that already has a project keeps its config; the import merges, no re-scaffold
    xml = tmp_path / "trading.xml"
    xml.write_text(_erwin_xml())
    out = tmp_path / "existing"
    out.mkdir()
    (out / "mdl-project.yaml").write_text(
        "name: existing\ndbt_target: snowflake_prod\nplatform_targets: [snowflake_prod]\n"
    )
    r = runner.invoke(app, ["import", "erwin", str(xml), "-o", str(out), "--apply"])
    assert r.exit_code == 0, r.output
    # the user's target is preserved, not overwritten by the scaffold default
    from mdl_core.yaml_io import load_file

    cfg = load_file(out / "mdl-project.yaml")
    assert cfg["dbt_target"] == "snowflake_prod"
