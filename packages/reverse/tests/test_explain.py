"""Tests for the explained-drift shape (mdl_reverse.explain).

The shape every drift surface consumes: per item, whether --reconcile would fold it
and the concrete action. Mirrors reconcile's safe/breaking boundary exactly."""

from __future__ import annotations

from pathlib import Path

from mdl_core.repo import ModelRepo
from mdl_reverse.drift import compute_drift
from mdl_reverse.explain import explain_report, render_explain_text
from mdl_reverse.manifest import read_manifest_dict

from manifest_fixtures import manifest_from_model

TARGET = "duckdb_dev"


def _report(model_dir: Path, mutate=None):
    repo = ModelRepo.load(model_dir)
    raw = manifest_from_model(repo.model, TARGET)
    if mutate:
        mutate(raw)
    proj = read_manifest_dict(raw)
    return compute_drift(repo.model, proj, TARGET)


def test_zero_drift_explains_clean(model_dir: Path):
    ex = explain_report(_report(model_dir))
    assert ex["items"] == []
    assert ex["has_breaking"] is False
    assert ex["safe_count"] == 0
    assert ex["breaking_count"] == 0
    assert "no drift" in render_explain_text(_report(model_dir))


def test_additive_column_is_reconcilable_with_action(model_dir: Path):
    def add_column(raw):
        node = raw["nodes"]["model.testproj.counterparty"]
        node["columns"]["new_col"] = {"name": "new_col", "data_type": "VARCHAR"}

    ex = explain_report(_report(model_dir, add_column))
    added = [i for i in ex["items"] if i["column"] == "new_col"]
    assert added, ex["items"]
    item = added[0]
    assert item["reconcilable"] is True
    assert item["reconcile_action"] is not None
    assert "new_col" in item["reconcile_action"]
    # it shows up in the reconcilable set and the safe count
    assert ex["safe_count"] >= 1
    assert any(i["column"] == "new_col" for i in ex["reconcilable"])


def test_breaking_drop_is_never_reconcilable(model_dir: Path):
    def drop_a_column(raw):
        node = raw["nodes"]["model.testproj.counterparty"]
        node["columns"].pop("legal_name", None)

    ex = explain_report(_report(model_dir, drop_a_column))
    breaking = [i for i in ex["items"] if i["severity"] == "breaking"]
    assert breaking, ex["items"]
    for i in breaking:
        assert i["reconcilable"] is False
        assert i["reconcile_action"] is None
    assert ex["has_breaking"] is True
    assert ex["breaking_count"] == len(breaking)
    # no breaking item leaks into the reconcilable set
    assert all(i["severity"] != "breaking" for i in ex["reconcilable"])


def test_explain_resolves_owning_file(model_dir: Path):
    from mdl_reverse.reconcile import model_name_to_ulid

    repo = ModelRepo.load(model_dir)

    def add_column(raw):
        node = raw["nodes"]["model.testproj.counterparty"]
        node["columns"]["new_col"] = {"name": "new_col", "data_type": "VARCHAR"}

    raw = manifest_from_model(repo.model, TARGET)
    add_column(raw)
    report = compute_drift(repo.model, read_manifest_dict(raw), TARGET)

    name_to_le = model_name_to_ulid(repo, TARGET)

    def file_for(model_name):
        le = name_to_le.get(model_name)
        return repo.path_for_ulid(le) if le else None

    ex = explain_report(report, file_for)
    hit = next(i for i in ex["items"] if i["column"] == "new_col")
    assert hit["file"] is not None
    assert hit["file"].endswith(".yaml")


def test_narrative_groups_and_flags_human_decision(model_dir: Path):
    def drop_a_column(raw):
        node = raw["nodes"]["model.testproj.counterparty"]
        node["columns"].pop("legal_name", None)

    text = render_explain_text(_report(model_dir, drop_a_column))
    assert "[breaking]" in text
    assert "human decision" in text
