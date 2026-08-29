"""Four-layer ontology validation + coverage report (spec §3.1).

Layers: industry < core < domain < specialised. Rules enforced:

1. Upward-only alignment: an object aligns to the immediately adjacent layer or
   higher. Because a single object declares only its own layer + a target IRI, we
   enforce the *intra-model* case: any object that aligns to another modelled term
   must target a term whose layer is >= its own layer's parent (i.e. not downward).
2. Alignment predicates are SKOS; exactMatch must not create a cycle (transitive
   check over modelled exactMatch edges).
3. Every `core` term must have an industry alignment or a reviewed
   `no_industry_equivalent: true` (§3.1 rule 3) — the CDO coverage report.
4. A specialised term duplicating a domain term by definition similarity raises a
   warning with the candidate (cheap offline token similarity; §3.1 rule 4).

Alignment lives in `ontology_refs` (spec §1): a list of `{predicate, uri, layer,
...}`. The object's own layer sits on `ontology_layer`; `no_industry_equivalent`
and `rationale` are object-level coverage facts. These checks read that shape; a
legacy single `ontology` alignment is folded into the same shape on load (see
`mdl_core.ir._OntologyMixin`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mdl_core.diagnostics import Diagnostic, DiagnosticSet, Severity
from mdl_core.ir import ConceptualEntity, Model, Term

_LAYER_RANK = {"industry": 0, "core": 1, "domain": 2, "specialised": 3}
_VALID_PREDICATES = {
    "skos:exactMatch",
    "skos:closeMatch",
    "skos:broadMatch",
    "skos:narrowMatch",
    "skos:relatedMatch",
}


@dataclass
class CoverageReport:
    """The report that sells the tool to a CDO (§3.1 rule 3)."""

    total_core: int = 0
    core_with_industry: int = 0
    core_exempt: int = 0  # reviewed no_industry_equivalent
    core_uncovered: list[str] = field(default_factory=list)  # names missing alignment
    by_layer: dict[str, int] = field(default_factory=dict)

    @property
    def coverage_pct(self) -> float:
        if self.total_core == 0:
            return 100.0
        covered = self.core_with_industry + self.core_exempt
        return round(100.0 * covered / self.total_core, 1)


def _ontology_objects(model: Model) -> list[ConceptualEntity | Term]:
    return [*model.conceptual_entities.values(), *model.terms.values()]


def _refs(o) -> list:
    """The object's ontology_refs (empty list when absent)."""
    return getattr(o, "ontology_refs", None) or []


def check_layers(model: Model, registry=None) -> DiagnosticSet:
    diags = DiagnosticSet()
    objs = _ontology_objects(model)
    by_name = {o.name.lower(): o for o in objs}

    _check_predicates(objs, diags)
    _check_upward(objs, by_name, diags)
    _check_exactmatch_cycles(objs, by_name, diags)
    _check_core_coverage(objs, diags)
    _check_duplicate_terms(objs, diags)
    if registry is not None:
        _check_iri_resolvable(objs, registry, diags)
    return diags


def _check_predicates(objs, diags: DiagnosticSet) -> None:
    for o in objs:
        for ref in _refs(o):
            if ref.predicate and ref.predicate not in _VALID_PREDICATES:
                diags.add(
                    Diagnostic(
                        code="MDL-E210",
                        severity=Severity.error,
                        message=(
                            f"{o.name!r} uses non-SKOS alignment predicate "
                            f"{ref.predicate!r}"
                        ),
                        path=o.id,
                    )
                )


def _check_upward(objs, by_name: dict, diags: DiagnosticSet) -> None:
    """No downward alignment: a term must not align to a term in a lower layer."""
    for o in objs:
        own = _LAYER_RANK.get(o.ontology_layer) if o.ontology_layer else None
        if own is None:
            continue
        for ref in _refs(o):
            if not ref.uri:
                continue
            target = _resolve_modelled(ref.uri, by_name)
            if target is None:
                continue  # external IRI; layer checked via registry, not here
            t_rank = (
                _LAYER_RANK.get(target.ontology_layer)
                if target.ontology_layer
                else None
            )
            if t_rank is None:
                continue
            if t_rank > own:
                diags.add(
                    Diagnostic(
                        code="MDL-E211",
                        severity=Severity.error,
                        message=(
                            f"{o.name!r} ({o.ontology_layer}) aligns downward to "
                            f"{target.name!r} ({target.ontology_layer}); "
                            f"alignment must be upward"
                        ),
                        path=o.id,
                    )
                )


def _check_exactmatch_cycles(objs, by_name: dict, diags: DiagnosticSet) -> None:
    """exactMatch is transitive-checked and must not create a cycle (§3.1 rule 2)."""
    edges: dict[str, str] = {}
    for o in objs:
        for ref in _refs(o):
            if ref.predicate == "skos:exactMatch" and ref.uri:
                target = _resolve_modelled(ref.uri, by_name)
                if target is not None:
                    edges[o.id] = target.id
                    break  # one exactMatch edge per object is enough to trace a cycle
    for start in edges:
        seen = set()
        cur = start
        while cur in edges:
            if cur in seen:
                obj = next((o for o in objs if o.id == start), None)
                diags.add(
                    Diagnostic(
                        code="MDL-E212",
                        severity=Severity.error,
                        message=f"exactMatch cycle involving {obj.name if obj else start!r}",
                        path=start,
                    )
                )
                break
            seen.add(cur)
            cur = edges[cur]


def _check_core_coverage(objs, diags: DiagnosticSet) -> None:
    for o in objs:
        if o.ontology_layer != "core":
            continue
        has_alignment = any(ref.uri for ref in _refs(o))
        if not has_alignment and not o.no_industry_equivalent:
            diags.add(
                Diagnostic(
                    code="MDL-E202",
                    severity=Severity.error,
                    message=(
                        f"core term {o.name!r} has no industry alignment and no reviewed "
                        f"no_industry_equivalent=true"
                    ),
                    path=o.id,
                )
            )
        elif o.no_industry_equivalent and not o.rationale:
            diags.add(
                Diagnostic(
                    code="MDL-W204",
                    severity=Severity.warning,
                    message=(
                        f"core term {o.name!r} exempt from industry alignment "
                        f"but has no rationale"
                    ),
                    path=o.id,
                )
            )


def _check_duplicate_terms(objs, diags: DiagnosticSet) -> None:
    """A specialised term duplicating a domain term by definition similarity (§3.1
    rule 4). Cheap offline token-Jaccard; do not over-engineer."""
    domain_terms = [o for o in objs if o.ontology_layer == "domain"]
    for o in objs:
        if o.ontology_layer != "specialised" or not o.definition:
            continue
        for dt in domain_terms:
            if not dt.definition:
                continue
            sim = _jaccard(o.definition, dt.definition)
            if sim >= 0.6:
                diags.add(
                    Diagnostic(
                        code="MDL-W205",
                        severity=Severity.warning,
                        message=(
                            f"specialised term {o.name!r} may duplicate domain term "
                            f"{dt.name!r} (definition similarity {sim:.0%})"
                        ),
                        path=o.id,
                    )
                )
                break


def _check_iri_resolvable(objs, registry, diags: DiagnosticSet) -> None:
    """Unresolvable prefixed IRIs are a validation error (spec §3.2)."""
    for o in objs:
        for ref in _refs(o):
            if not ref.uri:
                continue
            # only check external (prefixed, non-modelled) IRIs
            if _looks_like_iri(ref.uri) and not registry.resolves(ref.uri):
                diags.add(
                    Diagnostic(
                        code="MDL-E213",
                        severity=Severity.error,
                        message=f"{o.name!r} aligns to unresolvable IRI {ref.uri!r}",
                        path=o.id,
                    )
                )


def coverage_report(model: Model) -> CoverageReport:
    rpt = CoverageReport()
    for o in _ontology_objects(model):
        layer = o.ontology_layer
        if layer:
            rpt.by_layer[layer] = rpt.by_layer.get(layer, 0) + 1
        if layer == "core":
            rpt.total_core += 1
            if any(ref.uri for ref in _refs(o)):
                rpt.core_with_industry += 1
            elif o.no_industry_equivalent:
                rpt.core_exempt += 1
            else:
                rpt.core_uncovered.append(o.name)
    return rpt


# --- helpers ---------------------------------------------------------------


def _resolve_modelled(aligns_to: str, by_name: dict):
    """If `aligns_to` names another modelled term (by local/name), return it."""
    key = aligns_to.split(":")[-1].lower()
    return by_name.get(key)


def _looks_like_iri(s: str) -> bool:
    return ":" in s and not s.startswith(("http://", "https://")) or s.startswith("http")


def _jaccard(a: str, b: str) -> float:
    ta = {w for w in a.lower().split() if len(w) > 3}
    tb = {w for w in b.lower().split() if len(w) > 3}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
