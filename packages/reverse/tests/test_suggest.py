"""suggest_config — deterministic reverse: config discovery from a manifest."""

from __future__ import annotations

from mdl_core.ir import ReverseConfig
from mdl_reverse.manifest import read_manifest_dict
from mdl_reverse.suggest import suggest_config


def _manifest(models: list[tuple[str, str]]) -> dict:
    """models: list of (name, original_file_path)."""
    nodes = {}
    for name, path in models:
        fqn = ["wh"] + path.replace("models/", "").replace(".sql", "").split("/")
        nodes[f"model.wh.{name}"] = {
            "resource_type": "model", "name": name, "original_file_path": path, "fqn": fqn,
            "columns": {}, "config": {}, "tags": [], "meta": {},
        }
    return {
        "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
        "nodes": nodes,
    }


def _suggest(models):
    return suggest_config(read_manifest_dict(_manifest(models)))


def test_suggests_folder_layers_and_exclusions():
    report = _suggest([
        ("stg_orders", "models/staging/stg_orders.sql"),
        ("stg_customers", "models/staging/stg_customers.sql"),
        ("dim_customer", "models/marts/dims/dim_customer.sql"),
        ("fct_sales", "models/marts/facts/fct_sales.sql"),
        ("orders_tmp", "models/scratch/orders_tmp.sql"),
    ])
    roles = {layer["role"] for layer in report.block.get("layers", [])}
    assert "staging" in roles and "dimension" in roles and "fact" in roles
    assert any("scratch" in e for e in report.block.get("exclude", []))
    assert any("tmp" in e for e in report.block.get("exclude", []))
    # every rule has a rationale line
    assert report.rationale


def test_suggested_block_is_reverseconfig_valid():
    report = _suggest([
        ("dim_customer", "models/marts/dim_customer.sql"),
        ("dim_product", "models/marts/dim_product.sql"),
    ])
    # round-trips through the typed config -> the writer will accept it
    ReverseConfig.model_validate(report.block)


def test_prefix_layers_when_no_folders():
    report = _suggest([
        ("hub_party", "hub_party.sql"),
        ("hub_account", "hub_account.sql"),
        ("sat_party_details", "sat_party_details.sql"),
        ("sat_account_details", "sat_account_details.sql"),
    ])
    roles = {layer["role"] for layer in report.block.get("layers", [])}
    assert "hub" in roles and "satellite" in roles


def test_single_prefix_below_threshold_is_ignored():
    # one dim_ model alone (< MIN_PREFIX) shouldn't mint a prefix layer
    report = _suggest([("dim_lonely", "dim_lonely.sql")])
    prefixes = [
        layer for layer in report.block.get("layers", [])
        if "prefix" in layer.get("match", {})
    ]
    assert prefixes == []


def test_no_convention_yields_empty_block():
    report = _suggest([("random_thing", "random_thing.sql"), ("another", "another.sql")])
    assert report.block == {}


def test_suggest_then_classify_roundtrip():
    """The headline guarantee: suggest -> apply -> the config classifies the fixture as
    intended (a stg_ model excluded/staging, a dim_ model a dimension)."""
    from mdl_reverse.mapping import resolve_layer

    models = [
        ("stg_orders", "models/staging/stg_orders.sql"),
        ("stg_x", "models/staging/stg_x.sql"),
        ("dim_customer", "models/marts/dim_customer.sql"),
        ("dim_product", "models/marts/dim_product.sql"),
    ]
    report = _suggest(models)
    rc = ReverseConfig.model_validate(report.block)
    # dim_customer classifies as a dimension; stg_orders as staging (excluded)
    assert resolve_layer("dim_customer", [], "models/marts/dim_customer.sql", rc).role == "dimension"
    assert resolve_layer("stg_orders", [], "models/staging/stg_orders.sql", rc).role == "staging"


def _manifest_with_prefix(prefix: str, n: int, root: str = "my_project"):
    from mdl_reverse.manifest import ManifestModel, ManifestProjection

    models = {
        f"{prefix}model_{i}": ManifestModel(
            name=f"{prefix}model_{i}",
            unique_id=f"model.{root}.{prefix}model_{i}",
            package_name=root,
            path=f"models/{prefix}model_{i}.sql",
        )
        for i in range(n)
    }
    return ManifestProjection(models=models, root_project=root)


def test_suggest_reports_unclassified_custom_prefix():
    from mdl_reverse.suggest import suggest_config

    report = suggest_config(_manifest_with_prefix("pres_art_", 12))
    prefixes = {u["prefix"]: u["count"] for u in report.unclassified}
    assert prefixes.get("pres_art_") == 12
    # a known prefix is NOT reported as unclassified
    assert not any(u["prefix"] == "dim_" for u in report.unclassified)


def test_suggest_ignores_rare_unclassified_prefix():
    from mdl_reverse.suggest import suggest_config

    # a single model with an odd prefix is below _MIN_PREFIX -> not surfaced (noise)
    report = suggest_config(_manifest_with_prefix("zzz_odd_", 1))
    assert not report.unclassified
