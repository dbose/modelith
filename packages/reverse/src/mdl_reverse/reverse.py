"""Reverse engineering orchestrator (spec §6).

Lifts a compiled dbt manifest into Modelith IR: logical entities, attributes,
relationships, pattern detection — every non-mechanical inference recorded as a
decision-ledger proposal (§6.2) rather than guessed silently.

Round-trip fidelity (spec §12 M3 acceptance, property 2): when the manifest was
produced by Modelith's own emitter, each model carries `meta.mdl_ulid` (entity)
and `columns[].meta.mdl_ulid` (attribute). Reverse reads those back so ULID
identity survives generate->reverse->generate and the diff is semantically empty.
When the ULIDs are absent (a foreign project), fresh ULIDs are minted and the
lift is interactive.

`reverse()` returns a ReverseResult (the built IR objects + ledger proposals)
without writing; the CLI persists via a ModelRepo-style writer. This keeps the
function testable and side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
)
from mdl_reverse import lifting
from mdl_reverse.ledger import Confidence, Decision, DecisionLedger, Verdict
from mdl_reverse.manifest import ManifestModel, ManifestProjection

# SQL type -> abstract domain base type (inverse of the platform maps). Kept small
# and platform-agnostic; unknown types fall back to "string".
_SQL_TO_BASE = {
    "BIGINT": "bigint",
    "INTEGER": "integer",
    "INT": "integer",
    "NUMBER(38,0)": "bigint",
    "NUMERIC(38,0)": "decimal",
    "DECIMAL(38,0)": "decimal",
    "NUMERIC(38,2)": "decimal",
    "DECIMAL(38,2)": "decimal",
    "NUMBER(38,2)": "decimal",
    "VARCHAR": "string",
    "STRING": "string",
    "VARCHAR(65535)": "string",
    "VARCHAR(20)": "lei_code",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP_NTZ": "timestamp",
}


def _base_for(sql_type: str | None) -> str:
    if not sql_type:
        return "string"
    return _SQL_TO_BASE.get(sql_type.upper(), "string")


@dataclass
class ReverseResult:
    model: Model
    proposals: list[Decision] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)  # staging/intermediate model names

    def logical_count(self) -> int:
        return len(self.model.logical_entities)


def reverse(
    manifest: ManifestProjection,
    *,
    project_name: str = "reversed_model",
    target: str = "duckdb_dev",
    ledger: DecisionLedger | None = None,
    interactive: bool = False,
    auto_accept_high: bool = True,
) -> ReverseResult:
    """Lift a manifest into IR. `auto_accept_high` accepts high-confidence signals
    (FK constraints, relationships tests) by default per §6.2; medium signals are
    proposed only. `interactive=False` (the default, CI-safe) records proposals
    but does not prompt."""
    ledger = ledger or DecisionLedger()
    config = ProjectConfig(
        name=project_name,
        dbt_target=target,
        platform_targets=[target],
    )
    model = Model(config)
    proposals: list[Decision] = []
    excluded: list[str] = []

    # 1) Select business models (exclude staging/intermediate).
    business: dict[str, ManifestModel] = {}
    for name, mm in manifest.models.items():
        if lifting.is_staging(name, mm.tags):
            excluded.append(name)
            continue
        business[name] = mm

    known_models = set(business)

    # 2) Build logical entities + attributes with pattern detection.
    le_by_name: dict[str, LogicalEntity] = {}
    for name in sorted(business):
        mm = business[name]
        le, ce, entity_proposals = _lift_entity(mm, name, ledger, auto_accept_high)
        model.add(ce)
        model.add(le)
        le_by_name[name] = le
        proposals.extend(entity_proposals)

    # 3) Relationship inference (ranked signals, §6.2).
    for name in sorted(business):
        mm = business[name]
        rel_proposals = _infer_relationships(
            mm, name, le_by_name, known_models, ledger, model, auto_accept_high
        )
        proposals.extend(rel_proposals)

    return ReverseResult(model=model, proposals=proposals, excluded=excluded)


def _lift_entity(
    mm: ManifestModel,
    name: str,
    ledger: DecisionLedger,
    auto_accept_high: bool,
) -> tuple[LogicalEntity, ConceptualEntity, list[Decision]]:
    proposals: list[Decision] = []
    col_names = list(mm.columns)

    # Recover ULIDs if this manifest came from our own emitter (round-trip fidelity).
    le_ulid = mm.meta.get("mdl_ulid") if isinstance(mm.meta, dict) else None
    le_ulid = le_ulid or new_ulid()

    # SCD2 detection -> pattern + strip tracking columns from the logical view.
    scd = lifting.detect_scd2(col_names)
    dv = lifting.detect_data_vault(name, col_names)
    pattern = None
    if scd.is_scd2:
        pattern = "scd2"
        d = Decision(
            kind="scd2_pattern",
            signal="scd2_columns",
            confidence=Confidence.medium_high,
            subject=f"model {name!r} looks like SCD2 ({', '.join(scd.tracking_cols)})",
            evidence={"model": name, "columns": scd.tracking_cols},
            verdict=Verdict.accepted if auto_accept_high else Verdict.proposed,
        )
        if ledger.should_propose(d):
            ledger.record(d)
            proposals.append(d)
    elif dv.kind:
        pattern = dv.kind
        d = Decision(
            kind="data_vault_pattern",
            signal="dv_naming",
            confidence=Confidence.medium,
            subject=f"model {name!r} looks like a Data Vault {dv.kind}",
            evidence={"model": name, "kind": dv.kind},
        )
        if ledger.should_propose(d):
            ledger.record(d)
            proposals.append(d)

    scd_tracking = {c.lower() for c in scd.tracking_cols}
    bks = {c.lower() for c in lifting.business_key_candidates(name, col_names)}

    attributes: list[Attribute] = []
    for col_name, col in mm.columns.items():
        cl = col_name.lower()
        # Strip surrogate keys and SCD2 tracking columns from the logical entity.
        if lifting.is_surrogate_key(col_name):
            _propose_strip(name, col_name, "surrogate_key", ledger, proposals, auto_accept_high)
            continue
        if cl in scd_tracking:
            continue

        attr_ulid = col.meta.get("mdl_ulid") if isinstance(col.meta, dict) else None
        role = "business_key" if cl in bks else "attribute"
        attributes.append(
            Attribute(
                id=attr_ulid or new_ulid(),
                name=col_name,
                domain=_base_for(col.data_type),
                role=role,
                nullable=True,
            )
        )

    ce_ulid = new_ulid()
    ce = ConceptualEntity(
        id=ce_ulid,
        name=_titleize(name),
        definition=mm.description or None,
    )
    le = LogicalEntity(
        id=le_ulid,
        name=name,
        realises=ce_ulid,
        attributes=attributes,
        pattern=pattern,
    )
    return le, ce, proposals


def _propose_strip(
    model_name: str,
    col: str,
    kind: str,
    ledger: DecisionLedger,
    proposals: list[Decision],
    auto_accept_high: bool,
) -> None:
    d = Decision(
        kind="strip_column",
        signal=kind,
        confidence=Confidence.medium_high,
        subject=f"strip {kind} {model_name}.{col} from the logical view",
        evidence={"model": model_name, "column": col, "reason": kind},
        verdict=Verdict.accepted if auto_accept_high else Verdict.proposed,
    )
    if ledger.should_propose(d):
        ledger.record(d)
        proposals.append(d)


def _infer_relationships(
    mm: ManifestModel,
    name: str,
    le_by_name: dict[str, LogicalEntity],
    known_models: set[str],
    ledger: DecisionLedger,
    model: Model,
    auto_accept_high: bool,
) -> list[Decision]:
    proposals: list[Decision] = []
    le = le_by_name[name]

    # High-confidence: relationships tests declared in the manifest (§6.2).
    for col, to in mm.relationship_tests:
        target_le = le_by_name.get(to)
        if target_le is None:
            continue
        d = Decision(
            kind="relationship",
            signal="relationships_test",
            confidence=Confidence.high,
            subject=f"{name}.{col} -> {to} (dbt relationships test)",
            evidence={"from": name, "column": col, "to": to, "signal": "relationships_test"},
            verdict=Verdict.accepted if auto_accept_high else Verdict.proposed,
        )
        if ledger.should_propose(d):
            ledger.record(d)
            proposals.append(d)
        if d.verdict == Verdict.accepted:
            _add_relationship(model, le, target_le, col, name, to)

    # Medium-confidence: name+type heuristic (*_id matching a model). Propose only.
    declared = {(c, t) for c, t in mm.relationship_tests}
    for guess in lifting.foreign_key_candidates(name, list(mm.columns), known_models):
        if (guess.column, guess.target_entity) in declared:
            continue  # already covered by a test
        d = Decision(
            kind="relationship",
            signal="name_type",
            confidence=Confidence.medium,
            subject=f"{name}.{guess.column} -> {guess.target_entity} (name/type heuristic)",
            evidence={
                "from": name,
                "column": guess.column,
                "to": guess.target_entity,
                "signal": "name_type",
            },
        )
        if ledger.should_propose(d):
            ledger.record(d)
            proposals.append(d)
        # medium signals are never auto-accepted (§6.2) -> not added to the model

    return proposals


def apply_accepted_relationships(model: Model, ledger) -> int:
    """Add relationships for accepted-but-not-yet-materialised decisions.

    Interactive review flips a proposed relationship's verdict to `accepted` in the
    ledger AFTER the model was built, so those never made it into the model. Call
    this post-review to materialise them. Idempotent: skips any relationship whose
    endpoints are already linked. Returns the number added.
    """
    le_by_name = {le.name: le for le in model.logical_entities.values()}
    existing = {
        (r.from_.entity, r.to.entity) for r in model.relationships.values()
    }
    added = 0
    for d in ledger.decisions.values():
        if d.kind != "relationship" or d.verdict != Verdict.accepted:
            continue
        ev = d.evidence or {}
        frm = le_by_name.get(ev.get("from"))
        to = le_by_name.get(ev.get("to"))
        col = ev.get("column")
        if not frm or not to or (frm.id, to.id) in existing:
            continue
        _add_relationship(model, frm, to, col, frm.name, to.name)
        existing.add((frm.id, to.id))
        added += 1
    return added


def _add_relationship(
    model: Model,
    from_le: LogicalEntity,
    to_le: LogicalEntity,
    from_col: str,
    from_name: str,
    to_name: str,
) -> None:
    from_attr = next((a.id for a in from_le.attributes if a.name == from_col), None)
    to_bk = next((a.id for a in to_le.attributes if a.role == "business_key"), None)
    rel = Relationship(
        id=new_ulid(),
        name=f"{from_name}_has_{to_name}",
        **{"from": RelationshipEnd(entity=from_le.id, attributes=[from_attr] if from_attr else [])},
        to=RelationshipEnd(entity=to_le.id, attributes=[to_bk] if to_bk else []),
        cardinality="many_to_one",
    )
    model.add(rel)


def _titleize(name: str) -> str:
    n = name
    for pre in ("dim_", "fct_", "fact_", "stg_", "int_"):
        if n.lower().startswith(pre):
            n = n[len(pre) :]
            break
    return "".join(part.capitalize() for part in n.split("_"))
