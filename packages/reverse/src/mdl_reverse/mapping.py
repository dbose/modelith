"""Resolve a Modelith entity name to the dbt model that materialises it.

Drift compares the committed model against the compiled dbt project by model name. But
every warehouse names its models differently — a `price` entity may live as `stg_price`,
`price`, or `dim_price` — so an exact-name match produces false "model removed" drift.
This module turns the warehouse's convention (the `reverse:` block of mdl-project.yaml,
`ReverseConfig`) into a single resolver used by BOTH sides of drift, so the "expected"
and "unmanaged" sets always agree.

Precedence (highest first):
  1. `model_map[entity]` — an explicit per-entity override.
  2. `layers` — the first layer whose candidate name is actually in the manifest
     (or, with no manifest to check against, the first layer's candidate).
  3. the bare entity name — the historical behaviour, so an empty/absent config is
     byte-for-byte unchanged.

This is deliberately the ONLY place the mapping logic lives; `projection.py` and
`drift.py` both call `resolve_model_name`, and `staging_naming_from` bridges the layers'
staging prefix into the classification `ReverseNaming` drift already uses.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mdl_reverse.lifting import DEFAULT_NAMING, ReverseNaming

if TYPE_CHECKING:  # avoid a hard import cycle at runtime; core is a dependency anyway
    from mdl_core.ir import ReverseConfig


def _layer_candidate(layer, entity_name: str) -> str | None:
    """One candidate model name from a layer, or None if the layer produces nothing."""
    template = getattr(layer, "template", None)
    if template:
        try:
            return template.format(entity=entity_name)
        except (KeyError, IndexError):
            return None
    prefix = getattr(layer, "prefix", None) or ""
    suffix = getattr(layer, "suffix", None) or ""
    if not prefix and not suffix:
        return None
    return f"{prefix}{entity_name}{suffix}"


def resolve_model_name(
    entity_name: str,
    target: str | None,
    reverse: ReverseConfig | None,
    available: set[str] | None = None,
) -> str:
    """Return the dbt model name that materialises `entity_name` under this warehouse's
    convention. Always returns a name (never None) so a projection always has a key;
    whether that name is actually present is the caller's set-membership test.

    `available` (the manifest model names) lets layer resolution pick the candidate that
    truly exists; without it, the first layer that yields a candidate wins. An empty or
    absent `reverse` config falls straight through to the bare entity name.

    Precedence: explicit model_map > the bare entity name if the warehouse actually has
    it (the entity's own/canonical model — e.g. a generated core `price` alongside an
    upstream `stg_price`) > the first layer candidate present > the bare name.
    """
    if reverse is None or reverse.is_empty():
        return entity_name

    # 1. explicit override wins (case-insensitive on the entity key).
    if reverse.model_map:
        lowered = {k.lower(): v for k, v in reverse.model_map.items()}
        hit = lowered.get(entity_name.lower())
        if hit:
            return hit

    # 2. the bare name is the canonical match when the warehouse has it — layers are for
    # when it does NOT (a staging-only warehouse). This keeps a generated core `price`
    # governed as `price` even when `stg_price` also exists.
    if available is not None and entity_name in available:
        return entity_name

    # 3. layer patterns, in declared order.
    first_candidate: str | None = None
    for layer in reverse.layers or []:
        cand = _layer_candidate(layer, entity_name)
        if not cand:
            continue
        if available is not None:
            if cand in available:
                return cand
        elif first_candidate is None:
            first_candidate = cand
    if first_candidate is not None:
        return first_candidate

    # 4. bare name (historical behaviour).
    return entity_name


def staging_naming_from(reverse: ReverseConfig | None) -> ReverseNaming:
    """Bridge a ReverseConfig's staging layers into the `ReverseNaming` that drift's
    `is_staging` uses, so a project's declared staging prefixes suppress the right
    unmanaged models. Layers named 'staging'/'intermediate' (or any layer with a prefix,
    conservatively) contribute their prefix on top of the built-in defaults. Returns the
    default naming when nothing is configured — so behaviour is unchanged by default."""
    if reverse is None or not reverse.layers:
        return DEFAULT_NAMING
    # Only STAGING-ish layers contribute to is_staging — folding a mart_/dim_ prefix in
    # would wrongly suppress those models as unmanaged staging. A layer counts as staging
    # by its name (staging/intermediate/base/stg) — the label the user gave it.
    _STAGING_NAMES = {"staging", "intermediate", "base", "stg", "bronze"}
    prefixes = [
        layer.prefix
        for layer in reverse.layers
        if getattr(layer, "prefix", None)
        and (getattr(layer, "name", "") or "").lower() in _STAGING_NAMES
    ]
    if not prefixes:
        return DEFAULT_NAMING
    return ReverseNaming.merged({"staging_prefixes": prefixes})


# --- reverse-time classification (resolve_layer) -----------------------------
#
# The single ordered classifier the reverse pipeline consults to decide a dbt model's
# role (mirrors resolve_model_name for drift). Order: exempt > exclude > configured
# layers (first match) > the legacy ReverseNaming classifiers. With no classification
# config, it is byte-for-byte the historical is_staging -> is_reporting_rollup -> entity
# path, so behaviour is unchanged.

# Roles that mean "not a governed business entity" — dropped from the model.
EXCLUDED_ROLES = frozenset({"staging", "intermediate", "exclude"})
# Data Vault roles seed a LogicalEntity.pattern directly.
_DV_ROLE_PATTERN = {"hub": "hub", "link": "link", "satellite": "satellite", "bridge": "bridge"}


@dataclass
class LayerVerdict:
    """How reverse should treat one dbt model. `role` drives inclusion/exclusion and
    pattern; `target_form` is the modeling policy (Phase 2); `exempt` skips heuristics;
    `pattern` is set when the role is a Data Vault kind."""

    role: str  # LayerRole value, or "business" for the default/entity case
    layer_name: str | None = None
    target_form: str | None = None
    exempt: bool = False
    pattern: str | None = None


def _as_list(v) -> list[str]:
    if v is None:
        return []
    return [v] if isinstance(v, str) else list(v)


def _name_matches(name: str, needles: list[str]) -> bool:
    n = name.lower()
    # a needle is a prefix ("dim_") — startswith; support a bare exact name too
    return any(n == p.lower() or n.startswith(p.lower()) for p in needles)


def _path_matches(path: str | None, globs: list[str]) -> bool:
    if not path:
        return False
    p = path.lower()
    return any(fnmatch.fnmatch(p, g.lower()) for g in globs)


def _match_layer(match, name: str, tags: list[str], path: str | None) -> bool:
    """True if any predicate on a ReverseMatch matches the model."""
    if match is None:
        return False
    tagset = {t.lower() for t in (tags or [])}
    if _name_matches(name, _as_list(getattr(match, "prefix", None))):
        return True
    suffixes = [s.lower() for s in _as_list(getattr(match, "suffix", None))]
    if any(name.lower().endswith(s) for s in suffixes):
        return True
    if tagset & {t.lower() for t in _as_list(getattr(match, "tag", None))}:
        return True
    if _path_matches(path, _as_list(getattr(match, "path_glob", None))):
        return True
    return False


def _excluded_by(spec: list[str], name: str, tags: list[str], path: str | None) -> bool:
    """A reverse.exclude entry matches: `tag:x`, a path glob (contains / or *), or a
    name prefix/exact."""
    tagset = {t.lower() for t in (tags or [])}
    for raw in spec:
        s = raw.strip()
        if s.lower().startswith("tag:"):
            if s[4:].strip().lower() in tagset:
                return True
        elif "/" in s or "*" in s or "?" in s or "[" in s:
            if _path_matches(path, [s]) or fnmatch.fnmatch(name.lower(), s.lower()):
                return True
        elif _name_matches(name, [s]):
            return True
    return False


def resolve_layer(
    model_name: str,
    tags: list[str] | None,
    path: str | None,
    reverse: ReverseConfig | None,
    naming: ReverseNaming | None = None,
) -> LayerVerdict:
    """Classify one dbt model into a role. Order: exempt > exclude > configured layers
    (first match) > legacy ReverseNaming classifiers. `naming` is the (convention-folded)
    ReverseNaming for the legacy fallback; defaults to DEFAULT_NAMING."""
    from mdl_reverse import lifting

    naming = naming or DEFAULT_NAMING
    tags = tags or []

    if reverse is not None and not reverse.classification_is_empty():
        # 1. exempt — kept verbatim, heuristics skipped.
        if _excluded_by(list(reverse.exempt), model_name, tags, path) or any(
            e.lower() == model_name.lower() for e in reverse.exempt
        ):
            return LayerVerdict(role="business", exempt=True)
        # 2. hard exclude.
        if _excluded_by(list(reverse.exclude), model_name, tags, path):
            return LayerVerdict(role="exclude")
        # 3. configured layers, first match wins.
        for layer in reverse.layers:
            if layer.role and _match_layer(layer.match, model_name, tags, path):
                return LayerVerdict(
                    role=layer.role,
                    layer_name=layer.name,
                    target_form=layer.target_form,
                    pattern=_DV_ROLE_PATTERN.get(layer.role),
                )

    # 4. legacy fallback — byte-for-byte the historical path.
    if lifting.is_staging(model_name, tags, path, naming=naming):
        return LayerVerdict(role="staging")
    return LayerVerdict(role="business")


def naming_from_config(reverse: ReverseConfig | None) -> ReverseNaming:
    """Fold reverse.conventions (+ staging layer prefixes) into a ReverseNaming for the
    reverse pipeline. Returns DEFAULT_NAMING when nothing is configured."""
    overrides: dict[str, list[str]] = {}
    if reverse is not None and reverse.conventions:
        overrides.update({k: list(v) for k, v in reverse.conventions.items()})
    base = ReverseNaming.merged(overrides) if overrides else DEFAULT_NAMING
    # also fold staging-layer prefixes (as staging_naming_from does) so is_staging in the
    # legacy fallback sees them
    staging = staging_naming_from(reverse)
    if staging is DEFAULT_NAMING:
        return base
    if base is DEFAULT_NAMING:
        return staging
    # merge both: union their staging_prefixes onto the convention-folded base
    return ReverseNaming.merged({**overrides, "staging_prefixes": list(staging.staging_prefixes)})
