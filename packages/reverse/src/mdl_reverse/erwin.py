"""erwin XML import (spec §6.4).

Nobody migrates greenfield, so this is the go-to-market path. We map erwin's
subject areas, entities, attributes, domains, relationships and naming standards
into the IR.

erwin has several export shapes (the native erwin XML and an XMLSchema variant).
We parse defensively: tag/attribute names are matched case-insensitively and we
accept the common element layout below. Unrecognised structure is skipped rather
than fatal, so a partial import still yields a usable model the modeller refines.

Expected-ish shape (simplified erwin XML):

    <Model name="...">
      <SubjectArea name="Trading"/>
      <Entity name="Counterparty" subject_area="Trading">
        <Attribute name="counterparty_id" datatype="BIGINT" key="true" null="false"/>
        <Attribute name="legal_name" datatype="VARCHAR"/>
      </Entity>
      <Relationship name="trade_has_cpty" parent="Counterparty" child="Trade"
                    child_attr="counterparty_id" cardinality="many_to_one"/>
    </Model>
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
    SubjectArea,
)

# erwin datatype -> abstract base type.
_ERWIN_TYPE = {
    "bigint": "bigint",
    "integer": "integer",
    "int": "integer",
    "smallint": "integer",
    "numeric": "decimal",
    "decimal": "decimal",
    "number": "decimal",
    "varchar": "string",
    "char": "string",
    "text": "string",
    "nvarchar": "string",
    "boolean": "boolean",
    "bit": "boolean",
    "date": "date",
    "datetime": "timestamp",
    "timestamp": "timestamp",
}


def _base_type(erwin_type: str | None) -> str:
    if not erwin_type:
        return "string"
    t = erwin_type.split("(")[0].strip().lower()
    return _ERWIN_TYPE.get(t, "string")


def _lc(tag: str) -> str:
    return tag.split("}")[-1].lower()  # strip namespace, lowercase


def _attr(el: ET.Element, *names: str) -> str | None:
    """Case-insensitive attribute lookup across candidate names."""
    lower = {k.lower(): v for k, v in el.attrib.items()}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def _truthy(v: str | None) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes", "y"}


def _nullable(el: ET.Element, is_key: bool) -> bool:
    """Resolve nullability from erwin's varied spellings.
    - `notnull="true"` => not nullable
    - `null="false"`   => not nullable
    - keys default to not-null; everything else defaults to nullable."""
    notnull = _attr(el, "notnull", "not_null")
    if notnull is not None:
        return not _truthy(notnull)
    null = _attr(el, "null", "nullable")
    if null is not None:
        return _truthy(null)
    return not is_key


def import_erwin(text: str, *, project_name: str | None = None) -> Model:
    root = ET.fromstring(text)
    model_name = project_name or _attr(root, "name") or "erwin_import"
    model = Model(ProjectConfig(name=model_name))

    subject_areas: dict[str, str] = {}  # name -> ULID
    entities_by_name: dict[str, LogicalEntity] = {}
    ce_by_name: dict[str, ConceptualEntity] = {}

    # Walk all descendants so we tolerate wrapper elements.
    for el in root.iter():
        tag = _lc(el.tag)
        if tag in ("subjectarea", "subject_area"):
            name = _attr(el, "name")
            if name and name not in subject_areas:
                sa_id = new_ulid()
                subject_areas[name] = sa_id
                model.add(SubjectArea(id=sa_id, name=name))

    for el in root.iter():
        if _lc(el.tag) not in ("entity", "table"):
            continue
        name = _attr(el, "name")
        if not name:
            continue
        sa_name = _attr(el, "subject_area", "subjectarea")
        ce_id = new_ulid()
        ce = ConceptualEntity(
            id=ce_id,
            name=_titleize(name),
            subject_area=subject_areas.get(sa_name) if sa_name else None,
            definition=_attr(el, "definition", "comment"),
        )
        ce_by_name[name] = ce
        model.add(ce)

        attrs = []
        for child in el.iter():
            if _lc(child.tag) not in ("attribute", "column"):
                continue
            aname = _attr(child, "name")
            if not aname:
                continue
            is_key = _truthy(_attr(child, "key", "primary_key", "pk"))
            nullable = _nullable(child, is_key)
            attrs.append(
                Attribute(
                    id=new_ulid(),
                    name=aname,
                    domain=_base_type(_attr(child, "datatype", "type", "domain")),
                    role="business_key" if is_key else "attribute",
                    nullable=nullable,
                )
            )
        le = LogicalEntity(id=new_ulid(), name=name.lower(), realises=ce_id, attributes=attrs)
        entities_by_name[name] = le
        model.add(le)

    for el in root.iter():
        if _lc(el.tag) not in ("relationship", "fk", "foreignkey"):
            continue
        parent = _attr(el, "parent", "parent_entity", "to")  # one side
        child = _attr(el, "child", "child_entity", "from")  # many side
        p_le = entities_by_name.get(parent) if parent else None
        c_le = entities_by_name.get(child) if child else None
        if not p_le or not c_le:
            continue
        child_attr = _attr(el, "child_attr", "fk_column", "column")
        from_attr = _attr_id(c_le, child_attr) if child_attr else None
        to_bk = next((a.id for a in p_le.attributes if a.role == "business_key"), None)
        rel_name = _attr(el, "name") or f"{c_le.name}_has_{p_le.name}"
        model.add(
            Relationship(
                id=new_ulid(),
                name=rel_name,
                **{
                    "from": RelationshipEnd(
                        entity=c_le.id, attributes=[from_attr] if from_attr else []
                    )
                },
                to=RelationshipEnd(entity=p_le.id, attributes=[to_bk] if to_bk else []),
                cardinality=_cardinality(_attr(el, "cardinality")),
            )
        )
    return model


def import_erwin_file(path: str | Path, **kw) -> Model:
    return import_erwin(Path(path).read_text(encoding="utf-8"), **kw)


def _attr_id(le: LogicalEntity, col: str) -> str | None:
    for a in le.attributes:
        if a.name == col:
            return a.id
    return None


def _cardinality(v: str | None) -> str:
    m = (v or "").strip().lower()
    if m in {"one_to_one", "1:1"}:
        return "one_to_one"
    if m in {"one_to_many", "1:m", "1:n"}:
        return "one_to_many"
    if m in {"many_to_many", "m:n", "m:m"}:
        return "many_to_many"
    return "many_to_one"


def _titleize(name: str) -> str:
    return "".join(p.capitalize() for p in name.replace(" ", "_").split("_"))
