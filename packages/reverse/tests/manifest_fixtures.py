"""Build synthetic dbt manifest.json dicts from a model projection.

The acceptance path (spec §6.1) must work against `dbt compile --empty` output,
which we emulate here: a manifest with `nodes` of resource_type=model carrying
`columns[].data_type` and `config.contract.enforced`. This lets drift tests run
with no warehouse and no dbt install, while exercising the real manifest reader.
"""

from __future__ import annotations

from typing import Any

from mdl_core.ir import Model
from mdl_reverse.projection import project_model


def manifest_from_model(model: Model, target: str) -> dict[str, Any]:
    """A manifest that perfectly matches the model — i.e. zero drift baseline."""
    expected = project_model(model, target)
    nodes: dict[str, Any] = {}
    for name, em in expected.items():
        uid = f"model.testproj.{name}"
        columns = {
            c.name: {
                "name": c.name,
                "data_type": c.data_type,
                "description": None,
            }
            for c in em.columns.values()
        }
        nodes[uid] = {
            "resource_type": "model",
            "name": name,
            "columns": columns,
            "config": {"contract": {"enforced": em.contract_enforced}},
            "description": em.description,
            "meta": {},
            "tags": [],
        }
        # relationships tests as separate test nodes
        for col, to in em.relationships:
            tid = f"test.testproj.relationships_{name}_{col}"
            nodes[tid] = {
                "resource_type": "test",
                "name": f"relationships_{name}_{col}",
                "attached_node": uid,
                "test_metadata": {
                    "name": "relationships",
                    # real dbt stores this as a ref() expression; the reader normalises
                    "kwargs": {"column_name": col, "to": f"ref('{to}')"},
                },
            }
    return {
        "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
        "nodes": nodes,
    }
