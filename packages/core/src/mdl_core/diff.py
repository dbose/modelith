"""Semantic model-to-model diff, keyed by ULID (plan §J).

Two `Model` objects in, a dataclass tree out. No git, no dbt, no HTTP — the
callers (the server's diff API, `mdl diff`, the PR body) supply their own sides.

**Why ULID keying is the point.** erwin's Complete Compare matches objects by
NAME, so renaming `Counterparty` to `Legal Entity` reports an entity removed plus
an entity added, with every attribute duplicated on both sides and a reviewer left
to work out they are the same thing. Modelith's ULIDs are immutable and file-borne
(`rename_entity` mutates `name:` in place; `id:` never moves), so the same rename
is ONE cosmetic field change and the attributes underneath stay quiet.

The severity vocabulary is shared with drift (`mdl_core.severity`) so "breaking"
means the same thing in a diff, in CI and in the drift comment. `ChangeKind` is
NOT shared with `DriftKind`: that enum is manifest-shaped (`unmanaged_model`,
`contract_disabled`) and keyed by string names, which is exactly the name-based
identity this module exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mdl_core.ir import Attribute, LogicalEntity, Model
from mdl_core.severity import ChangeSeverity, _is_narrowing


class ChangeType(str, Enum):
    added = "added"
    removed = "removed"
    modified = "modified"


class ChangeKind(str, Enum):
    # object level
    object_added = "object_added"
    object_removed = "object_removed"
    object_renamed = "object_renamed"
    # meaning (route A)
    definition_changed = "definition_changed"
    synonym_added = "synonym_added"
    synonym_removed = "synonym_removed"
    stewardship_changed = "stewardship_changed"
    subject_area_changed = "subject_area_changed"
    subject_area_members_changed = "subject_area_members_changed"
    alignment_added = "alignment_added"
    alignment_removed = "alignment_removed"
    alignment_promoted = "alignment_promoted"
    # structure (route B)
    attribute_added = "attribute_added"
    attribute_removed = "attribute_removed"
    attribute_renamed = "attribute_renamed"
    attribute_domain_changed = "attribute_domain_changed"
    attribute_role_changed = "attribute_role_changed"
    attribute_nullability_changed = "attribute_nullability_changed"
    key_group_members_changed = "key_group_members_changed"
    key_group_type_changed = "key_group_type_changed"
    relationship_endpoint_changed = "relationship_endpoint_changed"
    relationship_cardinality_changed = "relationship_cardinality_changed"
    relationship_optionality_changed = "relationship_optionality_changed"
    relationship_identifying_changed = "relationship_identifying_changed"
    pattern_changed = "pattern_changed"
    unmanaged_changed = "unmanaged_changed"
    subtype_membership_changed = "subtype_membership_changed"
    category_membership_changed = "category_membership_changed"
    category_materialization_changed = "category_materialization_changed"
    domain_base_type_changed = "domain_base_type_changed"
    domain_values_changed = "domain_values_changed"
    # implementation / config
    physical_table_changed = "physical_table_changed"
    project_config_changed = "project_config_changed"
    term_map_changed = "term_map_changed"
    udp_changed = "udp_changed"
    other_field_changed = "other_field_changed"


S = ChangeSeverity
_SEVERITY: dict[ChangeKind, ChangeSeverity] = {
    ChangeKind.object_added: S.additive,
    ChangeKind.object_removed: S.breaking,
    ChangeKind.object_renamed: S.cosmetic,
    ChangeKind.definition_changed: S.cosmetic,
    ChangeKind.synonym_added: S.additive,
    ChangeKind.synonym_removed: S.cosmetic,
    ChangeKind.stewardship_changed: S.cosmetic,
    ChangeKind.subject_area_changed: S.cosmetic,
    ChangeKind.subject_area_members_changed: S.additive,
    ChangeKind.alignment_added: S.additive,
    ChangeKind.alignment_removed: S.cosmetic,
    ChangeKind.alignment_promoted: S.cosmetic,
    ChangeKind.attribute_added: S.additive,
    ChangeKind.attribute_removed: S.breaking,
    ChangeKind.attribute_renamed: S.breaking,
    ChangeKind.attribute_domain_changed: S.breaking,
    ChangeKind.attribute_role_changed: S.breaking,
    ChangeKind.attribute_nullability_changed: S.additive,  # refined below
    ChangeKind.key_group_members_changed: S.breaking,
    ChangeKind.key_group_type_changed: S.breaking,
    ChangeKind.relationship_endpoint_changed: S.breaking,
    ChangeKind.relationship_cardinality_changed: S.breaking,
    ChangeKind.relationship_optionality_changed: S.additive,
    ChangeKind.relationship_identifying_changed: S.breaking,
    ChangeKind.pattern_changed: S.breaking,
    ChangeKind.unmanaged_changed: S.breaking,
    ChangeKind.subtype_membership_changed: S.breaking,
    ChangeKind.category_membership_changed: S.breaking,
    ChangeKind.category_materialization_changed: S.breaking,
    ChangeKind.domain_base_type_changed: S.breaking,
    ChangeKind.domain_values_changed: S.additive,
    ChangeKind.physical_table_changed: S.breaking,
    ChangeKind.project_config_changed: S.cosmetic,
    ChangeKind.term_map_changed: S.cosmetic,
    ChangeKind.udp_changed: S.cosmetic,
    ChangeKind.other_field_changed: S.cosmetic,
}

_LABELS: dict[ChangeKind, str] = {
    ChangeKind.object_renamed: "Renamed",
    ChangeKind.definition_changed: "Definition changed",
    ChangeKind.synonym_added: "Synonym added",
    ChangeKind.synonym_removed: "Synonym removed",
    ChangeKind.stewardship_changed: "Stewardship changed",
    ChangeKind.subject_area_changed: "Moved to another subject area",
    ChangeKind.subject_area_members_changed: "Members changed",
    ChangeKind.alignment_added: "Ontology alignment proposed",
    ChangeKind.alignment_removed: "Ontology alignment removed",
    ChangeKind.alignment_promoted: "Ontology alignment accepted",
    ChangeKind.attribute_added: "Attribute added",
    ChangeKind.attribute_removed: "Attribute removed",
    ChangeKind.attribute_renamed: "Attribute renamed",
    ChangeKind.attribute_domain_changed: "Attribute domain changed",
    ChangeKind.attribute_role_changed: "Attribute role changed",
    ChangeKind.attribute_nullability_changed: "Attribute nullability changed",
    ChangeKind.key_group_members_changed: "Key columns changed",
    ChangeKind.key_group_type_changed: "Key type changed",
    ChangeKind.relationship_endpoint_changed: "Relationship endpoint changed",
    ChangeKind.relationship_cardinality_changed: "Cardinality changed",
    ChangeKind.relationship_optionality_changed: "Optionality changed",
    ChangeKind.relationship_identifying_changed: "Identifying changed",
    ChangeKind.pattern_changed: "Pattern changed",
    ChangeKind.unmanaged_changed: "Management changed",
    ChangeKind.subtype_membership_changed: "Subtypes changed",
    ChangeKind.category_membership_changed: "Category membership changed",
    ChangeKind.category_materialization_changed: "Materialization changed",
    ChangeKind.domain_base_type_changed: "Base type changed",
    ChangeKind.domain_values_changed: "Allowed values changed",
    ChangeKind.physical_table_changed: "Physical table changed",
    ChangeKind.project_config_changed: "Project setting changed",
    ChangeKind.term_map_changed: "KG mapping changed",
    ChangeKind.udp_changed: "Custom property changed",
    ChangeKind.other_field_changed: "Changed",
}

# Fields that must never be compared:
#   realised_by  - declared in the IR but NEVER populated on load (where_used
#                  computes the reverse traversal at call time), so comparing it
#                  reports phantom changes.
#   ontology     - the legacy block, folded into ontology_refs by a model
#                  validator on load; comparing both double-reports every
#                  migrated alignment.
#   kind         - a Literal, never differs.
_DERIVED_FIELDS = {"realised_by", "ontology", "kind", "id", "attributes"}

_HUMAN_KIND = {
    "conceptual_entity": "conceptual entity",
    "logical_entity": "logical entity",
    "subject_area": "subject area",
    "key_group": "key group",
    "code_set": "value set",
    "physical_table": "physical table",
}


@dataclass
class FieldChange:
    field: str
    kind: ChangeKind
    severity: ChangeSeverity
    label: str
    detail: str = ""
    before: Any = None
    after: Any = None

    def to_doc(self) -> dict:
        return {
            "field": self.field,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "label": self.label,
            "detail": self.detail,
            "before": self.before,
            "after": self.after,
        }


@dataclass
class ObjectChange:
    ulid: str
    object_kind: str
    change: ChangeType
    name_before: str | None = None
    name_after: str | None = None
    fields: list[FieldChange] = field(default_factory=list)
    children: list[ObjectChange] = field(default_factory=list)
    path: str | None = None  # filled by the server; core has no repo

    @property
    def renamed(self) -> bool:
        return any(f.kind is ChangeKind.object_renamed for f in self.fields)

    @property
    def severity(self) -> ChangeSeverity:
        best = ChangeSeverity.cosmetic
        for f in self.fields:
            if f.severity.rank > best.rank:
                best = f.severity
        for c in self.children:
            if c.severity.rank > best.rank:
                best = c.severity
        return best

    @property
    def display_name(self) -> str:
        return self.name_before or self.name_after or self.ulid

    def to_doc(self) -> dict:
        return {
            "ulid": self.ulid,
            "object_kind": self.object_kind,
            "object_kind_label": _HUMAN_KIND.get(self.object_kind, self.object_kind),
            "change": self.change.value,
            "name_before": self.name_before,
            "name_after": self.name_after,
            "renamed": self.renamed,
            "severity": self.severity.value,
            "path": self.path,
            "fields": [f.to_doc() for f in self.fields],
            "children": [c.to_doc() for c in self.children],
        }


@dataclass
class ModelDiff:
    objects: list[ObjectChange] = field(default_factory=list)
    config: list[FieldChange] = field(default_factory=list)
    base_label: str = "base"
    head_label: str = "head"

    def __bool__(self) -> bool:
        return bool(self.objects or self.config)

    @property
    def max_severity(self) -> ChangeSeverity | None:
        sevs = [o.severity for o in self.objects] + [f.severity for f in self.config]
        return max(sevs, key=lambda s: s.rank) if sevs else None

    @property
    def has_breaking(self) -> bool:
        return any(s is ChangeSeverity.breaking for s in (o.severity for o in self.objects))

    def touched_ulids(self) -> set[str]:
        out = {o.ulid for o in self.objects}
        for o in self.objects:
            out.update(c.ulid for c in o.children)
        return out

    def counts(self) -> dict[str, int]:
        c = {
            "added": 0, "removed": 0, "modified": 0,
            "breaking": 0, "additive": 0, "cosmetic": 0, "objects": len(self.objects),
        }
        for o in self.objects:
            c[o.change.value] += 1
            for f in o.fields:
                c[f.severity.value] = c.get(f.severity.value, 0) + 1
            for ch in o.children:
                for f in ch.fields:
                    c[f.severity.value] = c.get(f.severity.value, 0) + 1
        return c

    def to_doc(self) -> dict:
        ms = self.max_severity
        return {
            "base": {"label": self.base_label},
            "head": {"label": self.head_label},
            "counts": self.counts(),
            "max_severity": ms.value if ms else None,
            "has_breaking": self.has_breaking,
            "objects": [o.to_doc() for o in self.objects],
            "config": [f.to_doc() for f in self.config],
        }


# --- the walk -------------------------------------------------------------------

_TABLES = (
    "subject_areas", "conceptual_entities", "terms", "domains", "code_sets",
    "logical_entities", "relationships", "key_groups", "categories", "physical_tables",
)


def _kind_of(obj) -> str:
    k = getattr(obj, "kind", None)
    return k.value if hasattr(k, "value") else (k or type(obj).__name__.lower())


def _name_of(obj) -> str | None:
    return getattr(obj, "name", None)


def _fields_of(obj) -> dict[str, Any]:
    """Comparable scalar/collection fields, with the derived ones excluded."""
    dumped = obj.model_dump(by_alias=True)
    return {k: v for k, v in dumped.items() if k not in _DERIVED_FIELDS}


def _kind_for_field(obj_kind: str, name: str) -> ChangeKind:
    direct = {
        "definition": ChangeKind.definition_changed,
        "stewardship": ChangeKind.stewardship_changed,
        "subject_area": ChangeKind.subject_area_changed,
        "pattern": ChangeKind.pattern_changed,
        "unmanaged": ChangeKind.unmanaged_changed,
        "subtypes": ChangeKind.subtype_membership_changed,
        "cardinality": ChangeKind.relationship_cardinality_changed,
        "optionality": ChangeKind.relationship_optionality_changed,
        "identifying": ChangeKind.relationship_identifying_changed,
        "materialization": ChangeKind.category_materialization_changed,
        "base_type": ChangeKind.domain_base_type_changed,
        "allowed_values": ChangeKind.domain_values_changed,
        "values": ChangeKind.domain_values_changed,
        "term_map": ChangeKind.term_map_changed,
        "udp": ChangeKind.udp_changed,
        "type": ChangeKind.key_group_type_changed,
    }
    if name in direct:
        return direct[name]
    if name == "members":
        return (
            ChangeKind.subject_area_members_changed
            if obj_kind == "subject_area"
            else ChangeKind.key_group_members_changed
        )
    if name in ("from", "to", "supertype", "entity", "realises"):
        return (
            ChangeKind.relationship_endpoint_changed
            if obj_kind == "relationship"
            else ChangeKind.category_membership_changed
        )
    return ChangeKind.other_field_changed


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) if v else "none"
    return str(v)


def _diff_object(base, head, obj_kind: str) -> list[FieldChange]:
    out: list[FieldChange] = []
    bf, hf = _fields_of(base), _fields_of(head)

    if _name_of(base) != _name_of(head):
        out.append(
            FieldChange(
                field="name",
                kind=ChangeKind.object_renamed,
                severity=_SEVERITY[ChangeKind.object_renamed],
                label=_LABELS[ChangeKind.object_renamed],
                detail=f"{_name_of(base)} → {_name_of(head)}",
                before=_name_of(base),
                after=_name_of(head),
            )
        )

    # synonyms diff as add/remove rather than one opaque list change
    if "synonyms" in bf or "synonyms" in hf:
        b, h = set(bf.get("synonyms") or []), set(hf.get("synonyms") or [])
        for kind, delta in (
            (ChangeKind.synonym_added, sorted(h - b)),
            (ChangeKind.synonym_removed, sorted(b - h)),
        ):
            if delta:
                out.append(
                    FieldChange(
                        field="synonyms",
                        kind=kind,
                        severity=_SEVERITY[kind],
                        label=_LABELS[kind],
                        detail=", ".join(delta),
                        before=sorted(b),
                        after=sorted(h),
                    )
                )

    if "ontology_refs" in bf or "ontology_refs" in hf:
        out.extend(_diff_alignments(bf.get("ontology_refs") or [], hf.get("ontology_refs") or []))

    skip = {"name", "synonyms", "ontology_refs"}
    for key in sorted(set(bf) | set(hf)):
        if key in skip:
            continue
        b, h = bf.get(key), hf.get(key)
        if b == h:
            continue
        kind = _kind_for_field(obj_kind, key)
        sev = _SEVERITY.get(kind, ChangeSeverity.cosmetic)
        # nullability is directional: tightening breaks existing rows, relaxing does not
        if key == "nullable":
            kind = ChangeKind.attribute_nullability_changed
            sev = ChangeSeverity.breaking if (b and not h) else ChangeSeverity.additive
        out.append(
            FieldChange(
                field=key,
                kind=kind,
                severity=sev,
                label=_LABELS.get(kind, _LABELS[ChangeKind.other_field_changed]),
                detail=f"{_fmt(b)} → {_fmt(h)}",
                before=b,
                after=h,
            )
        )
    return out


def _diff_alignments(base: list, head: list) -> list[FieldChange]:
    out: list[FieldChange] = []
    b = {r.get("uri"): r for r in base if isinstance(r, dict)}
    h = {r.get("uri"): r for r in head if isinstance(r, dict)}
    for uri in sorted(set(h) - set(b)):
        k = ChangeKind.alignment_added
        out.append(
            FieldChange(
                field="ontology_refs",
                kind=k,
                severity=_SEVERITY[k],
                label=_LABELS[k],
                detail=f"{uri} ({h[uri].get('predicate', 'skos:closeMatch')})",
                before=None,
                after=uri,
            )
        )
    for uri in sorted(set(b) - set(h)):
        k = ChangeKind.alignment_removed
        out.append(
            FieldChange(
                field="ontology_refs", kind=k, severity=_SEVERITY[k],
                label=_LABELS[k], detail=str(uri), before=uri, after=None,
            )
        )
    for uri in sorted(set(b) & set(h)):
        if b[uri].get("status") != h[uri].get("status"):
            k = ChangeKind.alignment_promoted
            out.append(
                FieldChange(
                    field="ontology_refs", kind=k, severity=_SEVERITY[k],
                    label=_LABELS[k],
                    detail=f"{uri}: {b[uri].get('status')} → {h[uri].get('status')}",
                    before=b[uri].get("status"), after=h[uri].get("status"),
                )
            )
    return out


def _attr_change(a_base: Attribute | None, a_head: Attribute | None) -> ObjectChange | None:
    if a_base is None and a_head is None:
        return None
    if a_base is None:
        k = ChangeKind.attribute_added
        return ObjectChange(
            ulid=a_head.id, object_kind="attribute", change=ChangeType.added,
            name_after=a_head.name,
            fields=[FieldChange(
                field="attribute", kind=k, severity=_SEVERITY[k], label=_LABELS[k],
                detail=f"{a_head.name} ({a_head.domain or 'no domain'})",
                before=None, after=a_head.name,
            )],
        )
    if a_head is None:
        k = ChangeKind.attribute_removed
        return ObjectChange(
            ulid=a_base.id, object_kind="attribute", change=ChangeType.removed,
            name_before=a_base.name,
            fields=[FieldChange(
                field="attribute", kind=k, severity=_SEVERITY[k], label=_LABELS[k],
                detail=f"{a_base.domain or 'no domain'}"
                       f"{' · nullable' if a_base.nullable else ''}",
                before=a_base.name, after=None,
            )],
        )

    fields = _diff_object(a_base, a_head, "attribute")
    # re-label attribute-scoped kinds so the sentence names the attribute
    for f in fields:
        if f.kind is ChangeKind.object_renamed:
            f.kind = ChangeKind.attribute_renamed
            f.severity = _SEVERITY[ChangeKind.attribute_renamed]
            f.label = _LABELS[ChangeKind.attribute_renamed]
        elif f.field == "domain":
            f.kind = ChangeKind.attribute_domain_changed
            f.severity = ChangeSeverity.breaking
            f.label = _LABELS[ChangeKind.attribute_domain_changed]
            if _is_narrowing(str(f.before or "").upper(), str(f.after or "").upper()):
                f.detail += "  (narrowing)"
        elif f.field == "role":
            f.kind = ChangeKind.attribute_role_changed
            f.severity = _SEVERITY[ChangeKind.attribute_role_changed]
            f.label = _LABELS[ChangeKind.attribute_role_changed]
    if not fields:
        return None
    return ObjectChange(
        ulid=a_head.id, object_kind="attribute", change=ChangeType.modified,
        name_before=a_base.name, name_after=a_head.name, fields=fields,
    )


def _diff_attributes(base: LogicalEntity, head: LogicalEntity) -> list[ObjectChange]:
    b = {a.id: a for a in base.attributes}
    h = {a.id: a for a in head.attributes}
    order = list(h) + [i for i in b if i not in h]
    out = []
    for aid in order:
        ch = _attr_change(b.get(aid), h.get(aid))
        if ch:
            out.append(ch)
    return out


def diff_models(
    base: Model | None,
    head: Model | None,
    *,
    base_label: str = "base",
    head_label: str = "head",
) -> ModelDiff:
    """Compare two models, keyed by ULID.

    `None` on either side means the model did not exist at that ref — everything
    on the other side is added or removed, rather than an exception."""
    diff = ModelDiff(base_label=base_label, head_label=head_label)

    for table in _TABLES:
        b = getattr(base, table, {}) if base else {}
        h = getattr(head, table, {}) if head else {}
        for ulid in sorted(set(h) - set(b), key=lambda u: (_name_of(h[u]) or "", u)):
            obj = h[ulid]
            k = ChangeKind.object_added
            diff.objects.append(
                ObjectChange(
                    ulid=ulid, object_kind=_kind_of(obj), change=ChangeType.added,
                    name_after=_name_of(obj),
                    fields=[FieldChange(
                        field="object", kind=k, severity=_SEVERITY[k],
                        label=f"{_HUMAN_KIND.get(_kind_of(obj), _kind_of(obj))} added",
                        detail=_name_of(obj) or "", before=None, after=_name_of(obj),
                    )],
                )
            )
        for ulid in sorted(set(b) - set(h), key=lambda u: (_name_of(b[u]) or "", u)):
            obj = b[ulid]
            k = ChangeKind.object_removed
            diff.objects.append(
                ObjectChange(
                    ulid=ulid, object_kind=_kind_of(obj), change=ChangeType.removed,
                    name_before=_name_of(obj),
                    fields=[FieldChange(
                        field="object", kind=k, severity=_SEVERITY[k],
                        label=f"{_HUMAN_KIND.get(_kind_of(obj), _kind_of(obj))} removed",
                        detail=_name_of(obj) or "", before=_name_of(obj), after=None,
                    )],
                )
            )
        for ulid in sorted(set(b) & set(h), key=lambda u: (_name_of(h[u]) or "", u)):
            ob, oh = b[ulid], h[ulid]
            fields = _diff_object(ob, oh, _kind_of(oh))
            children = (
                _diff_attributes(ob, oh) if table == "logical_entities" else []
            )
            if fields or children:
                diff.objects.append(
                    ObjectChange(
                        ulid=ulid, object_kind=_kind_of(oh), change=ChangeType.modified,
                        name_before=_name_of(ob), name_after=_name_of(oh),
                        fields=fields, children=children,
                    )
                )

    diff.config = _diff_config(base, head)
    # breaking first, then by name, so the worst news is at the top
    diff.objects.sort(key=lambda o: (-o.severity.rank, o.display_name))
    return diff


def _diff_config(base: Model | None, head: Model | None) -> list[FieldChange]:
    if base is None or head is None:
        return []
    bc = base.config.model_dump()
    hc = head.config.model_dump()
    out = []
    for key in sorted(set(bc) | set(hc)):
        if bc.get(key) == hc.get(key):
            continue
        k = ChangeKind.project_config_changed
        out.append(
            FieldChange(
                field=key, kind=k, severity=_SEVERITY[k],
                label=f"Project setting {key} changed",
                detail=f"{_fmt(bc.get(key))} → {_fmt(hc.get(key))}",
                before=bc.get(key), after=hc.get(key),
            )
        )
    return out
