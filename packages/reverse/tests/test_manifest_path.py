"""ManifestModel carries the dbt model's folder location (path / fqn).

Folder-structure classification (a `models/marts/finance/...` path -> a finance mart
layer) needs the model's on-disk location. dbt records it as `original_file_path` and
`fqn` on each node; the reader must surface both. A source without them (DDL, an older
manifest) must keep the historical empty defaults — additive, no drift regression.
"""

from __future__ import annotations

from mdl_reverse.manifest import read_manifest_dict


def _manifest(nodes: dict) -> dict:
    return {
        "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
        "nodes": nodes,
    }


def test_reads_original_file_path_and_fqn():
    raw = _manifest(
        {
            "model.shop.dim_customer": {
                "resource_type": "model",
                "name": "dim_customer",
                "original_file_path": "models/marts/finance/dim_customer.sql",
                "fqn": ["shop", "marts", "finance", "dim_customer"],
                "columns": {},
                "config": {"contract": {"enforced": True}},
                "tags": [],
                "meta": {},
            }
        }
    )
    proj = read_manifest_dict(raw)
    m = proj.models["dim_customer"]
    assert m.path == "models/marts/finance/dim_customer.sql"
    assert m.fqn == ["shop", "marts", "finance", "dim_customer"]


def test_missing_path_fqn_defaults_are_empty():
    """A node without location info (or a DDL-derived model) keeps empty defaults."""
    raw = _manifest(
        {
            "model.shop.orders": {
                "resource_type": "model",
                "name": "orders",
                "columns": {},
                "config": {},
                "tags": [],
                "meta": {},
            }
        }
    )
    m = read_manifest_dict(raw).models["orders"]
    assert m.path is None
    assert m.fqn == []


def test_falls_back_to_plain_path_key():
    """Some manifests carry `path` (relative to model-paths) instead of
    original_file_path; accept it as a fallback."""
    raw = _manifest(
        {
            "model.shop.stg_orders": {
                "resource_type": "model",
                "name": "stg_orders",
                "path": "staging/stg_orders.sql",
                "columns": {},
                "config": {},
                "tags": [],
                "meta": {},
            }
        }
    )
    assert read_manifest_dict(raw).models["stg_orders"].path == "staging/stg_orders.sql"
