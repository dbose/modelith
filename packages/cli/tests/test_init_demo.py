"""`mdl init --demo` tests: pin the Phase 1 §4.2 activation bar.

The demo must scaffold a populated model (6 to 10 entities), pass `mdl validate`
offline with no config, and mint fresh ids each run so two demos never collide.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from mdl_cli.demo import scaffold_demo
from mdl_cli.main import app

runner = CliRunner()


def _logical_entities(model: Path) -> list[Path]:
    return sorted((model / "logical" / "entities").glob("*.yaml"))


def test_init_demo_no_path_defaults_to_home_not_cwd(tmp_path: Path, monkeypatch):
    """`mdl init --demo` with no path must NOT write into the current directory
    (the engineer's repo); it defaults to ~/modelith-demo. Regression guard for the
    pollution bug where the demo scaffolded into whatever repo you were standing in."""
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "some_repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    # Path.home() reads $HOME on posix; set it so the test never touches the real home.
    with mock.patch("mdl_cli.main.Path.home", return_value=home):
        result = runner.invoke(app, ["init", "--demo"])
    assert result.exit_code == 0, result.output
    # the demo landed in ~/modelith-demo ...
    assert (home / "modelith-demo" / "model" / "mdl-project.yaml").exists()
    # ... and the cwd (the "repo") is untouched
    assert not (cwd / "model").exists()
    assert list(cwd.iterdir()) == []


def test_init_demo_scaffolds_a_populated_model(tmp_path: Path):
    """`mdl init --demo` writes a model with 6 to 10 logical entities plus a dbt
    project, and exits 0 (spec §4.2: the first serve shows a real ERD, not a grid)."""
    result = runner.invoke(app, ["init", "--demo", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "demo model" in result.output

    entities = _logical_entities(tmp_path / "model")
    assert 6 <= len(entities) <= 10, [p.name for p in entities]
    # the dbt project came along (Model + prebuilt dbt project), source only
    assert (tmp_path / "transform" / "warehouse" / "dbt_project.yml").exists()
    assert (tmp_path / "transform" / "warehouse" / "profiles.yml").exists()
    assert list((tmp_path / "transform" / "warehouse" / "seeds").glob("*.csv"))


def test_init_demo_validates_offline(tmp_path: Path):
    """The activation metric: a freshly scaffolded demo passes `mdl validate` with
    no network and no config. The bundled project declares an empty ontology_stack,
    so validate never reaches a resolver."""
    runner.invoke(app, ["init", "--demo", str(tmp_path)])
    proj = (tmp_path / "model" / "mdl-project.yaml").read_text(encoding="utf-8")
    assert "ontology_stack: []" in proj  # offline guarantee

    result = runner.invoke(app, ["validate", "-m", str(tmp_path / "model")])
    assert result.exit_code == 0, result.output
    assert "validation passed" in result.output


def test_init_demo_generate_is_conflict_free(tmp_path: Path):
    """The bundle ships only hand-authored dbt files (seeds, staging, macros); the
    generate-owned core models are NOT bundled, so `mdl generate` creates them
    cleanly instead of colliding with a pre-generated copy."""
    runner.invoke(app, ["init", "--demo", str(tmp_path)])
    out = tmp_path / "transform" / "warehouse"
    result = runner.invoke(app, ["generate", "-m", str(tmp_path / "model"), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "conflict" not in result.output.lower()
    # a generated core model now exists next to the hand-authored staging models
    assert (out / "models" / "instrument.sql").exists()
    assert (out / "models" / "staging" / "stg_instrument.sql").exists()


def test_init_demo_mints_fresh_ids(tmp_path: Path):
    """Two demos in two dirs must not share ids, and references stay internally
    consistent (a logical entity's `realises` points at its own conceptual id)."""
    a, b = tmp_path / "a", tmp_path / "b"
    scaffold_demo(a)
    scaffold_demo(b)

    def realises(model: Path) -> str:
        text = (model / "model" / "logical" / "entities" / "counterparty.yaml").read_text()
        return next(line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("realises:"))

    def conceptual_id(model: Path) -> str:
        text = (model / "model" / "conceptual" / "entities" / "counterparty.yaml").read_text()
        return next(line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("id:"))

    # fresh between runs
    assert realises(a) != realises(b)
    # consistent within a run
    assert realises(a) == conceptual_id(a)
    assert realises(b) == conceptual_id(b)


