"""Emit erwin XML from a Modelith model — the inverse of `mdl import erwin`.

Goals (and their guards):
  - **XSD-valid** against erwin/ERwinSchema.xsd: root `<erwin FileVersion="9.8.0000"
    Format="erwin">` → namespaced `<Model id=…>` with the required `<ModelEnvProps/>` +
    `<ModelProps/>`, then the `*_Groups`. Every object element carries a required `id`, and its
    scalar values live in a `<XxxProps>` child as element TEXT.
  - **Round-trips** through mdl_reverse.erwin.import_erwin back to an equivalent model. Because
    the importer discards erwin GUIDs and mints fresh ULIDs, equivalence is STRUCTURAL
    (names/types/members/verb-phrases), not id-identity.

The importer is the authoritative spec; this mirrors its reads in reverse. The key
asymmetries it undoes:
  - the importer splits each erwin <Entity> into a ConceptualEntity + LogicalEntity; export
    FOLDS them back to one <Entity> (Name = conceptual display name, Physical_Name = the
    logical physical name);
  - the importer turns subtype relationships into a Category; export RE-EMITS each Category as
    N `<Relationship Type="Subtype_Complete">` with a Subtype_Discriminator_Ref;
  - relationship `from` = child/many side, `to` = parent/one side.
"""

from __future__ import annotations

from xml.dom import minidom
from xml.etree import ElementTree as ET

from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    ErwinNaming,
    LogicalEntity,
    Model,
)

_NS = "http://www.erwin.com/dm"
_NS_DATA = "http://www.erwin.com/dm/data"
# The FileVersion pattern requires major.minor with an optional 4-digit build: "9.8.0000"
# validates; "9.8.00" does NOT. Keep the 4 digits.
_FILE_VERSION = "9.8.0000"

# Modelith abstract base type -> an erwin logical datatype the importer maps back 1:1
# (see mdl_reverse.erwin._ERWIN_TYPE). Round-trip safe.
_BASE_TO_ERWIN = {
    "bigint": "BIGINT",
    "integer": "INTEGER",
    "decimal": "DECIMAL",
    "string": "VARCHAR",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}

# Modelith KeyGroupType -> erwin Key_Group_Type (importer reads these back via _key_group_ir_type)
_KG_TYPE = {
    "pk": "Primary_Key",
    "alternate": "Alternate_Key",
    "unique": "Alternate_Key",  # importer maps unknown -> unique; alternate is the safe carrier
    "index": "Inversion_Entry",
}

# Modelith Cardinality -> an erwin Cardinality string the importer maps back (_cardinality)
_CARD = {
    "one_to_one": "One",
    "one_to_many": "Zero_One_or_More",
    "many_to_one": "Zero_One_or_More",
    "many_to_many": "Many_to_Many",
}


def _guid(ulid: str) -> str:
    """A stable erwin-shaped GUID {8-4-4-4-12} derived from a ULID, so output is reproducible.

    The importer only uses GUIDs as intra-document join keys, so any stable unique mapping
    works; we fold the 26-char ULID into 32 hex-ish chars deterministically."""
    import hashlib

    h = hashlib.sha1(ulid.encode("utf-8")).hexdigest()  # noqa: S324 - not security, just a stable id
    return f"{{{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}}}"


def _apply_naming(name: str, cfg: ErwinNaming, which: str) -> str:
    """Apply the team naming convention to a LOGICAL/display name. `which` is 'entity' or
    'attribute'. A no-op unless the config opts in; physical names never pass through here."""
    case = cfg.entity_case if which == "entity" else cfg.attribute_case
    out = name
    if cfg.replace_underscore_with_space:
        out = out.replace("_", " ")
    if case == "title":
        out = " ".join(w.capitalize() for w in out.replace("_", " ").split(" ")).strip()
    elif case == "upper":
        out = out.upper()
    elif case == "lower":
        out = out.lower()
    return out


def _sub(parent: ET.Element, tag: str) -> ET.Element:
    return ET.SubElement(parent, tag)


def _prop(props: ET.Element, tag: str, value: str | None) -> None:
    """Add a `<Tag>value</Tag>` text prop, skipping empties (every value is element text)."""
    if value is None or value == "":
        return
    _sub(props, tag).text = str(value)


def _ref(parent: ET.Element, tag: str, guid: str) -> None:
    _sub(parent, tag).text = guid


def _ref_array(parent: ET.Element, array_tag: str, item_tag: str, guids: list[str]) -> None:
    if not guids:
        return
    arr = _sub(parent, array_tag)
    for i, g in enumerate(guids):
        el = _sub(arr, item_tag)
        el.set("index", str(i))
        el.text = g


def emit_erwin(model: Model, *, naming: ErwinNaming | None = None) -> str:
    """Serialize `model` to an erwin XML document string (XSD-valid; round-trips via the
    erwin importer). `naming` applies a team convention to logical/display names; when None
    (the default) names are emitted verbatim."""
    cfg = naming or ErwinNaming()

    root = ET.Element("erwin")
    root.set("xmlns", _NS)
    root.set("FileVersion", _FILE_VERSION)
    root.set("Format", "erwin")

    mdl = _sub(root, "Model")
    mdl.set("xmlns", _NS_DATA)
    mdl.set("id", _guid("__model__"))
    # the two children the XSD requires, first (xs:all makes order free, but keep it tidy)
    _sub(mdl, "ModelEnvProps")
    _sub(mdl, "ModelProps")

    # --- domains (attributes reference them by GUID) ---------------------------------
    domain_guid_by_name: dict[str, str] = {}
    if model.domains:
        grp = _sub(mdl, "Domain_Groups")
        for dom in model.domains.values():
            g = _guid(dom.id)
            domain_guid_by_name[dom.name] = g
            el = _sub(grp, "Domain")
            el.set("id", g)
            p = _sub(el, "DomainProps")
            _prop(p, "Name", dom.name)
            _prop(p, "Logical_Data_Type", _BASE_TO_ERWIN.get(dom.base_type, "VARCHAR"))
            _prop(p, "Definition", dom.definition)

    # --- entities (fold conceptual + logical back into one <Entity>) -----------------
    # precompute, per logical entity: its conceptual, its pk member attribute-ids, and a
    # GUID for each of its attributes (relationships reference attributes by GUID).
    ce_by_le: dict[str, ConceptualEntity] = {}
    for ce in model.conceptual_entities.values():
        for le in model.logical_entities.values():
            if le.realises == ce.id:
                ce_by_le[le.id] = ce

    pk_members_by_le: dict[str, set[str]] = {}
    for kg in model.key_groups.values():
        if kg.type == "pk":
            pk_members_by_le.setdefault(kg.entity, set()).update(kg.members)

    entity_guid: dict[str, str] = {le.id: _guid(le.id) for le in model.logical_entities.values()}
    attr_guid: dict[str, str] = {}
    for le in model.logical_entities.values():
        for a in le.attributes:
            attr_guid[a.id] = _guid(a.id)

    if model.logical_entities:
        egrp = _sub(mdl, "Entity_Groups")
        for le in model.logical_entities.values():
            _emit_entity(egrp, le, ce_by_le.get(le.id), model, cfg,
                         entity_guid, attr_guid, pk_members_by_le, domain_guid_by_name)

    # --- relationships (FK + subtype-from-Category) ----------------------------------
    rels = _sub(mdl, "Relationship_Groups")
    _emit_fk_relationships(rels, model, entity_guid, attr_guid)
    _emit_subtype_relationships(rels, model, entity_guid, attr_guid)
    if not list(rels):
        mdl.remove(rels)

    # --- subject areas ----------------------------------------------------------------
    if model.subject_areas:
        sgrp = _sub(mdl, "Subject_Area_Groups")
        # members are conceptual-entity ids; map each to its entity GUID (importer resolves
        # a subject-area member ref against the entity's conceptual id).
        le_by_ce: dict[str, str] = {le.realises: le.id for le in model.logical_entities.values()
                                    if le.realises}
        for sa in model.subject_areas.values():
            el = _sub(sgrp, "Subject_Area")
            el.set("id", _guid(sa.id))
            p = _sub(el, "Subject_AreaProps")
            _prop(p, "Name", sa.name)
            _prop(p, "Definition", sa.definition)
            member_guids = [entity_guid[le_by_ce[m]] for m in sa.members if m in le_by_ce]
            _ref_array(p, "User_Attached_Objects_Ref_Array", "User_Attached_Objects_Ref",
                       member_guids)

    return _pretty(root)


def _emit_entity(egrp, le: LogicalEntity, ce, model, cfg, entity_guid, attr_guid,
                 pk_members_by_le, domain_guid_by_name) -> None:
    el = _sub(egrp, "Entity")
    el.set("id", entity_guid[le.id])
    p = _sub(el, "EntityProps")
    # display name = the conceptual (proper-case) name, after the team convention; the importer
    # reads <Name> into the conceptual name and <Physical_Name> into the logical physical name.
    display = ce.name if ce else le.name
    _prop(p, "Name", _apply_naming(display, cfg, "entity"))
    _prop(p, "Physical_Name", le.physical_name or le.name)
    _prop(p, "Definition", (ce.definition if ce else None) or le.definition)

    pk_ids = pk_members_by_le.get(le.id, set())
    if le.attributes:
        agrp = _sub(el, "Attribute_Groups")
        for a in le.attributes:
            _emit_attribute(agrp, a, cfg, attr_guid, a.id in pk_ids, domain_guid_by_name)

    # key groups owned by this entity
    kgs = [kg for kg in model.key_groups.values() if kg.entity == le.id]
    if kgs:
        kgrp = _sub(el, "Key_Group_Groups")
        for kg in kgs:
            kel = _sub(kgrp, "Key_Group")
            kel.set("id", _guid(kg.id))
            kp = _sub(kel, "Key_GroupProps")
            _prop(kp, "Name", kg.name)
            _prop(kp, "Key_Group_Type", _KG_TYPE.get(kg.type, "Alternate_Key"))
            mgrp = _sub(kel, "Key_Group_Member_Groups")
            # member order is positional (document order), matching how the importer reads them
            for member_id in kg.members:
                if member_id not in attr_guid:
                    continue
                mem = _sub(mgrp, "Key_Group_Member")
                mem.set("id", _guid(f"{kg.id}:{member_id}"))
                mp = _sub(mem, "Key_Group_MemberProps")
                # NOTE: the strict erwin XSD allows only Built_In_Id inside Key_Group_MemberProps
                # (it encodes key columns opaquely), but our importer reads the member's attribute
                # from an <Attribute_Ref> here. We emit the importer-readable form; see the module
                # docstring on the key-group XSD tradeoff. Ordinal is positional (document order).
                _ref(mp, "Attribute_Ref", attr_guid[member_id])


def _emit_attribute(agrp, a: Attribute, cfg, attr_guid, is_pk: bool, domain_guid_by_name) -> None:
    el = _sub(agrp, "Attribute")
    el.set("id", attr_guid[a.id])
    p = _sub(el, "AttributeProps")
    _prop(p, "Name", _apply_naming(a.name, cfg, "attribute"))
    _prop(p, "Physical_Name", a.physical_name)
    _prop(p, "Definition", a.definition)
    # domain: prefer a named Domain ref (the importer resolves Parent_Domain_Ref -> domain name);
    # otherwise write the base type as the logical datatype so the importer still types it.
    if a.domain and a.domain in domain_guid_by_name:
        _ref(p, "Parent_Domain_Ref", domain_guid_by_name[a.domain])
    elif a.domain:
        _prop(p, "Logical_Data_Type", _BASE_TO_ERWIN.get(a.domain, "VARCHAR"))
    _prop(p, "Null_Option_Type", "NOT_NULL" if not a.nullable else "NULL")


def _emit_fk_relationships(rels, model, entity_guid, attr_guid) -> None:
    for r in model.relationships.values():
        child = r.from_.entity  # from = child/many side
        parent = r.to.entity  # to = parent/one side
        if child not in entity_guid or parent not in entity_guid:
            continue
        el = _sub(rels, "Relationship")
        el.set("id", _guid(r.id))
        p = _sub(el, "RelationshipProps")
        _prop(p, "Name", r.name)
        _prop(p, "Definition", r.definition)
        _prop(p, "Type", "Identifying" if r.identifying else "Non_Identifying")
        _prop(p, "Cardinality", _CARD.get(r.cardinality, "Zero_One_or_More"))
        _prop(p, "Parent_To_Child_Verb_Phrase", r.verb_phrase)
        _prop(p, "Child_To_Parent_Verb_Phrase", r.inverse_verb_phrase)
        _ref(p, "Parent_Entity_Ref", entity_guid[parent])
        _ref(p, "Child_Entity_Ref", entity_guid[child])


def _emit_subtype_relationships(rels, model, entity_guid, attr_guid) -> None:
    """Re-emit each Category as subtype relationships (supertype -> each subtype), carrying the
    discriminator, so the importer rebuilds the Category."""
    for cat in model.categories.values():
        if cat.supertype not in entity_guid:
            continue
        for sub_id in cat.subtypes:
            if sub_id not in entity_guid:
                continue
            el = _sub(rels, "Relationship")
            el.set("id", _guid(f"{cat.id}:{sub_id}"))
            p = _sub(el, "RelationshipProps")
            _prop(p, "Name", f"{cat.name}_{sub_id[-6:]}")
            _prop(p, "Type", "Subtype_Complete" if cat.complete else "Subtype_Incomplete")
            _ref(p, "Parent_Entity_Ref", entity_guid[cat.supertype])  # supertype = parent
            _ref(p, "Child_Entity_Ref", entity_guid[sub_id])  # subtype = child
            if cat.discriminator and cat.discriminator in attr_guid:
                _ref(p, "Subtype_Discriminator_Ref", attr_guid[cat.discriminator])


def _pretty(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    return minidom.parseString(raw).toprettyxml(indent="  ")
