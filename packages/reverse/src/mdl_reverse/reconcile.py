"""Reconcile: fold additive + cosmetic drift into the model (spec §5.4).

`mdl drift --reconcile` opens a PR against the model repo with the additive and
cosmetic deltas already applied. This is the adoption mechanism: teams that never
open the modeling tool keep the model current.

We mutate the raw ruamel YAML nodes in place (via ModelRepo) so comments and key
order survive, then ModelRepo.save() writes them back. Only additive (new column)
and cosmetic (description) changes are applied; breaking drift is never
auto-reconciled — it must be a human decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from mdl_core.ids import new_ulid
from mdl_core.repo import ModelRepo
from mdl_reverse.drift import DriftKind, DriftReport


@dataclass
class ReconcileResult:
    applied: list[str]
    skipped_breaking: int


# Reverse of projection._TYPE_MAP: SQL type -> abstract domain base type, for
# recording a newly-adopted column with a sensible domain.
_SQL_TO_BASE = {
    "BIGINT": "bigint",
    "INTEGER": "integer",
    "VARCHAR": "string",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "DECIMAL(38,0)": "decimal",
    "DECIMAL(38,2)": "decimal",
    "VARCHAR(20)": "lei_code",
}


def _base_for(sql_type: str | None) -> str:
    if not sql_type:
        return "string"
    return _SQL_TO_BASE.get(sql_type.upper(), "string")


def reconcile(repo: ModelRepo, report: DriftReport, target: str) -> ReconcileResult:
    """Apply additive/cosmetic deltas to repo's raw nodes. Caller then saves."""
    # Map model_name -> logical entity ULID, so we know which file to edit.
    name_to_le: dict[str, str] = {}
    pt_by_logical = {
        pt.realises: pt for pt in repo.model.physical_tables.values() if pt.target == target
    }
    for le in repo.model.logical_entities.values():
        pt = pt_by_logical.get(le.id)
        name = pt.name.lower() if pt else le.name
        name_to_le[name] = le.id

    applied: list[str] = []
    skipped_breaking = 0

    for item in report.items:
        if item.severity.value == "breaking":
            skipped_breaking += 1
            continue
        if item.kind == DriftKind.column_added:
            le_id = name_to_le.get(item.model)
            if le_id and _add_column(repo, le_id, item.column, item.payload):
                applied.append(f"add column {item.model}.{item.column}")
        elif item.kind == DriftKind.description_changed:
            le_id = name_to_le.get(item.model)
            if le_id and _set_description(repo, le_id, item.payload.get("dbt_description")):
                applied.append(f"update description {item.model}")

    return ReconcileResult(applied=applied, skipped_breaking=skipped_breaking)


def _add_column(repo: ModelRepo, le_id: str, col: str | None, payload: dict) -> bool:
    if not col:
        return False
    node = repo.raw_for_ulid(le_id)
    if node is None:
        return False
    attrs = node.setdefault("attributes", [])
    # don't duplicate
    for a in attrs:
        if a.get("name") == col:
            return False
    new_attr = {
        "id": new_ulid(),
        "name": col,
        "domain": _base_for(payload.get("data_type")),
        "role": "attribute",
        "nullable": True,
    }
    attrs.append(new_attr)
    return True


def _set_description(repo: ModelRepo, le_id: str, description: str | None) -> bool:
    if description is None:
        return False
    le = repo.model.logical_entities.get(le_id)
    if le is None or not le.realises:
        return False
    ce_node = repo.raw_for_ulid(le.realises)
    if ce_node is None:
        return False
    ce_node["definition"] = description
    return True
