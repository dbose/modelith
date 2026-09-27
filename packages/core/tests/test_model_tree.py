"""The model_tree query — the navigable entity→attribute structure the VS Code Model
Explorer drills into, with the source file per entity for click-to-open."""

from __future__ import annotations

from mdl_core.query import model_tree
from mdl_core.repo import ModelRepo


def _repo(model_dir):
    return ModelRepo.load(model_dir)


def test_model_tree_entities_and_attributes(model_dir):
    repo = _repo(model_dir)
    tree = model_tree(repo.model, path_for=repo.path_for_ulid)
    by_name = {e["name"]: e for e in tree["entities"]}
    assert {"counterparty", "trade"} <= set(by_name)

    cp = by_name["counterparty"]
    attr_names = [a["name"] for a in cp["attributes"]]
    # attributes keep their authored order
    assert attr_names == ["counterparty_id", "legal_name"]


def test_model_tree_marks_primary_key(model_dir):
    tree = model_tree(_repo(model_dir).model, path_for=_repo(model_dir).path_for_ulid)
    cp = next(e for e in tree["entities"] if e["name"] == "counterparty")
    pk = next(a for a in cp["attributes"] if a["name"] == "counterparty_id")
    other = next(a for a in cp["attributes"] if a["name"] == "legal_name")
    # counterparty_id is a business_key → is_pk; legal_name is not
    assert pk["is_pk"] is True
    assert other["is_pk"] is False
    assert other["role"] == "attribute"


def test_model_tree_carries_source_file_that_exists(model_dir):
    # the `file` per entity is what a click opens; it must be a real relative path on disk
    repo = _repo(model_dir)
    tree = model_tree(repo.model, path_for=repo.path_for_ulid)
    cp = next(e for e in tree["entities"] if e["name"] == "counterparty")
    assert cp["file"] == "logical/entities/counterparty.yaml"
    assert (model_dir / cp["file"]).exists()


def test_model_tree_without_path_for_has_null_file(model_dir):
    # pure Model (no ModelRepo file map) → file is null, never crashes
    tree = model_tree(_repo(model_dir).model)
    assert all(e["file"] is None for e in tree["entities"])


def test_model_tree_scopes_to_subject_area(model_dir):
    repo = _repo(model_dir)
    # both fixture entities are homed in Trading; an unknown area scopes to nothing
    scoped = model_tree(repo.model, path_for=repo.path_for_ulid, subject_area="Trading")
    assert scoped["entities"]
    empty = model_tree(repo.model, subject_area="no-such-area")
    assert empty["entities"] == []
