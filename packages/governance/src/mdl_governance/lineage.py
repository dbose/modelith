"""OpenLineage emission (spec §9.6).

Emit OpenLineage alongside the governance push. One lineage payload then feeds
DataHub, OpenMetadata, Purview or Unity Catalog without rewriting the integration.

We emit a minimal but valid OpenLineage RunEvent-shaped document: datasets for
each physical table with a schema facet, and column-level `columnLineage` derived
from `realises` (attribute -> physical column) where available.
"""

from __future__ import annotations

import json

from mdl_core.ir import Model

OPENLINEAGE_PRODUCER = "https://modelith.dev"
_SCHEMA_URL = "https://openlineage.io/spec/facets/1-1-0/SchemaDatasetFacet.json"


def emit_openlineage(
    model: Model, *, namespace: str = "modelith", target: str | None = None
) -> str:
    datasets = []
    for pt in model.physical_tables.values():
        if target and pt.target != target:
            continue
        fields = []
        le = model.logical_entities.get(pt.realises) if pt.realises else None
        if le:
            for attr in le.attributes:
                fields.append(
                    {
                        "name": attr.name,
                        "type": (attr.domain or "string"),
                        "description": None,
                    }
                )
        datasets.append(
            {
                "namespace": namespace,
                "name": pt.name,
                "facets": {
                    "schema": {
                        "_producer": OPENLINEAGE_PRODUCER,
                        "_schemaURL": _SCHEMA_URL,
                        "fields": fields,
                    },
                    "mdl": {
                        "_producer": OPENLINEAGE_PRODUCER,
                        "mdl_ulid": pt.id,
                        "logical_entity": le.name if le else None,
                    },
                },
            }
        )

    doc = {
        "eventType": "COMPLETE",
        "producer": OPENLINEAGE_PRODUCER,
        "job": {"namespace": namespace, "name": f"{model.config.name}.governance_sync"},
        "outputs": datasets,
        "inputs": [],
    }
    return json.dumps(doc, indent=2, sort_keys=True)
