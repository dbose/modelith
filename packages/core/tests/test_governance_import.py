"""catalog -> git import: writeback values become real, reviewable YAML edits.

The import must reuse the mutation engine (comment-preserving) and never invent
objects for unknown external ids.
"""

from __future__ import annotations

from dataclasses import dataclass

from mdl_core.commands import apply_command
from mdl_core.governance_import import writeback_to_commands
from mdl_core.repo import ModelRepo


# Minimal stand-ins mirroring mdl_governance.spi.WritebackValue / WritebackSet so
# the core test stays free of the governance dependency (layering §1.3).
@dataclass
class _V:
    external_id: str
    model_path: str
    value: object


@dataclass
class _WB:
    values: list


def _counterparty(model):
    return next(c for c in model.conceptual_entities.values() if c.name == "Counterparty")


def test_writeback_maps_meaning_fields(model_dir):
    model = ModelRepo.load(model_dir).model
    ce = _counterparty(model)
    wb = _WB(
        values=[
            _V(f"mdl:{ce.id}", "definition", "An updated, catalog-authored definition."),
            _V(f"mdl:{ce.id}", "synonyms", ["Counterparty", "CPTY", "Trading partner"]),
            _V(f"mdl:{ce.id}", "stewardship.owner", "risk-data-office"),
            _V(f"mdl:{ce.id}", "stewardship.steward", "a.hough"),
        ]
    )
    cmds = writeback_to_commands(model, wb)
    ops = {op for op, _ in cmds}
    assert ops == {"set_definition", "update_synonyms", "set_stewardship"}
    # owner + steward coalesce into ONE set_stewardship command
    stew = next(p for op, p in cmds if op == "set_stewardship")
    assert stew["owner"] == "risk-data-office" and stew["steward"] == "a.hough"

    # applying through the engine actually writes YAML
    for op, payload in cmds:
        apply_command(model_dir, op, payload)
    reloaded = _counterparty(ModelRepo.load(model_dir).model)
    assert reloaded.definition == "An updated, catalog-authored definition."
    assert "Trading partner" in reloaded.synonyms


def test_writeback_ignores_unknown_ids_and_paths(model_dir):
    model = ModelRepo.load(model_dir).model
    wb = _WB(
        values=[
            _V("mdl:01UNKNOWN0000000000000000XX", "definition", "ghost"),
            _V(f"mdl:{_counterparty(model).id}", "governance.classification", "PII"),
        ]
    )
    # unknown id dropped; non-glossary path (classification) dropped
    assert writeback_to_commands(model, wb) == []


def test_split_synonyms_from_string(model_dir):
    model = ModelRepo.load(model_dir).model
    ce = _counterparty(model)
    wb = _WB(values=[_V(f"mdl:{ce.id}", "synonyms", "A, B ,C")])
    cmds = writeback_to_commands(model, wb)
    assert cmds == [("update_synonyms", {"id": ce.id, "synonyms": ["A", "B", "C"]})]
