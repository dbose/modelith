"""Previewing staged changes without writing to disk (plan §A)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from mdl_core.ids import new_ulid


def _snapshot(root: Path) -> dict[str, str]:
    """Every YAML file and its content hash — the ground truth for 'disk untouched'."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.yaml"))
    }


def _ce(client, name: str) -> str:
    doc = client.get("/api/model").json()
    return next(e for e in doc["entities"] if e["name"] == name)["conceptual"]["id"]


def _le(client, name: str) -> str:
    doc = client.get("/api/model").json()
    return next(e for e in doc["entities"] if e["name"] == name)["id"]


# --- the test that matters most --------------------------------------------------


def test_previewing_a_delete_does_not_touch_disk(client, model_dir):
    """ModelRepo.remove_file and rename_file unlink IMMEDIATELY. Without the
    temp-root redirect in preview_model, this exact call deletes the user's files —
    reproduced during design, three of them. If this test ever fails, the redirect
    has been removed and previews are eating models."""
    before = _snapshot(model_dir)
    assert before, "fixture should have files"

    r = client.post(
        "/api/preview",
        json={"changes": [{"op": "delete_entity", "payload": {"id": _le(client, "counterparty"), "cascade": True}}]},
    )
    assert r.status_code == 200 and r.json()["ok"]

    after = _snapshot(model_dir)
    assert after == before, "preview modified the model on disk"


def test_preview_of_a_rename_does_not_move_files(client, model_dir):
    """rename_file unlinks the old path too."""
    before = _snapshot(model_dir)
    client.post(
        "/api/preview",
        json={"changes": [{"op": "rename_entity", "payload": {"id": _le(client, "trade"), "name": "deal"}}]},
    )
    assert _snapshot(model_dir) == before


def test_preview_leaves_the_fingerprint_unchanged(client):
    """A preview that bumped the fingerprint would make the client think the model
    changed on disk and refetch in a loop."""
    fp = client.get("/api/model").json()["fingerprint"]
    r = client.post(
        "/api/preview",
        json={"changes": [{"op": "create_entity", "payload": {"name": "x"}}]},
    )
    assert r.json()["fingerprint"] == fp
    assert client.get("/api/model").json()["fingerprint"] == fp


# --- the projection ---------------------------------------------------------------


def test_preview_projects_the_staged_world(client):
    live = client.get("/api/model").json()
    r = client.post(
        "/api/preview",
        json={"changes": [{"op": "create_entity", "payload": {"name": "custody_account"}}]},
    ).json()
    assert len(r["model"]["entities"]) == len(live["entities"]) + 1
    assert "custody_account" in [e["name"] for e in r["model"]["entities"]]


def test_preview_returns_the_diff_against_disk(client):
    """The review screen reads this. Diffing the WORKING TREE returns nothing for a
    staged change, because staging never writes — that was a real shipped bug."""
    r = client.post(
        "/api/preview",
        json={
            "changes": [
                {"op": "set_definition", "payload": {"id": _ce(client, "counterparty"), "definition": "Restated."}}
            ]
        },
    ).json()
    assert len(r["diff"]["objects"]) == 1
    assert r["diff"]["objects"][0]["fields"][0]["kind"] == "definition_changed"


def test_preview_applies_changes_in_order(client):
    """A create followed by an edit of the created object — the dependent case."""
    le_id, ce_id = new_ulid(), new_ulid()
    r = client.post(
        "/api/preview",
        json={
            "changes": [
                {"op": "create_entity", "payload": {"name": "custody_account", "id": le_id, "conceptual_id": ce_id}},
                {"op": "add_attribute", "payload": {"entity_id": le_id, "name": "account_no"}},
            ]
        },
    ).json()
    assert r["ok"]
    ent = next(e for e in r["model"]["entities"] if e["id"] == le_id)
    assert "account_no" in [a["name"] for a in ent["attributes"]]


def test_preview_reports_created_ids(client):
    r = client.post(
        "/api/preview",
        json={"changes": [{"op": "create_entity", "payload": {"name": "made_up"}}]},
    ).json()
    assert r["created_ids"]["0"] in [e["id"] for e in r["model"]["entities"]]


def test_preview_surfaces_diagnostics(client):
    r = client.post("/api/preview", json={"changes": []}).json()
    assert r["ok"] and isinstance(r["diagnostics"], list)


# --- failure handling -------------------------------------------------------------


def test_an_invalid_change_names_its_index_and_still_projects(client):
    """'change #2 is invalid' beats a blank screen."""
    r = client.post(
        "/api/preview",
        json={
            "changes": [
                {"op": "set_definition", "payload": {"id": _ce(client, "counterparty"), "definition": "fine"}},
                {"op": "set_definition", "payload": {"id": "01NOPE", "definition": "broken"}},
            ]
        },
    ).json()
    assert r["ok"] is False and r["failed_index"] == 1
    assert r["error"]
    # the first change is still reflected, so the user sees where it stopped
    assert len(r["diff"]["objects"]) == 1


def test_an_unknown_op_is_reported_not_raised(client):
    r = client.post("/api/preview", json={"changes": [{"op": "nonsense", "payload": {}}]})
    assert r.status_code == 200
    assert r.json()["failed_index"] == 0 and "unknown command" in r.json()["error"]


def test_changes_must_be_a_list(client):
    assert client.post("/api/preview", json={"changes": "nope"}).status_code == 422


def test_preview_is_available_in_read_only_mode(model_dir):
    """The modeler app needs preview in exactly the modes where /api/command is not
    available — it writes nothing, so read-only is no reason to withhold it."""
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    ro = TestClient(create_app(model_dir, read_only=True))
    r = ro.post("/api/preview", json={"changes": [{"op": "create_entity", "payload": {"name": "x"}}]})
    assert r.status_code == 200 and r.json()["ok"]
    assert ro.post("/api/command", json={}).status_code in (404, 405)


def test_preview_can_be_scoped_to_a_subject_area(client, model_dir):
    from mdl_core.commands import apply_command
    from mdl_core.repo import ModelRepo

    sa = next(iter(ModelRepo.load(model_dir).model.subject_areas))
    use_case = apply_command(
        model_dir, "create_subject_area", {"name": "UseCase-Scoped"}
    ).created_id
    apply_command(
        model_dir,
        "add_subject_area_members",
        {"id": use_case, "members": [_ce(client, "counterparty")]},
    )
    r = client.post(
        "/api/preview", json={"changes": [], "subject_area": use_case}
    ).json()
    assert [e["name"] for e in r["model"]["entities"]] == ["counterparty"]
    assert sa  # the other area still exists, just not in scope


# --- the round-trip: preview must equal propose -----------------------------------


def test_previewed_model_equals_proposed_model(tmp_path_factory):
    """The property the whole staging model rests on: what the user reviewed is what
    lands in the PR. Catches ULID drift, op-ordering bugs, and any divergence
    between the preview path and the apply path in one assertion."""
    import subprocess

    from fastapi.testclient import TestClient
    from mdl_server import create_app

    from mdl_core.repo import ModelRepo

    from model_builders import write_model

    root = tmp_path_factory.mktemp("roundtrip")
    write_model(root)
    for cmd in (
        ["init", "-q", "."],
        ["config", "user.email", "t@t.co"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "base"],
        ["branch", "-M", "main"],
    ):
        subprocess.run(["git", "-C", str(root), *cmd], check=True)

    client = TestClient(create_app(root))
    ce = _ce(client, "counterparty")
    le_id, ce_id = new_ulid(), new_ulid()
    changes = [
        {"op": "set_definition", "payload": {"id": ce, "definition": "Restated for the PR."}},
        {"op": "create_entity", "payload": {"name": "custody_account", "id": le_id, "conceptual_id": ce_id}},
        {"op": "add_attribute", "payload": {"entity_id": le_id, "name": "account_no"}},
    ]

    previewed = client.post("/api/preview", json={"changes": changes}).json()
    assert previewed["ok"], previewed.get("error")

    r = client.post(
        "/api/git/propose",
        json={"user": "a.hough", "title": "Add custody account", "body": "", "changes": changes},
    ).json()
    assert r["ok"], r

    subprocess.run(["git", "-C", str(root), "checkout", "-q", r["branch"]], check=True)
    proposed = ModelRepo.load(root).model

    # same objects, same identities — the created entity keeps the ULID the user saw
    assert le_id in proposed.logical_entities
    assert proposed.logical_entities[le_id].realises == ce_id
    assert "account_no" in [a.name for a in proposed.logical_entities[le_id].attributes]
    assert proposed.conceptual_entities[ce].definition == "Restated for the PR."

    previewed_names = sorted(e["name"] for e in previewed["model"]["entities"])
    proposed_names = sorted(e.name for e in proposed.logical_entities.values())
    assert previewed_names == proposed_names
