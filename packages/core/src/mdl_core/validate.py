"""Validator (spec §2.4, §3.1).

Runs in order: schema validation (done at load via pydantic), referential
integrity of ULIDs, naming lint, ontology alignment rules. Semantic joinability
(§6) and pattern conformance are stubbed with clear extension points for later
milestones.

Exit codes distinguish error from warning (spec §2.4) via DiagnosticSet.
"""

from __future__ import annotations

from mdl_core.diagnostics import Diagnostic, DiagnosticSet, Severity
from mdl_core.ir import (
    ConceptualEntity,
    Model,
    Term,
)
from mdl_core.naming import lint as naming_lint

# Ontology layer ordering for the upward-alignment rule (spec §3.1 rule 1).
_LAYER_ORDER = {"industry": 0, "core": 1, "domain": 2, "specialised": 3}


def validate(model: Model) -> DiagnosticSet:
    diags = DiagnosticSet()
    _check_referential_integrity(model, diags)
    _check_key_groups(model, diags)
    _check_categories(model, diags)
    _check_ontology_layers(model, diags)
    _check_proposed_alignments(model, diags)
    _check_term_maps(model, diags)
    naming_diags, _ = naming_lint(model)
    diags.extend(naming_diags)
    return diags


def _iri_ok(value: str) -> bool:
    """A term-map IRI is acceptable if it's an absolute URL or a prefixed CURIE
    (``prefix:local``). Prefix resolution against the registry happens at emit time;
    here we only reject obviously malformed values."""
    if value.startswith(("http://", "https://")):
        return True
    # a CURIE: exactly one colon, non-empty prefix and local part, no whitespace
    if ":" in value and " " not in value:
        prefix, _, local = value.partition(":")
        return bool(prefix) and bool(local)
    return False


def _template_columns(template: str) -> list[str]:
    """The ``{column}`` placeholders in an rr:template."""
    import re

    return re.findall(r"\{([^}]+)\}", template)


def _check_term_maps(model: Model, diags: DiagnosticSet) -> None:
    """Validate R2RML term-map overrides (spec §3.3, KG mapping):
    subject-template columns must exist on the entity, and IRIs must be well-formed."""
    for le in model.logical_entities.values():
        tm = le.term_map
        if tm is None:
            continue
        if tm.class_iri and not _iri_ok(tm.class_iri):
            diags.add(
                Diagnostic(
                    code="MDL-R201",
                    severity=Severity.error,
                    message=(
                        f"entity {le.name!r} term_map.class_iri {tm.class_iri!r} "
                        f"is not a valid IRI"
                    ),
                    path=le.id,
                )
            )
        if tm.subject_template:
            attr_names = {a.name for a in le.attributes}
            for col in _template_columns(tm.subject_template):
                if col not in attr_names:
                    diags.add(
                        Diagnostic(
                            code="MDL-R202",
                            severity=Severity.error,
                            message=(
                                f"entity {le.name!r} term_map.subject_template references "
                                f"column {col!r} which is not an attribute of the entity"
                            ),
                            path=le.id,
                        )
                    )
        for attr in le.attributes:
            atm = attr.term_map
            if atm is None:
                continue
            if atm.predicate_iri and not _iri_ok(atm.predicate_iri):
                diags.add(
                    Diagnostic(
                        code="MDL-R201",
                        severity=Severity.error,
                        message=(
                            f"attribute {attr.name!r} term_map.predicate_iri "
                            f"{atm.predicate_iri!r} is not a valid IRI"
                        ),
                        path=attr.id,
                    )
                )
            if atm.datatype and not _iri_ok(atm.datatype):
                diags.add(
                    Diagnostic(
                        code="MDL-R203",
                        severity=Severity.error,
                        message=(
                            f"attribute {attr.name!r} term_map.datatype "
                            f"{atm.datatype!r} is not a valid IRI"
                        ),
                        path=attr.id,
                    )
                )


def _check_referential_integrity(model: Model, diags: DiagnosticSet) -> None:
    ids = model.all_ulids()

    def ref(target: str | None, code: str, ctx: str, owner: str | None = None) -> None:
        if target is not None and target not in ids:
            diags.add(
                Diagnostic(
                    code=code,
                    severity=Severity.error,
                    message=f"{ctx} references unknown ULID {target!r}",
                    # point at the object DECLARING the bad ref, so editors can
                    # map the diagnostic to the file the fix belongs in
                    path=owner or target,
                )
            )

    for ce in model.conceptual_entities.values():
        ref(ce.subject_area, "MDL-E101", f"conceptual entity {ce.name!r} subject_area", ce.id)
        for rb in ce.realised_by:
            ref(rb, "MDL-E101", f"conceptual entity {ce.name!r} realised_by", ce.id)

    for le in model.logical_entities.values():
        ref(le.realises, "MDL-E102", f"logical entity {le.name!r} realises", le.id)
        for st in le.subtypes:
            ref(st, "MDL-E102", f"logical entity {le.name!r} subtype", le.id)

    for rel in model.relationships.values():
        ref(rel.from_.entity, "MDL-E103", f"relationship {rel.name!r} from.entity", rel.id)
        ref(rel.to.entity, "MDL-E103", f"relationship {rel.name!r} to.entity", rel.id)
        for a in rel.from_.attributes:
            ref(a, "MDL-E103", f"relationship {rel.name!r} from.attributes", rel.id)
        for a in rel.to.attributes:
            ref(a, "MDL-E103", f"relationship {rel.name!r} to.attributes", rel.id)

    for kg in model.key_groups.values():
        ref(kg.entity, "MDL-E105", f"key group {kg.name!r} entity", kg.id)
        for m in kg.members:
            ref(m, "MDL-E105", f"key group {kg.name!r} member", kg.id)

    # a domain's value_set must resolve to a known CodeSet (by name)
    code_set_names = {cs.name for cs in model.code_sets.values()}
    for dom in model.domains.values():
        if dom.value_set and dom.value_set not in code_set_names:
            diags.add(
                Diagnostic(
                    code="MDL-E109",
                    severity=Severity.error,
                    message=f"domain {dom.name!r} value_set references unknown code set "
                    f"{dom.value_set!r}",
                    path=dom.id,
                )
            )

    for cat in model.categories.values():
        ref(cat.supertype, "MDL-E110", f"category {cat.name!r} supertype", cat.id)
        for st in cat.subtypes:
            ref(st, "MDL-E110", f"category {cat.name!r} subtype", cat.id)
        ref(cat.discriminator, "MDL-E110", f"category {cat.name!r} discriminator", cat.id)

    for pt in model.physical_tables.values():
        ref(pt.realises, "MDL-E104", f"physical table {pt.name!r} realises", pt.id)
        for col in pt.columns:
            ref(col.realises, "MDL-E104", f"physical table {pt.name!r} column {col.name!r}", pt.id)


def _check_key_groups(model: Model, diags: DiagnosticSet) -> None:
    """Key-group semantics (P0-pre): members belong to the owning entity, keys are
    non-empty, and each entity has at most one primary key."""
    pk_count: dict[str, int] = {}
    for kg in model.key_groups.values():
        le = model.logical_entities.get(kg.entity)
        if le is None:
            continue  # dangling entity ref already reported by referential check
        entity_attr_ids = {a.id for a in le.attributes}
        for m in kg.members:
            if m in model.all_ulids() and m not in entity_attr_ids:
                diags.add(
                    Diagnostic(
                        code="MDL-E106",
                        severity=Severity.error,
                        message=(
                            f"key group {kg.name!r} member {m!r} is not an attribute "
                            f"of its entity {le.name!r}"
                        ),
                        path=kg.id,
                    )
                )
        if not kg.members:
            diags.add(
                Diagnostic(
                    code="MDL-W107",
                    severity=Severity.warning,
                    message=f"key group {kg.name!r} has no members",
                    path=kg.id,
                )
            )
        if kg.type == "pk":
            pk_count[kg.entity] = pk_count.get(kg.entity, 0) + 1

    for entity_id, n in pk_count.items():
        if n > 1:
            le = model.logical_entities.get(entity_id)
            diags.add(
                Diagnostic(
                    code="MDL-E108",
                    severity=Severity.error,
                    message=f"entity {le.name if le else entity_id!r} has {n} primary keys (max 1)",
                    path=entity_id,
                )
            )


def _check_categories(model: Model, diags: DiagnosticSet) -> None:
    """Subtype/supertype (category) semantics: discriminator must be a supertype
    attribute; subtypes must differ from the supertype; single_table needs a
    discriminator to select the subtype."""
    for cat in model.categories.values():
        sup = model.logical_entities.get(cat.supertype)
        if sup is None:
            continue  # dangling supertype already reported by referential check
        sup_attr_ids = {a.id for a in sup.attributes}
        if cat.discriminator and cat.discriminator not in sup_attr_ids:
            if cat.discriminator in model.all_ulids():
                diags.add(
                    Diagnostic(
                        code="MDL-E111",
                        severity=Severity.error,
                        message=f"category {cat.name!r} discriminator is not an attribute "
                        f"of its supertype {sup.name!r}",
                        path=cat.id,
                    )
                )
        if cat.supertype in cat.subtypes:
            diags.add(
                Diagnostic(
                    code="MDL-E112",
                    severity=Severity.error,
                    message=f"category {cat.name!r} lists its supertype as a subtype",
                    path=cat.id,
                )
            )
        if cat.materialization == "single_table" and not cat.discriminator:
            diags.add(
                Diagnostic(
                    code="MDL-W113",
                    severity=Severity.warning,
                    message=f"category {cat.name!r} is single_table but has no "
                    f"discriminator to select the subtype",
                    path=cat.id,
                )
            )


def _check_ontology_layers(model: Model, diags: DiagnosticSet) -> None:
    """Rule 1: an object may only align upward to an adjacent-or-higher layer."""
    objs: list[ConceptualEntity | Term] = [
        *model.conceptual_entities.values(),
        *model.terms.values(),
    ]
    for obj in objs:
        layer = obj.ontology_layer
        if layer is None:
            continue
        own_rank = _LAYER_ORDER.get(layer)
        if own_rank is None:
            continue
        has_alignment = any(r.uri for r in obj.ontology_refs)
        # `core` term must have an industry alignment or explicit exemption (rule 3).
        if layer == "core" and not has_alignment and not obj.no_industry_equivalent:
            diags.add(
                Diagnostic(
                    code="MDL-E202",
                    severity=Severity.error,
                    message=(
                        f"core term {obj.name!r} has no industry alignment and no "
                        f"reviewed no_industry_equivalent=true"
                    ),
                    path=obj.id,
                )
            )
        # If it aligns to a prefixed IRI we cannot resolve the target layer here
        # (that needs the ontology package, M4). We validate the intra-model case
        # where alignment carries a declared target layer via naming convention is
        # deferred; for now enforce that non-industry layers declare an alignment.
        if own_rank > 0 and not has_alignment and not obj.no_industry_equivalent:
            diags.add(
                Diagnostic(
                    code="MDL-W203",
                    severity=Severity.warning,
                    message=(
                        f"{obj.kind.value} {obj.name!r} in layer {layer!r} declares "
                        f"no upward alignment"
                    ),
                    path=obj.id,
                )
            )


def check_rename_orphans(model: Model, ulid: str) -> list[str]:
    """Property-4 support: after a ULID rename, no downstream ref should be orphaned.
    Returns a list of dangling references to `ulid` — used by tests to assert zero."""
    orphans: list[str] = []
    for le in model.logical_entities.values():
        if le.realises == ulid:
            orphans.append(f"logical {le.name} realises")
    for rel in model.relationships.values():
        if rel.from_.entity == ulid or rel.to.entity == ulid:
            orphans.append(f"relationship {rel.name}")
    return orphans


def _check_proposed_alignments(model: Model, diags: DiagnosticSet) -> None:
    """SME-proposed ontology alignments are warnings until an architect promotes
    them to accepted (collaboration model §5.1)."""
    objs = [*model.conceptual_entities.values(), *model.terms.values()]
    for o in objs:
        for ref in o.ontology_refs:
            if ref.status == "proposed" and ref.uri:
                diags.add(
                    Diagnostic(
                        code="MDL-W206",
                        severity=Severity.warning,
                        message=(
                            f"{o.name!r}: ontology alignment to {ref.uri!r} is "
                            f"proposed — awaiting architect promotion "
                            f"(mdl ontology promote)"
                        ),
                        path=o.id,
                    )
                )
