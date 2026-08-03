"""Interactive-accept of a medium-confidence relationship must materialise it.

Medium-confidence relationships are proposed (not auto-added) at build time. When
the user accepts one in `mdl reverse --interactive`, the ledger verdict flips to
accepted AFTER the model is built — so the relationship was never added. This
covers the post-review materialisation.
"""

from __future__ import annotations

from mdl_reverse.ledger import Confidence, Decision, DecisionLedger, Verdict
from mdl_reverse.manifest import read_manifest_dict
from mdl_reverse.reverse import apply_accepted_relationships
from mdl_reverse.reverse import reverse as run_reverse


def _manifest():
    return {
        "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json"},
        "nodes": {
            "model.w.dim_customer": {
                "resource_type": "model",
                "name": "dim_customer",
                "columns": {"customer_id": {"data_type": "BIGINT"}},
                "config": {},
            },
            "model.w.fct_orders": {
                "resource_type": "model",
                "name": "fct_orders",
                "columns": {
                    "order_id": {"data_type": "BIGINT"},
                    "customer_id": {"data_type": "BIGINT"},  # FK by name/type heuristic
                },
                "config": {},
            },
        },
    }


def test_accepted_medium_relationship_is_materialised():
    proj = read_manifest_dict(_manifest())
    ledger = DecisionLedger()
    result = run_reverse(proj, project_name="w", target="duckdb_dev", ledger=ledger, interactive=True)

    # the FK was proposed (medium) but NOT added yet
    assert len(result.model.relationships) == 0

    # simulate the interactive accept: flip the pending relationship verdict
    for d in ledger.pending():
        if d.kind == "relationship":
            d.verdict = Verdict.accepted

    added = apply_accepted_relationships(result.model, ledger)
    assert added == 1
    assert len(result.model.relationships) == 1
    rel = next(iter(result.model.relationships.values()))
    frm = result.model.logical_entities[rel.from_.entity]
    to = result.model.logical_entities[rel.to.entity]
    assert frm.name == "fct_orders" and to.name == "dim_customer"


def test_apply_is_idempotent():
    proj = read_manifest_dict(_manifest())
    ledger = DecisionLedger()
    result = run_reverse(proj, project_name="w", target="duckdb_dev", ledger=ledger, interactive=True)
    for d in ledger.pending():
        if d.kind == "relationship":
            d.verdict = Verdict.accepted
    assert apply_accepted_relationships(result.model, ledger) == 1
    # second call adds nothing (endpoints already linked)
    assert apply_accepted_relationships(result.model, ledger) == 0


def test_rejected_relationship_not_added():
    proj = read_manifest_dict(_manifest())
    ledger = DecisionLedger()
    result = run_reverse(proj, project_name="w", target="duckdb_dev", ledger=ledger, interactive=True)
    for d in ledger.pending():
        if d.kind == "relationship":
            d.verdict = Verdict.rejected
    assert apply_accepted_relationships(result.model, ledger) == 0
    assert len(result.model.relationships) == 0
    _ = (Confidence, Decision)  # imported for parity with sibling tests
