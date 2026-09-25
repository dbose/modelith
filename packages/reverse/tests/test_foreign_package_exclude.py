"""dbt tooling metadata (dbt_artifacts, elementary) must be auto-excluded from reverse.

The reported bug: `dim_dbt_models`, `fct_dbt_invocations`, … got reverse-engineered as
business entities. They belong to installed packages, not the root project — the clean
signal is the manifest's package_name. They are dropped by default; --include-packages
keeps a named one.
"""

from __future__ import annotations

from mdl_reverse.manifest import ManifestModel, ManifestProjection, read_manifest
from mdl_reverse.reverse import classification_summary, reverse


def _proj():
    return ManifestProjection(
        root_project="my_project",
        models={
            "dim_customer": ManifestModel(
                name="dim_customer",
                unique_id="model.my_project.dim_customer",
                package_name="my_project",
            ),
            "dim_dbt_models": ManifestModel(
                name="dim_dbt_models",
                unique_id="model.dbt_artifacts.dim_dbt_models",
                package_name="dbt_artifacts",
            ),
            "fct_dbt_invocations": ManifestModel(
                name="fct_dbt_invocations",
                unique_id="model.dbt_artifacts.fct_dbt_invocations",
                package_name="dbt_artifacts",
            ),
        },
    )


def test_foreign_packages_excluded_by_default():
    result = reverse(_proj())
    names = {le.name for le in result.model.logical_entities.values()}
    assert "customer" in {n.lower() for n in names} or "dim_customer" in names
    assert not any("dbt" in n.lower() for n in names)  # no dbt-meta entities
    excluded = {name for name, _ in result.excluded_foreign}
    assert excluded == {"dim_dbt_models", "fct_dbt_invocations"}


def test_include_packages_keeps_them():
    result = reverse(_proj(), include_packages={"dbt_artifacts"})
    assert result.excluded_foreign == []
    assert len(result.model.logical_entities) == 3


def test_summary_reports_foreign_exclusions():
    s = classification_summary(reverse(_proj()))
    assert len(s.excluded_foreign) == 2
    assert all(pkg == "dbt_artifacts" for _, pkg in s.excluded_foreign)


def test_no_root_project_keeps_everything():
    # a source with no root_project (DDL, older manifest) never foreign-excludes
    proj = _proj()
    proj.root_project = None
    result = reverse(proj)
    assert result.excluded_foreign == []


def test_manifest_reader_captures_package_and_root(tmp_path):
    m = tmp_path / "manifest.json"
    m.write_text(
        '{"metadata":{"project_name":"my_project",'
        '"dbt_schema_version":"https://schemas.getdbt.com/dbt/manifest/v12.json"},'
        '"nodes":{"model.dbt_artifacts.dim_dbt_models":{"resource_type":"model",'
        '"name":"dim_dbt_models","package_name":"dbt_artifacts","columns":{}}}}',
        encoding="utf-8",
    )
    proj = read_manifest(m)
    assert proj.root_project == "my_project"
    assert proj.models["dim_dbt_models"].package_name == "dbt_artifacts"
