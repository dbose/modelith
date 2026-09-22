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


def test_bare_name_wins_when_present_over_layer():
    """When both the bare name and a layer candidate exist, the bare name is the
    entity's canonical model (e.g. a generated core `price` alongside `stg_price`)."""
    rc = ReverseConfig(layers=[ReverseLayer(name="staging", prefix="stg_")])
    assert resolve_model_name("price", TARGET, rc, available={"price", "stg_price"}) == "price"


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


def test_scaffolded_demo_is_drift_clean_after_generate(tmp_path: Path):
    """The scaffolded demo (`mdl init --demo`) ships NO reverse block, matching demo/ibor.
    A Modelith entity maps to a dbt MART model (dbt's own 'entity layer'), which is the
    bare-named core model `mdl generate` writes (benchmark, price, …) — NOT the thin `stg_`
    passthrough. So once generated, entity `benchmark` matches core mart `benchmark` by
    bare name, `stg_benchmark` is suppressed as staging, and drift is clean with zero
    config.

    Regression guard against the earlier mistake of shipping a `reverse.layers: staging`
    block, which forced entities onto the passthrough staging models and produced a wall
    of false column/contract/relationship drift.
    """
    from mdl_cli.demo import scaffold_demo

    scaffold_demo(tmp_path)
    model_dir = tmp_path / "model"
    repo = ModelRepo.load(model_dir)
    target = repo.model.config.dbt_target or "duckdb_dev"

    # The demo must NOT ship a reverse block — bare-name matching against generated marts
    # is correct; a staging layer would be the bug.
    reverse = getattr(repo.model.config, "reverse", None)
    assert reverse is None or reverse.is_empty(), "demo must not ship a reverse block"

    # Emulate the POST-generate warehouse: a bare-name core mart per entity (full columns
    # + contract + relationship tests, from manifest_from_model) PLUS a thin stg_ passthrough
    # (empty columns) that must be suppressed, not matched.
    raw = manifest_from_model(repo.model, target)
    for entity in [le.name for le in repo.model.logical_entities.values()]:
        raw["nodes"][f"model.pension_ibor.stg_{entity}"] = {
            "resource_type": "model",
            "name": f"stg_{entity}",
            "columns": {},
            "config": {"contract": {"enforced": False}},
            "tags": [],
            "meta": {},
        }
    proj = read_manifest_dict(raw)

    report = compute_drift(repo.model, proj, target, reverse=reverse)
    assert report.items == [], [f"{i.kind.value}: {i.detail}" for i in report.items]


def test_scaffolded_demo_pre_generate_is_honest_model_removed(tmp_path: Path):
    """Before `mdl generate`, only the stg_ passthroughs exist and the core marts don't.
    Drift then honestly reports the marts as `model_removed` (they aren't built yet) —
    NOT a wall of column/contract diffs against the staging passthrough. This is the
    'run mdl generate first' signal the walkthrough/README guide the user through."""
    from mdl_cli.demo import scaffold_demo

    scaffold_demo(tmp_path)
    repo = ModelRepo.load(tmp_path / "model")
    target = repo.model.config.dbt_target or "duckdb_dev"

    # Pre-generate: ONLY stg_ models exist (rename every core model to stg_).
    raw = manifest_from_model(repo.model, target)
    for node in raw["nodes"].values():
        if node.get("resource_type") == "model":
            node["name"] = f"stg_{node['name']}"
    proj = read_manifest_dict(raw)

    report = compute_drift(repo.model, proj, target, reverse=None)
    kinds = {i.kind for i in report.items}
    # honest signal: the marts are missing (model_removed), not passthrough column noise
    assert DriftKind.model_removed in kinds
    assert DriftKind.column_dropped not in kinds
    assert DriftKind.contract_disabled not in kinds


# --- resolve_layer classification (Phase 1) ----------------------------------

from mdl_core.ir import ReverseLayer as _RL  # noqa: E402
from mdl_core.ir import ReverseMatch as _RM  # noqa: E402
from mdl_reverse.mapping import resolve_layer  # noqa: E402


def test_resolve_layer_empty_config_is_legacy():
    # no config -> legacy is_staging classification, unchanged
    assert resolve_layer("stg_orders", [], None, None).role == "staging"
    assert resolve_layer("dim_customer", [], None, None).role == "business"


def test_resolve_layer_order_exempt_exclude_layer_legacy():
    rc = ReverseConfig(
        exempt=["LEGACY"],
        exclude=["*_tmp", "tag:deprecated"],
        layers=[_RL(name="dims", role="dimension", match=_RM(prefix="dim_"))],
    )
    assert resolve_layer("LEGACY", [], None, rc).exempt is True
    assert resolve_layer("orders_tmp", [], None, rc).role == "exclude"
    assert resolve_layer("x", ["deprecated"], None, rc).role == "exclude"
    assert resolve_layer("dim_customer", [], None, rc).role == "dimension"
    # unmatched falls to legacy
    assert resolve_layer("stg_x", [], None, rc).role == "staging"


def test_resolve_layer_path_glob_and_dv_pattern():
    rc = ReverseConfig(
        layers=[
            _RL(name="finance", role="mart", match=_RM(path_glob="models/marts/finance/**")),
            _RL(name="vault", role="hub", match=_RM(prefix="hub_")),
        ]
    )
    assert resolve_layer("revenue", [], "models/marts/finance/revenue.sql", rc).role == "mart"
    v = resolve_layer("hub_party", [], None, rc)
    assert v.role == "hub" and v.pattern == "hub"


def test_reverse_with_config_classifies_by_folder_and_seeds_pattern():
    """End-to-end: folder/prefix rules include/exclude the right models and a hub role
    seeds the Data Vault pattern."""
    nodes = {}

    def mk(name, path, cols):
        nodes[f"model.wh.{name}"] = {
            "resource_type": "model", "name": name, "original_file_path": path,
            "columns": {c: {"name": c, "data_type": "BIGINT"} for c in cols},
            "config": {"contract": {"enforced": True}}, "tags": [], "meta": {},
        }

    mk("customer", "models/marts/dims/customer.sql", ["customer_id", "name"])
    mk("bronze_raw", "models/bronze/bronze_raw.sql", ["id"])
    mk("orders_tmp", "models/scratch/orders_tmp.sql", ["id"])
    mk("hub_party", "models/vault/hub_party.sql", ["party_hashkey", "party_bk"])
    raw = {
        "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
        "nodes": nodes,
    }
    proj = read_manifest_dict(raw)
    rc = ReverseConfig(
        exclude=["models/scratch/**"],
        layers=[
            _RL(name="bronze", role="staging", match=_RM(path_glob="models/bronze/**")),
            _RL(name="dims", role="dimension", match=_RM(path_glob="models/marts/dims/**")),
            _RL(name="vault", role="hub", match=_RM(prefix="hub_")),
        ],
    )
    res = compute_reverse(proj, rc)
    ents = {le.name: le.pattern for le in res.model.logical_entities.values()}
    assert set(ents) == {"customer", "hub_party"}
    assert ents["hub_party"] == "hub"
    assert set(res.excluded) == {"bronze_raw", "orders_tmp"}


def compute_reverse(proj, rc):
    from mdl_reverse.ledger import DecisionLedger
    from mdl_reverse.reverse import reverse as _rev

    return _rev(proj, project_name="wh", ledger=DecisionLedger(), reverse_config=rc)
