"""A neutral ER view of a Model, shared by the SQL DDL, Mermaid, and DBML emitters.

Walking the IR once into a small, format-agnostic structure keeps the three renderers
thin and consistent: they all see the same tables, columns, keys, and foreign keys,
so a relationship anchored on one column in the DDL is anchored on the same column in
the Mermaid and DBML output. Primary/foreign-key resolution reuses the exact rules the
dbt emitter uses (a `pk` KeyGroup is authoritative, else `role: business_key`), so the
interchange formats never disagree with the generated warehouse contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mdl_core.ir import LogicalEntity, Model


@dataclass
class Column:
    name: str
    base_type: str  # abstract logical type (domain.base_type or the raw domain name)
    sql_type: str  # dialect-specific type, via the platform adapter
    nullable: bool
    pk: bool
    unique: bool  # single-column unique/alternate key
    definition: str | None


@dataclass
class ForeignKey:
    # column names on this table -> (target table, target column names), paired
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    name: str
    # inferred cardinality of THIS (child) side: a plain FK is many-to-one; a FK that
    # is also unique is one-to-one. Used by Mermaid/DBML crow's-foot notation.
    to_one: bool  # the child row references exactly one parent (always true for a FK)
    child_unique: bool  # the FK column set is unique on the child -> 1:1


@dataclass
class Table:
    name: str
    definition: str | None
    columns: list[Column] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    pk_columns: list[str] = field(default_factory=list)  # ordered


def _pk_attr_ids(model: Model, le: LogicalEntity) -> list[str]:
    """Ordered PK attribute ids: a `pk` KeyGroup wins (member order preserved), else
    the `role: business_key` attributes. Mirrors the dbt emitter."""
    for kg in model.key_groups.values():
        if kg.entity == le.id and kg.type == "pk":
            return list(kg.members)
    return [a.id for a in le.attributes if a.role == "business_key"]


def _single_unique_attr_ids(model: Model, le: LogicalEntity) -> set[str]:
    out: set[str] = set()
    for kg in model.key_groups.values():
        if kg.entity == le.id and kg.type in ("alternate", "unique") and len(kg.members) == 1:
            out.add(kg.members[0])
    return out


def build_tables(model: Model, adapter) -> list[Table]:
    """Project the model's logical entities into ER tables with columns, PKs, and FKs.

    `adapter` is a dbt PlatformAdapter (from mdl_emit_dbt.platforms.get_adapter) used
    only for domain -> SQL type mapping, so DDL types match what dbt would emit."""
    ent_by_id = model.logical_entities
    name_by_id = {le.id: le.name for le in ent_by_id.values()}
    tables: dict[str, Table] = {}

    for le in sorted(ent_by_id.values(), key=lambda e: e.name):
        pk_ids = _pk_attr_ids(model, le)
        pk_set = set(pk_ids)
        uniq_ids = _single_unique_attr_ids(model, le)
        attr_name = {a.id: a.name for a in le.attributes}
        cols: list[Column] = []
        for a in le.attributes:
            dom = model.domain_by_name(a.domain)
            sql_type = adapter.map_domain(dom).sql_type if dom else adapter.map_base_type(a.domain)
            cols.append(
                Column(
                    name=a.name,
                    base_type=(dom.base_type if dom else (a.domain or "string")),
                    sql_type=sql_type,
                    # a PK column is never null regardless of the attribute flag
                    nullable=a.nullable and a.id not in pk_set,
                    pk=a.id in pk_set,
                    unique=a.id in uniq_ids,
                    definition=a.definition,
                )
            )
        tables[le.id] = Table(
            name=le.name,
            definition=le.definition,
            columns=cols,
            pk_columns=[attr_name[i] for i in pk_ids if i in attr_name],
        )

    # foreign keys from relationships (from = child/many side, to = parent/one side)
    for rel in sorted(model.relationships.values(), key=lambda r: r.name):
        child = tables.get(rel.from_.entity)
        parent_name = name_by_id.get(rel.to.entity)
        if child is None or parent_name is None:
            continue
        child_ent = ent_by_id[rel.from_.entity]
        parent_ent = ent_by_id.get(rel.to.entity)
        c_names = _resolve_names(child_ent, rel.from_.attributes)
        p_names = _resolve_names(parent_ent, rel.to.attributes) if parent_ent else []
        if not c_names:
            continue  # nothing concrete to anchor a FK to
        child_unique = rel.cardinality in ("one_to_one",) or _cols_unique(model, child_ent, c_names)
        child.foreign_keys.append(
            ForeignKey(
                columns=c_names,
                ref_table=parent_name,
                ref_columns=p_names,
                name=rel.name,
                to_one=True,
                child_unique=child_unique,
            )
        )
    return list(tables.values())


def _resolve_names(entity, attr_ids: list[str]) -> list[str]:
    if entity is None:
        return []
    by_id = {a.id: a.name for a in entity.attributes}
    return [by_id[i] for i in attr_ids if i in by_id]


def _cols_unique(model: Model, entity, col_names: list[str]) -> bool:
    """Is this exact column set a PK or unique key on the entity? Then the FK is 1:1."""
    name_to_id = {a.name: a.id for a in entity.attributes}
    ids = {name_to_id[n] for n in col_names if n in name_to_id}
    if not ids:
        return False
    for kg in model.key_groups.values():
        if kg.entity == entity.id and kg.type in ("pk", "alternate", "unique"):
            if set(kg.members) == ids:
                return True
    return False
