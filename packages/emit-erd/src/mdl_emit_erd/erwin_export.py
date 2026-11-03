"""Emit erwin XML from a Modelith model — the inverse of `mdl import erwin`.

Goals (and their guards):
  - **Imports into real erwin 10.10.** erwin writes a specific dialect: an UPPERCASE
    `{GUID}+00000000` object id, NUMERIC enum codes (Relationship_Type 2/7, Cardinality -3/-1,
    Null_Option_Type 0/1, Attribute Type 0/100, Entity Type 1, Key_Group_Type PK/AKn/IFn), and
    FileVersion "10.10.<build>". Emitting the word forms instead made erwin's import engine (EAT)
    hang. We emit the codes; the mdl_reverse importer reads both codes and words, so round-trips
    hold. The published ERwinSchema.xsd is LOOSER than erwin's real behaviour in some places
    (it types the enums as free strings) and STRICTER in others (its FileVersion pattern rejects
    the 10.10 form erwin itself writes), so XSD-validity is a structural check, not the
    compatibility oracle — a real erwin import is.
  - Skeleton: root `<erwin FileVersion=… Format="erwin">` → namespaced `<Model id=…>` with the
    required `<ModelEnvProps/>` + `<ModelProps/>`, then the `*_Groups`. Every object element
    carries a required `id`, and its scalar values live in a `<XxxProps>` child as element TEXT.
  - **Round-trips** through mdl_reverse.erwin.import_erwin back to an equivalent model. Because
    the importer discards erwin GUIDs and mints fresh ULIDs, equivalence is STRUCTURAL
    (names/types/members/verb-phrases), not id-identity.
  - A SCOPED export (one subject area) emits only that area's entities, keeps FKs WITHIN the
    area, and DROPS cross-area FKs (no stub entity — erwin chokes on a thin stub); cross-area
    FKs are re-established in erwin after the slice imports.

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
# Real erwin 10.10 writes FileVersion as "10.10.<build>" (e.g. "10.10.38485"). The published
# ERwinSchema.xsd pattern is stricter than what erwin itself emits (it allows only a single
# leading digit), so erwin's own exports don't validate against it — but erwin's IMPORTER
# accepts the 10.10 form, and a real-erwin import is the target here, not XSD-validity. The
# XSD-validation tests tolerate this by validating against the looser dialect erwin actually
# reads; see the test module.
_FILE_VERSION = "10.10.38485"

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

# --- erwin 10.10 numeric enum codes ------------------------------------------------
# erwin 10.10 writes these properties as NUMERIC codes, not words. Our exporter used to emit the
# word forms (e.g. "Non_Identifying", "Zero_One_or_More"); a real erwin import either rejected
# them or hung on the resulting malformed graph. Values decoded from a real erwin 10.10 export of
# the same model (the authoritative source — the published XSD types all of these as free strings
# and does not carry the codes). The erwin importer in mdl_reverse reads BOTH the numeric codes
# and the old word forms, so these are round-trip safe.

# Key_Group_Type: PK, AKn (alternate key n), IFn (inversion entry / index n). Not purely numeric
# — erwin uses PK and the AK/IF families with a 1-based ordinal per entity. We emit the first of
# each family (AK1 / IF1); the importer reads the family prefix.
_KG_TYPE = {
    "pk": "PK",
    "alternate": "AK1",
    "unique": "AK1",  # importer maps unknown -> unique; alternate is the safe carrier
    "index": "IF1",
}

# Cardinality: -3 = zero-one-or-more (the many side), -1 = exactly one.
_CARD = {
    "one_to_one": "-1",
    "one_to_many": "-3",
    "many_to_one": "-3",
    "many_to_many": "-3",
}

# Relationship_Type: 2 = identifying, 7 = non-identifying.
_REL_TYPE_IDENTIFYING = "2"
_REL_TYPE_NON_IDENTIFYING = "7"

# Null_Option_Type: 0 = null allowed, 1 = not null.
_NULL_ALLOWED = "0"
_NULL_NOT = "1"

# Attribute Type: 0 = primary-key member, 100 = ordinary (non-key) attribute.
_ATTR_TYPE_PK_MEMBER = "0"
_ATTR_TYPE_OTHER = "100"

# Entity Type: 1 (the code a real erwin export carries on every entity).
_ENTITY_TYPE = "1"


# erwin's native object id is an UPPERCASE brace-wrapped GUID followed by a "+8hex" serial, e.g.
# "{0007EEEF-A625-4777-B5CB-6BCF63932FF4}+00000000" (observed in a real erwin 10.10 export). A
# freshly minted object starts at serial 0 -> "+00000000". erwin's import engine (EAT) validates
# this exact shape and HANGS on a lowercase or suffix-less id, which is what our Modelith-native
# objects used to emit. Both parts matter: the GUID must be UPPERCASE and carry the serial.
# Preserved ids (an erwin-origin object's `erwin_id`) already have this shape and are emitted
# verbatim by `_oid`, so the normalisation here only ever touches ids we mint ourselves.
_ERWIN_SERIAL = "+00000000"


def _guid(key: str) -> str:
    """A stable erwin-native id {UPPERCASE-8-4-4-4-12}+00000000 derived from a key, so output is
    reproducible.

    Used for synthetic keys (the model root, key-group members) and as the fallback for an
    object with no preserved erwin GUID (a Modelith-native object erwin treats as new). The
    UPPERCASE GUID and the "+00000000" serial are BOTH REQUIRED: erwin's import engine hangs on a
    lowercase or bare brace GUID. The importer only uses ids as intra-document join keys and is
    case-insensitive on them, so any stable unique value round-trips through Modelith; the exact
    erwin shape matters only for real erwin's own import."""
    import hashlib

    h = hashlib.sha1(key.encode("utf-8")).hexdigest().upper()  # noqa: S324 - stable id, not security
    return f"{{{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}}}{_ERWIN_SERIAL}"


def _oid(obj) -> str:
    """The erwin GUID for a model object: its PRESERVED original `erwin_id` when the object came
    from an erwin import (so a re-import matches/updates it, no duplicate), else a stable
    synthetic GUID from its ULID (a Modelith-native object erwin will treat as new)."""
    return getattr(obj, "erwin_id", None) or _guid(obj.id)


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


def _scope_entity_ids(model: Model, subject_area: str) -> set[str]:
    """The logical-entity ids that belong to `subject_area` (its name or id). SubjectArea.members
    are CONCEPTUAL ids, so map each to the logical entity that realises it."""
    le_by_ce = {le.realises: le.id for le in model.logical_entities.values() if le.realises}
    ids: set[str] = set()
    for sa in model.subject_areas.values():
        if sa.name == subject_area or sa.id == subject_area:
            for m in sa.members:
                if m in le_by_ce:
                    ids.add(le_by_ce[m])
    return ids


def emit_erwin(
    model: Model,
    *,
    naming: ErwinNaming | None = None,
    subject_area: str | None = None,
    template: str | bytes | None = None,
    model_name: str | None = None,
) -> str:
    """Serialize `model` to an erwin XML document string.

    `naming` applies a team convention to logical/display names (None = verbatim).

    `subject_area` (a name or id) scopes the export to ONE subject area: only that area's member
    entities are emitted, FKs within the area are kept, and cross-area FKs are DROPPED (no stub —
    erwin chokes on a thin stub; the neighbours already exist in the target mart). None = the
    whole model.

    `template` is a native erwin 10.10 XML export (a path string or its raw bytes) to CLONE the
    model-level envelope from — ModelProps, domains, themes, images, page setup. This is the only
    path PROVEN to open in real erwin Data Modeler 10.10: a from-scratch envelope opens empty and
    freezes erwin's import engine. When `template` is given, this delegates to
    `emit_erwin_from_template` (which also writes the full FK key-group structure erwin needs).
    When `template` is None, the self-built envelope below is emitted: XSD-valid and round-trips
    through Modelith's own importer, but NOT guaranteed to open in real erwin. `model_name` names
    the cloned model (template path only; default "Modelith")."""
    if template is not None:
        from mdl_emit_erd.erwin_template import emit_erwin_from_template

        return emit_erwin_from_template(
            model,
            template,
            naming=naming,
            subject_area=subject_area,
            model_name=model_name,
        )

    cfg = naming or ErwinNaming()
    scope = _scope_entity_ids(model, subject_area) if subject_area else None

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

    # conceptual lookup, PK membership, and GUID maps (GUIDs prefer the preserved erwin_id).
    ce_by_le: dict[str, ConceptualEntity] = {}
    for ce in model.conceptual_entities.values():
        for le in model.logical_entities.values():
            if le.realises == ce.id:
                ce_by_le[le.id] = ce
    pk_members_by_le: dict[str, set[str]] = {}
    for kg in model.key_groups.values():
        if kg.type == "pk":
            pk_members_by_le.setdefault(kg.entity, set()).update(kg.members)
    entity_guid: dict[str, str] = {le.id: _oid(le) for le in model.logical_entities.values()}
    attr_guid: dict[str, str] = {}
    for le in model.logical_entities.values():
        for a in le.attributes:
            attr_guid[a.id] = _oid(a)

    # Which relationships to emit. For a SCOPED export we keep only relationships whose BOTH ends
    # are inside the subject area. A cross-SA relationship (one end outside) is DROPPED, not
    # stubbed: erwin chokes on a thin stub entity, and the practical workflow is to re-establish
    # cross-area FKs in erwin after the slice's entities are imported. Within-area FKs are exact.
    # (Unscoped export keeps every relationship, as before.)
    in_scope_rels = []
    for r in model.relationships.values():
        c, pnt = r.from_.entity, r.to.entity
        if c not in entity_guid or pnt not in entity_guid:
            continue
        if scope is None or (c in scope and pnt in scope):
            in_scope_rels.append(r)
    # subtype (Category) relationships: keep only when supertype AND every emitted subtype are
    # in scope, for the same reason (no cross-boundary stub).
    in_scope_cats = []
    for cat in model.categories.values():
        if cat.supertype not in entity_guid:
            continue
        subs = [s for s in cat.subtypes if s in entity_guid]
        if scope is None:
            in_scope_cats.append(cat)
        elif cat.supertype in scope:
            # drop subtypes that fall outside the area; keep the category if any remain in scope
            in_scope_subs = [s for s in subs if s in scope]
            if in_scope_subs:
                in_scope_cats.append(cat)

    def _emit(le):
        return scope is None or le.id in scope

    # --- domains (only those referenced by an EXPORTED attribute) --------------------
    exported_domains: set[str] = set()
    if scope is None:
        exported_domains = {d.name for d in model.domains.values()}
    else:
        for le in model.logical_entities.values():
            if _emit(le):
                for a in le.attributes:
                    if a.domain:
                        exported_domains.add(a.domain)
    domain_guid_by_name: dict[str, str] = {}
    doms = [d for d in model.domains.values() if d.name in exported_domains]
    if doms:
        grp = _sub(mdl, "Domain_Groups")
        for dom in doms:
            g = _oid(dom)
            domain_guid_by_name[dom.name] = g
            el = _sub(grp, "Domain")
            el.set("id", g)
            p = _sub(el, "DomainProps")
            _prop(p, "Name", dom.name)
            _prop(p, "Logical_Data_Type", _BASE_TO_ERWIN.get(dom.base_type, "VARCHAR"))
            _prop(p, "Definition", dom.definition)

    # --- entities (only in-scope entities, emitted in full; no cross-boundary stubs) --
    full_les = [le for le in model.logical_entities.values() if _emit(le)]
    if full_les:
        egrp = _sub(mdl, "Entity_Groups")
        for le in full_les:
            _emit_entity(egrp, le, ce_by_le.get(le.id), model, cfg,
                         entity_guid, attr_guid, pk_members_by_le, domain_guid_by_name)

    # --- relationships (FK + subtype-from-Category) ----------------------------------
    rels = _sub(mdl, "Relationship_Groups")
    for r in in_scope_rels:
        _emit_fk_relationship(rels, r, entity_guid, attr_guid)
    for cat in in_scope_cats:
        _emit_subtype_relationship(rels, cat, entity_guid, attr_guid)
    if not list(rels):
        mdl.remove(rels)

    # --- subject areas (the scoped one, or all) --------------------------------------
    le_by_ce: dict[str, str] = {le.realises: le.id for le in model.logical_entities.values()
                                if le.realises}
    sas = [sa for sa in model.subject_areas.values()
           if subject_area is None or sa.name == subject_area or sa.id == subject_area]
    if sas:
        sgrp = _sub(mdl, "Subject_Area_Groups")
        for sa in sas:
            el = _sub(sgrp, "Subject_Area")
            el.set("id", _oid(sa))
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
    _prop(p, "Type", _ENTITY_TYPE)

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
            kel.set("id", _oid(kg))
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
    _prop(p, "Null_Option_Type", _NULL_ALLOWED if a.nullable else _NULL_NOT)
    # Attribute Type: erwin distinguishes a PK-member (0) from an ordinary column (100).
    _prop(p, "Type", _ATTR_TYPE_PK_MEMBER if is_pk else _ATTR_TYPE_OTHER)


def _emit_fk_relationship(rels, r, entity_guid, attr_guid) -> None:
    child = r.from_.entity  # from = child/many side
    parent = r.to.entity  # to = parent/one side
    el = _sub(rels, "Relationship")
    el.set("id", _oid(r))
    p = _sub(el, "RelationshipProps")
    _prop(p, "Name", r.name)
    _prop(p, "Definition", r.definition)
    _prop(p, "Type", _REL_TYPE_IDENTIFYING if r.identifying else _REL_TYPE_NON_IDENTIFYING)
    _prop(p, "Cardinality", _CARD.get(r.cardinality, "-3"))
    _prop(p, "Parent_To_Child_Verb_Phrase", r.verb_phrase)
    _prop(p, "Child_To_Parent_Verb_Phrase", r.inverse_verb_phrase)
    _ref(p, "Parent_Entity_Ref", entity_guid[parent])
    _ref(p, "Child_Entity_Ref", entity_guid[child])


def _emit_subtype_relationship(rels, cat, entity_guid, attr_guid) -> None:
    """Re-emit a Category as subtype relationships (supertype -> each subtype), carrying the
    discriminator, so the importer rebuilds the Category."""
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
