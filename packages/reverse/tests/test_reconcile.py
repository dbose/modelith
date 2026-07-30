"""Reconcile: additive/cosmetic deltas fold into the model, breaking is skipped."""

from __future__ import annotations

from pathlib import Path

from mdl_core.repo import ModelRepo
from mdl_reverse.drift import compute_drift
from mdl_reverse.manifest import read_manifest_dict
from mdl_reverse.reconcile import reconcile

from manifest_fixtures import manifest_from_model

TARGET = "duckdb_dev"


def test_reconcile_adds_column_and_preserves_comments(model_dir: Path):
    # Add a comment to the counterparty logical entity file to prove survival.
    le_file = model_dir / "logical" / "entities" / "counterparty.yaml"
    le_file.write_text("# hand comment\n" + le_file.read_text())

    repo = ModelRepo.load(model_dir)
    raw = manifest_from_model(repo.model, TARGET)
    raw["nodes"]["model.testproj.counterparty"]["columns"]["adopted_col"] = {
        "name": "adopted_col",
        "data_type": "VARCHAR",
    }
    # Also inject a breaking drop that reconcile must NOT touch.
    raw["nodes"]["model.testproj.counterparty"]["columns"].pop("legal_name", None)

    proj = read_manifest_dict(raw)
    report = compute_drift(repo.model, proj, TARGET)
    result = reconcile(repo, report, TARGET)
    repo.save()

    assert any("adopted_col" in a for a in result.applied)
    assert result.skipped_breaking >= 1

    text = le_file.read_text()
    assert "# hand comment" in text  # comment preserved
    assert "adopted_col" in text  # column added

    # Reload -> the model now validates and includes the new attribute.
    repo2 = ModelRepo.load(model_dir)
    le = next(e for e in repo2.model.logical_entities.values() if e.name == "counterparty")
    assert any(a.name == "adopted_col" for a in le.attributes)


def test_reconcile_is_idempotent(model_dir: Path):
    repo = ModelRepo.load(model_dir)
    raw = manifest_from_model(repo.model, TARGET)
    raw["nodes"]["model.testproj.counterparty"]["columns"]["adopted_col"] = {
        "name": "adopted_col",
        "data_type": "VARCHAR",
    }
    proj = read_manifest_dict(raw)

    report = compute_drift(repo.model, proj, TARGET)
    reconcile(repo, report, TARGET)
    repo.save()

    # Second pass: the column now exists, so drift is clean and reconcile is a no-op.
    repo2 = ModelRepo.load(model_dir)
    report2 = compute_drift(repo2.model, read_manifest_dict(raw), TARGET)
    result2 = reconcile(repo2, report2, TARGET)
    assert result2.applied == []
