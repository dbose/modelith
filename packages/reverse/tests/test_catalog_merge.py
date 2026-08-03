"""catalog.json merge: reverse must see the REAL physical columns.

dbt's manifest only records columns documented in .yml, so undocumented physical
columns (surrogate keys, SCD2 tracking) are invisible to a manifest-only reverse.
`dbt docs generate` produces catalog.json by introspecting the warehouse; merging
it gives reverse the full column set so SCD2/surrogate detection actually fires.
"""

from __future__ import annotations

import json

from mdl_reverse.manifest import read_manifest


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_catalog_fills_undocumented_columns(tmp_path):
    # manifest: dim_customer documents only customer_id (the tested column)
    _write(
        tmp_path / "manifest.json",
        {
            "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json"},
            "nodes": {
                "model.w.dim_customer": {
                    "resource_type": "model",
                    "name": "dim_customer",
                    "columns": {"customer_id": {"data_type": "BIGINT"}},
                    "config": {},
                }
            },
        },
    )
    # catalog: the real warehouse columns, including surrogate + SCD2 tracking
    _write(
        tmp_path / "catalog.json",
        {
            "nodes": {
                "model.w.dim_customer": {
                    "metadata": {"name": "dim_customer"},
                    "columns": {
                        "customer_sk": {"type": "VARCHAR"},
                        "customer_id": {"type": "BIGINT"},
                        "name": {"type": "VARCHAR"},
                        "valid_from": {"type": "DATE"},
                        "valid_to": {"type": "DATE"},
                        "is_current": {"type": "BOOLEAN"},
                    },
                }
            }
        },
    )
    proj = read_manifest(tmp_path / "manifest.json")
    cols = set(proj.models["dim_customer"].columns)
    # manifest-only would be just {customer_id}; merge brings in the rest
    assert {"customer_sk", "valid_from", "valid_to", "is_current"} <= cols


def test_no_catalog_falls_back_to_manifest(tmp_path):
    _write(
        tmp_path / "manifest.json",
        {
            "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json"},
            "nodes": {
                "model.w.dim_customer": {
                    "resource_type": "model",
                    "name": "dim_customer",
                    "columns": {"customer_id": {"data_type": "BIGINT"}},
                    "config": {},
                }
            },
        },
    )
    # no catalog.json → manifest columns only, no crash
    proj = read_manifest(tmp_path / "manifest.json")
    assert set(proj.models["dim_customer"].columns) == {"customer_id"}
