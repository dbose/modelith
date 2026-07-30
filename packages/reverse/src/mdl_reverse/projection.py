"""Project the committed model into the expected dbt shape (spec §5.4).

Drift compares the committed model against the compiled dbt project. To diff
apples-to-apples we project the model into the same {model_name -> columns} shape
the manifest exposes. This projection intentionally mirrors the emitter's naming
(model name = physical table name lowercased, or logical entity name) and type
mapping, without depending on emit-dbt (layering §1.3: reverse depends only on
core). The small type map is duplicated here deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mdl_core.ir import LogicalEntity, Model


@dataclass
class ExpectedColumn:
    name: str
    data_type: str | None
    nullable: bool = True
    is_business_key: bool = False
    description: str | None = None


@dataclass
class ExpectedModel:
    name: str
    columns: dict[str, ExpectedColumn] = field(default_factory=dict)
    contract_enforced: bool = True
    description: str | None = None
    relationships: list[tuple[str, str]] = field(default_factory=list)  # (column, to)


# Abstract base type -> canonical SQL type (uppercased to match manifest._norm_type).
_TYPE_MAP = {
    "bigint": "BIGINT",
    "int": "INTEGER",
    "integer": "INTEGER",
    "string": "VARCHAR",
    "text": "VARCHAR",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "decimal": "DECIMAL(38,0)",
    "identifier_bigint": "BIGINT",
    "lei_code": "VARCHAR(20)",
}


def _map_type(base: str | None) -> str:
    return _TYPE_MAP.get((base or "string").lower(), "VARCHAR")


def project_model(model: Model, target: str) -> dict[str, ExpectedModel]:
    """Return {model_name -> ExpectedModel} for the given physical target."""
    pt_by_logical = {
        pt.realises: pt for pt in model.physical_tables.values() if pt.target == target
    }
    # Relationship tests the model declares (many-side column -> one-side ref).
    rels_by_entity = _relationships_by_entity(model)

    out: dict[str, ExpectedModel] = {}
    for le in model.logical_entities.values():
        pt = pt_by_logical.get(le.id)
        name = (pt.name.lower() if pt else le.name)
        cols: dict[str, ExpectedColumn] = {}
        for attr in le.attributes:
            dom = model.domain_by_name(attr.domain)
            base = dom.base_type if dom else attr.domain
            cols[attr.name] = ExpectedColumn(
                name=attr.name,
                data_type=_map_type(base),
                nullable=attr.nullable,
                is_business_key=(attr.role == "business_key"),
            )
        ce = model.conceptual_entities.get(le.realises) if le.realises else None
        out[name] = ExpectedModel(
            name=name,
            columns=cols,
            contract_enforced=True,
            description=(ce.definition if ce else None),
            relationships=rels_by_entity.get(le.id, []),
        )
    return out


def _relationships_by_entity(model: Model) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    attr_name = {}
    for le in model.logical_entities.values():
        for a in le.attributes:
            attr_name[a.id] = a.name
    le_name = {le.id: le.name for le in model.logical_entities.values()}
    for rel in model.relationships.values():
        many_entity = rel.from_.entity
        col_ids = rel.from_.attributes
        to_entity = rel.to.entity
        col = attr_name.get(col_ids[0]) if col_ids else None
        to_ref = le_name.get(to_entity)
        if col and to_ref:
            out.setdefault(many_entity, []).append((col, to_ref))
    return out


def business_key_of(le: LogicalEntity) -> str | None:
    for a in le.attributes:
        if a.role == "business_key":
            return a.name
    return None
