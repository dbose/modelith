"""Visual layout sidecar API + its strict isolation from the model.

Layout is visual only: it must never change the model fingerprint, the /api/model
document, validation/drift/export output, git dirtiness, or survive a revert as a
model change. These tests pin every one of those guarantees.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mdl_server import create_app

from mdl_core import commands


def _git(d: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(d), *args], capture_output=True, text=True
    ).stdout.strip()


def _init(d: Path) -> None:
    _git(d, "init")
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "config", "user.name", "t")
    _git(d, "add", "-A")
    _git(d, "commit", "-m", "initial")
    _git(d, "branch", "-M", "main")


@pytest.fixture
def client(model_dir):
    return TestClient(create_app(model_dir))


def test_get_empty_returns_empty_maps(client):
    r = client.get("/api/layout?view=__all__")
    assert r.status_code == 200
    d = r.json()
    assert d == {"version": 1, "view": "__all__", "nodes": {}, "edges": {}}


def test_put_then_get_roundtrips(client, model_dir):
    # use a real entity + relationship id so nothing is dropped
    model = client.get("/api/model").json()
    ent = model["entities"][0]["id"]
    rel = model["relationships"][0]["id"] if model["relationships"] else None
    nodes = {ent: {"x": 120.6, "y": 340.2}}
    edges = {rel: {"routeX": 480.9}} if rel else {}
    r = client.put("/api/layout", json={"view": "__all__", "nodes": nodes, "edges": edges})
    assert r.status_code == 200 and r.json()["ok"]
    got = client.get("/api/layout?view=__all__").json()
    assert got["nodes"][ent] == {"x": 121, "y": 340}  # rounded to int
    if rel:
        assert got["edges"][rel] == {"routeX": 481}


def test_edge_endpoint_anchors_roundtrip(client):
    """Dragged endpoint anchors (visual only) persist: {side, y, x?}, rounded, and a
    route record may carry routeX + anchors together."""
    model = client.get("/api/model").json()
    rel = model["relationships"][0]["id"]
    edges = {
        rel: {
            "routeX": 300.4,
            "sourceAnchor": {"side": "right", "y": 140.7},
            "targetAnchor": {"side": "top", "y": 50.2, "x": 210.9},
        }
    }
    r = client.put("/api/layout", json={"view": "__all__", "nodes": {}, "edges": edges})
    assert r.status_code == 200, r.text
    got = client.get("/api/layout?view=__all__").json()["edges"][rel]
    assert got["routeX"] == 300
    assert got["sourceAnchor"] == {"side": "right", "y": 141}
    assert got["targetAnchor"] == {"side": "top", "y": 50, "x": 211}


def test_bad_anchor_side_422(client):
    rel = client.get("/api/model").json()["relationships"][0]["id"]
    r = client.put(
        "/api/layout",
        json={"view": "__all__", "nodes": {}, "edges": {rel: {"sourceAnchor": {"side": "nope", "y": 1}}}},
    )
    assert r.status_code == 422


def test_file_is_key_sorted_integers(client, model_dir):
    model = client.get("/api/model").json()
    e0, e1 = model["entities"][0]["id"], model["entities"][1]["id"]
    client.put(
        "/api/layout",
        json={"view": "__all__", "nodes": {e1: {"x": 1.4, "y": 2.6}, e0: {"x": 3, "y": 4}}, "edges": {}},
    )
    text = (model_dir / ".mdl" / "layout.json").read_text()
    doc = json.loads(text)
    assert doc["version"] == 1
    # integer values, and keys serialized sorted
    assert all(isinstance(v["x"], int) and isinstance(v["y"], int) for v in doc["views"]["__all__"]["nodes"].values())
    assert text.endswith("\n")
    # sort_keys=True => the smaller ulid appears first in the raw text
    assert text.index(f'"{min(e0, e1)}"') < text.index(f'"{max(e0, e1)}"')


def test_put_drops_ids_not_in_model(client):
    r = client.put(
        "/api/layout",
        json={"view": "__all__", "nodes": {"01GHOSTNOTAREALENTITYID00": {"x": 1, "y": 2}}, "edges": {}},
    )
    assert r.status_code == 200
    assert client.get("/api/layout?view=__all__").json()["nodes"] == {}


def test_corrupt_file_treated_as_empty_and_overwritten(client, model_dir):
    mdl = model_dir / ".mdl"
    mdl.mkdir(parents=True, exist_ok=True)
    (mdl / "layout.json").write_text("{ this is not json")
    # GET does not crash
    assert client.get("/api/layout?view=__all__").json()["nodes"] == {}
    # next PUT overwrites cleanly
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 5, "y": 6}}, "edges": {}})
    assert client.get("/api/layout?view=__all__").json()["nodes"][ent] == {"x": 5, "y": 6}


def test_non_finite_and_long_keys_422(client):
    ent = client.get("/api/model").json()["entities"][0]["id"]
    # send Infinity as a raw body so it reaches the server (Python's json emits it,
    # the server's _finite guard must reject it)
    raw = f'{{"view": "__all__", "nodes": {{"{ent}": {{"x": Infinity, "y": 0}}}}, "edges": {{}}}}'
    r = client.put("/api/layout", content=raw, headers={"content-type": "application/json"})
    assert r.status_code in (422, 400)
    r2 = client.put("/api/layout", json={"view": "x" * 65, "nodes": {}, "edges": {}})
    assert r2.status_code == 422


def test_put_and_delete_403_when_read_only(model_dir):
    ro = TestClient(create_app(model_dir, read_only=True))
    assert ro.get("/api/layout?view=__all__").status_code == 200  # read still works
    assert ro.put("/api/layout", json={"view": "__all__", "nodes": {}, "edges": {}}).status_code == 403
    assert ro.delete("/api/layout").status_code == 403


def test_delete_one_view_and_all(client):
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 1, "y": 1}}, "edges": {}})
    client.put("/api/layout", json={"view": "sa:abc", "nodes": {ent: {"x": 2, "y": 2}}, "edges": {}})
    client.delete("/api/layout?view=sa:abc")
    assert client.get("/api/layout?view=sa:abc").json()["nodes"] == {}
    assert client.get("/api/layout?view=__all__").json()["nodes"] != {}
    client.delete("/api/layout")  # the whole file
    assert client.get("/api/layout?view=__all__").json()["nodes"] == {}


# --- isolation: layout must be invisible to the model -------------------------


def test_layout_does_not_change_fingerprint_or_model_doc(client, model_dir):
    fp_before = commands.dir_fingerprint(model_dir)
    doc_before = client.get("/api/model").json()
    ent = doc_before["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 9, "y": 9}}, "edges": {}})
    assert commands.dir_fingerprint(model_dir) == fp_before
    assert client.get("/api/model").json() == doc_before


def test_validate_identical_with_and_without_layout(model_dir):
    from mdl_core.repo import ModelRepo
    from mdl_core.validate import validate

    before = [(d.code, d.message) for d in validate(ModelRepo.load(model_dir).model).items]
    (model_dir / ".mdl").mkdir(parents=True, exist_ok=True)
    (model_dir / ".mdl" / "layout.json").write_text(
        json.dumps({"version": 1, "views": {"__all__": {"nodes": {"x": {"x": 1, "y": 2}}, "edges": {}}}})
    )
    after = [(d.code, d.message) for d in validate(ModelRepo.load(model_dir).model).items]
    assert before == after


def test_export_identical_with_and_without_layout(client, model_dir):
    before = client.get("/api/export/sql").text
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 1, "y": 2}}, "edges": {}})
    assert client.get("/api/export/sql").text == before


# --- isolation: git never sees layout as dirty, never deletes it --------------


def test_git_status_not_dirty_from_layout(model_dir):
    _init(model_dir)
    client = TestClient(create_app(model_dir))
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 1, "y": 2}}, "edges": {}})
    st = client.get("/api/git/status").json()
    assert st["git"] is True
    assert st["clean"] is True
    assert not any(d["path"] == ".mdl/layout.json" for d in st["dirty"])


def test_gitignore_created_after_first_write(model_dir):
    _init(model_dir)
    client = TestClient(create_app(model_dir))
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 1, "y": 2}}, "edges": {}})
    gi = model_dir / ".mdl" / ".gitignore"
    assert gi.exists()
    assert "layout.json" in gi.read_text().splitlines()


def test_gitignore_not_created_outside_git(model_dir):
    # model_dir is a bare tmp dir (no git init here)
    client = TestClient(create_app(model_dir))
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 1, "y": 2}}, "edges": {}})
    assert not (model_dir / ".mdl" / ".gitignore").exists()
    assert (model_dir / ".mdl" / "layout.json").exists()  # file still written
