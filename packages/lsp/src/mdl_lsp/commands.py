"""LSP command executors — all delegate to the single core mutation engine.

`mdl.lift` is the selection-scoped reverse (spec §6.3 done right): ONE dbt model
lifted into the Modelith model, through the same lifting heuristics and decision
ledger as `mdl reverse` — never a whole-project sweep.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.commands import apply_command
from mdl_core.yaml_io import dump_str
from mdl_lsp.workspace import ModelWorkspace
from mdl_reverse.ledger import DecisionLedger
from mdl_reverse.reverse import _lift_entity


class LspCommandError(Exception):
    pass


def lift_model(ws: ModelWorkspace, sql_path: str) -> str:
    """Selection-scoped lift: one dbt model -> conceptual + logical entity,
    surrogate keys stripped, SCD2 detected, proposals recorded in the ledger."""
    if ws.model_dir is None:
        raise LspCommandError("no Modelith model in this workspace")
    manifest = ws.manifest
    if manifest is None:
        raise LspCommandError("no manifest.json — run `dbt parse` first")
    name = Path(sql_path).stem
    mm = manifest.models.get(name)
    if mm is None:
        raise LspCommandError(f"model {name!r} not in the manifest")
    if ws.entity_for_dbt_model(name) is not None:
        raise LspCommandError(f"{name!r} is already in the Modelith model")

    ledger = DecisionLedger.load(ws.model_dir)
    le, ce, _proposals = _lift_entity(mm, name, ledger, auto_accept_high=True)

    repo = ws.repo
    assert repo is not None

    def _dump(obj) -> dict:
        return obj.model_dump(by_alias=True, exclude_none=True, mode="json")

    ce_rel = f"conceptual/entities/{le.name}.yaml"
    le_rel = f"logical/entities/{le.name}.yaml"
    repo.raw[ce_rel] = _dump(ce)
    repo.raw[le_rel] = _dump(le)
    repo.file_ulid[ce_rel] = ce.id
    repo.file_ulid[le_rel] = le.id
    # write via yaml_io for consistent formatting
    (ws.model_dir / ce_rel).parent.mkdir(parents=True, exist_ok=True)
    (ws.model_dir / le_rel).parent.mkdir(parents=True, exist_ok=True)
    (ws.model_dir / ce_rel).write_text(dump_str(_dump(ce)), encoding="utf-8")
    (ws.model_dir / le_rel).write_text(dump_str(_dump(le)), encoding="utf-8")
    ledger.save(ws.model_dir)
    return f"lifted {name!r} into the model ({len(le.attributes)} attributes)"


def adopt_column(ws: ModelWorkspace, entity_name: str, column: str, sql_type: str | None) -> str:
    """Spec `mdl adopt`, scoped to the representable case: an additive column."""
    if ws.model_dir is None:
        raise LspCommandError("no Modelith model in this workspace")
    le = ws.entity_for_dbt_model(entity_name)
    if le is None:
        raise LspCommandError(f"no entity {entity_name!r} in the model")
    from mdl_reverse.reverse import _base_for

    apply_command(
        ws.model_dir,
        "add_attribute",
        {"entity_id": le.id, "name": column, "domain": _base_for(sql_type), "nullable": True},
    )
    return f"adopted {entity_name}.{column} into the model"


def unmanage(ws: ModelWorkspace, entity_name: str) -> str:
    """Spec `mdl unmanage`: entity stays in the model; its SQL becomes
    engineer-owned — the emitter stops emitting the file."""
    if ws.model_dir is None:
        raise LspCommandError("no Modelith model in this workspace")
    le = ws.entity_for_dbt_model(entity_name)
    if le is None:
        raise LspCommandError(f"no entity {entity_name!r} in the model")
    apply_command(ws.model_dir, "set_unmanaged", {"id": le.id, "unmanaged": True})
    return f"{entity_name!r} is now engineer-owned; Modelith will not regenerate its SQL"


def declare_relationship(
    ws: ModelWorkspace, from_entity: str, column: str, to_entity: str
) -> str:
    if ws.model_dir is None:
        raise LspCommandError("no Modelith model in this workspace")
    frm = ws.entity_for_dbt_model(from_entity)
    to = ws.entity_for_dbt_model(to_entity)
    if frm is None or to is None:
        raise LspCommandError("both entities must exist in the model")
    from_attr = ws.attribute(frm, column)
    to_bk = next((a for a in to.attributes if a.role == "business_key"), None)
    apply_command(
        ws.model_dir,
        "create_relationship",
        {
            "from_entity": frm.id,
            "to_entity": to.id,
            "from_attribute": from_attr.id if from_attr else None,
            "to_attribute": to_bk.id if to_bk else None,
            "cardinality": "many_to_one",
        },
    )
    return f"declared {from_entity}.{column} → {to_entity}"
