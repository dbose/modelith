"""Source-of-truth switch: /api/glossary/config + the /propose server guard.

When the catalog masters the glossary, the /sme app renders meaning-fields
read-only — but the real boundary is the server: /api/git/propose must REFUSE a
definition/synonym/stewardship edit no matter what a scripted client sends.
"""

from __future__ import annotations

import pytest


def _set_collibra(model_dir):
    p = model_dir / "mdl-project.yaml"
    p.write_text(
        p.read_text()
        + "glossary:\n"
        + "  source_of_truth: collibra\n"
        + "  catalog_url: https://acme.collibra.com\n"
        + "  catalog_name: Collibra\n"
    )


@pytest.fixture
def client(model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    return TestClient(create_app(model_dir))


def test_config_defaults_to_git(client):
    cfg = client.get("/api/glossary/config").json()
    assert cfg["source_of_truth"] == "git"
    assert cfg["catalog_owned_fields"] == []  # git masters → everything editable


def test_config_reports_catalog_ownership(client, model_dir):
    _set_collibra(model_dir)
    cfg = client.get("/api/glossary/config").json()
    assert cfg["source_of_truth"] == "collibra"
    assert cfg["catalog_url"] == "https://acme.collibra.com"
    assert set(cfg["catalog_owned_fields"]) == {"definition", "synonyms", "stewardship"}


def _counterparty_id(client):
    terms = client.get("/api/glossary/terms").json()["terms"]
    return next(t["id"] for t in terms if t["name"] == "Counterparty")


def test_propose_refuses_meaning_edit_when_catalog_masters(client, model_dir):
    _set_collibra(model_dir)
    cid = _counterparty_id(client)
    r = client.post(
        "/api/git/propose",
        json={
            "user": "a.hough",
            "title": "tweak",
            "changes": [{"op": "set_definition", "payload": {"id": cid, "definition": "x"}}],
        },
    )
    assert r.status_code == 409
    assert "Collibra" in r.json()["error"]


def test_propose_allows_alignment_proposal_when_catalog_masters(client, model_dir):
    # alignment is Modelith's to own even when the catalog masters definitions
    _set_collibra(model_dir)
    cid = _counterparty_id(client)
    r = client.post(
        "/api/git/propose",
        json={
            "user": "a.hough",
            "title": "propose mapping",
            "changes": [
                {
                    "op": "set_alignment",
                    "payload": {
                        "id": cid,
                        "aligns_to": "fibo-fnd-pty-pty:PartyInRole",
                        "alignment": "skos:closeMatch",
                        "status": "proposed",
                    },
                }
            ],
        },
    )
    # not blocked by the source-of-truth guard (may still 200 or degrade on git)
    assert r.status_code != 409 or "Collibra" not in r.json().get("error", "")
