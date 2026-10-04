"""/api/import dispatches the erwin format to the rich reader + model_to_commands."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from mdl_server.app import create_app


def _erwin_xml() -> str:
    fixture = Path(__file__).resolve().parents[2] / "reverse" / "tests" / "test_erwin.py"
    return fixture.read_text().split('_ERWIN = """')[1].split('"""')[0]


def test_api_import_erwin_returns_changes(tmp_path):
    (tmp_path / "mdl-project.yaml").write_text("name: t\ndbt_target: duckdb\n")
    client = TestClient(create_app(tmp_path))
    r = client.post("/api/import", json={"format": "erwin", "content": _erwin_xml()})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["tables"] == 3  # 3 entities
    ops = {c["op"] for c in data["changes"]}
    assert "create_entity" in ops and "create_relationship" in ops and "create_key_group" in ops
    assert any("view" in w.lower() for w in data["warnings"])  # a View was skipped


def test_api_import_empty_content_422(tmp_path):
    (tmp_path / "mdl-project.yaml").write_text("name: t\ndbt_target: duckdb\n")
    client = TestClient(create_app(tmp_path))
    r = client.post("/api/import", json={"format": "erwin", "content": ""})
    assert r.status_code == 422


def test_import_preview_returns_diff_doc_and_changes(tmp_path):
    """Pre-write review: /api/import/preview returns a ModelDiffDoc (the review view's shape)
    PLUS the import's changes + warnings, and writes nothing."""
    (tmp_path / "mdl-project.yaml").write_text("name: t\ndbt_target: duckdb\n")
    client = TestClient(create_app(tmp_path))
    r = client.post("/api/import/preview", json={"format": "erwin", "content": _erwin_xml()})
    assert r.status_code == 200, r.text
    doc = r.json()
    # the ModelDiffDoc contract the canvas review view consumes
    assert doc["ok"] is True
    assert "objects" in doc and isinstance(doc["objects"], list)
    assert "max_severity" in doc and "has_breaking" in doc
    assert doc["base"]["label"] == "current model"
    assert doc["head"]["label"] == "erwin import"
    # into an empty model, the 3 erwin entities are all ADDED
    names = {o.get("name_after") for o in doc["objects"]}
    assert {"counterparty", "trade", "institution"} <= names
    assert all(o["change"] == "added" for o in doc["objects"] if o["object_kind"] == "logical_entity")
    # the write payload rides along for the apply action
    ops = {c["op"] for c in doc["changes"]}
    assert "create_entity" in ops
    assert isinstance(doc["warnings"], list)
    # it wrote nothing — the model dir still has only the project file
    assert not (tmp_path / "logical").exists()


def test_import_preview_non_erwin_422(tmp_path):
    (tmp_path / "mdl-project.yaml").write_text("name: t\ndbt_target: duckdb\n")
    client = TestClient(create_app(tmp_path))
    r = client.post("/api/import/preview", json={"format": "sql", "content": "CREATE TABLE t(x int);"})
    assert r.status_code == 422


def test_import_stash_roundtrip_is_one_shot(tmp_path):
    """A host (VS Code) stashes .xml content; the canvas fetches it once by token."""
    (tmp_path / "mdl-project.yaml").write_text("name: t\ndbt_target: duckdb\n")
    client = TestClient(create_app(tmp_path))
    put = client.post("/api/import/stash", json={"content": "<erwin/>"})
    assert put.status_code == 200
    token = put.json()["token"]
    got = client.get(f"/api/import/stash/{token}")
    assert got.status_code == 200 and got.json()["content"] == "<erwin/>"
    # consumed -> a second read is 404
    assert client.get(f"/api/import/stash/{token}").status_code == 404
    # empty content is rejected
    assert client.post("/api/import/stash", json={"content": ""}).status_code == 422
