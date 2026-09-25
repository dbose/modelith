"""Workspace status assessor: correct stage + ranked next-actions per pipeline state."""

from __future__ import annotations

from mdl_core.status import assess

from model_builders import write_model


def _action_ids(status):
    return [a.id for a in status.next_actions]


def test_empty_workspace(tmp_path):
    st = assess(tmp_path)  # no model at all
    assert st.stage == "no-models"
    assert st.entity_count == 0
    assert "reverse" in _action_ids(st)


def test_reviewed_offers_docs(tmp_path):
    write_model(tmp_path)
    st = assess(tmp_path)
    assert st.entity_count > 0
    assert st.pending_count == 0  # write_model leaves no pending ledger
    assert st.stage in ("reviewed", "ready")
    assert "docs-generate" in _action_ids(st)


def test_pending_decisions_block_docs(tmp_path):
    write_model(tmp_path)
    # seed a pending decision in the ledger
    from mdl_reverse.ledger import Confidence, Decision, DecisionLedger

    ledger = DecisionLedger()
    ledger.record(
        Decision(
            kind="relationship",
            signal="name_type",
            confidence=Confidence.medium,
            subject="orders.customer_id -> customer",
        )
    )
    ledger.save(tmp_path)

    st = assess(tmp_path)
    assert st.stage == "needs-review"
    assert st.pending_count == 1
    assert st.pending_by_confidence == {"medium": 1}
    ids = _action_ids(st)
    assert "review" in ids
    # docs are not offered while proposals are pending
    assert "docs-generate" not in ids


def test_manifest_enables_drift(tmp_path):
    write_model(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    st = assess(tmp_path, manifest=manifest)
    assert st.has_manifest is True
    assert "drift" in _action_ids(st)


def test_docs_freshness(tmp_path):
    write_model(tmp_path)
    docs = tmp_path / "target" / "mdl-docs"
    docs.mkdir(parents=True)
    index = docs / "index.html"
    index.write_text("<html></html>", encoding="utf-8")
    st = assess(tmp_path)
    assert st.docs_generated is True
    # to_dict round-trips for the JSON surface
    d = st.to_dict()
    assert d["stage"] == st.stage
    assert isinstance(d["next_actions"], list)
