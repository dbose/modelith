import json
import sys
from pathlib import Path

import pytest

_CORE_TESTS = Path(__file__).resolve().parents[2] / "core" / "tests"
_REV_TESTS = Path(__file__).resolve().parents[2] / "reverse" / "tests"
sys.path.insert(0, str(_CORE_TESTS))
sys.path.insert(0, str(_REV_TESTS))

from manifest_fixtures import manifest_from_model  # noqa: E402
from model_builders import write_model  # noqa: E402


@pytest.fixture
def workspace(tmp_path):
    """An IBoR-shaped workspace: model/ + transform/ (generated) + manifest with
    colleague drift (added column, extra hand model)."""
    from mdl_lsp.workspace import ModelWorkspace

    from mdl_core.repo import ModelRepo
    from mdl_emit_dbt.emitter import DbtEmitter

    root = tmp_path
    model_dir = root / "model"
    model_dir.mkdir()
    write_model(model_dir)
    dbt = root / "transform"
    (dbt / "models").mkdir(parents=True)
    (dbt / "dbt_project.yml").write_text("name: t\nprofile: t\n")
    repo = ModelRepo.load(model_dir)
    DbtEmitter(repo.model, "duckdb_dev").generate(dbt, write=True)

    # manifest = perfect mirror + colleague drift
    raw = manifest_from_model(repo.model, "duckdb_dev")
    raw["nodes"]["model.testproj.counterparty"]["columns"]["rating_grade"] = {
        "name": "rating_grade", "data_type": "VARCHAR",
    }
    raw["nodes"]["model.t.fct_hand"] = {
        "resource_type": "model", "name": "fct_hand",
        "columns": {"trade_id": {"name": "trade_id", "data_type": "VARCHAR"}},
        "config": {}, "meta": {}, "tags": [],
    }
    (dbt / "target").mkdir()
    (dbt / "target" / "manifest.json").write_text(json.dumps(raw))
    (dbt / "models" / "fct_hand.sql").write_text("select trade_id from {{ ref('stg_trade') }}\n")
    # colleague also declares the added column in schema.yml's generated region
    sy = dbt / "models" / "schema.yml"
    sy.write_text(sy.read_text().replace(
        "      - name: legal_name",
        "      - name: rating_grade\n        data_type: VARCHAR\n      - name: legal_name", 1))
    return ModelWorkspace(root)
