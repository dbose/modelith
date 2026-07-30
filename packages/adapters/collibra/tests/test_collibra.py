"""Collibra adapter tests (spec §9.3, §9.4, §12 acceptance)."""

from __future__ import annotations

import pytest
from mdl_adapter_collibra import CollibraAdapter, MockTransport
from mdl_governance import ForeignPlanError, Profile, build_graph
from mdl_governance.spi import ChangeType, SyncPlan


def _adapter(preexisting=None, writeback_rows=None):
    return CollibraAdapter(
        transport=MockTransport(
            preexisting=set(preexisting or []),
            writeback_rows=list(writeback_rows or []),
        )
    )


def _profile(profiles_dir):
    return Profile.load(profiles_dir / "collibra-oob.yaml")


def test_plan_never_writes(model, profiles_dir):
    transport = MockTransport()
    adapter = CollibraAdapter(transport=transport)
    plan = adapter.plan(build_graph(model), _profile(profiles_dir))
    # planning must not have imported anything
    assert transport.imported_batches == []
    assert plan.creates()  # there is work to do
    assert plan.signature.startswith("sha256:")


def test_plan_is_clean_and_all_create_on_empty_catalog(model, profiles_dir):
    # §12 acceptance: a clean plan against an (empty) sandbox.
    adapter = _adapter()
    plan = adapter.plan(build_graph(model), _profile(profiles_dir))
    assert all(c.change == ChangeType.create for c in plan.changes)


def test_idempotent_second_run_is_update_not_duplicate(model, profiles_dir):
    graph = build_graph(model)
    # simulate first sync: everything now exists in the catalog by external_id
    existing = {a.external_id for a in graph.assets}
    adapter = _adapter(preexisting=existing)
    plan = adapter.plan(graph, _profile(profiles_dir))
    assert plan.creates() == []
    assert all(c.change == ChangeType.update for c in plan.changes)


def test_apply_executes_and_batches(model, profiles_dir):
    transport = MockTransport()
    adapter = CollibraAdapter(transport=transport)
    plan = adapter.plan(build_graph(model), _profile(profiles_dir))
    result = adapter.apply(plan)
    assert result.applied == len(plan.changes)
    assert result.created == len(plan.creates())
    assert transport.imported_count == len(plan.changes)


def test_apply_refuses_foreign_plan(model, profiles_dir):
    adapter = _adapter()
    plan = adapter.plan(build_graph(model), _profile(profiles_dir))
    # tamper with the plan after generation -> signature mismatch
    plan.changes[0].name = "HACKED"
    with pytest.raises(ForeignPlanError):
        adapter.apply(plan)


def test_apply_refuses_other_adapters_plan(model, profiles_dir):
    adapter = _adapter()
    foreign = SyncPlan(adapter="datahub", profile_name="x", changes=[])
    foreign.signature = foreign.compute_signature()
    with pytest.raises(ForeignPlanError):
        adapter.apply(foreign)


def test_pull_writeback(profiles_dir):
    rows = [
        {"attribute": "Data Classification", "externalId": "mdl:01ABC", "value": "PII"},
        {"attribute": "Retention Period", "externalId": "mdl:01ABC", "value": "7y"},
    ]
    adapter = _adapter(writeback_rows=rows)
    wb = adapter.pull(_profile(profiles_dir))
    paths = {v.model_path for v in wb.values}
    assert "governance.classification" in paths
    assert "governance.retention" in paths


def test_capabilities():
    caps = _adapter().capabilities()
    assert caps.name == "collibra"
    assert caps.supports_writeback and caps.supports_lineage
