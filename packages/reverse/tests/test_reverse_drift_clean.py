"""North-star trust metric: a freshly-reversed model is drift-clean against its own
warehouse.

The whole value proposition of `mdl reverse` + `mdl drift --check` is a trustworthy
gate. If reversing a warehouse and immediately drifting the result against that SAME
warehouse reports drift — especially *breaking* drift — the gate cries wolf on day one
and a team disables it within a week (the finding an in-silico buyer simulation
surfaced). This test pins that to zero: reverse(manifest) then drift(reversed model,
same manifest) must have NO BREAKING drift.

We reverse a hand-authored manifest+catalog with realistic warehouse types (DECIMAL,
DATE, BOOLEAN, VARCHAR) — not one derived from a Modelith model — because that is what
a real `dbt docs generate` produces and where reverse's type fidelity is actually
exercised.
"""

from __future__ import annotations

from typing import Any

from mdl_reverse.drift import DriftSeverity, compute_drift
from mdl_reverse.manifest import read_manifest_dict
from mdl_reverse.reverse import reverse

TARGET = "duckdb_dev"


def _manifest_and_catalog() -> tuple[dict[str, Any], dict[str, Any]]:
    """A small realistic warehouse: a customer dimension (natural + surrogate key, a
    DECIMAL, a DATE, a BOOLEAN) and an orders fact with an FK to it. Types are the real
    warehouse types dbt's catalog carries."""

    def model_node(name: str, cols: dict[str, str], rels=None) -> dict[str, Any]:
        return {
            "resource_type": "model",
            "name": name,
            "columns": {c: {"name": c, "data_type": t, "description": None} for c, t in cols.items()},
            "config": {"contract": {"enforced": True}},
            "description": f"{name} model",
            "meta": {},
            "tags": [],
        }

    dim_cols = {
        "customer_sk": "VARCHAR",
        "customer_id": "INTEGER",
        "email": "VARCHAR",
        "lifetime_value": "DECIMAL(18,2)",
        "signup_date": "DATE",
        "is_active": "BOOLEAN",
    }
    fct_cols = {
        "order_sk": "VARCHAR",
        "order_id": "INTEGER",
        "customer_id": "INTEGER",
        "amount": "DECIMAL(18,2)",
        "ordered_at": "TIMESTAMP",
    }
    nodes: dict[str, Any] = {
        "model.shop.dim_customer": model_node("dim_customer", dim_cols),
        "model.shop.fct_order": model_node("fct_order", fct_cols),
        # a relationships test: fct_order.customer_id -> dim_customer
        "test.shop.relationships_fct_order_customer_id": {
            "resource_type": "test",
            "name": "relationships_fct_order_customer_id",
            "attached_node": "model.shop.fct_order",
            "test_metadata": {
                "name": "relationships",
                "kwargs": {"column_name": "customer_id", "to": "ref('dim_customer')"},
            },
        },
    }
    manifest = {
        "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
        "nodes": nodes,
    }
    # the catalog carries the same real types (what dbt docs generate introspects)
    catalog = {
        "nodes": {
            "model.shop.dim_customer": {
                "metadata": {"name": "dim_customer"},
                "columns": {c: {"type": t, "name": c} for c, t in dim_cols.items()},
            },
            "model.shop.fct_order": {
                "metadata": {"name": "fct_order"},
                "columns": {c: {"type": t, "name": c} for c, t in fct_cols.items()},
            },
        }
    }
    return manifest, catalog


def _project(manifest, catalog):
    proj = read_manifest_dict(manifest)
    # merge catalog types the way read_manifest(path) does for a real project
    from mdl_reverse.manifest import _merge_catalog

    _merge_catalog(proj, catalog)
    return proj


def test_reverse_then_drift_has_no_breaking_drift():
    """The north-star trust metric. Reverse the warehouse, drift the reversed model
    against that same warehouse: there must be NO breaking drift. Breaking drift here
    means reverse produced a model that disagrees with the warehouse it came from —
    the exact 'cries wolf on day one' failure."""
    manifest, catalog = _manifest_and_catalog()
    proj = _project(manifest, catalog)

    result = reverse(proj, project_name="shop", target=TARGET)

    # drift the reversed model against the SAME warehouse projection
    report = compute_drift(result.model, proj, TARGET)

    breaking = [i for i in report.items if i.severity == DriftSeverity.breaking]
    assert not breaking, (
        "a freshly-reversed model must be drift-clean against its own warehouse; "
        f"got breaking drift: {[(i.model, i.column, i.detail) for i in breaking]}"
    )
