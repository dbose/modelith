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
    """
    if reverse is None or reverse.is_empty():
        return entity_name

    # 1. explicit override wins (case-insensitive on the entity key).
    if reverse.model_map:
        lowered = {k.lower(): v for k, v in reverse.model_map.items()}
        hit = lowered.get(entity_name.lower())
        if hit:
            return hit

    # 2. layer patterns, in declared order.
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

    # 3. bare name (historical behaviour).
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
