"""Subject-area workspace endpoints and the scoped projection (plan §D)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdl_core.commands import apply_command


@pytest.fixture
def sa_id(model_dir: Path) -> str:
    from mdl_core.repo import ModelRepo

    return next(iter(ModelRepo.load(model_dir).model.subject_areas))


@pytest.fixture
def ids(model_dir: Path) -> dict:
    from mdl_core.repo import ModelRepo

    m = ModelRepo.load(model_dir).model
    ce = next(c for c in m.conceptual_entities.values() if c.name == "Counterparty")
    ce2 = next(c for c in m.conceptual_entities.values() if c.name == "Trade")
    return {"ce": ce.id, "ce2": ce2.id}


def test_list_subject_areas_carries_membership(client, model_dir, sa_id, ids):
    apply_command(model_dir, "add_subject_area_members", {"id": sa_id, "members": [ids["ce"]]})
    doc = client.get("/api/glossary/subject-areas").json()
    sa = next(s for s in doc["subject_areas"] if s["id"] == sa_id)
    assert sa["members"] == [ids["ce"]] and sa["member_count"] == 1


def test_detail_splits_available_and_included(client, model_dir, sa_id, ids):
    apply_command(model_dir, "add_subject_area_members", {"id": sa_id, "members": [ids["ce"]]})
    doc = client.get(f"/api/glossary/subject-area/{sa_id}").json()
    assert doc["ok"]
    assert [t["id"] for t in doc["included"]] == [ids["ce"]]
    assert ids["ce"] not in [t["id"] for t in doc["available"]]
    assert ids["ce2"] in [t["id"] for t in doc["available"]]


def test_detail_surfaces_the_home_membership_gap(client, model_dir, sa_id, ids):
    """MDL-W114 as an affordance: Counterparty is homed here but not a member."""
    apply_command(model_dir, "add_subject_area_members", {"id": sa_id, "members": [ids["ce2"]]})
    doc = client.get(f"/api/glossary/subject-area/{sa_id}").json()
    assert doc["inconsistent"] == [ids["ce"]]


def test_unknown_subject_area_is_404(client):
    assert client.get("/api/glossary/subject-area/01NOPE").status_code == 404


def test_expand_returns_hops_with_provenance(client, model_dir, sa_id, ids):
    r = client.post(
        f"/api/glossary/subject-area/{sa_id}/expand",
        json={"seeds": [ids["ce2"]], "direction": "ancestors", "levels": 1},
    )
    doc = r.json()
    assert doc["ok"] and len(doc["hops"]) == 1
    hop = doc["hops"][0]
    assert hop["id"] == ids["ce"] and hop["name"] == "Counterparty"
    # the picker must be able to say WHY, not just what
    assert hop["via"] == "relationship" and hop["via_name"] == "trade_has_counterparty"
    assert doc["added"] == [ids["ce"]]


def test_expand_writes_nothing(client, model_dir, sa_id, ids):
    """A preview: the SME confirms before anything is added."""
    from mdl_core.repo import ModelRepo

    client.post(
        f"/api/glossary/subject-area/{sa_id}/expand",
        json={"seeds": [ids["ce2"]], "direction": "both", "levels": 3},
    )
    assert ModelRepo.load(model_dir).model.subject_areas[sa_id].members == []


@pytest.mark.parametrize("levels", [0, 99, "many"])
def test_expand_rejects_bad_levels(client, sa_id, levels):
    r = client.post(
        f"/api/glossary/subject-area/{sa_id}/expand", json={"seeds": [], "levels": levels}
    )
    assert r.status_code == 422


def test_expand_rejects_a_bad_direction(client, sa_id):
    r = client.post(
        f"/api/glossary/subject-area/{sa_id}/expand", json={"seeds": [], "direction": "sideways"}
    )
    assert r.status_code == 422


# --- scoped projection ----------------------------------------------------------


def test_scoped_model_filters_entities_and_half_edges(client, model_dir, ids):
    """A use-case view holding ONE entity: only it is in scope, and the
    relationship to the other is dropped — a half-edge breaks the React Flow
    render. (Both fixture entities are HOMED in Trading, so scoping that area
    correctly keeps both; a use-case view is the honest test of membership.)"""
    use_case = apply_command(
        model_dir, "create_subject_area", {"name": "UseCase-ApraStressTesting"}
    ).created_id
    apply_command(
        model_dir, "add_subject_area_members", {"id": use_case, "members": [ids["ce"]]}
    )
    doc = client.get("/api/model", params={"subject_area": use_case}).json()

    assert doc["scope"] == use_case
    assert [e["name"] for e in doc["entities"]] == ["counterparty"]
    assert doc["relationships"] == []  # trade is out of scope
    assert doc["counts"]["entities"] == 1
    # the picker still needs every area, so this list is never filtered
    assert len(doc["subject_areas"]) == len(client.get("/api/model").json()["subject_areas"])


def test_scoped_model_keeps_an_edge_when_both_ends_are_in(client, model_dir, ids):
    use_case = apply_command(
        model_dir, "create_subject_area", {"name": "UseCase-Both"}
    ).created_id
    apply_command(
        model_dir,
        "set_subject_area_members",
        {"id": use_case, "members": [ids["ce"], ids["ce2"]]},
    )
    doc = client.get("/api/model", params={"subject_area": use_case}).json()
    assert {e["name"] for e in doc["entities"]} == {"counterparty", "trade"}
    assert len(doc["relationships"]) == 1


def test_home_tagged_entities_are_in_scope_without_being_members(client, model_dir, sa_id):
    """The scope is the UNION of members and objects homed here — the two answer
    different questions and a view wants both."""
    doc = client.get("/api/model", params={"subject_area": sa_id}).json()
    assert {e["name"] for e in doc["entities"]} == {"counterparty", "trade"}


def test_unscoped_model_is_unchanged(client):
    doc = client.get("/api/model").json()
    assert doc["scope"] is None and len(doc["entities"]) == 2


def test_unknown_scope_is_empty_not_a_500(client):
    doc = client.get("/api/model", params={"subject_area": "01NOPE"}).json()
    assert doc["entities"] == [] and doc["counts"]["entities"] == 0


def test_workspace_is_available_in_read_only_mode(model_dir, sa_id):
    """The SME can browse what an area contains on a read-only deployment."""
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    ro = TestClient(create_app(model_dir, read_only=True))
    assert ro.get("/api/glossary/subject-areas").status_code == 200
    assert ro.get(f"/api/glossary/subject-area/{sa_id}").status_code == 200
    assert ro.post(f"/api/glossary/subject-area/{sa_id}/expand", json={"seeds": []}).status_code == 200
