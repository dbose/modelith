"""Neutral imported-model view + conversion to a Modelith command list.

The parsers all produce an ImportedModel; `to_commands()` is the single place that
maps it onto the editing engine's ops, so every import path behaves identically."""

from __future__ import annotations

from dataclasses import dataclass, field

from mdl_core.ids import new_ulid


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
