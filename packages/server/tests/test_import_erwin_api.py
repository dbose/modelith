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
