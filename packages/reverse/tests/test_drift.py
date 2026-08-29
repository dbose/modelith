"""Drift classification tests (spec §5.4)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.repo import ModelRepo
from mdl_reverse.drift import DriftKind, DriftSeverity, compute_drift
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


def test_zero_drift_baseline(model_dir: Path):
    report = _report(model_dir)
    assert report.items == [], [i.detail for i in report.items]
    assert not report.has_breaking


def test_column_dropped_is_breaking(model_dir: Path):
    def drop_a_column(raw):
        node = raw["nodes"]["model.testproj.counterparty"]
        # remove a column the model still declares
        node["columns"].pop("legal_name", None)

    report = _report(model_dir, drop_a_column)
    dropped = [i for i in report.items if i.kind == DriftKind.column_dropped]
    assert dropped and dropped[0].severity == DriftSeverity.breaking
    assert report.has_breaking


def test_new_column_in_dbt_is_additive(model_dir: Path):
    def add_column(raw):
        node = raw["nodes"]["model.testproj.counterparty"]
        node["columns"]["new_col"] = {"name": "new_col", "data_type": "VARCHAR"}

    report = _report(model_dir, add_column)
    added = [i for i in report.items if i.kind == DriftKind.column_added]
    assert added and added[0].severity == DriftSeverity.additive
    assert not report.has_breaking


def test_type_narrowing_is_breaking(model_dir: Path):
    def narrow(raw):
        node = raw["nodes"]["model.testproj.counterparty"]
        node["columns"]["counterparty_id"]["data_type"] = "INTEGER"  # BIGINT->INTEGER

    report = _report(model_dir, narrow)
    narrowed = [i for i in report.items if i.kind == DriftKind.type_narrowed]
    assert narrowed and narrowed[0].severity == DriftSeverity.breaking


def test_type_change_nonnarrowing_still_breaking(model_dir: Path):
    def change(raw):
        node = raw["nodes"]["model.testproj.counterparty"]
        node["columns"]["legal_name"]["data_type"] = "DATE"

    report = _report(model_dir, change)
    changed = [i for i in report.items if i.kind == DriftKind.type_changed]
    assert changed and changed[0].severity == DriftSeverity.breaking


def test_scd2_system_columns_are_cosmetic_not_breaking():
    """F2 regression: a reversed SCD2 model whose emitter uses valid_from/valid_to must
    not fail its own drift gate when the source manifest used dbt_valid_from/to. The
    same tracking column under different vendor prefixes is a cosmetic rename."""
    from mdl_core.ir import (
        Attribute,
        ConceptualEntity,
        LogicalEntity,
        Model,
        ProjectConfig,
    )
    from mdl_reverse.manifest import ManifestColumn, ManifestModel, ManifestProjection

    m = Model(ProjectConfig(name="t"))
    ce = ConceptualEntity(id="01J0000000000000000000CE00", name="Customer")
    le = LogicalEntity(
        id="01J0000000000000000000LE00", name="dim_customers", realises=ce.id,
        pattern="scd2",
        attributes=[Attribute(id="01J000000000000000000A100", name="customer_id",
                              role="business_key", domain="bigint")],
    )
    m.add(ce)
    m.add(le)

    # manifest: business col + dbt-snapshot tracking columns
    man = ManifestProjection(models={
        "dim_customers": ManifestModel(name="dim_customers", unique_id="model.t.dim_customers",
            contract_enforced=True,
            columns={
                "customer_id": ManifestColumn("customer_id", "BIGINT"),
                "dbt_valid_from": ManifestColumn("dbt_valid_from", "TIMESTAMP"),
                "dbt_valid_to": ManifestColumn("dbt_valid_to", "TIMESTAMP"),
                "dbt_scd_id": ManifestColumn("dbt_scd_id", "VARCHAR"),
            })
    })
    report = compute_drift(m, man, TARGET)
    kinds = {i.kind for i in report.items}
    assert DriftKind.system_column_renamed in kinds
    assert not report.has_breaking, [i.detail for i in report.items if i.severity == DriftSeverity.breaking]
    # the reconciled pairs are cosmetic
    renames = [i for i in report.items if i.kind == DriftKind.system_column_renamed]
    assert all(i.severity == DriftSeverity.cosmetic for i in renames)


def test_unmanaged_model(model_dir: Path):
    def add_model(raw):
        raw["nodes"]["model.testproj.stray"] = {
            "resource_type": "model",
            "name": "stray",
            "columns": {},
            "config": {"contract": {"enforced": True}},
        }

    report = _report(model_dir, add_model)
    unmanaged = [i for i in report.items if i.kind == DriftKind.unmanaged_model]
    assert unmanaged and unmanaged[0].severity == DriftSeverity.unmanaged
    assert not report.has_breaking  # unmanaged warns, doesn't fail by default


def test_model_removed_from_dbt_is_breaking(model_dir: Path):
    def remove_model(raw):
        raw["nodes"].pop("model.testproj.trade", None)
        # also drop its relationship test
        for k in list(raw["nodes"]):
            if "relationships_trade" in k:
                raw["nodes"].pop(k)

    report = _report(model_dir, remove_model)
    removed = [i for i in report.items if i.kind == DriftKind.model_removed]
    assert removed and removed[0].severity == DriftSeverity.breaking


def test_relationship_removed_is_breaking(model_dir: Path):
    def drop_rel(raw):
        for k in list(raw["nodes"]):
            if raw["nodes"][k].get("resource_type") == "test":
                raw["nodes"].pop(k)

    report = _report(model_dir, drop_rel)
    rel = [i for i in report.items if i.kind == DriftKind.relationship_removed]
    assert rel and rel[0].severity == DriftSeverity.breaking


def test_description_change_is_cosmetic(model_dir: Path):
    def change_desc(raw):
        raw["nodes"]["model.testproj.counterparty"]["description"] = "totally new text"

    report = _report(model_dir, change_desc)
    cosmetic = [i for i in report.items if i.kind == DriftKind.description_changed]
    assert cosmetic and cosmetic[0].severity == DriftSeverity.cosmetic
    assert not report.has_breaking


def test_cosmetic_type_spelling_not_flagged(model_dir: Path):
    def respell(raw):
        # BIGINT vs bigint / whitespace should normalise, not drift
        raw["nodes"]["model.testproj.counterparty"]["columns"]["counterparty_id"][
            "data_type"
        ] = "bigint"

    report = _report(model_dir, respell)
    types = [i for i in report.items if i.kind in (DriftKind.type_changed, DriftKind.type_narrowed)]
    assert types == []
