"""OSI v0.1.1 emit + import (spec §4.2 object mapping).

Pinned to the tagged osi-0.1.1-rc1 release. All v0.1.1-specific structure lives
here; the IR never mentions OSI. Mapping (§4.2):

  Model package                         -> semantic_model
  Logical entity w/ physical realisation-> datasets[] (source = physical target)
  Business/unique keys                  -> primary_key / unique_keys
  Attribute                             -> fields[], expression.dialects[] per target
  Time attribute                        -> fields[].dimension.is_time: true
  Relationship (many->one)              -> relationships[] (from many side)
  Metric                                -> metrics[] with dialect expressions
  Synonyms/definition/ontology IRI      -> ai_context (instructions/synonyms/examples)
  Anything Modelith-specific            -> custom_extensions (vendor_name: MODELITH)

Multi-dialect expressions (§4.3) are close to free because the model holds
multi-target physical models: we emit ANSI_SQL plus one entry per configured
platform target.
"""

from __future__ import annotations

from mdl_core.ir import LogicalEntity, Model
from mdl_core.yaml_io import dump_str, load_str

OSI_VERSION = "0.1.1"
VENDOR = "MODELITH"

_TIME_BASES = {"date", "timestamp"}


def emit(model: Model, *, targets: list[str] | None = None) -> str:
    """Emit an OSI v0.1.1 semantic_model document as YAML text."""
    targets = targets or model.config.platform_targets or ["ansi"]
    datasets = []
    relationships = []
    metrics = []

    pt_by_target: dict[str, dict[str, object]] = {}
    for pt in model.physical_tables.values():
        pt_by_target.setdefault(pt.target, {})[pt.realises] = pt

    fks = _fks(model)

    for le in sorted(model.logical_entities.values(), key=lambda e: e.name):
        ds = _dataset(model, le, targets, pt_by_target)
        datasets.append(ds)
        for col, to_name, to_cols in fks.get(le.id, []):
            relationships.append(
                {
                    "name": f"{le.name}_to_{to_name}",
                    "from": {"dataset": le.name, "columns": [col]},
                    "to": {"dataset": to_name, "columns": to_cols},
                }
            )
        for attr in le.attributes:
            if attr.role == "measure":
                metrics.append(
                    {
                        "name": f"total_{attr.name}",
                        "expression": {
                            "dialects": _dialect_exprs(f"SUM({attr.name})", targets)
                        },
                    }
                )

    doc = {
        "osi_version": OSI_VERSION,
        "semantic_model": {
            "name": model.config.name,
            "datasets": datasets,
        },
    }
    if relationships:
        doc["semantic_model"]["relationships"] = relationships
    if metrics:
        doc["semantic_model"]["metrics"] = metrics
    return dump_str(doc)


def _dataset(model: Model, le: LogicalEntity, targets, pt_by_target) -> dict:
    # source from a physical target if available
    source = None
    for tgt in targets:
        pt = pt_by_target.get(tgt, {}).get(le.id)
        if pt is not None:
            source = {"target": tgt, "table": pt.name}
            break

    business_keys = [a.name for a in le.attributes if a.role == "business_key"]
    fields = []
    for attr in le.attributes:
        f = {
            "name": attr.name,
            "expression": {"dialects": _dialect_exprs(attr.name, targets)},
        }
        base = (attr.domain or "").lower()
        if base in _TIME_BASES:
            f["dimension"] = {"is_time": True}
        # ai_context + custom_extensions carry ontology/glossary (§4.2, §4.3)
        ai, ext = _attr_context(attr)
        if ai:
            f["ai_context"] = ai
        if ext:
            f["custom_extensions"] = ext
        fields.append(f)

    ds = {
        "name": le.name,
        "fields": fields,
    }
    if source:
        ds["source"] = source
    if business_keys:
        ds["primary_key"] = business_keys

    ai, ext = _entity_context(model, le)
    if ai:
        ds["ai_context"] = ai
    if ext:
        ds["custom_extensions"] = ext
    return ds


def _attr_context(attr) -> tuple[dict, dict]:
    ai: dict = {}
    ext: dict = {}
    if attr.ontology and attr.ontology.aligns_to:
        ai["instructions"] = f"Aligned to ontology term {attr.ontology.aligns_to}"
        ext = {"vendor_name": VENDOR, "ontology_iri": attr.ontology.aligns_to}
        if attr.ontology.alignment:
            ext["ontology_alignment"] = attr.ontology.alignment
    return ai, ext


def _entity_context(model: Model, le: LogicalEntity) -> tuple[dict, dict]:
    ai: dict = {}
    ext: dict = {"vendor_name": VENDOR, "mdl_ulid": le.id}
    ce = model.conceptual_entities.get(le.realises) if le.realises else None
    if ce:
        if ce.definition:
            ai["instructions"] = ce.definition.strip()
        if ce.synonyms:
            ai["synonyms"] = list(ce.synonyms)
        if ce.ontology and ce.ontology.aligns_to:
            ext["ontology_iri"] = ce.ontology.aligns_to
            if ce.ontology.layer:
                ext["ontology_layer"] = ce.ontology.layer
        ext["glossary_term"] = ce.name
    return ai, ext


def _dialect_exprs(expr: str, targets: list[str]) -> list[dict]:
    """ANSI_SQL plus one entry per platform target (§4.3 multi-dialect)."""
    out = [{"dialect": "ANSI_SQL", "sql": expr}]
    for tgt in targets:
        dialect = _dialect_name(tgt)
        out.append({"dialect": dialect, "sql": expr})
    # de-dup while preserving order
    seen = set()
    deduped = []
    for e in out:
        if e["dialect"] in seen:
            continue
        seen.add(e["dialect"])
        deduped.append(e)
    return deduped


def _dialect_name(target: str) -> str:
    for p in ("snowflake", "redshift", "iceberg", "trino", "duckdb"):
        if target.startswith(p):
            return p.upper()
    return "ANSI_SQL"


def _fks(model: Model) -> dict[str, list[tuple[str, str, list[str]]]]:
    attr_name = {a.id: a.name for le in model.logical_entities.values() for a in le.attributes}
    le_name = {le.id: le.name for le in model.logical_entities.values()}
    bk = {}
    for le in model.logical_entities.values():
        bk[le.id] = [a.name for a in le.attributes if a.role == "business_key"]
    out: dict[str, list[tuple[str, str, list[str]]]] = {}
    for rel in model.relationships.values():
        col = attr_name.get(rel.from_.attributes[0]) if rel.from_.attributes else None
        to_name = le_name.get(rel.to.entity)
        to_cols = bk.get(rel.to.entity, [])
        if col and to_name:
            out.setdefault(rel.from_.entity, []).append((col, to_name, to_cols or [col]))
    return out


# --- import (spec §4.3: OSI import is required, not optional) ----------------


def parse(text: str) -> dict:
    """Lift an OSI v0.1.1 doc into a neutral dict the IR-builder consumes:
    {entities: [{name, source, primary_key, fields, ai_context, ext}], relationships: [...]}"""
    data = load_str(text) or {}
    sm = data.get("semantic_model") or {}
    entities = []
    for ds in sm.get("datasets", []) or []:
        fields = []
        for f in ds.get("fields", []) or []:
            ext = f.get("custom_extensions") or {}
            fields.append(
                {
                    "name": f.get("name"),
                    "is_time": bool((f.get("dimension") or {}).get("is_time")),
                    "ontology_iri": ext.get("ontology_iri"),
                }
            )
        ext = ds.get("custom_extensions") or {}
        entities.append(
            {
                "name": ds.get("name"),
                "source": ds.get("source"),
                "primary_key": ds.get("primary_key") or [],
                "fields": fields,
                "ai_context": ds.get("ai_context") or {},
                "mdl_ulid": ext.get("mdl_ulid"),
                "ontology_iri": ext.get("ontology_iri"),
                "glossary_term": ext.get("glossary_term"),
            }
        )
    relationships = []
    for rel in sm.get("relationships", []) or []:
        relationships.append(
            {
                "from": (rel.get("from") or {}).get("dataset"),
                "from_columns": (rel.get("from") or {}).get("columns") or [],
                "to": (rel.get("to") or {}).get("dataset"),
                "to_columns": (rel.get("to") or {}).get("columns") or [],
            }
        )
    return {
        "name": sm.get("name", "imported"),
        "entities": entities,
        "relationships": relationships,
    }
