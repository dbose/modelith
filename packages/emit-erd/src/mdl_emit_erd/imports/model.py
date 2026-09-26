"""Neutral imported-model view + conversion to a Modelith command list.

The parsers all produce an ImportedModel; `to_commands()` is the single place that
maps it onto the editing engine's ops, so every import path behaves identically.

`model_to_commands()` is the richer sibling: it turns a full `mdl_core.ir.Model` (subject
areas, categories, domains, conceptual entities, keys, relationships — everything a thin
ImportedModel drops) into the same op vocabulary, so a rich importer (erwin) can ride the
exact preview/apply path the simple ones use without flattening its structure away."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mdl_core.ids import new_ulid

if TYPE_CHECKING:
    from mdl_core.ir import Model


@dataclass
class ImportedColumn:
    name: str
    base_type: str = "string"  # abstract logical type
    nullable: bool = True
    pk: bool = False
    unique: bool = False
    definition: str | None = None


@dataclass
class ImportedFk:
    columns: list[str]
    ref_table: str
    ref_columns: list[str] = field(default_factory=list)


@dataclass
class ImportedTable:
    name: str
    columns: list[ImportedColumn] = field(default_factory=list)
    foreign_keys: list[ImportedFk] = field(default_factory=list)
    definition: str | None = None


@dataclass
class ImportedModel:
    tables: list[ImportedTable] = field(default_factory=list)
    # a note the UI can surface about what the format could/couldn't carry
    warnings: list[str] = field(default_factory=list)


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def to_commands(m: ImportedModel) -> list[dict]:
    """Turn an imported model into an ordered {op, payload} change list.

    Client-style ULIDs are minted here so entity/attribute/relationship identities are
    stable from preview through to a proposal (the same reason the canvas mints them).
    Order matters: entities before their attributes, attributes before the keys and
    relationships that reference them."""
    commands: list[dict] = []
    entity_id: dict[str, str] = {}  # table name -> logical entity ULID
    attr_id: dict[tuple[str, str], str] = {}  # (table, column) -> attribute ULID

    # 1. entities
    for t in m.tables:
        eid = new_ulid()
        entity_id[t.name] = eid
        payload: dict = {"name": t.name, "id": eid, "conceptual_id": new_ulid()}
        if t.definition:
            payload["definition"] = t.definition
        commands.append({"op": "create_entity", "payload": payload})

    # 2. attributes
    for t in m.tables:
        for c in t.columns:
            aid = new_ulid()
            attr_id[(t.name, c.name)] = aid
            commands.append(
                {
                    "op": "add_attribute",
                    "payload": {
                        "entity_id": entity_id[t.name],
                        "id": aid,
                        "name": c.name,
                        "domain": c.base_type,
                        "nullable": c.nullable,
                        # a PK column maps to the business-key role so downstream
                        # (and a re-export) treats it as the identifier
                        "role": "business_key" if c.pk else "attribute",
                    },
                }
            )

    # 3. primary keys as pk KeyGroups (composite-capable)
    for t in m.tables:
        pk_cols = [c.name for c in t.columns if c.pk]
        if pk_cols:
            members = [attr_id[(t.name, c)] for c in pk_cols if (t.name, c) in attr_id]
            commands.append(
                {
                    "op": "create_key_group",
                    "payload": {
                        "entity": entity_id[t.name],
                        "id": new_ulid(),
                        "name": f"pk_{_slug(t.name)}",
                        "type": "pk",
                        "members": members,
                    },
                }
            )

    # 4. foreign keys as relationships (child = from/many, parent = to/one)
    for t in m.tables:
        for fk in t.foreign_keys:
            if fk.ref_table not in entity_id:
                m.warnings.append(
                    f"foreign key on {t.name} references unknown table {fk.ref_table!r}; skipped"
                )
                continue
            from_attr = attr_id.get((t.name, fk.columns[0])) if fk.columns else None
            to_attr = (
                attr_id.get((fk.ref_table, fk.ref_columns[0])) if fk.ref_columns else None
            )
            payload = {
                "id": new_ulid(),
                "from_entity": entity_id[t.name],
                "to_entity": entity_id[fk.ref_table],
                "cardinality": "many_to_one",
            }
            if from_attr:
                payload["from_attribute"] = from_attr
            if to_attr:
                payload["to_attribute"] = to_attr
            commands.append({"op": "create_relationship", "payload": payload})

    return commands


def model_to_commands(model: Model) -> list[dict]:
    """Turn a full IR Model into an ordered {op, payload} change list that `apply_command`
    reconstructs, preserving subject areas, domains, keys, relationships, and categories.

    Fresh ULIDs are minted for every object (the source Model's ids are not reused), and
    order is dependency-safe: subject areas + domains, then entities (carrying their
    subject-area membership), then attributes, key groups, relationships, and categories
    that reference them."""
    commands: list[dict] = []
    ent_id: dict[str, str] = {}  # source logical-entity id -> new entity id
    attr_id: dict[str, str] = {}  # source attribute id -> new attribute id
    sa_id: dict[str, str] = {}  # source subject-area id -> new subject-area id
    ce_id: dict[str, str] = {}  # source conceptual id -> new conceptual id

    # 1. subject areas (entities reference them at create time)
    for sa in model.subject_areas.values():
        nid = new_ulid()
        sa_id[sa.id] = nid
        p: dict = {"id": nid, "name": sa.name}
        if sa.definition:
            p["definition"] = sa.definition
        commands.append({"op": "create_subject_area", "payload": p})

    # which subject area each conceptual entity belongs to (SA.members holds conceptual ids)
    ce_to_sa: dict[str, str] = {}
    for sa in model.subject_areas.values():
        for ce in sa.members:
            ce_to_sa.setdefault(ce, sa_id[sa.id])

    # 2. domains
    for dom in model.domains.values():
        p = {"id": new_ulid(), "name": dom.name, "base_type": dom.base_type}
        if dom.definition:
            p["definition"] = dom.definition
        if dom.allowed_values:
            p["allowed_values"] = list(dom.allowed_values)
        commands.append({"op": "create_domain", "payload": p})

    # 3. entities (+ their subject-area home tag via the conceptual entity)
    for le in model.logical_entities.values():
        nid = new_ulid()
        ent_id[le.id] = nid
        new_ce = new_ulid()
        if le.realises:
            ce_id[le.realises] = new_ce
        p = {"id": nid, "name": le.name, "conceptual_id": new_ce, "display_name": le.name}
        if le.definition:
            p["definition"] = le.definition
        sa_for = ce_to_sa.get(le.realises) if le.realises else None
        if sa_for:
            p["subject_area"] = sa_for
        commands.append({"op": "create_entity", "payload": p})

    # 3b. subject-area membership (the inclusion list; distinct from the home tag). Emitted
    # after entities so the new conceptual ids exist. Members are conceptual ids.
    for sa in model.subject_areas.values():
        members = [ce_id[ce] for ce in sa.members if ce in ce_id]
        if members:
            commands.append(
                {
                    "op": "set_subject_area_members",
                    "payload": {"id": sa_id[sa.id], "members": members},
                }
            )

    # 4. attributes
    for le in model.logical_entities.values():
        for a in le.attributes:
            aid = new_ulid()
            attr_id[a.id] = aid
            commands.append(
                {
                    "op": "add_attribute",
                    "payload": {
                        "entity_id": ent_id[le.id],
                        "id": aid,
                        "name": a.name,
                        "domain": a.domain or "string",
                        "nullable": a.nullable,
                        "role": a.role,
                    },
                }
            )

    # 5. key groups (pk + alternate/unique)
    for kg in model.key_groups.values():
        if kg.entity not in ent_id:
            continue
        members = [attr_id[m] for m in kg.members if m in attr_id]
        if not members:
            continue
        commands.append(
            {
                "op": "create_key_group",
                "payload": {
                    "entity": ent_id[kg.entity],
                    "id": new_ulid(),
                    "name": kg.name,
                    "type": kg.type,
                    "members": members,
                },
            }
        )

    # 6. relationships
    for rel in model.relationships.values():
        if rel.from_.entity not in ent_id or rel.to.entity not in ent_id:
            continue
        p = {
            "id": new_ulid(),
            "name": rel.name,
            "from_entity": ent_id[rel.from_.entity],
            "to_entity": ent_id[rel.to.entity],
            "cardinality": rel.cardinality,
        }
        if rel.from_.attributes and rel.from_.attributes[0] in attr_id:
            p["from_attribute"] = attr_id[rel.from_.attributes[0]]
        if rel.to.attributes and rel.to.attributes[0] in attr_id:
            p["to_attribute"] = attr_id[rel.to.attributes[0]]
        commands.append({"op": "create_relationship", "payload": p})

    # 7. categories (subtype/supertype)
    for cat in model.categories.values():
        if cat.supertype not in ent_id:
            continue
        subs = [ent_id[s] for s in cat.subtypes if s in ent_id]
        if not subs:
            continue
        p = {
            "id": new_ulid(),
            "name": cat.name,
            "supertype": ent_id[cat.supertype],
            "subtypes": subs,
            "materialization": cat.materialization,
        }
        if cat.discriminator and cat.discriminator in attr_id:
            p["discriminator"] = attr_id[cat.discriminator]
        else:
            # no discriminator carried -> use table_per_subtype so it applies cleanly
            p["materialization"] = "table_per_subtype"
        commands.append({"op": "create_category", "payload": p})

    return commands
