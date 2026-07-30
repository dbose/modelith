"""Read-API tests (spec §13.5: serves the canvas, never owns state)."""

from __future__ import annotations


def test_model_endpoint_shape(client):
    r = client.get("/api/model")
    assert r.status_code == 200
    doc = r.json()
    assert doc["project"]["name"] == "testmodel"
    names = {e["name"] for e in doc["entities"]}
    assert {"counterparty", "trade"} <= names
    cpty = next(e for e in doc["entities"] if e["name"] == "counterparty")
    # pre-joined conceptual context
    assert cpty["conceptual"]["name"] == "Counterparty"
    assert cpty["conceptual"]["ontology"]["aligns_to"] == "fibo-fnd-pty-pty:PartyInRole"
    assert cpty["conceptual"]["stewardship"]["owner"] == "risk"
    # attribute rows carry role + type for the erwin-style node
    bk = next(a for a in cpty["attributes"] if a["role"] == "business_key")
    assert bk["name"] == "counterparty_id" and bk["nullable"] is False
    # relationship edge with both ULID endpoints
    rel = doc["relationships"][0]
    assert rel["cardinality"] == "many_to_one"
    assert rel["from"]["entity"] and rel["to"]["entity"]
    assert doc["counts"]["entities"] == 2


def test_entity_endpoint(client):
    doc = client.get("/api/model").json()
    ulid = doc["entities"][0]["id"]
    r = client.get(f"/api/entities/{ulid}")
    assert r.status_code == 200
    assert r.json()["id"] == ulid
    assert client.get("/api/entities/01NOPE0000000000000000000X").status_code == 404


def test_diagnostics_endpoint(client):
    r = client.get("/api/diagnostics")
    assert r.status_code == 200
    assert r.json()["has_errors"] is False


def test_reload_on_each_request(client, model_dir):
    # state stays in git: an on-disk rename shows up without server restart
    f = model_dir / "logical" / "entities" / "counterparty.yaml"
    f.write_text(f.read_text().replace("name: counterparty", "name: renamed_cpty"))
    names = {e["name"] for e in client.get("/api/model").json()["entities"]}
    assert "renamed_cpty" in names


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"
