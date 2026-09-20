"""Entity -> warehouse-model name resolution (reverse.model_map / reverse.layers).

Every warehouse names its dbt models differently — a `price` entity may materialise as
`stg_price`, `price`, or `dim_price`. Drift must resolve the entity to the right model
name via the project's convention instead of assuming an exact-name match, or it reports
false "model removed". These tests pin the resolver precedence and prove drift stops
false-positiving once a mapping is configured, while an empty/absent config is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.ir import ReverseConfig, ReverseLayer
from mdl_core.repo import ModelRepo
from mdl_reverse.drift import DriftKind, compute_drift
from mdl_reverse.lifting import DEFAULT_NAMING
from mdl_reverse.manifest import read_manifest_dict
from mdl_reverse.mapping import resolve_model_name, staging_naming_from

from manifest_fixtures import manifest_from_model

TARGET = "duckdb_dev"


# --- resolver precedence -----------------------------------------------------


def test_none_and_empty_config_return_bare_name():
    assert resolve_model_name("price", TARGET, None) == "price"
    assert resolve_model_name("price", TARGET, ReverseConfig()) == "price"


def test_model_map_wins_over_layers():
    rc = ReverseConfig(
        model_map={"price": "fct_price"},
        layers=[ReverseLayer(name="staging", prefix="stg_")],
    )
    # explicit map wins even though a staging layer would yield stg_price
    assert resolve_model_name("price", TARGET, rc, available={"stg_price", "fct_price"}) == "fct_price"


def test_model_map_is_case_insensitive_on_entity():
    rc = ReverseConfig(model_map={"Price": "stg_price"})
    assert resolve_model_name("price", TARGET, rc) == "stg_price"


def test_layer_prefix_resolves_to_available_candidate():
    rc = ReverseConfig(layers=[ReverseLayer(name="staging", prefix="stg_")])
    assert resolve_model_name("price", TARGET, rc, available={"stg_price"}) == "stg_price"


def test_layer_template_resolves():
    rc = ReverseConfig(layers=[ReverseLayer(name="dimension", template="dim_{entity}")])
    assert resolve_model_name("price", TARGET, rc, available={"dim_price"}) == "dim_price"


def test_first_layer_present_in_manifest_wins():
    rc = ReverseConfig(
        layers=[
            ReverseLayer(name="dimension", template="dim_{entity}"),
            ReverseLayer(name="staging", prefix="stg_"),
        ]
    )
    # dim_price not present -> falls through to stg_price which is
    assert resolve_model_name("price", TARGET, rc, available={"stg_price"}) == "stg_price"


def test_no_candidate_present_falls_back_to_bare():
    rc = ReverseConfig(layers=[ReverseLayer(name="staging", prefix="stg_")])
    # available given but candidate absent -> bare name (a TRUE removal downstream)
    assert resolve_model_name("price", TARGET, rc, available={"something_else"}) == "price"


def test_no_available_uses_first_layer_candidate():
    rc = ReverseConfig(layers=[ReverseLayer(name="staging", prefix="stg_")])
    assert resolve_model_name("price", TARGET, rc, available=None) == "stg_price"


# --- staging_naming_from bridge ----------------------------------------------


def test_staging_naming_from_none_is_default():
    assert staging_naming_from(None) == DEFAULT_NAMING
    assert staging_naming_from(ReverseConfig()) == DEFAULT_NAMING


def test_staging_naming_from_folds_only_staging_layers():
    rc = ReverseConfig(
        layers=[
            ReverseLayer(name="staging", prefix="bronze_"),
            ReverseLayer(name="dimension", prefix="dim_"),  # must NOT become staging
        ]
    )
    naming = staging_naming_from(rc)
    assert "bronze_" in naming.staging_prefixes
    assert "dim_" not in naming.staging_prefixes
    assert "stg_" in naming.staging_prefixes  # defaults preserved


# --- drift end-to-end: a mapping removes the false model_removed --------------


def _report(model_dir: Path, reverse=None, mutate=None):
    repo = ModelRepo.load(model_dir)
    raw = manifest_from_model(repo.model, TARGET)
    if mutate:
        mutate(raw)
    proj = read_manifest_dict(raw)
    return compute_drift(repo.model, proj, TARGET, reverse=reverse)


def _rename_all_models_to_stg(raw: dict) -> None:
    """Rename every model node price -> stg_price, etc., to emulate a staging warehouse
    whose models are all stg_-prefixed (the scaffolded-demo situation)."""
    nodes = raw["nodes"]
    for uid in list(nodes):
        node = nodes[uid]
        if node.get("resource_type") != "model":
            continue
        node["name"] = f"stg_{node['name']}"


def test_staging_warehouse_without_mapping_is_false_drift(model_dir: Path):
    """Baseline: rename all models to stg_* and DON'T configure a mapping -> every entity
    shows as model_removed (the false-positive we're fixing)."""
    report = _report(model_dir, reverse=None, mutate=_rename_all_models_to_stg)
    removed = [i for i in report.items if i.kind == DriftKind.model_removed]
    assert removed, "expected false model_removed without a mapping"


def test_staging_layer_mapping_clears_the_false_drift(model_dir: Path):
    """With a staging layer, price resolves to stg_price -> zero model_removed, and the
    stg_* models are not reported as unmanaged either (both sides agree)."""
    rc = ReverseConfig(layers=[ReverseLayer(name="staging", prefix="stg_")])
    report = _report(model_dir, reverse=rc, mutate=_rename_all_models_to_stg)
    removed = [i for i in report.items if i.kind == DriftKind.model_removed]
    unmanaged = [i for i in report.items if i.kind == DriftKind.unmanaged_model]
    assert removed == [], [i.detail for i in removed]
    assert unmanaged == [], [i.detail for i in unmanaged]


def test_explicit_model_map_clears_one_and_suppresses_its_unmanaged(model_dir: Path):
    """An explicit model_map entry both matches the entity AND stops its target model
    being reported as unmanaged."""
    repo = ModelRepo.load(model_dir)
    entity = next(iter(repo.model.logical_entities.values())).name
    rc = ReverseConfig(model_map={entity: f"weird_{entity}"})

    def rename_one(raw):
        for node in raw["nodes"].values():
            if node.get("resource_type") == "model" and node["name"] == entity:
                node["name"] = f"weird_{entity}"

    report = _report(model_dir, reverse=rc, mutate=rename_one)
    removed = [i for i in report.items if i.kind == DriftKind.model_removed and i.model == entity]
    unmanaged = [
        i for i in report.items if i.kind == DriftKind.unmanaged_model and i.model == f"weird_{entity}"
    ]
    assert removed == [], "mapped entity must not be model_removed"
    assert unmanaged == [], "the map target must not be reported unmanaged"


def test_map_to_missing_model_is_still_true_removal(model_dir: Path):
    """A mapping to a name the warehouse doesn't have is a REAL model_removed, not
    silently swallowed."""
    repo = ModelRepo.load(model_dir)
    entity = next(iter(repo.model.logical_entities.values())).name
    rc = ReverseConfig(model_map={entity: "does_not_exist"})

    def drop_that_model(raw):
        for uid in list(raw["nodes"]):
            node = raw["nodes"][uid]
            if node.get("resource_type") == "model" and node["name"] == entity:
                del raw["nodes"][uid]

    report = _report(model_dir, reverse=rc, mutate=drop_that_model)
    removed = [i for i in report.items if i.kind == DriftKind.model_removed]
    assert any(i.model == "does_not_exist" for i in removed)


def test_model_removed_items_carry_map_candidate_payload(model_dir: Path):
    """model_removed items hint the UI's 'Map to dbt model…' action via payload."""
    report = _report(model_dir, reverse=None, mutate=_rename_all_models_to_stg)
    removed = [i for i in report.items if i.kind == DriftKind.model_removed]
    assert removed and all(i.payload.get("map_candidate") for i in removed)
