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
    _check_ontology_layers(model, diags)
    naming_diags, _ = naming_lint(model)
    diags.extend(naming_diags)
    return diags


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

    for pt in model.physical_tables.values():
        ref(pt.realises, "MDL-E104", f"physical table {pt.name!r} realises", pt.id)
        for col in pt.columns:
            ref(col.realises, "MDL-E104", f"physical table {pt.name!r} column {col.name!r}", pt.id)


def _check_ontology_layers(model: Model, diags: DiagnosticSet) -> None:
    """Rule 1: an object may only align upward to an adjacent-or-higher layer."""
    objs: list[ConceptualEntity | Term] = [
        *model.conceptual_entities.values(),
        *model.terms.values(),
    ]
    for obj in objs:
        ont = obj.ontology
        if ont is None or ont.layer is None:
            continue
        own_rank = _LAYER_ORDER.get(ont.layer)
        if own_rank is None:
            continue
        # `core` term must have an industry alignment or explicit exemption (rule 3).
        if ont.layer == "core" and not ont.aligns_to and not ont.no_industry_equivalent:
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
        if own_rank > 0 and not ont.aligns_to and not ont.no_industry_equivalent:
            diags.add(
                Diagnostic(
                    code="MDL-W203",
                    severity=Severity.warning,
                    message=(
                        f"{obj.kind.value} {obj.name!r} in layer {ont.layer!r} declares "
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
