"""CLI: `mdl ontology status` (JSON review queue + coverage) and promote/reject by uri."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.main import app
from mdl_core.commands import apply_command
from mdl_core.repo import ModelRepo

_CORE_TESTS = Path(__file__).resolve().parents[2] / "core" / "tests"
sys.path.insert(0, str(_CORE_TESTS))
from model_builders import write_model  # noqa: E402

runner = CliRunner()

_URI = "https://spec.edmcouncil.org/fibo/Party"


def _seed_proposed(model_dir: Path) -> str:
    """Add a proposed alignment to the first conceptual entity; return its name."""
    ce = next(iter(ModelRepo.load(model_dir).model.conceptual_entities.values()))
    apply_command(
        model_dir,
        "set_alignment",
        {"id": ce.id, "aligns_to": _URI, "alignment": "skos:closeMatch",
         "status": "proposed", "ref_layer": "industry"},
    )
    return ce.name


def test_status_lists_proposed_and_coverage(tmp_path):
    write_model(tmp_path)
    name = _seed_proposed(tmp_path)
    r = runner.invoke(app, ["ontology", "status", "-m", str(tmp_path), "--format", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert any(p["name"] == name and p["uri"] == _URI for p in data["proposed"])
    assert "coverage_pct" in data["coverage"]


def test_promote_by_uri_clears_the_queue(tmp_path):
    write_model(tmp_path)
    name = _seed_proposed(tmp_path)
    assert runner.invoke(
        app, ["ontology", "promote", name, "-m", str(tmp_path), "--uri", _URI]
    ).exit_code == 0
    r = runner.invoke(app, ["ontology", "status", "-m", str(tmp_path), "--format", "json"])
    assert not json.loads(r.output)["proposed"]  # promoted -> no longer proposed


def test_reject_by_uri_removes_the_ref(tmp_path):
    write_model(tmp_path)
    name = _seed_proposed(tmp_path)
    assert runner.invoke(
        app, ["ontology", "reject", name, "-m", str(tmp_path), "--uri", _URI]
    ).exit_code == 0
    r = runner.invoke(app, ["ontology", "status", "-m", str(tmp_path), "--format", "json"])
    assert not json.loads(r.output)["proposed"]


def test_status_empty_when_nothing_proposed(tmp_path):
    write_model(tmp_path)
    r = runner.invoke(app, ["ontology", "status", "-m", str(tmp_path), "--format", "json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["proposed"] == []
