"""MetricFlow emitter (spec §8).

Emits dbt MetricFlow `semantic_models` and `metrics` YAML from the logical model.
The entity graph already holds the join graph MetricFlow needs, so this is a
projection, not a new authoring surface:

- one semantic_model per logical entity, `model: ref('<physical name>')`
- entities[]: primary (business key) + foreign (from relationships)
- dimensions[]: non-key, non-measure attributes (time dims flagged for date/timestamp)
- measures[]: attributes with role == measure
- metrics[]: a simple `sum`/`count` metric per measure (starting point)
"""

from __future__ import annotations

from mdl_core.ir import LogicalEntity, Model
from mdl_core.yaml_io import dump_str

_TIME_BASES = {"date", "timestamp"}


def emit_metricflow(model: Model, target: str) -> str:
    semantic_models = []
    metrics = []

    pt_by_logical = {
        pt.realises: pt for pt in model.physical_tables.values() if pt.target == target
    }
    fks_by_entity = _fks_by_entity(model)

    for le in sorted(model.logical_entities.values(), key=lambda e: e.name):
        pt = pt_by_logical.get(le.id)
        phys_name = pt.name.lower() if pt else le.name

        entities = []
        bk = _business_key(le)
        if bk:
            entities.append({"name": le.name, "type": "primary", "expr": bk})
        for col, to_name in fks_by_entity.get(le.id, []):
            entities.append({"name": to_name, "type": "foreign", "expr": col})

        dimensions = []
        measures = []
        for attr in le.attributes:
            if attr.role == "business_key":
                continue
            if attr.role == "measure":
                measures.append({"name": attr.name, "agg": "sum", "expr": attr.name})
                continue
            dim = {"name": attr.name, "type": "categorical", "expr": attr.name}
            base = (attr.domain or "").lower()
            if base in _TIME_BASES:
                dim["type"] = "time"
                dim["type_params"] = {"time_granularity": "day"}
            dimensions.append(dim)

        sm = {
            "name": le.name,
            "model": f"ref('{phys_name}')",
            "entities": entities,
        }
        if dimensions:
            sm["dimensions"] = dimensions
        if measures:
            sm["measures"] = measures
        semantic_models.append(sm)

        # one metric per measure as a starting point (spec: projection, not authoring)
        for m in measures:
            metrics.append(
                {
                    "name": f"total_{m['name']}",
                    "type": "simple",
                    "type_params": {"measure": m["name"]},
                }
            )

    doc = {"semantic_models": semantic_models}
    if metrics:
        doc["metrics"] = metrics
    return dump_str(doc)


def _business_key(le: LogicalEntity) -> str | None:
    for a in le.attributes:
        if a.role == "business_key":
            return a.name
    return None


def _fks_by_entity(model: Model) -> dict[str, list[tuple[str, str]]]:
    attr_name = {a.id: a.name for le in model.logical_entities.values() for a in le.attributes}
    le_name = {le.id: le.name for le in model.logical_entities.values()}
    out: dict[str, list[tuple[str, str]]] = {}
    for rel in model.relationships.values():
        col = attr_name.get(rel.from_.attributes[0]) if rel.from_.attributes else None
        to_name = le_name.get(rel.to.entity)
        if col and to_name:
            out.setdefault(rel.from_.entity, []).append((col, to_name))
    return out
