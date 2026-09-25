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
from mdl_reverse.ledger import (
    DEFAULT_AUTO_ACCEPT,
    Confidence,
    Decision,
    DecisionLedger,
    Verdict,
    verdict_for,
)
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


# Base type by family, matched after stripping precision/scale. A warehouse emits
# DECIMAL(18,2), NUMBER(10,4), VARCHAR(255) … in countless (precision, scale) variants;
# the exact-match table above can't enumerate them, so an unlisted DECIMAL used to fall
# through to "string" — which then reported phantom breaking type drift the moment you
# drifted a freshly-reversed model against its own warehouse (the north-star trust bug).
_FAMILY_TO_BASE = {
    "DECIMAL": "decimal", "NUMERIC": "decimal", "NUMBER": "decimal", "DEC": "decimal",
    "FLOAT": "decimal", "DOUBLE": "decimal", "REAL": "decimal",
    "BIGINT": "bigint", "INT8": "bigint",
    "INTEGER": "integer", "INT": "integer", "INT4": "integer", "SMALLINT": "integer",
    "VARCHAR": "string", "CHAR": "string", "TEXT": "string", "STRING": "string",
    "NVARCHAR": "string", "CHARACTER": "string",
    "BOOLEAN": "boolean", "BOOL": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp", "DATETIME": "timestamp", "TIMESTAMPTZ": "timestamp",
    "TIMESTAMP_NTZ": "timestamp", "TIMESTAMP_TZ": "timestamp", "TIMESTAMP_LTZ": "timestamp",
}


def _base_for(sql_type: str | None) -> str:
    if not sql_type:
        return "string"
    t = sql_type.upper().strip()
    # Exact match first — keeps domain-specific overrides like VARCHAR(20) -> lei_code.
    if t in _SQL_TO_BASE:
        return _SQL_TO_BASE[t]
    # Otherwise strip the precision/scale (or length) and match the type FAMILY, so any
    # DECIMAL(p,s) / NUMERIC(p,s) / VARCHAR(n) / TIMESTAMP(n) resolves correctly.
    family = t.split("(", 1)[0].strip()
    return _FAMILY_TO_BASE.get(family, "string")


@dataclass
class ReverseResult:
    model: Model
    proposals: list[Decision] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)  # staging/intermediate model names
    # models dropped because they belong to an installed dbt package (dbt_artifacts,
    # elementary, …), not the root project — tool metadata, not business models.
    # each entry is (model_name, package_name).
    excluded_foreign: list[tuple[str, str]] = field(default_factory=list)

    def logical_count(self) -> int:
        return len(self.model.logical_entities)


@dataclass
class ClassificationSummary:
    """What reverse decided, grouped by the rule that fired — so a misclassification
    on a non-standard name (`gold_` treated as an entity, a real dim demoted to a
    rollup) is visible at reverse time, not discovered later at PR review."""

    excluded_staging: list[str] = field(default_factory=list)
    rollups_unmanaged: list[str] = field(default_factory=list)
    scd2_detected: list[str] = field(default_factory=list)
    data_vault: list[str] = field(default_factory=list)
    surrogate_keys_stripped: list[str] = field(default_factory=list)  # "entity.col"
    entities_kept: list[str] = field(default_factory=list)  # governed business entities
    entities_keyless: list[str] = field(default_factory=list)  # managed but no BK
    # (model, package) pairs excluded as installed-package tool metadata
    excluded_foreign: list[tuple[str, str]] = field(default_factory=list)

    def line_count(self) -> int:
        return sum(
            len(v) for v in (
                self.excluded_staging, self.rollups_unmanaged, self.scd2_detected,
                self.data_vault, self.surrogate_keys_stripped, self.excluded_foreign,
            )
        )


def classification_summary(result: ReverseResult) -> ClassificationSummary:
    """Derive the per-rule classification summary from a ReverseResult."""
    s = ClassificationSummary(
        excluded_staging=sorted(result.excluded),
        excluded_foreign=sorted(result.excluded_foreign),
    )
    for d in result.proposals:
        ev = d.evidence or {}
        if d.kind == "reporting_rollup":
            s.rollups_unmanaged.append(ev.get("model", d.subject))
        elif d.kind == "scd2_pattern":
            s.scd2_detected.append(ev.get("model", d.subject))
        elif d.kind == "data_vault_pattern":
            s.data_vault.append(ev.get("model", d.subject))
        elif d.kind == "strip_column" and ev.get("reason") == "surrogate_key":
            s.surrogate_keys_stripped.append(
                f"{ev.get('model', '?')}.{ev.get('column', '?')}"
            )
    for le in result.model.logical_entities.values():
        if le.unmanaged:
            continue
        has_bk = any(a.role == "business_key" for a in le.attributes)
        (s.entities_kept if has_bk else s.entities_keyless).append(le.name)
    for lst in (s.rollups_unmanaged, s.scd2_detected, s.data_vault,
                s.surrogate_keys_stripped, s.entities_kept, s.entities_keyless):
        lst.sort()
    return s


def reverse(
    manifest: ManifestProjection,
    *,
    project_name: str = "reversed_model",
    target: str = "duckdb_dev",
    ledger: DecisionLedger | None = None,
    interactive: bool = False,
    auto_accept: Confidence | None = DEFAULT_AUTO_ACCEPT,
    auto_accept_high: bool | None = None,
    naming: lifting.ReverseNaming | None = None,
    reverse_config=None,
    include_packages: set[str] | None = None,
) -> ReverseResult:
    """Lift a manifest into IR. `auto_accept` is the confidence FLOOR: any inference at
    or above it is accepted automatically; everything below is left `proposed` for
    manual review in the ledger. `None` means a manual-always policy — nothing is
    auto-accepted regardless of score. The default floor (medium-high) accepts
    high-confidence signals (FK constraints, relationships tests) per §6.2.

    `auto_accept_high` is the deprecated boolean predecessor: True -> the default floor,
    False -> None (manual-always). If given, it overrides `auto_accept`.

    `interactive=False` (the default, CI-safe) records proposals but does not prompt.

    `naming` overrides the built-in reverse conventions (rollup/staging/surrogate/scd2
    prefixes) so divergent-named projects (medallion `gold_`, `f_`/`d_`, non-English)
    are handled; None uses the defaults."""
    ledger = ledger or DecisionLedger()
    naming = naming or lifting.DEFAULT_NAMING
    # The deprecated boolean, if explicitly passed, wins for back-compat.
    if auto_accept_high is not None:
        auto_accept = DEFAULT_AUTO_ACCEPT if auto_accept_high else None
    floor = auto_accept
    config = ProjectConfig(
        name=project_name,
        dbt_target=target,
        platform_targets=[target],
    )
    # Carry the reverse config we were classified with back onto the model, so the
    # written mdl-project.yaml round-trips the `reverse:` block (exclude/layers/…) that
    # drove this run instead of an empty one. The writer preserves a user's existing file
    # on re-reverse, and this ensures a FIRST write into a fresh dir also keeps the block.
    if reverse_config is not None:
        config.reverse = reverse_config
    model = Model(config)
    proposals: list[Decision] = []
    excluded: list[str] = []

    # 1) Classify every model into a role via the single ordered resolver (exempt >
    # exclude > configured layers > legacy is_staging). With no classification config this
    # is byte-for-byte the historical is_staging exclusion.
    from mdl_reverse.mapping import EXCLUDED_ROLES, resolve_layer

    # Foreign-package models (dbt_artifacts, elementary, …) are tool metadata, not
    # business models — drop them by default. A model is foreign when its package is set,
    # differs from the root project, and isn't explicitly kept via include_packages.
    root_project = getattr(manifest, "root_project", None)
    keep_pkgs = {p.lower() for p in (include_packages or set())}
    excluded_foreign: list[tuple[str, str]] = []

    business: dict[str, ManifestModel] = {}
    verdicts: dict[str, object] = {}
    for name, mm in manifest.models.items():
        pkg = getattr(mm, "package_name", None)
        if (
            pkg
            and root_project
            and pkg != root_project
            and pkg.lower() not in keep_pkgs
        ):
            excluded_foreign.append((name, pkg))
            continue
        v = resolve_layer(name, mm.tags, getattr(mm, "path", None), reverse_config, naming)
        if v.role in EXCLUDED_ROLES:
            excluded.append(name)
            continue
        business[name] = mm
        verdicts[name] = v

    known_models = set(business)

    # 2) Build logical entities + attributes with pattern detection.
    le_by_name: dict[str, LogicalEntity] = {}
    for name in sorted(business):
        mm = business[name]
        le, ce, entity_proposals = _lift_entity(
            mm, name, ledger, floor, naming, verdict=verdicts.get(name)
        )
        model.add(ce)
        model.add(le)
        le_by_name[name] = le
        proposals.extend(entity_proposals)

    # 3) Relationship inference (ranked signals, §6.2).
    for name in sorted(business):
        mm = business[name]
        rel_proposals = _infer_relationships(
            mm, name, le_by_name, known_models, ledger, model, floor, naming
        )
        proposals.extend(rel_proposals)

    return ReverseResult(
        model=model,
        proposals=proposals,
        excluded=excluded,
        excluded_foreign=excluded_foreign,
    )


def _lift_entity(
    mm: ManifestModel,
    name: str,
    ledger: DecisionLedger,
    floor: Confidence | None,
    naming: lifting.ReverseNaming = lifting.DEFAULT_NAMING,
    verdict=None,
) -> tuple[LogicalEntity, ConceptualEntity, list[Decision]]:
    proposals: list[Decision] = []
    col_names = list(mm.columns)

    # Recover ULIDs if this manifest came from our own emitter (round-trip fidelity).
    le_ulid = mm.meta.get("mdl_ulid") if isinstance(mm.meta, dict) else None
    le_ulid = le_ulid or new_ulid()

    # A configured layer role that names a Data Vault kind (hub/link/satellite/bridge)
    # seeds the pattern authoritatively — applied AFTER detection below so SCD2/DV column
    # stripping still runs, but the declared role wins for the final pattern.
    role_pattern = getattr(verdict, "pattern", None) if verdict is not None else None

    # SCD2 detection -> pattern + strip tracking columns from the logical view.
    scd = lifting.detect_scd2(col_names, naming)
    dv = lifting.detect_data_vault(name, col_names, naming)
    pattern = None
    if scd.is_scd2:
        pattern = "scd2"
        d = Decision(
            kind="scd2_pattern",
            signal="scd2_columns",
            confidence=Confidence.medium_high,
            subject=f"model {name!r} looks like SCD2 ({', '.join(scd.tracking_cols)})",
            evidence={"model": name, "columns": scd.tracking_cols},
            verdict=verdict_for(Confidence.medium_high, floor),
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
            verdict=verdict_for(Confidence.medium, floor),
        )
        if ledger.should_propose(d):
            ledger.record(d)
            proposals.append(d)

    scd_tracking = {c.lower() for c in scd.tracking_cols}
    col_types = {cn: c.data_type for cn, c in mm.columns.items() if c.data_type}
    # An explicitly declared primary key (DDL, or a future information_schema read) is
    # authoritative and wins over the name heuristic — this is what lets a composite
    # table-level PRIMARY KEY (...) become the business key. Surrogate-key PK columns are
    # still stripped below, so a `_sk` PK doesn't survive as a business key. The dbt
    # manifest carries no PK, so `declared_pk` is empty there and we fall back as before.
    declared_pk = {
        cn.lower()
        for cn, c in mm.columns.items()
        if isinstance(c.meta, dict) and c.meta.get("pk")
    }
    if declared_pk:
        bks = declared_pk
    else:
        bks = {
            c.lower()
            for c in lifting.business_key_candidates(name, col_names, col_types, naming)
        }

    attributes: list[Attribute] = []
    for col_name, col in mm.columns.items():
        cl = col_name.lower()
        # Strip surrogate keys and SCD2 tracking columns from the logical entity. A
        # `_key` that is the dimension's natural key (e.g. date_key on dim_date) is
        # kept — is_surrogate_key uses the entity name + type to decide.
        if lifting.is_surrogate_key(
            col_name, entity=name, data_type=col.data_type, naming=naming
        ):
            _propose_strip(name, col_name, "surrogate_key", ledger, proposals, floor)
            continue
        if cl in scd_tracking:
            continue

        attr_ulid = col.meta.get("mdl_ulid") if isinstance(col.meta, dict) else None
        role = "business_key" if cl in bks else "attribute"
        # Nullability: honour the source when it actually knows (DDL's NOT NULL/PK carry
        # `nullable` in meta; a future information_schema read will too). The dbt-manifest
        # path cannot recover nullability and leaves it absent, so it keeps the historical
        # default of True — unchanged, so the round-trip's documented-lossy set holds.
        nullable = col.meta.get("nullable") if isinstance(col.meta, dict) else None
        attributes.append(
            Attribute(
                id=attr_ulid or new_ulid(),
                name=col_name,
                domain=_base_for(col.data_type),
                role=role,
                nullable=True if nullable is None else bool(nullable),
            )
        )

    # A reporting rollup (mart_/rpt_/agg_/kpi_ with no business key) is kept for
    # lineage but marked unmanaged, so it doesn't pollute the governed model as a
    # keyless "business entity". The engineer can promote it if it IS one.
    is_rollup = lifting.is_reporting_rollup(name, col_names, col_types, mm.tags, naming)
    if is_rollup:
        d = Decision(
            kind="reporting_rollup",
            signal="rollup_naming",
            confidence=Confidence.medium_high,
            subject=f"model {name!r} looks like a reporting rollup; kept unmanaged",
            evidence={"model": name, "reason": "rollup name + no business key"},
            verdict=verdict_for(Confidence.medium_high, floor),
        )
        if ledger.should_propose(d):
            ledger.record(d)
            proposals.append(d)

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
        # A declared layer role (hub/link/satellite/bridge) wins over the detected pattern.
        pattern=role_pattern or pattern,
        unmanaged=True if is_rollup else None,
    )
    return le, ce, proposals


def _propose_strip(
    model_name: str,
    col: str,
    kind: str,
    ledger: DecisionLedger,
    proposals: list[Decision],
    floor: Confidence | None,
) -> None:
    d = Decision(
        kind="strip_column",
        signal=kind,
        confidence=Confidence.medium_high,
        subject=f"strip {kind} {model_name}.{col} from the logical view",
        evidence={"model": model_name, "column": col, "reason": kind},
        verdict=verdict_for(Confidence.medium_high, floor),
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
    floor: Confidence | None,
    naming: lifting.ReverseNaming = lifting.DEFAULT_NAMING,
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
            verdict=verdict_for(Confidence.high, floor),
        )
        if ledger.should_propose(d):
            ledger.record(d)
            proposals.append(d)
        if d.verdict == Verdict.accepted:
            _add_relationship(model, le, target_le, col, name, to)

    # Medium-confidence: name+type heuristic (*_id matching a model). Proposed unless
    # the auto-accept floor reaches medium (then accepted + materialised, like a test).
    declared = {(c, t) for c, t in mm.relationship_tests}
    for guess in lifting.foreign_key_candidates(name, list(mm.columns), known_models, naming):
        if (guess.column, guess.target_entity) in declared:
            continue  # already covered by a test
        target_le = le_by_name.get(guess.target_entity)
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
            verdict=verdict_for(Confidence.medium, floor),
        )
        if ledger.should_propose(d):
            ledger.record(d)
            proposals.append(d)
        if d.verdict == Verdict.accepted and target_le is not None:
            _add_relationship(model, le, target_le, guess.column, name, guess.target_entity)

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
