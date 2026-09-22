"""Merge helpers for the `reverse:` config block of mdl-project.yaml.

The `reverse:` block is the single git-committed source of truth for how a warehouse's
dbt models map to entities (layers/roles, conventions, exclude, exempt, model_map,
target_form). These helpers merge a partial `reverse:` block into an existing one so the
CLI can author config from three sources — suggest-config, import-config (a shared team
standard), and the drift-UI mapping action — all through ONE merge, comment-preserving at
the file layer (the CLI passes plain/ruamel dicts; this module only decides how keys
combine).

Merge is ADDITIVE by default (the safe choice for evolving a config): lists union
(dedup, order-preserving), dicts shallow-merge (incoming wins per key), scalars overwrite.
`replace=True` overwrites each provided top-level key wholesale instead. Keys absent from
the incoming block are always left untouched.
"""

from __future__ import annotations

from typing import Any

# reverse: keys whose values are lists and should UNION on an additive merge.
_LIST_KEYS = frozenset({"layers", "exclude", "exempt"})
# keys whose values are dicts and should shallow-merge (incoming wins per key).
_DICT_KEYS = frozenset({"model_map", "conventions"})


def _union_lists(existing: list, incoming: list) -> list:
    """Order-preserving union. dicts (e.g. layer objects) dedup by their JSON identity so
    re-importing the same standard doesn't append duplicates; scalars dedup by value."""
    out = list(existing)
    seen = {_key(x) for x in existing}
    for item in incoming:
        k = _key(item)
        if k not in seen:
            out.append(item)
            seen.add(k)
    return out


def _key(item: Any) -> str:
    import json

    try:
        return json.dumps(item, sort_keys=True, default=str)
    except TypeError:
        return str(item)


def merge_reverse_block(
    existing: dict | None, incoming: dict, *, replace: bool = False
) -> dict:
    """Return the merged `reverse:` block. `existing` is the current block (or None),
    `incoming` the partial block to fold in. Additive by default; `replace` overwrites
    each incoming top-level key wholesale. Never drops a key that `incoming` doesn't set."""
    merged: dict = dict(existing or {})
    for key, inc_val in incoming.items():
        if replace or key not in merged:
            merged[key] = inc_val
            continue
        cur = merged[key]
        if key in _LIST_KEYS and isinstance(cur, list) and isinstance(inc_val, list):
            merged[key] = _union_lists(cur, inc_val)
        elif key in _DICT_KEYS and isinstance(cur, dict) and isinstance(inc_val, dict):
            merged[key] = {**cur, **inc_val}
        else:
            merged[key] = inc_val  # scalar (e.g. target_form) or type mismatch -> overwrite
    return merged
