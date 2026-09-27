"""System test: the ModelDiffDoc the review view (canvas DiffView Phase 1) consumes.

The canvas review view filters and per-field-selects over the diff the server produces
from /api/preview. This asserts the exact wire contract that per-field selection depends
on — every changed object carries a stable ulid, and its field changes carry a `field`
key — so a frontend `(ulid, field)` selection maps to a real server change. If this shape
drifts, the canvas selection silently breaks; this test is the guard.
"""

from __future__ import annotations


def _le(client, name: str) -> str:
    doc = client.get("/api/model").json()
    return next(e for e in doc["entities"] if e["name"] == name)["id"]


def test_preview_diff_has_ulid_and_field_keyed_changes(client):
    """A modify preview yields a diff whose objects have ulids and whose field changes
    have `field` keys — the (ulid, field) identity the review view selects over."""
    r = client.post(
        "/api/preview",
        json={
            "changes": [
                {"op": "rename_entity", "payload": {"id": _le(client, "trade"), "name": "deal"}}
            ]
        },
    )
    assert r.status_code == 200
    doc = r.json()
    assert doc["ok"]
    diff = doc["diff"]
    assert diff["objects"], "a rename should produce at least one changed object"

    obj = diff["objects"][0]
    # the identity half of a (ulid, field) selection key
    assert obj["ulid"], "every changed object carries a stable ulid"
    assert obj["change"] in ("added", "removed", "modified")
    # the field half — each field change is independently addressable
    for f in obj["fields"]:
        assert "field" in f and f["field"], "each field change has a stable field key"
        assert "severity" in f
        assert f["severity"] in ("breaking", "additive", "cosmetic", "unmanaged")


def test_preview_diff_change_types_cover_the_filter_categories(client):
    """The review view's filters (additions/modifications/renames/deletions) key off
    `change` and `renamed`. A rename must be flagged renamed=True with change=modified,
    so the 'Renames' filter isolates it — the exact field the frontend filters on."""
    r = client.post(
        "/api/preview",
        json={
            "changes": [
                {"op": "rename_entity", "payload": {"id": _le(client, "counterparty"), "name": "party"}}
            ]
        },
    )
    diff = r.json()["diff"]
    renamed = [o for o in diff["objects"] if o["renamed"]]
    assert renamed, "a rename must surface as a renamed object for the Renames filter"
    assert all(o["change"] == "modified" for o in renamed)


def test_preview_diff_is_stable_for_selection(client):
    """The same staged change previewed twice yields the same object ulids — so a
    per-field selection the user made does not shift under them on a re-preview."""
    change = [
        {"op": "rename_entity", "payload": {"id": _le(client, "trade"), "name": "deal"}}
    ]
    first = client.post("/api/preview", json={"changes": change}).json()["diff"]
    second = client.post("/api/preview", json={"changes": change}).json()["diff"]
    assert [o["ulid"] for o in first["objects"]] == [o["ulid"] for o in second["objects"]]
