"""erwin XML import — the real erwin Data Modeler export format (spec §6.4).

Nobody migrates to a modeling tool greenfield, so this is the go-to-market on-ramp: point
Modelith at an erwin export and get a committable model back.

The real erwin XML (per the erwin XSDs at erwin/schemas/) is nothing like a flat table
dump. It is:

  - **Namespaced.** Root `{http://www.erwin.com/dm}erwin` -> `{.../dm/data}Model`, and
    every object element lives in the data namespace.
  - **GUID-referenced.** Every object carries a required `id` attribute (a GUID); objects
    point at each other by that id, held in `<..._Ref>` elements (text) or index-ordered
    `<..._Ref>` children of a `<..._Ref_Array>`.
  - **Props-as-text.** An object's scalar data lives in a sibling `<XxxProps>` element
    whose CHILDREN are the properties, each carrying its value as element text —
    `<EntityProps><Name>Customer</Name>...</EntityProps>`, never an XML attribute.

Objects are grouped in `<Object>_Groups` containers under Model (Entity_Groups -> Entity,
Relationship_Groups -> Relationship, ...). Keys live inside each Entity as `Key_Group`s.

**Streaming.** Real exports run 7-10MB, so we parse with ElementTree.iterparse and clear
each top-level object element as it closes — the full document is never held as a DOM. We
index every object by id in one streaming pass, then resolve the cross-references.

**Defensive.** Unknown object groups, unknown property values, and dangling references are
skipped with a warning, never fatal — a novel or partial export yields a usable model plus
notes rather than a crash.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from io import BytesIO, StringIO
from pathlib import Path

from mdl_core.ids import new_ulid
from mdl_core.ir import (
    Attribute,
    Category,
    ConceptualEntity,
    Domain,
    KeyGroup,
    LogicalEntity,
    Model,
    ProjectConfig,
    Relationship,
    RelationshipEnd,
    SubjectArea,
)

# erwin namespaces (see the XSD imports).
_NS_DATA = "http://www.erwin.com/dm/data"

# erwin logical/physical datatype -> Modelith abstract base type. Values look like
# "VARCHAR(40)" / "BIGINT" / "NUMBER(18,2)"; strip the size and lowercase.
_ERWIN_TYPE = {
    "bigint": "bigint",
    "integer": "integer",
    "int": "integer",
    "smallint": "integer",
    "tinyint": "integer",
    "numeric": "decimal",
    "decimal": "decimal",
    "number": "decimal",
    "float": "decimal",
    "double": "decimal",
    "real": "decimal",
    "money": "decimal",
    "varchar": "string",
    "varchar2": "string",
    "nvarchar": "string",
    "nvarchar2": "string",
    "char": "string",
    "nchar": "string",
    "text": "string",
    "clob": "string",
    "boolean": "boolean",
    "bit": "boolean",
    "date": "date",
    "datetime": "timestamp",
    "datetime2": "timestamp",
    "timestamp": "timestamp",
    "time": "timestamp",
}

# The maximum export we'll attempt to hold the object index for. iterparse streams, but the
# extracted-record index still grows with object count; ~50MB of XML is already an enormous
# model and a sane upper bound. Above it we warn and parse anyway (best-effort).
_SIZE_WARN_BYTES = 50 * 1024 * 1024


def _base_type(erwin_type: str | None) -> str:
    if not erwin_type:
        return "string"
    t = erwin_type.split("(")[0].strip().lower()
    return _ERWIN_TYPE.get(t, "string")


def _local(tag: str) -> str:
    """Local element name, namespace stripped."""
    return tag.split("}")[-1]


@dataclass
class _Obj:
    """A lightweight extracted record for one erwin object, kept in the id index so the
    full XML element can be cleared from memory after extraction."""

    kind: str  # local element name, e.g. "Entity", "Relationship", "Domain"
    id: str
    props: dict[str, str]  # single-value props: name -> text
    refs: dict[str, str]  # single refs: prop-name -> target GUID
    ref_arrays: dict[str, list[str]]  # array refs: prop-name -> ordered target GUIDs
    children: list[_Obj] = field(default_factory=list)  # nested objects (attrs, key groups)


@dataclass
class ErwinImport:
    model: Model
    warnings: list[str] = field(default_factory=list)


# --- streaming parse ------------------------------------------------------------


def _extract(el: ET.Element) -> _Obj:
    """Extract an object element (Entity / Relationship / ...) into a record. Reads its
    `<XxxProps>` child's properties (text values, single refs, ref arrays) and recurses
    into nested `<Object>_Groups` for child objects (attributes, key groups, members)."""
    obj = _Obj(
        kind=_local(el.tag),
        id=el.get("id") or new_ulid(),
        props={},
        refs={},
        ref_arrays={},
    )
    for child in el:
        name = _local(child.tag)
        if name.endswith("Props"):
            _read_props(child, obj)
        elif name.endswith("_Groups"):
            # a container of nested objects (Attribute_Groups -> Attribute, ...)
            for sub in child:
                if sub.get("id") is not None or list(sub):
                    obj.children.append(_extract(sub))
    return obj


def _read_props(props_el: ET.Element, obj: _Obj) -> None:
    """Read a `<XxxProps>` element's children into the object's props/refs/ref_arrays."""
    for p in props_el:
        name = _local(p.tag)
        if name.endswith("_Ref_Array"):
            key = name[: -len("_Array")]  # Foo_Ref_Array -> Foo_Ref
            items: list[tuple[int, str]] = []
            for ref in p:
                guid = (ref.text or "").strip()
                if guid:
                    try:
                        idx = int(ref.get("index", "0"))
                    except ValueError:
                        idx = len(items)
                    items.append((idx, guid))
            obj.ref_arrays[key] = [g for _, g in sorted(items)]
        elif name.endswith("_Ref"):
            guid = (p.text or "").strip()
            if guid:
                obj.refs[name] = guid
        else:
            text = (p.text or "").strip()
            if text:
                obj.props[name] = text


# Top-level object element names we index (inside their *_Groups). Nested objects
# (Attribute, Key_Group, Key_Group_Member) are captured via _extract recursion.
_TOP_OBJECTS = frozenset(
    {"Entity", "Relationship", "Subject_Area", "Domain", "View", "ER_Diagram"}
)

# Object names that appear NESTED inside a mapped object (consumed by _extract, not at the
# Model level). Their `_Groups` fire an `end` before the parent object's, so they must not
# be reported as "skipped Model-level objects".
_NESTED_OBJECTS = frozenset(
    {
        "Attribute",
        "Attribute_X",
        "Key_Group",
        "Key_Group_X",
        "Key_Group_Member",
        "Key_Sequence_Column",
        "Check_Constraint_Usage",
        "Constraint_X",
        "Extended_Notes",
        "History_Information",
        "Display_Style_Sheet",
    }
)


def _parse_stream(source) -> tuple[dict[str, _Obj], list[_Obj], list[str]]:
    """Stream the erwin XML, returning (id -> object index, top-level objects in order,
    warnings). Clears each processed top-level element so a 10MB file never fully
    materialises as a DOM."""
    index: dict[str, _Obj] = {}
    ordered: list[_Obj] = []
    warnings: list[str] = []
    skipped_groups: set[str] = set()

    # iterparse fires `end` for a child BEFORE its parent, so we act only on TOP-LEVEL
    # object elements (Entity, Relationship, …). Their _extract recurses into the still-intact
    # nested groups (Attribute_Groups, Key_Group_Groups) from the subtree, then we clear the
    # whole subtree. A `*_Groups` whose object is neither a top-level mapped object nor a
    # known nested one is a genuinely-unmapped Model-level object type — record it once.
    context = ET.iterparse(source, events=("end",))
    for _event, el in context:
        name = _local(el.tag)
        if name in _TOP_OBJECTS:
            obj = _extract(el)
            ordered.append(obj)
            _index(obj, index)
            el.clear()  # release this object's subtree (children already extracted)
        elif name.endswith("_Groups") and name != "Model":
            base = name[: -len("_Groups")]
            if base and base not in _TOP_OBJECTS and base not in _NESTED_OBJECTS:
                skipped_groups.add(base)

    for base in sorted(skipped_groups):
        warnings.append(f"skipped erwin objects of type {base!r} (not mapped to Modelith)")
    return index, ordered, warnings


def _index(obj: _Obj, index: dict[str, _Obj]) -> None:
    index[obj.id] = obj
    for child in obj.children:
        _index(child, index)


# --- value mapping --------------------------------------------------------------


def _name_of(obj: _Obj, fallback: str | None = None) -> str | None:
    return obj.props.get("Name") or fallback


def _is_key_group_pk(kg: _Obj) -> bool:
    t = (kg.props.get("Key_Group_Type") or "").strip().lower()
    return t in {"primary_key", "pk", "0", "primary"}


def _key_group_ir_type(kg: _Obj) -> str:
    t = (kg.props.get("Key_Group_Type") or "").strip().lower()
    if t in {"primary_key", "pk", "0", "primary"}:
        return "pk"
    if t in {"alternate_key", "ak", "alternate"}:
        return "alternate"
    if t in {"inversion_entry", "inversion", "index"}:
        return "index"
    return "unique"


def _cardinality(v: str | None) -> str:
    m = (v or "").strip().lower()
    if m in {"zero_or_one", "one", "1:1", "exactly_one"}:
        return "one_to_one"
    if m in {"one_or_more", "zero_one_or_more", "many", "1:n", "1:m"}:
        return "many_to_one"
    if m in {"many_to_many", "m:n", "m:m"}:
        return "many_to_many"
    return "many_to_one"


def _nullable(null_option: str | None, is_key: bool) -> bool:
    m = (null_option or "").strip().lower()
    if m in {"not_null", "notnull", "no_nulls", "1"}:
        return False
    if m in {"null", "nulls_allowed", "0"}:
        return True
    return not is_key  # keys default not-null, everything else nullable


# --- build the Model ------------------------------------------------------------


def import_erwin(source: str | Path, *, project_name: str | None = None) -> ErwinImport:
    """Import a real erwin XML export into a Modelith Model. `source` may be a path, an
    XML string, or an already-opened binary/text stream. Returns the model + warnings for
    anything the import couldn't carry (views, transforms, layout, unknown groups)."""
    stream, size_hint = _open(source)
    warnings: list[str] = []
    if size_hint and size_hint > _SIZE_WARN_BYTES:
        warnings.append(
            f"erwin export is very large ({size_hint // (1024 * 1024)}MB); import may be slow"
        )

    index, ordered, parse_warnings = _parse_stream(stream)
    warnings.extend(parse_warnings)

    model = Model(ProjectConfig(name=project_name or "erwin_import"))

    # pass 1: entities (+ conceptual), attributes, key groups, domains, subject areas.
    domain_name_by_id: dict[str, str] = {}
    for obj in ordered:
        if obj.kind == "Domain":
            dname = _name_of(obj)
            if dname:
                domain_name_by_id[obj.id] = dname

    ce_id_by_entity: dict[str, str] = {}  # erwin entity id -> Modelith conceptual id
    le_by_erwin_id: dict[str, LogicalEntity] = {}  # erwin entity id -> LogicalEntity
    attr_le_by_erwin_id: dict[str, str] = {}  # erwin attribute id -> Modelith attribute id
    attr_ir_by_erwin_id: dict[str, Attribute] = {}

    # domains first (attributes reference them by name)
    for obj in ordered:
        if obj.kind != "Domain":
            continue
        dname = _name_of(obj)
        if not dname:
            continue
        model.add(
            Domain(
                id=new_ulid(),
                name=dname,
                base_type=_base_type(
                    obj.props.get("Logical_Data_Type") or obj.props.get("Physical_Data_Type")
                ),
                definition=obj.props.get("Definition"),
            )
        )

    for obj in ordered:
        if obj.kind != "Entity":
            continue
        ename = _name_of(obj)
        if not ename:
            continue
        # which attributes are in the pk key group -> business_key role
        pk_attr_ids: set[str] = set()
        for kg in obj.children:
            if kg.kind == "Key_Group" and _is_key_group_pk(kg):
                for member in kg.children:
                    ar = member.refs.get("Attribute_Ref")
                    if ar:
                        pk_attr_ids.add(ar)

        attrs: list[Attribute] = []
        for a in obj.children:
            if a.kind != "Attribute":
                continue
            aname = _name_of(a)
            if not aname:
                continue
            is_key = a.id in pk_attr_ids
            domain_ref = a.refs.get("Parent_Domain_Ref")
            domain = domain_name_by_id.get(domain_ref) if domain_ref else None
            if not domain:
                raw_type = a.props.get("Logical_Data_Type") or a.props.get("Physical_Data_Type")
                domain = _base_type(raw_type)
            attr = Attribute(
                id=new_ulid(),
                name=aname,
                physical_name=a.props.get("Physical_Name"),
                definition=a.props.get("Definition"),
                domain=domain,
                role="business_key" if is_key else "attribute",
                nullable=_nullable(a.props.get("Null_Option_Type"), is_key),
            )
            attrs.append(attr)
            attr_le_by_erwin_id[a.id] = attr.id
            attr_ir_by_erwin_id[a.id] = attr

        ce = ConceptualEntity(
            id=new_ulid(), name=_titleize(ename), definition=obj.props.get("Definition")
        )
        model.add(ce)
        ce_id_by_entity[obj.id] = ce.id
        le = LogicalEntity(
            id=new_ulid(),
            # a logical name is a dbt-ish identifier: lowercase the erwin display name
            # (the proper-case original is preserved as the conceptual name + physical_name).
            name=_ident(ename),
            physical_name=obj.props.get("Physical_Name") or ename,
            realises=ce.id,
            definition=obj.props.get("Definition"),
            attributes=attrs,
        )
        model.add(le)
        le_by_erwin_id[obj.id] = le

        # non-pk key groups -> KeyGroup objects (pk keys are also emitted, for clarity)
        for kg in obj.children:
            if kg.kind != "Key_Group":
                continue
            members = [
                attr_le_by_erwin_id[m.refs["Attribute_Ref"]]
                for m in kg.children
                if m.refs.get("Attribute_Ref") in attr_le_by_erwin_id
            ]
            if not members:
                continue
            model.add(
                KeyGroup(
                    id=new_ulid(),
                    entity=le.id,
                    name=_name_of(kg) or f"{_key_group_ir_type(kg)}_{ename}",
                    type=_key_group_ir_type(kg),
                    members=members,
                )
            )

    # FK-domain inheritance: a migrated FK column (Parent_Attribute_Ref) inherits its
    # parent PK column's domain, so the domains match (erwin migrates the type; a bare
    # child column would otherwise default to 'string' and fail FK domain validation).
    for erwin_aid, attr in attr_ir_by_erwin_id.items():
        src = index.get(erwin_aid)
        if src is None:
            continue
        parent_ref = src.refs.get("Parent_Attribute_Ref")
        if parent_ref and parent_ref in attr_ir_by_erwin_id:
            attr.domain = attr_ir_by_erwin_id[parent_ref].domain

    # pass 2: relationships (+ detect subtype -> Category) and subject-area membership.
    _build_relationships(
        ordered, index, model, le_by_erwin_id, attr_le_by_erwin_id, ce_id_by_entity, warnings
    )
    _build_subject_areas(ordered, model, ce_id_by_entity, warnings)

    # notes for what erwin had that we dropped
    if any(o.kind == "ER_Diagram" for o in ordered):
        warnings.append("ER diagram layout (positions) was not imported; Modelith auto-layouts")
    if any(o.kind == "View" for o in ordered):
        warnings.append("erwin views were not imported (Modelith models entities, not views)")

    if not model.logical_entities:
        warnings.append(
            "no entities found — is this a valid erwin export? "
            "(expected a namespaced <erwin><Model>… document)"
        )

    return ErwinImport(model=model, warnings=warnings)


def _build_relationships(
    ordered, index, model, le_by_erwin_id, attr_le_by_erwin_id, ce_id_by_entity, warnings
) -> None:
    subtype_children_by_parent: dict[str, list[str]] = {}
    subtype_discriminator_by_parent: dict[str, str] = {}  # parent erwin id -> attr erwin id
    for obj in ordered:
        if obj.kind != "Relationship":
            continue
        parent = obj.refs.get("Parent_Entity_Ref")
        child = obj.refs.get("Child_Entity_Ref")
        p_le = le_by_erwin_id.get(parent) if parent else None
        c_le = le_by_erwin_id.get(child) if child else None
        if not p_le or not c_le:
            warnings.append(
                f"relationship {_name_of(obj) or obj.id!r} references an unknown entity; skipped"
            )
            continue

        rtype = (obj.props.get("Type") or "").strip().lower()
        disc_ref = obj.refs.get("Subtype_Discriminator_Ref")
        if "subtype" in rtype or disc_ref:
            # supertype = parent, subtype = child; collect for a Category below
            subtype_children_by_parent.setdefault(parent, []).append(child)
            if disc_ref and parent not in subtype_discriminator_by_parent:
                subtype_discriminator_by_parent[parent] = disc_ref
            continue

        # FK attribute mapping: prefer a child attribute whose Parent_Relationship_Ref is
        # this relationship; else fall back to the relationship's own Attribute_Ref.
        from_attr = _fk_child_attr(index, obj.id, child, attr_le_by_erwin_id)
        to_bk = next((a.id for a in p_le.attributes if a.role == "business_key"), None)
        model.add(
            Relationship(
                id=new_ulid(),
                name=_name_of(obj) or f"{c_le.name}_has_{p_le.name}",
                definition=obj.props.get("Definition"),
                **{
                    "from": RelationshipEnd(
                        entity=c_le.id, attributes=[from_attr] if from_attr else []
                    )
                },
                to=RelationshipEnd(entity=p_le.id, attributes=[to_bk] if to_bk else []),
                cardinality=_cardinality(obj.props.get("Cardinality")),
                identifying="identifying" in rtype,
                verb_phrase=obj.props.get("Parent_To_Child_Verb_Phrase"),
                inverse_verb_phrase=obj.props.get("Child_To_Parent_Verb_Phrase"),
            )
        )

    # emit a Category per supertype that had subtype relationships
    for parent_id, child_ids in subtype_children_by_parent.items():
        sup = le_by_erwin_id.get(parent_id)
        subs = [le_by_erwin_id[c].id for c in child_ids if c in le_by_erwin_id]
        if sup is None or not subs:
            continue
        disc_erwin = subtype_discriminator_by_parent.get(parent_id)
        disc = attr_le_by_erwin_id.get(disc_erwin) if disc_erwin else None
        # The default materialization (single_table) needs a discriminator; when erwin
        # gave none, use table_per_subtype so the model still validates.
        model.add(
            Category(
                id=new_ulid(),
                name=f"{sup.name}_category",
                supertype=sup.id,
                subtypes=subs,
                discriminator=disc,
                materialization="single_table" if disc else "table_per_subtype",
            )
        )


def _fk_child_attr(index, rel_id, child_entity_id, attr_le_by_erwin_id) -> str | None:
    """The child attribute that migrated from this relationship (its
    Parent_Relationship_Ref points at rel_id), mapped to the Modelith attribute id."""
    child = index.get(child_entity_id)
    if child is None:
        return None
    for a in child.children:
        if a.kind == "Attribute" and a.refs.get("Parent_Relationship_Ref") == rel_id:
            mapped = attr_le_by_erwin_id.get(a.id)
            if mapped:
                return mapped
    return None


def _build_subject_areas(ordered, model, ce_id_by_entity, warnings) -> None:
    for obj in ordered:
        if obj.kind != "Subject_Area":
            continue
        sname = _name_of(obj)
        if not sname:
            continue
        member_guids: list[str] = []
        for key in ("User_Attached_Objects_Ref", "Auto_Attached_Objects_Ref"):
            member_guids.extend(obj.ref_arrays.get(key, []))
        members: list[str] = []
        seen: set[str] = set()
        for guid in member_guids:
            ce = ce_id_by_entity.get(guid)  # only keep refs that resolve to an entity
            if ce and ce not in seen:
                seen.add(ce)
                members.append(ce)
        model.add(
            SubjectArea(
                id=new_ulid(),
                name=sname,
                definition=obj.props.get("Definition"),
                members=members,
            )
        )


def _open(source: str | Path):
    """Return (stream, size_hint_bytes|None) for iterparse. Accepts a path, an XML string,
    or an open stream."""
    if isinstance(source, Path):
        return str(source), source.stat().st_size if source.exists() else None
    if isinstance(source, str):
        # a filesystem path vs raw XML: a path won't start with '<' and will exist
        p = Path(source)
        if len(source) < 4096 and not source.lstrip().startswith("<") and p.exists():
            return source, p.stat().st_size
        return StringIO(source), len(source.encode("utf-8"))
    if isinstance(source, bytes):
        return BytesIO(source), len(source)
    return source, None  # already a stream


def import_erwin_file(path: str | Path, **kw) -> ErwinImport:
    return import_erwin(Path(path), **kw)


def _titleize(name: str) -> str:
    return "".join(p.capitalize() for p in name.replace(" ", "_").split("_"))


def _ident(name: str) -> str:
    """A dbt-ish logical identifier from an erwin display name: lowercase, spaces to
    underscores. 'Counterparty' -> 'counterparty', 'Trade Event' -> 'trade_event'."""
    return name.strip().lower().replace(" ", "_")
