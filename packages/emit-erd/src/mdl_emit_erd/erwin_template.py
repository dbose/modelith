"""Emit erwin 10.10 XML by CLONING a native erwin export (the "template") and injecting the
Modelith model's content into it.

Why clone instead of invent
----------------------------
A from-scratch erwin document (an empty ``<ModelProps/>`` + ``<ModelEnvProps/>`` and
hand-built domains) opens in erwin Data Modeler 10.10 as an empty ``Model_1`` and then FREEZES
the import engine at 100% CPU. A native erwin export carries a large model-level envelope
(ModelProps, domains, themes, images, naming options, page setup, data-vault objects) that erwin
expects to be present and internally consistent. The reliable path, proven against real erwin, is
to take a native export of the TARGET mart as a template, strip its content groups (entities,
relationships, subject areas, diagrams), keep everything else, and write our own entities into
that envelope. The model then opens on the first attempt.

This mirrors the reference implementation ``tools/ddl_to_erwin_xml.py`` (which builds the same
document from a PostgreSQL DDL script), but the content here is driven by Modelith's IR, so no SQL
parsing is involved — the entities, attributes, key groups and relationships come straight off the
model.

The template file is supplied at RUNTIME (a 7-10 MB native export); it is never committed. When no
template is given, ``emit_erwin`` falls back to its self-built envelope (see ``erwin_export.py``),
which is XSD-valid and round-trips through Modelith but is NOT guaranteed to open in real erwin.

Key structural facts this reproduces (decoded from a native erwin 10.10 export; the published XSD
types the enums as free strings and does not carry them):
  - Object ids: ``{UPPERCASE-GUID}+8hex`` (serial). A preserved ``erwin_id`` is re-emitted verbatim.
  - Entity ``Type``: ``1`` independent, ``6`` dependent (has an identifying parent).
  - Attribute ``Type``: ``0`` primary-key member, ``100`` other. ``Null_Option_Type`` ``0``/``1``.
  - Relationship ``Type``: ``2`` identifying, ``7`` non-identifying. ``Null_Option_Type``
    ``101`` mandatory / ``100`` optional. ``Cardinality`` ``-1`` one / ``-3`` zero-one-or-more.
  - FK structure is NOT just two entity refs: the parent's migrated key group is referenced via
    ``Key_Group_Ref``; the child carries an ``IFn`` key group with ``Relationship_Ref`` and each
    member's ``Parent_Key_Group_Member_Ref``; the child FK attributes carry ``Parent_Attribute_Ref``
    and ``Parent_Relationship_Ref``.
  - Domains are resolved BY NAME from the template (``Number`` / ``String`` / ``d_date`` / ``d_bit``
    / ``Datetime``), not invented, so types match the mart.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path

from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    ErwinNaming,
    KeyGroup,
    LogicalEntity,
    Model,
)

try:  # lxml preserves the template's namespaces, prefixes and attribute order on round-trip.
    from lxml import etree as ET
except ImportError as exc:  # pragma: no cover - surfaced as a clear CLI error
    raise ImportError(
        "erwin template export needs lxml (clone a native erwin export). Install modelith with "
        "the erwin extra, or `pip install lxml`."
    ) from exc

_NS = "http://www.erwin.com/dm/data"


def _n(tag: str) -> str:
    return f"{{{_NS}}}{tag}"


def _ln(el) -> str:
    return ET.QName(el).localname


# Default Image_Ref / Icon_Image_Ref seen on entities in a native erwin export. They point at
# image ids in the template's Image_Groups; the ones below are the tested mart's. Overridable so a
# different template's image ids can be supplied, and droppable (empty) if the template has none.
_DEFAULT_IMAGE_REF = "{D1F75789-86EE-4187-BF2D-5F05AB9963EF}+00000016"
_DEFAULT_ICON_REF = "{D1F75789-86EE-4187-BF2D-5F05AB9963EF}+00000015"

_GUID_RE = re.compile(r"^\{[0-9A-Fa-f-]{36}\}\+[0-9A-Fa-f]{8}$")


def _guid(key: str, suffix: str = "+00000000") -> str:
    """A stable erwin-native id ``{UPPERCASE-8-4-4-4-12}+<suffix>`` derived from ``key``."""
    h = hashlib.sha1(key.encode("utf-8")).hexdigest().upper()  # noqa: S324 - stable id, not security
    return f"{{{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}}}{suffix}"


def _oid(obj, fallback_key: str, suffix: str = "+00000000") -> str:
    """Preserve an object's original erwin GUID (so a re-import matches it), else mint one."""
    return getattr(obj, "erwin_id", None) or _guid(fallback_key, suffix)


def _apply_naming(name: str, cfg: ErwinNaming, which: str) -> str:
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


def _sub(parent, tag: str, text: str | None = None, **attrs):
    el = ET.SubElement(parent, _n(tag))
    for k, v in attrs.items():
        el.set(k, v)
    if text is not None:
        el.text = text
    return el


def _order_array(parent, array_tag: str, item_tag: str, ids: list[str]) -> None:
    arr = _sub(parent, array_tag)
    for i, g in enumerate(ids):
        _sub(arr, item_tag, g, index=str(i))


# Modelith base datatype -> (logical datatype literal, template domain family key). The domain key
# is resolved to a real Domain id by NAME from the template; the literal is written as a fallback
# Logical_Data_Type when the template has no matching domain.
_BASE_TO_ERWIN: dict[str, tuple[str, str]] = {
    "bigint": ("NUMBER(19,0)", "Number"),
    "integer": ("NUMBER(10,0)", "Number"),
    "decimal": ("NUMBER(38,6)", "Number"),
    "string": ("VARCHAR(255)", "String"),
    "boolean": ("BYTE", "Boolean"),
    "date": ("DATE", "Date"),
    "timestamp": ("TIMESTAMP", "Datetime"),
}


def _scope_entity_ids(model: Model, subject_area: str) -> set[str]:
    """Logical-entity ids in ``subject_area`` (its name or id); SA members are CONCEPTUAL ids."""
    le_by_ce = {le.realises: le.id for le in model.logical_entities.values() if le.realises}
    ids: set[str] = set()
    for sa in model.subject_areas.values():
        if sa.name == subject_area or sa.id == subject_area:
            for m in sa.members:
                if m in le_by_ce:
                    ids.add(le_by_ce[m])
    return ids


def emit_erwin_from_template(
    model: Model,
    template: str | Path | bytes,
    *,
    naming: ErwinNaming | None = None,
    subject_area: str | None = None,
    model_name: str | None = None,
    physical: bool = False,
    image_ref: str | None = _DEFAULT_IMAGE_REF,
    icon_ref: str | None = _DEFAULT_ICON_REF,
    date_domain: str = "d_date",
    bool_domain: str = "d_bit",
) -> str:
    """Clone the native erwin export ``template`` and inject ``model`` into it; return XML text.

    ``template`` is a path, or the raw bytes, of a native erwin 10.10 XML export of the target mart.
    Its model-level envelope (ModelProps, domains, themes, images, page setup) is kept; its content
    (entities, relationships, subject areas, diagrams) is replaced with the model's.

    ``subject_area`` scopes the export to one area (its name or id): only that area's entities are
    emitted, FKs within the area are kept, and cross-area FKs are dropped (no stub — erwin chokes on
    a thin stub, and the neighbours already exist in the target mart).
    """
    cfg = naming or ErwinNaming()
    scope = _scope_entity_ids(model, subject_area) if subject_area else None
    name = model_name or "Modelith"

    src = template if isinstance(template, bytes) else Path(template).read_bytes()
    root = ET.fromstring(src)  # noqa: S320 - trusted local template the user supplied
    tree = ET.ElementTree(root)
    model_el = next(c for c in root if _ln(c) == "Model")
    groups = {_ln(c): c for c in model_el}

    _reset_envelope(root, model_el, groups, name)
    dom_id = _domain_resolver(groups, date_domain, bool_domain)

    # --- resolve the content from the IR -------------------------------------------------
    entity_ids = {
        le.id: le for le in model.logical_entities.values() if scope is None or le.id in scope
    }
    ce_by_le: dict[str, ConceptualEntity] = {}
    for le in entity_ids.values():
        if le.realises and le.realises in model.conceptual_entities:
            ce_by_le[le.id] = model.conceptual_entities[le.realises]

    # key groups per entity, in a stable order (PK first, then AK, then IF); carries members.
    kgs_by_entity: dict[str, list[KeyGroup]] = defaultdict(list)
    for kg in model.key_groups.values():
        if kg.entity in entity_ids:
            kgs_by_entity[kg.entity].append(kg)
    _KG_ORDER = {"pk": 0, "alternate": 1, "unique": 1, "index": 2}
    for lst in kgs_by_entity.values():
        lst.sort(key=lambda kg: (_KG_ORDER.get(kg.type, 3), kg.name))

    pk_members: dict[str, list[str]] = {}
    for eid, lst in kgs_by_entity.items():
        for kg in lst:
            if kg.type == "pk":
                pk_members[eid] = _dedupe(kg.members)
                break
    # Inferred PK (spec 3.3): no explicit pk key group -> the business-key attributes ARE the PK.
    for eid, le in entity_ids.items():
        if eid not in pk_members:
            bks = [a.id for a in le.attributes if a.role in ("business_key", "surrogate_key")]
            if bks:
                pk_members[eid] = _dedupe(bks)

    # relationships whose both ends are in scope (cross-area dropped, never stubbed).
    rels = [
        r
        for r in model.relationships.values()
        if r.from_.entity in entity_ids and r.to.entity in entity_ids
    ]
    rels.sort(key=lambda r: r.name)

    # which key group on the parent a relationship migrates (its PK), and the child's FK columns.
    child_rels: dict[str, list] = defaultdict(list)
    for r in rels:
        child_rels[r.from_.entity].append(r)
    for lst in child_rels.values():
        lst.sort(key=lambda r: (not r.identifying, r.name))

    # the first relationship that introduced each child FK column (so its Parent_Attribute_Ref is
    # written once, on the migrated column).
    fk_first: dict[tuple[str, str], tuple] = {}
    for lst in child_rels.values():
        for r in lst:
            p_pk = pk_members.get(r.to.entity, [])
            c_cols = r.from_.attributes or p_pk  # child FK columns, else mirror the parent PK
            for c_attr, p_attr in zip(c_cols, p_pk, strict=False):
                fk_first.setdefault((r.from_.entity, c_attr), (r, p_attr))

    ids = _IdMint(model, entity_ids, kgs_by_entity)

    # --- entities (dependency order so parents precede children, like the reference impl) --
    deps: dict[str, set[str]] = defaultdict(set)
    for r in rels:
        if r.from_.entity != r.to.entity:
            deps[r.from_.entity].add(r.to.entity)
    order: list[str] = []
    done: set[str] = set()
    rest = sorted(entity_ids, key=lambda e: entity_ids[e].name)
    while rest:
        nxt = [e for e in rest if deps[e] <= done] or rest[:1]
        for e in nxt:
            order.append(e)
            done.add(e)
            rest.remove(e)

    eg = groups["Entity_Groups"]
    for eid in order:
        _emit_entity(
            eg, entity_ids[eid], ce_by_le.get(eid), cfg, model, ids, dom_id, pk_members,
            kgs_by_entity, child_rels, fk_first, physical, image_ref, icon_ref,
        )

    # --- relationships -------------------------------------------------------------------
    rg = groups["Relationship_Groups"]
    for r in rels:
        _emit_relationship(rg, r, entity_ids, pk_members, kgs_by_entity, ids)

    # --- subject area(s) -----------------------------------------------------------------
    _emit_subject_areas(groups["Subject_Area_Groups"], model, subject_area, order, rels, ids)

    _drop_dangling_refs(root)
    return ET.tostring(tree, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode(
        "utf-8"
    )


def _reset_envelope(root, model_el, groups: dict, name: str) -> None:
    """Strip the template's CONTENT, keep its settings, and remint the model identity."""
    for k in ("Entity_Groups", "Relationship_Groups", "Subject_Area_Groups"):
        grp = groups.get(k)
        if grp is None:
            grp = _sub(model_el, k)  # a template missing a content group gets an empty one
            groups[k] = grp
        else:
            for c in list(grp):
                grp.remove(c)
    for k in ("ER_Diagram_Groups", "Annotation_Groups"):
        if k in groups:
            model_el.remove(groups[k])
    for c in list(root):
        if _ln(c) == "Model_Proxy_Object":
            root.remove(c)
    for el in list(root.iter(_n("Dependent_Objects_Ref_Array"))):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)

    mid = _guid("model:" + name, "+00000001")
    model_el.set("id", mid)
    model_el.set("name", name)
    mp = groups.get("ModelProps")
    mev = groups.get("ModelEnvProps")
    if mp is not None:
        for tag in (
            "Proxy_Short_Id", "Current_ER_Diagram_Ref", "Template_Path", "Template_Control_Value",
            "Template_Objects", "History_List_Array",
        ):
            for el in mp.findall(_n(tag)):
                mp.remove(el)
        _set_text(mp, "Name", name)
        _set_text(mp, "Long_Id", mid)
    if mev is not None:
        _set_text(mev, "Locator", "")
        _set_text(mev, "Persistence_Unit_Id", _guid("unit:" + name, "+01000000"))


def _set_text(parent, tag: str, text: str) -> None:
    el = parent.find(_n(tag))
    if el is None:
        el = _sub(parent, tag)
    el.text = text


def _domain_resolver(groups: dict, date_domain: str, bool_domain: str):
    """A ``family_key -> domain id`` lookup, resolving names from the template's Domain_Groups."""
    domains: dict[str, str] = {}
    dg = groups.get("Domain_Groups")
    if dg is not None:
        for d in dg:
            p = d.find(_n("DomainProps"))
            if p is not None:
                nm = p.findtext(_n("Name"))
                if nm:
                    domains[nm] = d.get("id")
    default = domains.get("String") or domains.get("<default>")
    name_by_family = {
        "Number": "Number",
        "String": "String",
        "Date": date_domain,
        "Datetime": "Datetime",
        "Boolean": bool_domain,
    }

    def resolve(family: str) -> str | None:
        return domains.get(name_by_family.get(family, family)) or default

    return resolve


class _IdMint:
    """Deterministic erwin GUIDs for every content object, preferring a preserved ``erwin_id``."""

    def __init__(self, model: Model, entity_ids: dict, kgs_by_entity: dict):
        self._ent = {eid: _oid(le, f"ent:{eid}") for eid, le in entity_ids.items()}
        self._attr: dict[str, str] = {}
        for le in entity_ids.values():
            for a in le.attributes:
                self._attr[a.id] = _oid(a, f"attr:{le.id}:{a.id}")
        self._rel = {r.id: _oid(r, f"rel:{r.id}") for r in model.relationships.values()}
        self._kg: dict[str, str] = {}
        for lst in kgs_by_entity.values():
            for kg in lst:
                self._kg[kg.id] = _oid(kg, f"kg:{kg.id}")

    def ent(self, eid: str) -> str:
        return self._ent[eid]

    def attr(self, aid: str) -> str:
        return self._attr[aid]

    def rel(self, rid: str) -> str:
        return self._rel[rid]

    def kg(self, kid: str) -> str:
        return self._kg[kid]

    def kg_member(self, kid: str, aid: str) -> str:
        return _guid(f"{self.kg(kid)}:{aid}")

    def if_kg(self, rid: str) -> str:
        return _guid(f"if:{self.rel(rid)}")

    def if_member(self, rid: str, aid: str) -> str:
        return _guid(f"ifm:{self.rel(rid)}:{aid}")


def _emit_entity(
    eg, le: LogicalEntity, ce, cfg, model, ids: _IdMint, dom_id, pk_members, kgs_by_entity,
    child_rels, fk_first, physical: bool, image_ref, icon_ref,
) -> None:
    pk = pk_members.get(le.id, [])
    dependent = any(r.identifying for r in child_rels.get(le.id, []))
    display = ce.name if ce else le.name
    ent_name = _apply_naming(display, cfg, "entity")
    el = _sub(eg, "Entity", id=ids.ent(le.id), name=ent_name)
    p = _sub(el, "EntityProps")
    definition = (ce.definition if ce else None) or le.definition
    if definition:
        _sub(p, "Definition", definition)
    _sub(p, "Name", ent_name)
    if physical:
        _sub(p, "Physical_Name", le.physical_name or le.name)
    _sub(p, "Long_Id", ids.ent(le.id))
    _sub(p, "Type", "6" if dependent else "1")

    by_id = {a.id: a for a in le.attributes}
    ordered = [by_id[a] for a in pk if a in by_id] + [a for a in le.attributes if a.id not in pk]
    _order_array(
        p, "Attributes_Order_Ref_Array", "Attributes_Order_Ref", [ids.attr(a.id) for a in ordered]
    )
    if image_ref:
        _sub(p, "Image_Ref", image_ref)
    if icon_ref:
        _sub(p, "Icon_Image_Ref", icon_ref)

    ag = _sub(el, "Attribute_Groups")
    for a in ordered:
        _emit_attribute(ag, a, le, cfg, ids, dom_id, a.id in pk, fk_first, physical)

    _emit_key_groups(el, le, ent_name, cfg, ids, pk_members, kgs_by_entity, child_rels)


def _emit_attribute(ag, a: Attribute, le, cfg, ids: _IdMint, dom_id, is_pk: bool, fk_first,
                    physical: bool) -> None:
    attr_name = _apply_naming(a.name, cfg, "attribute")
    el = _sub(ag, "Attribute", id=ids.attr(a.id), name=attr_name)
    p = _sub(el, "AttributeProps")
    if a.definition:
        _sub(p, "Definition", a.definition)
    _sub(p, "Name", attr_name)
    if physical and a.physical_name:
        _sub(p, "Physical_Name", a.physical_name)
    _sub(p, "Long_Id", ids.attr(a.id))
    _sub(p, "Type", "0" if is_pk else "100")
    _sub(p, "Null_Option_Type", "1" if (not a.nullable or is_pk) else "0")
    key = (le.id, a.id)
    if key in fk_first:
        r, p_attr = fk_first[key]
        _sub(p, "Parent_Attribute_Ref", ids.attr(p_attr))
        _sub(p, "Parent_Relationship_Ref", ids.rel(r.id))
    literal, family = _BASE_TO_ERWIN.get((a.domain or "string").lower(), ("VARCHAR(255)", "String"))
    _sub(p, "Logical_Data_Type", literal)
    resolved = dom_id(family)
    if resolved:
        _sub(p, "Parent_Domain_Ref", resolved)


def _emit_key_groups(el, le, ent_name, cfg, ids: _IdMint, pk_members, kgs_by_entity,
                    child_rels) -> None:
    kgs = _sub(el, "Key_Group_Groups")

    def write(kid: str, kname: str, ktype: str, members: list[str], *, rel=None, parents=None):
        k = _sub(kgs, "Key_Group", id=kid, name=kname)
        kp = _sub(k, "Key_GroupProps")
        _sub(kp, "Name", kname)
        _sub(kp, "Long_Id", kid)
        _sub(kp, "Key_Group_Type", ktype)
        if rel is not None:
            _sub(kp, "Relationship_Ref", ids.rel(rel.id))
        mids = [_mid(m) for m in members]
        _order_array(kp, "Key_Group_Members_Order_Ref_Array", "Key_Group_Members_Order_Ref", mids)
        mg = _sub(k, "Key_Group_Member_Groups")
        for i, (aid, mid) in enumerate(zip(members, mids, strict=False), 1):
            mem_name = _apply_naming_attr(aid, le, cfg)
            mm = _sub(mg, "Key_Group_Member", id=mid, name=mem_name)
            mp = _sub(mm, "Key_Group_MemberProps")
            _sub(mp, "Name", mem_name)
            _sub(mp, "Long_Id", mid)
            _sub(mp, "Attribute_Ref", ids.attr(aid))
            if parents and aid in parents:
                _sub(mp, "Parent_Key_Group_Member_Ref", parents[aid])
            _sub(mp, "Key_Group_Member_Order", str(i))

    # explicit key groups (PK / AK / IF), named with erwin's XPK/XAKn/XIFn convention.
    ak = 0
    inv = 0
    emitted_pk = False
    for kg in kgs_by_entity.get(le.id, []):
        members = _dedupe(kg.members)
        if kg.type == "pk":
            _mid = lambda m, kid=ids.kg(kg.id): _guid(f"{kid}:{m}")  # noqa: E731
            write(ids.kg(kg.id), f"XPK{ent_name}", "PK", members)
            emitted_pk = True
        elif kg.type in ("alternate", "unique"):
            ak += 1
            _mid = lambda m, kid=ids.kg(kg.id): _guid(f"{kid}:{m}")  # noqa: E731
            write(ids.kg(kg.id), f"XAK{ak}{ent_name}", f"AK{ak}", members)
        else:  # index
            inv += 1
            _mid = lambda m, kid=ids.kg(kg.id): _guid(f"{kid}:{m}")  # noqa: E731
            write(ids.kg(kg.id), f"XIF{inv}{ent_name}", f"IF{inv}", members)

    # inferred PK (no explicit pk key group) -> emit it as an explicit PK key group (spec 3.3).
    if not emitted_pk and pk_members.get(le.id):
        kid = _guid(f"pk:{le.id}")
        _mid = lambda m, k=kid: _guid(f"{k}:{m}")  # noqa: E731
        write(kid, f"XPK{ent_name}", "PK", pk_members[le.id])

    # child IFn key groups, one per incoming FK (the migrated foreign key), carrying the back-refs
    # erwin needs to wire the relationship to the parent's key group members.
    for n, r in enumerate(child_rels.get(le.id, []), 1):
        p_pk = pk_members.get(r.to.entity, [])
        c_cols = r.from_.attributes or p_pk
        parent_kid = _parent_pk_kid(r.to.entity, pk_members, kgs_by_entity, ids)
        parents = {
            c: _guid(f"{parent_kid}:{p}") for c, p in zip(c_cols, p_pk, strict=False)
        } if parent_kid else {}
        kid = ids.if_kg(r.id)
        _mid = lambda m, k=kid: _guid(f"ifm:{k}:{m}")  # noqa: E731
        write(kid, f"XIF{n}{ent_name}", f"IF{n}", list(c_cols), rel=r, parents=parents)


def _parent_pk_kid(parent_eid: str, pk_members, kgs_by_entity, ids: _IdMint) -> str | None:
    """The GUID of the parent entity's PK key group (explicit if present, else the inferred one)."""
    for kg in kgs_by_entity.get(parent_eid, []):
        if kg.type == "pk":
            return ids.kg(kg.id)
    if pk_members.get(parent_eid):
        return _guid(f"pk:{parent_eid}")
    return None


def _apply_naming_attr(aid: str, le, cfg) -> str:
    for a in le.attributes:
        if a.id == aid:
            return _apply_naming(a.name, cfg, "attribute")
    return aid


def _emit_relationship(rg, r, entity_ids, pk_members, kgs_by_entity, ids: _IdMint) -> None:
    child = entity_ids[r.from_.entity]
    c_by_id = {a.id: a for a in child.attributes}
    c_cols = r.from_.attributes or pk_members.get(r.to.entity, [])
    mandatory = r.identifying or r.optionality == "mandatory" or all(
        not c_by_id[c].nullable for c in c_cols if c in c_by_id
    )
    child_pk = pk_members.get(r.from_.entity, [])
    one = bool(c_cols) and set(c_cols) == set(child_pk)

    el = _sub(rg, "Relationship", id=ids.rel(r.id), name=r.name)
    p = _sub(el, "RelationshipProps")
    _sub(p, "Name", r.name)
    if r.definition:
        _sub(p, "Definition", r.definition)
    _sub(p, "Long_Id", ids.rel(r.id))
    _sub(p, "Type", "2" if r.identifying else "7")
    _sub(p, "Null_Option_Type", "101" if mandatory else "100")
    _sub(p, "Cardinality", "-1" if one else "-3")
    if r.verb_phrase:
        _sub(p, "Parent_To_Child_Verb_Phrase", r.verb_phrase)
    if r.inverse_verb_phrase:
        _sub(p, "Child_To_Parent_Verb_Phrase", r.inverse_verb_phrase)
    parent_kid = _parent_pk_kid(r.to.entity, pk_members, kgs_by_entity, ids)
    if parent_kid:
        _sub(p, "Key_Group_Ref", parent_kid)
    _sub(p, "Parent_Entity_Ref", ids.ent(r.to.entity))
    _sub(p, "Child_Entity_Ref", ids.ent(r.from_.entity))


def _emit_subject_areas(sg, model, subject_area, order, rels, ids: _IdMint) -> None:
    le_by_ce = {
        le.realises: le.id for le in model.logical_entities.values() if le.realises
    }
    sas = [
        sa
        for sa in model.subject_areas.values()
        if subject_area is None or sa.name == subject_area or sa.id == subject_area
    ]
    scope_order = set(order)
    for sa in sas:
        sid = _oid(sa, f"sa:{sa.id}")
        s = _sub(sg, "Subject_Area", id=sid, name=sa.name)
        sp = _sub(s, "Subject_AreaProps")
        _sub(sp, "Name", sa.name)
        if sa.definition:
            _sub(sp, "Definition", sa.definition)
        _sub(sp, "Long_Id", sid)
        _sub(sp, "Object_Order", "0")
        rel_ids = [ids.rel(r.id) for r in rels]
        _order_array(sp, "Auto_Attached_Objects_Ref_Array", "Auto_Attached_Objects_Ref", rel_ids)
        members = [
            ids.ent(le_by_ce[m]) for m in sa.members if m in le_by_ce and le_by_ce[m] in scope_order
        ]
        _order_array(
            sp, "User_Attached_Objects_Ref_Array", "User_Attached_Objects_Ref", members
        )


def _drop_dangling_refs(root) -> None:
    """Remove any ``*_Ref`` that points at a GUID no longer in the document (template leftovers)."""
    known = {e.get("id") for e in root.iter() if e.get("id")}
    known |= {e.text for e in root.iter(_n("Long_Id")) if e.text}
    for e in list(root.iter()):
        if not isinstance(e.tag, str):
            continue
        if "Ref" in _ln(e) and e.text and _GUID_RE.match(e.text.strip()):
            if e.text.strip() not in known:
                parent = e.getparent()
                if parent is not None:
                    parent.remove(e)


def _dedupe(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
