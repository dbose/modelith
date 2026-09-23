"""`mdl model render` + `mdl diff --against <dir>` — the compare-reversed-models CLI.

Two independent reverses have different ULIDs, so `--against` keys by name: identical
models diff to nothing, a real change shows the delta. `model render` emits the ULID-free
canonical text the VS Code compare feeds to native diff.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.demo import scaffold_demo
from mdl_cli.main import app

runner = CliRunner()


def _demo(root: Path) -> Path:
    scaffold_demo(root)
    return root / "model"


def test_model_render_is_ulid_free(tmp_path):
    model = _demo(tmp_path)
    r = runner.invoke(app, ["model", "render", "-m", str(model)])
    assert r.exit_code == 0, r.output
    assert "entity" in r.output
    import re
    assert not re.search(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", r.output), "a ULID leaked into render"


def test_diff_against_identical_copy_is_empty(tmp_path):
    model = _demo(tmp_path)
    # a byte copy is the same model with the SAME ULIDs -> also empty, but exercises the path
    import shutil
    other = tmp_path / "model-copy"
    shutil.copytree(model, other)
    r = runner.invoke(app, ["diff", "-m", str(model), "--against", str(other)])
    assert r.exit_code == 0, r.output
    assert "no changes" in r.output


def test_diff_against_detects_a_change(tmp_path):
    model = _demo(tmp_path)
    import shutil
    other = tmp_path / "model-2"
    shutil.copytree(model, other)
    # drop one entity file from `other` so the diff reports it removed (by name)
    ent_dir = other / "logical" / "entities"
    victims = sorted(ent_dir.glob("*.yaml"))
    assert victims, "demo should have logical entities"
    victims[0].unlink()
    r = runner.invoke(app, ["diff", "-m", str(model), "--against", str(other)])
    # a removed entity is breaking -> exit 2, and named in the output
    assert r.exit_code == 2, r.output
    assert "removed" in r.output.lower()
