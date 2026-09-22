"""Merge semantics for the reverse: config block (config_io.merge_reverse_block)."""

from __future__ import annotations

from mdl_reverse.config_io import merge_reverse_block


def test_merge_into_empty_is_the_incoming():
    assert merge_reverse_block(None, {"exclude": ["*_tmp"]}) == {"exclude": ["*_tmp"]}
    assert merge_reverse_block({}, {"target_form": "3nf"}) == {"target_form": "3nf"}


def test_lists_union_order_preserving_dedup():
    existing = {"exclude": ["*_tmp", "a"]}
    merged = merge_reverse_block(existing, {"exclude": ["a", "*_scratch"]})
    assert merged["exclude"] == ["*_tmp", "a", "*_scratch"]  # a not duplicated, order kept


def test_layers_dedup_by_identity():
    layer = {"name": "dims", "role": "dimension", "match": {"prefix": "dim_"}}
    existing = {"layers": [layer]}
    # re-importing the same layer object doesn't append a duplicate
    merged = merge_reverse_block(existing, {"layers": [dict(layer)]})
    assert merged["layers"] == [layer]
    # a genuinely new layer appends
    other = {"name": "facts", "role": "fact", "match": {"prefix": "fct_"}}
    merged2 = merge_reverse_block(existing, {"layers": [other]})
    assert merged2["layers"] == [layer, other]


def test_dicts_shallow_merge_incoming_wins():
    existing = {"model_map": {"price": "stg_price"}, "conventions": {"fk_suffixes": ["_id"]}}
    merged = merge_reverse_block(
        existing,
        {"model_map": {"order": "fct_order"}, "conventions": {"fk_suffixes": ["_fk"]}},
    )
    assert merged["model_map"] == {"price": "stg_price", "order": "fct_order"}
    # incoming wins for a shared key
    assert merged["conventions"] == {"fk_suffixes": ["_fk"]}


def test_scalars_overwrite():
    assert merge_reverse_block({"target_form": "denormalized"}, {"target_form": "3nf"})[
        "target_form"
    ] == "3nf"


def test_replace_overwrites_lists_wholesale():
    existing = {"exclude": ["*_tmp", "*_old"]}
    merged = merge_reverse_block(existing, {"exclude": ["*_new"]}, replace=True)
    assert merged["exclude"] == ["*_new"]


def test_absent_incoming_keys_are_left_untouched():
    existing = {"exclude": ["*_tmp"], "target_form": "3nf"}
    merged = merge_reverse_block(existing, {"exempt": ["LEGACY"]})
    assert merged["exclude"] == ["*_tmp"]  # untouched
    assert merged["target_form"] == "3nf"  # untouched
    assert merged["exempt"] == ["LEGACY"]  # added
