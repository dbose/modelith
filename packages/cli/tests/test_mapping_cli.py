"""`mdl mapping set/unset/list` — authoring reverse.model_map from the CLI (the write
path the Drift-UI "Map to dbt model…" action drives). The reverse: block of
mdl-project.yaml is the single source of truth; these commands round-trip it
comment-preserving and expose the unmatched sets + ranked candidates the UI needs.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.demo import scaffold_demo
from mdl_cli.main import app

runner = CliRunner()


def _project(model_dir: Path) -> str:
    return (model_dir / "mdl-project.yaml").read_text(encoding="utf-8")


def test_mapping_set_adds_model_map_preserving_layers_and_comments(tmp_path: Path):
    """`mapping set` folds into an existing reverse block without disturbing a
    pre-declared layer or its comment (comment-preserving round-trip)."""
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    proj = m / "mdl-project.yaml"
    # Seed a project that already has a commented reverse.layers block (the demo ships
    # none — bare-name matching — so we add one here to exercise preservation).
    proj.write_text(
        proj.read_text(encoding="utf-8")
        + "# our warehouse's dimensional layer\n"
        + "reverse:\n  layers:\n    - name: dimension\n      prefix: dim_\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["mapping", "set", "price", "fct_price", "-m", str(m)])
    assert r.exit_code == 0, r.output
    text = _project(m)
    # the new mapping is present ...
    assert "model_map:" in text
    assert "price: fct_price" in text
    # ... and the pre-existing layer + its explanatory comment survived
    assert "layers:" in text
    assert "prefix: dim_" in text
    assert "# our warehouse's dimensional layer" in text  # comment preserved (round-trip)


def test_mapping_unset_removes_entry(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    runner.invoke(app, ["mapping", "set", "price", "fct_price", "-m", str(m)])
    r = runner.invoke(app, ["mapping", "unset", "price", "-m", str(m)])
    assert r.exit_code == 0, r.output
    assert "price: fct_price" not in _project(m)


def test_mapping_list_json_shape(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    runner.invoke(app, ["mapping", "set", "price", "stg_price", "-m", str(m)])
    r = runner.invoke(app, ["mapping", "list", "-m", str(m), "--format", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["model_map"] == {"price": "stg_price"}
    assert "unmatched_entities" in data and "unclaimed_models" in data


def _write_manifest(path: Path, names: list[str]) -> None:
    nodes = {
        f"model.x.{n}": {
            "resource_type": "model",
            "name": n,
            "columns": {},
            "config": {"contract": {"enforced": True}},
            "tags": [],
            "meta": {},
        }
        for n in names
    }
    path.write_text(
        json.dumps(
            {
                "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
                "nodes": nodes,
            }
        ),
        encoding="utf-8",
    )


def test_mapping_list_with_manifest_reports_unmatched(tmp_path: Path):
    """With a manifest of only raw_* models (nothing the staging layer resolves to),
    every entity is unmatched and every model is unclaimed."""
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, ["raw_prices", "raw_instruments"])
    r = runner.invoke(
        app, ["mapping", "list", "-m", str(m), "--manifest", str(manifest), "--format", "json"]
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert "price" in data["unmatched_entities"]
    assert set(data["unclaimed_models"]) == {"raw_prices", "raw_instruments"}


def test_mapping_list_rank_for_puts_best_match_first(tmp_path: Path):
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    manifest = tmp_path / "manifest.json"
    # stg_price would be claimed by the staging layer, so leave it out; rank the rest.
    _write_manifest(manifest, ["dim_customer", "price_history", "raw_x"])
    r = runner.invoke(
        app,
        [
            "mapping", "list", "-m", str(m),
            "--manifest", str(manifest), "--rank-for", "price", "--format", "json",
        ],
    )
    assert r.exit_code == 0, r.output
    ranked = json.loads(r.stdout)["unclaimed_models"]
    # price_history (contains "price") ranks ahead of dim_customer / raw_x
    assert ranked[0] == "price_history"


def test_mapping_set_then_drift_is_clean(tmp_path: Path):
    """The write path closes the loop: after mapping an entity to its stg_ model, the
    manifest set-diff `mdl mapping list` reports no unmatched entity for it."""
    scaffold_demo(tmp_path)
    m = tmp_path / "model"
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, ["weird_price"])
    runner.invoke(app, ["mapping", "set", "price", "weird_price", "-m", str(m)])
    r = runner.invoke(
        app, ["mapping", "list", "-m", str(m), "--manifest", str(manifest), "--format", "json"]
    )
    data = json.loads(r.stdout)
    assert "price" not in data["unmatched_entities"]
    assert "weird_price" not in data["unclaimed_models"]
