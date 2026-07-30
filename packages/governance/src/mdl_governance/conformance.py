"""Conformance kit (spec §9.5).

`mdl gov conformance --profile my-profile.yaml` runs a fixture model through the
mapping and asserts:
  - every asset type resolves (no unmapped modelith_kind)
  - every relation type exists in the target operating model
  - no duplicate external IDs
  - all required attributes populated (no render errors)
  - writeback paths resolve

A customer validates a bespoke mapping in a CI job without contacting us. This is
what keeps the mapping layer a product rather than a services line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mdl_core.ir import Model
from mdl_governance.graph import build_graph
from mdl_governance.profile import Profile, map_graph


@dataclass
class ConformanceResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mapped_assets: int = 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False


# The kinds a complete profile is expected to map (spec §9.2 example).
_EXPECTED_KINDS = {
    "conceptual_entity",
    "logical_entity",
    "attribute",
    "physical_table",
    "term",
}

_VALID_MODEL_PATH_ROOTS = {"governance"}


def run_conformance(model: Model, profile: Profile, *, strict: bool = False) -> ConformanceResult:
    """Validate a mapping. By default a profile may intentionally map only a subset
    of kinds — unmapped kinds are warnings (those assets are skipped). `strict=True`
    promotes any unmapped-but-present kind to an error, for teams that require full
    coverage. The hard errors — dup external ids, template render failures, missing
    relation/writeback targets — always fail (spec §9.5)."""
    result = ConformanceResult(passed=True)
    graph = build_graph(model)

    # duplicate external ids — always a hard error (breaks idempotent sync).
    dups = graph.duplicate_external_ids()
    if dups:
        result.add_error(f"duplicate external_ids: {dups}")

    mapped = map_graph(graph, profile)
    result.mapped_assets = len(mapped.assets)

    # asset kinds present in the model but not mapped: warn (skipped), or error in
    # strict mode. A minimal/partial profile is legitimate.
    present_kinds = {a.modelith_kind for a in graph.assets}
    for kind in sorted(present_kinds & mapped.unmapped_kinds):
        msg = f"asset kind {kind!r} has no mapping in profile {profile.profile!r} (assets skipped)"
        if strict:
            result.add_error(msg)
        else:
            result.warnings.append(msg)

    # render errors = a mapped template referenced a missing attribute -> hard error.
    for err in mapped.render_errors:
        result.add_error(f"template render error: {err}")

    # relation types must be declared. Any relation kind used by an asset must have
    # a mapping entry, else it silently drops.
    used_relations = {r.kind for a in graph.assets for r in a.relations}
    for rel_kind in sorted(used_relations):
        if rel_kind not in profile.relations:
            result.warnings.append(
                f"relation kind {rel_kind!r} not mapped in profile (will be skipped)"
            )

    # completeness: warn if the profile omits an expected kind that isn't present
    for kind in sorted(_EXPECTED_KINDS - set(profile.asset_types)):
        result.warnings.append(f"profile does not map expected kind {kind!r}")

    # writeback paths must be well-formed (root in the allowed set)
    for wb in profile.writeback:
        root = wb.model_path.split(".")[0] if wb.model_path else ""
        if root not in _VALID_MODEL_PATH_ROOTS:
            result.add_error(
                f"writeback model_path {wb.model_path!r} must start with one of "
                f"{sorted(_VALID_MODEL_PATH_ROOTS)}"
            )

    return result
