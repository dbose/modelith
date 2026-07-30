"""Mutation engine tests (E2): comment preservation, concurrency, lifecycle."""

from __future__ import annotations

import pytest
from mdl_server.commands import (
    CommandError,
    StaleModelError,
    apply_command,
    dir_fingerprint,
)

from mdl_core.repo import ModelRepo


def _entity_id(model_dir, name):
    repo = ModelRepo.load(model_dir)
    return next(e.id for e in repo.model.logical_entities.values() if e.name == name)


def test_create_entity_minted_ulids_and_validates(model_dir):
    result = apply_command(model_dir, "create_entity", {"name": "Custody Account"})
    assert result.ok and result.created_id
    repo = ModelRepo.load(model_dir)
    le = repo.model.logical_entities[result.created_id]
    assert le.name == "custody_account"
    assert le.realises in repo.model.conceptual_entities
    assert (model_dir / "logical" / "entities" / "custody_account.yaml").exists()
    assert not any(d["severity"] == "error" for d in result.diagnostics)


def test_rename_preserves_ulid_and_comments(model_dir):
    # hand comment in the file must survive a UI rename
    f = model_dir / "logical" / "entities" / "counterparty.yaml"
    f.write_text("# reviewed by risk 2026-07\n" + f.read_text())
    eid = _entity_id(model_dir, "counterparty")

    apply_command(model_dir, "rename_entity", {"id": eid, "name": "trading_partner"})

    new_f = model_dir / "logical" / "entities" / "trading_partner.yaml"
    assert new_f.exists() and not f.exists()  # file follows the name
    assert "# reviewed by risk 2026-07" in new_f.read_text()  # comment survived
    assert _entity_id(model_dir, "trading_partner") == eid  # ULID unchanged


def test_stale_fingerprint_rejected(model_dir):
    fp = dir_fingerprint(model_dir)
    # external edit (git pull / IDE) changes the tree
    (model_dir / "mdl-project.yaml").write_text(
        (model_dir / "mdl-project.yaml").read_text() + "# touched\n"
    )
    with pytest.raises(StaleModelError):
        apply_command(model_dir, "set_definition", {"id": "x", "definition": "y"}, fp)


def test_attribute_lifecycle(model_dir):
    eid = _entity_id(model_dir, "counterparty")
    r = apply_command(
        model_dir,
        "add_attribute",
        {"entity_id": eid, "name": "lei_code", "domain": "string", "nullable": True},
    )
    aid = r.created_id
    apply_command(
        model_dir,
        "update_attribute",
        {"entity_id": eid, "attribute_id": aid, "nullable": False, "role": "attribute"},
    )
    repo = ModelRepo.load(model_dir)
    attr = next(
        a for a in repo.model.logical_entities[eid].attributes if a.id == aid
    )
    assert attr.name == "lei_code" and attr.nullable is False

    apply_command(model_dir, "delete_attribute", {"entity_id": eid, "attribute_id": aid})
    repo = ModelRepo.load(model_dir)
    assert all(a.id != aid for a in repo.model.logical_entities[eid].attributes)


def test_delete_attribute_referenced_by_relationship_refused(model_dir):
    repo = ModelRepo.load(model_dir)
    rel = next(iter(repo.model.relationships.values()))
    from_entity, from_attr = rel.from_.entity, rel.from_.attributes[0]
    with pytest.raises(CommandError, match="referenced by relationship"):
        apply_command(
            model_dir,
            "delete_attribute",
            {"entity_id": from_entity, "attribute_id": from_attr},
        )


def test_set_alignment_and_clear(model_dir):
    eid = _entity_id(model_dir, "trade")
    apply_command(
        model_dir,
        "set_alignment",
        {
            "id": eid,
            "aligns_to": "fibo-fnd-agr-ctr:Contract",
            "alignment": "skos:exactMatch",
            "layer": "core",
        },
    )
    repo = ModelRepo.load(model_dir)
    ce = repo.model.conceptual_entities[repo.model.logical_entities[eid].realises]
    assert ce.ontology.aligns_to == "fibo-fnd-agr-ctr:Contract"
    assert ce.ontology.layer == "core"

    apply_command(model_dir, "clear_alignment", {"id": eid})
    repo = ModelRepo.load(model_dir)
    ce = repo.model.conceptual_entities[repo.model.logical_entities[eid].realises]
    assert ce.ontology is None or ce.ontology.aligns_to is None


def test_relationship_lifecycle(model_dir):
    r = apply_command(model_dir, "create_entity", {"name": "custodian"})
    cust_id = r.created_id
    bk = apply_command(
        model_dir,
        "add_attribute",
        {"entity_id": cust_id, "name": "custodian_id", "role": "business_key", "nullable": False},
    ).created_id
    trade_id = _entity_id(model_dir, "trade")
    rel = apply_command(
        model_dir,
        "create_relationship",
        {"from_entity": trade_id, "to_entity": cust_id, "to_attribute": bk},
    )
    repo = ModelRepo.load(model_dir)
    assert rel.created_id in repo.model.relationships

    apply_command(model_dir, "delete_relationship", {"id": rel.created_id})
    assert rel.created_id not in ModelRepo.load(model_dir).model.relationships


def test_delete_entity_requires_cascade(model_dir):
    eid = _entity_id(model_dir, "counterparty")  # has a relationship
    with pytest.raises(CommandError, match="cascade"):
        apply_command(model_dir, "delete_entity", {"id": eid})
    apply_command(model_dir, "delete_entity", {"id": eid, "cascade": True})
    repo = ModelRepo.load(model_dir)
    assert all(le.name != "counterparty" for le in repo.model.logical_entities.values())
    assert len(repo.model.relationships) == 0  # cascaded
    # model still referentially valid
    r = apply_command(model_dir, "set_definition", {"id": _entity_id(model_dir, "trade"), "definition": "x"})
    assert not any(d["severity"] == "error" for d in r.diagnostics)


def test_command_endpoint_and_read_only(model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    rw = TestClient(create_app(model_dir))
    resp = rw.post(
        "/api/command", json={"op": "create_entity", "payload": {"name": "benchmark"}}
    )
    assert resp.status_code == 200 and resp.json()["ok"]

    # stale fingerprint -> 409
    stale = rw.post(
        "/api/command",
        json={"op": "create_entity", "payload": {"name": "x"}, "fingerprint": "0:0:0"},
    )
    assert stale.status_code == 409

    ro = TestClient(create_app(model_dir, read_only=True))
    resp = ro.post("/api/command", json={"op": "create_entity", "payload": {"name": "y"}})
    assert resp.status_code in (404, 405)  # router not mounted
    assert ro.get("/api/model").json()["read_only"] is True
