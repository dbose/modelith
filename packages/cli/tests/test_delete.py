"""`mdl delete entity` — safe-by-default deletion with impact preflight."""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.main import app
from mdl_core.repo import ModelRepo

_CORE_TESTS = Path(__file__).resolve().parents[2] / "core" / "tests"
sys.path.insert(0, str(_CORE_TESTS))
from model_builders import write_model  # noqa: E402

runner = CliRunner()


def _mk(tmp_path):
    write_model(tmp_path)
    return tmp_path


def _add_widget(m):
    """A fresh entity with no relationships/physical tables (unreferenced)."""
    r = runner.invoke(app, ["new", "entity", "widget", "-m", str(m)])
    assert r.exit_code == 0, r.output


def test_delete_unreferenced_entity(tmp_path):
    m = _mk(tmp_path)
    _add_widget(m)
    r = runner.invoke(app, ["delete", "entity", "widget", "-m", str(m), "--yes"])
    assert r.exit_code == 0, r.output
    assert "deleted entity 'widget'" in r.output
    repo = ModelRepo.load(m)
    assert all(e.name != "widget" for e in repo.model.logical_entities.values())


def test_delete_refuses_referenced_entity_without_cascade(tmp_path):
    m = _mk(tmp_path)
    # `trade` is referenced by the trade_has_counterparty relationship
    r = runner.invoke(app, ["delete", "entity", "trade", "-m", str(m), "--yes"])
    assert r.exit_code == 1
    assert "refusing" in r.output and "--cascade" in r.output
    # nothing was deleted
    repo = ModelRepo.load(m)
    assert any(e.name == "trade" for e in repo.model.logical_entities.values())


def test_delete_cascade_removes_and_stays_valid(tmp_path):
    m = _mk(tmp_path)
    r = runner.invoke(app, ["delete", "entity", "trade", "-m", str(m), "--cascade", "--yes"])
    assert r.exit_code == 0, r.output
    repo = ModelRepo.load(m)
    assert all(e.name != "trade" for e in repo.model.logical_entities.values())
    assert len(repo.model.relationships) == 0


def test_delete_unknown_entity(tmp_path):
    m = _mk(tmp_path)
    r = runner.invoke(app, ["delete", "entity", "nonesuch", "-m", str(m), "--yes"])
    assert r.exit_code == 1
    assert "no logical entity named 'nonesuch'" in r.output


def test_delete_prompts_without_yes(tmp_path):
    m = _mk(tmp_path)
    _add_widget(m)
    # answer "n" to the confirmation → aborted, nothing deleted
    r = runner.invoke(app, ["delete", "entity", "widget", "-m", str(m)], input="n\n")
    assert r.exit_code == 0
    assert "aborted" in r.output
    repo = ModelRepo.load(m)
    assert any(e.name == "widget" for e in repo.model.logical_entities.values())
