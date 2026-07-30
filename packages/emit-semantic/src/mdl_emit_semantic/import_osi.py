"""OSI import -> IR (spec §4.3).

`mdl import osi <file>` lifts an OSI model into logical entities and relationships
so teams with an existing semantic layer are not starting over. Version detection
uses the doc's `osi_version`; parsing is delegated to the version-isolated module
so the IR never depends on a spec version.

ULID identity is recovered from custom_extensions.mdl_ulid when the OSI file was
emitted by Modelith (round-trip); otherwise fresh ULIDs are minted.
"""

from __future__ import annotations

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
from mdl_core.yaml_io import load_str
from mdl_emit_semantic.osi import DEFAULT_VERSION, get_osi


def import_osi(text: str, *, project_name: str | None = None) -> Model:
    raw = load_str(text) or {}
    version = str(raw.get("osi_version", DEFAULT_VERSION))
    parsed = get_osi(version).parse(text)

    config = ProjectConfig(name=project_name or parsed.get("name", "imported"))
    model = Model(config)

    le_by_name: dict[str, LogicalEntity] = {}
    for ent in parsed["entities"]:
        name = ent["name"]
        pk = {c.lower() for c in ent.get("primary_key", [])}
        attrs = []
        for f in ent["fields"]:
            fname = f["name"]
            attrs.append(
                Attribute(
                    id=new_ulid(),
                    name=fname,
                    domain="timestamp" if f.get("is_time") else "string",
                    role="business_key" if fname.lower() in pk else "attribute",
                    nullable=True,
                )
            )
        ce_id = new_ulid()
        ai = ent.get("ai_context") or {}
        ce = ConceptualEntity(
            id=ce_id,
            name=(ent.get("glossary_term") or _titleize(name)),
            definition=ai.get("instructions"),
            synonyms=list(ai.get("synonyms") or []),
        )
        model.add(ce)
        le = LogicalEntity(
            id=ent.get("mdl_ulid") or new_ulid(),
            name=name,
            realises=ce_id,
            attributes=attrs,
        )
        model.add(le)
        le_by_name[name] = le

    for rel in parsed["relationships"]:
        frm = le_by_name.get(rel["from"])
        to = le_by_name.get(rel["to"])
        if not frm or not to:
            continue
        from_cols = rel.get("from_columns") or []
        to_cols = rel.get("to_columns") or []
        from_attr = _attr_id(frm, from_cols[0]) if from_cols else None
        to_attr = _attr_id(to, to_cols[0]) if to_cols else None
        model.add(
            Relationship(
                id=new_ulid(),
                name=f"{rel['from']}_to_{rel['to']}",
                **{
                    "from": RelationshipEnd(
                        entity=frm.id, attributes=[from_attr] if from_attr else []
                    )
                },
                to=RelationshipEnd(entity=to.id, attributes=[to_attr] if to_attr else []),
                cardinality="many_to_one",
            )
        )
    return model


def _attr_id(le: LogicalEntity, col: str) -> str | None:
    for a in le.attributes:
        if a.name == col:
            return a.id
    return None


def _titleize(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))
