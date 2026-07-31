"""Glossary read API + where_used traversal (SME app, collab §5.1)."""

from __future__ import annotations

import pytest
from mdl_server.projection import where_used

from mdl_core.repo import ModelRepo


def test_where_used_traverses_conceptual_to_physical(model_dir):
    # generate physical tables so where_used has something to find
    from mdl_emit_dbt.emitter import DbtEmitter

    repo = ModelRepo.load(model_dir)
    # add a physical table by hand (the fixture model has none by default)
    from mdl_core.ids import new_ulid

    le = next(e for e in repo.model.logical_entities.values() if e.name == "counterparty")
    pt_file = model_dir / "physical" / "duckdb_dev" / "tables" / "dim_counterparty.yaml"
    pt_file.parent.mkdir(parents=True, exist_ok=True)
    pt_file.write_text(
        f"id: {new_ulid()}\nkind: physical_table\ntarget: duckdb_dev\n"
        f"realises: {le.id}\nname: DIM_COUNTERPARTY\nmaterialization: table\n"
    )
    repo = ModelRepo.load(model_dir)
    ce_id = le.realises
    used = where_used(repo.model, ce_id)
    assert len(used) == 1
    assert used[0]["logical_entity"] == "counterparty"
    assert used[0]["physical"][0]["name"] == "DIM_COUNTERPARTY"
    _ = DbtEmitter  # imported for parity with other tests


@pytest.fixture
def gclient(model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    return TestClient(create_app(model_dir))


def test_glossary_terms_includes_conceptual_and_terms(gclient, model_dir):
    # add a standalone glossary term (no logical realisation)
    (model_dir / "conceptual" / "terms").mkdir(parents=True, exist_ok=True)
    from mdl_core.ids import new_ulid

    (model_dir / "conceptual" / "terms" / "obligor.yaml").write_text(
        f"id: {new_ulid()}\nkind: term\nname: Obligor\n"
        f"definition: A party that owes a financial obligation.\n"
        f"synonyms: [Borrower]\n"
    )
    doc = gclient.get("/api/glossary/terms").json()
    names = {t["name"] for t in doc["terms"]}
    assert "Counterparty" in names  # a conceptual entity
    assert "Obligor" in names  # a standalone term
    obligor = next(t for t in doc["terms"] if t["name"] == "Obligor")
    assert obligor["kind"] == "term"
    assert "Borrower" in obligor["synonyms"]
    # conceptual entity carries steward + where_used; term carries neither
    cpty = next(t for t in doc["terms"] if t["name"] == "Counterparty")
    assert cpty["stewardship"]["owner"] == "risk"
    assert cpty["ontology"]["aligns_to"] == "fibo-fnd-pty-pty:PartyInRole"


def test_glossary_search_and_subject_filter(gclient):
    doc = gclient.get("/api/glossary/terms", params={"q": "legal person"}).json()
    assert any(t["name"] == "Counterparty" for t in doc["terms"])
    assert doc["subject_areas"]  # subject areas surfaced for the picker
    sa_id = doc["subject_areas"][0]["id"]
    scoped = gclient.get("/api/glossary/terms", params={"subject_area": sa_id}).json()
    assert all(
        (t["subject_area"] or {}).get("id") == sa_id for t in scoped["terms"] if t["subject_area"]
    )


def test_glossary_term_detail_and_404(gclient):
    doc = gclient.get("/api/glossary/terms").json()
    cpty = next(t for t in doc["terms"] if t["name"] == "Counterparty")
    detail = gclient.get(f"/api/glossary/term/{cpty['id']}").json()
    assert detail["id"] == cpty["id"]
    assert gclient.get("/api/glossary/term/01NOPE0000000000000000000X").status_code == 404


def test_glossary_available_in_read_only(model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    ro = TestClient(create_app(model_dir, read_only=True))
    assert ro.get("/api/glossary/terms").status_code == 200
