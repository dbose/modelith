"""The erwin fields (verb phrases + physical names) surface in the projection and are
editable via the mutation commands — so imported data is visible and refinable in the UI."""

from __future__ import annotations

from pathlib import Path

from mdl_server.projection import project

from mdl_core.commands import apply_command
from mdl_core.repo import ModelRepo
from mdl_reverse.erwin import import_erwin
from mdl_reverse.writer import write_model

_FIXTURE = Path(__file__).resolve().parents[2] / "reverse" / "tests" / "test_erwin.py"


def _erwin_model(tmp_path: Path) -> Path:
    xml = _FIXTURE.read_text().split('_ERWIN = """')[1].split('"""')[0]
    write_model(import_erwin(xml).model, tmp_path)
    return tmp_path


def test_projection_emits_verb_phrases_and_physical_names(tmp_path):
    repo = ModelRepo.load(_erwin_model(tmp_path))
    proj = project(repo.model)
    rel = next(r for r in proj["relationships"] if r["name"] == "trade_has_counterparty")
    assert rel["verb_phrase"] == "is party to"
    assert rel["inverse_verb_phrase"] == "is with"
    cp = next(e for e in proj["entities"] if e["name"] == "counterparty")
    assert cp["physical_name"] == "COUNTERPARTY"


def test_update_relationship_verb_phrases(tmp_path):
    d = _erwin_model(tmp_path)
    repo = ModelRepo.load(d)
    rel = next(r for r in repo.model.relationships.values() if r.name == "trade_has_counterparty")
    apply_command(d, "update_relationship", {"id": rel.id, "verb_phrase": "settles with"})
    apply_command(d, "update_relationship", {"id": rel.id, "inverse_verb_phrase": ""})
    out = ModelRepo.load(d).model.relationships[rel.id]
    assert out.verb_phrase == "settles with"
    assert out.inverse_verb_phrase is None  # empty clears


def test_rename_entity_sets_physical_name(tmp_path):
    d = _erwin_model(tmp_path)
    repo = ModelRepo.load(d)
    cp = next(e for e in repo.model.logical_entities.values() if e.name == "counterparty")
    apply_command(d, "rename_entity", {"id": cp.id, "physical_name": "CPTY_TBL"})
    assert ModelRepo.load(d).model.logical_entities[cp.id].physical_name == "CPTY_TBL"


def test_update_attribute_physical_name(tmp_path):
    d = _erwin_model(tmp_path)
    repo = ModelRepo.load(d)
    cp = next(e for e in repo.model.logical_entities.values() if e.name == "counterparty")
    a = cp.attributes[0]
    apply_command(
        d, "update_attribute", {"entity_id": cp.id, "attribute_id": a.id, "physical_name": "CP_ID"}
    )
    out = ModelRepo.load(d).model.logical_entities[cp.id]
    assert out.attributes[0].physical_name == "CP_ID"
