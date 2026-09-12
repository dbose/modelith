"""The shared query layer (mdl_core.query) that both AI frontends read from."""

from __future__ import annotations

from mdl_core.query import get_entity, get_model_context, list_entities
from mdl_core.repo import ModelRepo


def _model(model_dir):
    return ModelRepo.load(model_dir).model


def test_list_entities_names_and_counts(model_dir):
    ents = list_entities(_model(model_dir))
    by_name = {e["name"]: e for e in ents}
    assert {"counterparty", "trade"} <= set(by_name)
    # counterparty has two attributes in the builder
    assert by_name["counterparty"]["attribute_count"] == 2
    assert by_name["counterparty"]["subject_area"] == "Trading"


def test_list_entities_scoped_to_subject_area(model_dir):
    # Both entities are homed in Trading; an unknown area scopes to nothing.
    assert list_entities(_model(model_dir), subject_area="Trading")
    assert list_entities(_model(model_dir), subject_area="no-such-area") == []


def test_get_entity_by_name_carries_relationships(model_dir):
    e = get_entity(_model(model_dir), "counterparty")
    assert e is not None
    assert e["name"] == "counterparty"
    assert e["conceptual"]["name"] == "Counterparty"
    # the trade_has_counterparty relationship touches counterparty
    assert any(r["name"] == "trade_has_counterparty" for r in e["relationships"])
    assert {a["name"] for a in e["attributes"]} == {"counterparty_id", "legal_name"}


def test_get_entity_unknown_returns_none(model_dir):
    assert get_entity(_model(model_dir), "does_not_exist") is None


def test_get_entity_accepts_a_ulid(model_dir):
    model = _model(model_dir)
    le = next(e for e in model.logical_entities.values() if e.name == "trade")
    assert get_entity(model, le.id)["name"] == "trade"


def test_get_model_context_summary(model_dir):
    ctx = get_model_context(_model(model_dir))
    assert ctx["project"] == "testmodel"
    assert ctx["counts"]["entities"] == 2
    assert ctx["counts"]["relationships"] == 1
    assert any(a["name"] == "Trading" for a in ctx["subject_areas"])
    assert any(r["name"] == "trade_has_counterparty" for r in ctx["relationships"])
