"""Model-tab import/export endpoints: /api/export/{fmt}, /api/export, /api/import."""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture
def git_model_dir(model_dir):
    for a in (
        ["init", "-q", str(model_dir)],
        ["-C", str(model_dir), "config", "user.email", "t@t.co"],
        ["-C", str(model_dir), "config", "user.name", "t"],
        ["-C", str(model_dir), "add", "-A"],
        ["-C", str(model_dir), "commit", "-qm", "base"],
    ):
        subprocess.run(["git", *a], check=True)
    return model_dir


@pytest.fixture
def client(model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    return TestClient(create_app(model_dir))


def test_export_formats_list(client):
    body = client.get("/api/export").json()
    ids = {f["id"] for f in body["formats"]}
    assert {"sql", "mermaid", "dbml", "erwin", "csv"} <= ids


def test_export_erwin(client):
    """The Export menu's erwin option serves a downloadable erwin XML document."""
    r = client.get("/api/export/erwin")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert 'filename="' in r.headers["content-disposition"] and ".xml" in (
        r.headers["content-disposition"]
    )
    assert r.text.lstrip().startswith("<?xml") and 'Format="erwin"' in r.text


# A minimal native-erwin-shaped template the POST route can clone (envelope + content to strip).
_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm" FileVersion="10.10.38485" Format="erwin">
  <Model xmlns="http://www.erwin.com/dm/data" id="{OLD}+00000001" name="Mart">
    <ModelEnvProps><Locator>C:/m.erwin</Locator><Persistence_Unit_Id>{U}+01000000</Persistence_Unit_Id></ModelEnvProps>
    <ModelProps><Name>Mart</Name><Long_Id>{OLD}+00000001</Long_Id></ModelProps>
    <Domain_Groups>
      <Domain id="{DS}+00000000"><DomainProps><Name>String</Name></DomainProps></Domain>
      <Domain id="{DN}+00000000"><DomainProps><Name>Number</Name></DomainProps></Domain>
    </Domain_Groups>
    <Entity_Groups/>
    <Relationship_Groups/>
    <Subject_Area_Groups/>
  </Model>
</erwin>
"""


def test_export_erwin_with_template_clones_and_downloads(client):
    """POST /api/export/erwin clones an uploaded native erwin template and returns erwin XML."""
    r = client.post(
        "/api/export/erwin",
        files={"template": ("mart.xml", _TEMPLATE, "application/xml")},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "attachment" in r.headers["content-disposition"]
    # the template's domains survive (the envelope is cloned), the model is reminted
    assert "<Name>String</Name>" in r.text and "<Name>Number</Name>" in r.text
    assert 'Format="erwin"' in r.text


def test_export_erwin_with_empty_template_422(client):
    r = client.post(
        "/api/export/erwin",
        files={"template": ("mart.xml", "", "application/xml")},
    )
    assert r.status_code == 422


def test_export_sql(client):
    r = client.get("/api/export/sql", params={"dialect": "postgres"})
    assert r.status_code == 200
    assert "CREATE TABLE" in r.text
    assert "attachment" in r.headers["content-disposition"]


def test_export_mermaid(client):
    r = client.get("/api/export/mermaid")
    assert r.status_code == 200 and r.text.startswith("erDiagram")


def test_export_unknown_format_404(client):
    assert client.get("/api/export/nope").status_code == 404


def test_import_sql_returns_changes(client):
    r = client.post(
        "/api/import",
        json={
            "format": "sql",
            "content": "CREATE TABLE widget (widget_id integer PRIMARY KEY, name varchar NOT NULL);",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["tables"] == 1
    ops = {c["op"] for c in body["changes"]}
    assert "create_entity" in ops and "add_attribute" in ops and "create_key_group" in ops


def test_import_roundtrips_through_preview(client):
    """An imported change list is valid input to /api/preview (it can be staged)."""
    imp = client.post(
        "/api/import",
        json={"format": "sql", "content": "CREATE TABLE gadget (gadget_id integer PRIMARY KEY);"},
    ).json()
    prev = client.post("/api/preview", json={"changes": imp["changes"]})
    assert prev.status_code == 200 and prev.json()["ok"], prev.text
    names = [e["name"] for e in prev.json()["model"]["entities"]]
    assert "gadget" in names


def test_import_empty_content_422(client):
    assert client.post("/api/import", json={"format": "sql", "content": ""}).status_code == 422


def test_import_unknown_format_422(client):
    r = client.post("/api/import", json={"format": "xml", "content": "x"})
    assert r.status_code == 422


def test_import_json_schema(client):
    r = client.post(
        "/api/import",
        json={
            "format": "json-schema",
            "content": '{"$defs":{"Thing":{"type":"object","properties":{"id":{"type":"integer"}}}}}',
        },
    )
    assert r.status_code == 200 and r.json()["tables"] == 1
