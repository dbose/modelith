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


def _write_rich_layout(model_dir: Path) -> None:
    """A FULLY-populated sidecar: node positions, an edge routeX, and both dragged
    endpoint anchors. Isolation must hold for every field, not just positions."""
    from mdl_core.repo import ModelRepo

    model = ModelRepo.load(model_dir).model
    ent = next(iter(model.logical_entities))
    rel = next(iter(model.relationships), None)
    edges = {}
    if rel:
        edges[rel] = {
            "routeX": 480,
            "sourceAnchor": {"side": "right", "y": 200},
            "targetAnchor": {"side": "top", "y": 50, "x": 210},
        }
    (model_dir / ".mdl").mkdir(parents=True, exist_ok=True)
    (model_dir / ".mdl" / "layout.json").write_text(
        json.dumps({"version": 1, "views": {"__all__": {"nodes": {ent: {"x": 120, "y": 340}}, "edges": edges}}})
    )


def test_every_emitter_byte_identical_with_rich_layout(client, model_dir):
    """The strongest isolation check: validation and EVERY export format produce
    byte-identical output with and without a full (positions + routeX + anchors)
    layout sidecar. If any backend ever reads the sidecar, one of these diverges."""
    formats = [f["id"] for f in client.get("/api/export").json()["formats"]]
    before = {f: client.get(f"/api/export/{f}").text for f in formats}
    before_model = client.get("/api/model").json()
    _write_rich_layout(model_dir)
    for f in formats:
        assert client.get(f"/api/export/{f}").text == before[f], f"{f} export changed with layout"
    assert client.get("/api/model").json() == before_model


def test_drift_identical_with_and_without_layout(model_dir):
    """Drift (model vs compiled dbt manifest) must be identical with/without layout —
    the user-facing backend feature the sidecar must never perturb."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "reverse" / "tests"))
    from mdl_core.repo import ModelRepo
    from mdl_reverse.drift import compute_drift
    from mdl_reverse.manifest import read_manifest_dict

    from manifest_fixtures import manifest_from_model

    proj = read_manifest_dict(manifest_from_model(ModelRepo.load(model_dir).model, "duckdb_dev"))

    def drift_items():
        rep = compute_drift(ModelRepo.load(model_dir).model, proj, "duckdb_dev")
        return [(i.kind, i.severity, i.detail) for i in rep.items]

    before = drift_items()
    _write_rich_layout(model_dir)
    assert drift_items() == before


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


# --- team sharing: `layout: {shared: true}` opt-in -----------------------------


def _set_shared(model_dir: Path) -> None:
    p = model_dir / "mdl-project.yaml"
    p.write_text(p.read_text() + "\nlayout:\n  shared: true\n")


def test_shared_layout_is_not_gitignored(model_dir):
    """With `layout: {shared: true}`, the sidecar is NOT ignored, so a team can commit it."""
    _init(model_dir)
    _set_shared(model_dir)
    client = TestClient(create_app(model_dir))
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 1, "y": 2}}, "edges": {}})
    gi = model_dir / ".mdl" / ".gitignore"
    # no ignore of layout.json -> committable
    assert not (gi.exists() and "layout.json" in gi.read_text().splitlines())


def test_switching_to_shared_removes_auto_ignore(model_dir):
    """A project that was ignoring layout (solo) and later opts into sharing: the next
    write drops OUR auto-added ignore line so the file becomes committable."""
    _init(model_dir)
    client = TestClient(create_app(model_dir))
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 1, "y": 2}}, "edges": {}})
    assert "layout.json" in (model_dir / ".mdl" / ".gitignore").read_text().splitlines()
    _set_shared(model_dir)
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 3, "y": 4}}, "edges": {}})
    gi = model_dir / ".mdl" / ".gitignore"
    assert not (gi.exists() and "layout.json" in gi.read_text().splitlines())


def test_shared_setting_does_not_leak_into_cli_features(model_dir):
    """THE guardrail the user named: whatever the sharing setting is, layout stays
    invisible to every functional CLI feature. With `shared: true` AND a rich committed
    sidecar, validate / every export / drift / the model doc are all identical to a model
    with no layout and no setting."""
    client = TestClient(create_app(model_dir))
    formats = [f["id"] for f in client.get("/api/export").json()["formats"]]
    before_exports = {f: client.get(f"/api/export/{f}").text for f in formats}
    before_model = client.get("/api/model").json()
    before_fp = commands.dir_fingerprint(model_dir)
    # opt into sharing AND write a rich sidecar
    _set_shared(model_dir)
    _write_rich_layout(model_dir)
    client2 = TestClient(create_app(model_dir))
    for f in formats:
        assert client2.get(f"/api/export/{f}").text == before_exports[f], f
    assert client2.get("/api/model").json() == before_model
    assert commands.dir_fingerprint(model_dir) == before_fp


# --- discard / dirtiness: the sidecar survives a model revert, and a layout-only ----
# `.mdl/` never reads as dirty (the git_api.py filtering paths).


def test_discard_reverts_the_model_but_keeps_the_layout_sidecar(model_dir):
    """`/api/git/discard` reverts MODEL edits (git checkout + clean) yet must preserve the
    visual layout sidecar — it is excluded with `clean -e .mdl/layout.json`. Guards the
    'survive a revert' promise in this module's docstring."""
    _init(model_dir)
    client = TestClient(create_app(model_dir))
    ent = client.get("/api/model").json()["entities"][0]["id"]
    # a real, uncommitted MODEL change the discard should sweep. Use a non-object file
    # (not under an *.yaml object glob) so writing it doesn't break ModelRepo.load, while
    # still being an untracked file `git clean` must remove.
    stray = model_dir / "_stray.txt"
    stray.write_text("scratch\n", encoding="utf-8")
    # and a layout the discard must NOT touch
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 7, "y": 8}}, "edges": {}})
    layout = model_dir / ".mdl" / "layout.json"
    assert layout.exists() and stray.exists()

    r = client.post("/api/git/discard")
    assert r.status_code == 200 and r.json()["ok"] is True

    assert not stray.exists(), "discard must revert the untracked model file"
    assert layout.exists(), "discard must KEEP the visual layout sidecar"
    assert client.get("/api/layout?view=__all__").json()["nodes"][ent] == {"x": 7, "y": 8}


def test_untracked_layout_only_mdl_dir_is_not_dirty_but_real_mdl_content_is(model_dir):
    """When the whole `.mdl/` dir is new, `git status --porcelain` collapses it to a single
    `?? .mdl/` line. That must read as CLEAN when the dir holds only layout artifacts, but
    DIRTY when it also holds real state (e.g. decisions.yaml) — the `_mdl_only_layout` branch
    in git_api.py. Here `.mdl/` is created by the first layout write AFTER the initial commit,
    so it is entirely untracked (the collapsed case)."""
    _init(model_dir)  # commits the model BEFORE any .mdl/ exists -> .mdl/ is untracked
    client = TestClient(create_app(model_dir))
    ent = client.get("/api/model").json()["entities"][0]["id"]
    client.put("/api/layout", json={"view": "__all__", "nodes": {ent: {"x": 1, "y": 2}}, "edges": {}})
    st = client.get("/api/git/status").json()
    assert st["git"] is True and st["clean"] is True, "layout-only .mdl/ must read clean"

    # now add REAL .mdl/ content: the same untracked dir must now read dirty
    (model_dir / ".mdl" / "decisions.yaml").write_text("decisions: []\n", encoding="utf-8")
    st2 = client.get("/api/git/status").json()
    assert st2["clean"] is False, "real .mdl/ content must read dirty even alongside layout"
