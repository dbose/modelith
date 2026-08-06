"""Project a Model into the canvas-facing JSON shape.

The canvas wants a pre-joined graph: logical entities carrying their conceptual
context (definition, ontology, stewardship, subject area) and attribute rows, plus
relationship edges keyed by ULID. One projection function keeps the wire shape in
a single place, versioned alongside the canvas.
"""

from __future__ import annotations

from mdl_core.ir import Model


def _enum_values(model: Model, domain_name: str | None) -> list | None:
    """Enumeration values for an attribute's domain (inline or via a CodeSet), so
    the canvas can flag enum-constrained attributes. None when not an enumeration."""
    dom = model.domain_by_name(domain_name)
    if dom is None:
        return None
    if dom.allowed_values:
        return list(dom.allowed_values)
    if dom.value_set:
        cs = model.code_set_by_name(dom.value_set)
        if cs and cs.values:
            return [v.code for v in cs.values]
    return None


def where_used(model: Model, conceptual_id: str) -> list[dict]:
    """Forward-traverse a conceptual entity to the dbt models that realise it:
    conceptual.id -> logical entities (realises == id) -> physical tables
    (realises == logical.id). `ConceptualEntity.realised_by` exists in the IR but
    is never populated on load, so we compute the reverse links here. This is the
    "where used" reassurance an SME needs before editing a term."""
    used: list[dict] = []
    for le in sorted(model.logical_entities.values(), key=lambda e: e.name):
        if le.realises != conceptual_id:
            continue
        physical = [
            {"name": pt.name, "target": pt.target, "materialization": pt.materialization}
            for pt in sorted(model.physical_tables.values(), key=lambda p: p.name)
            if pt.realises == le.id
        ]
        used.append(
            {
                "logical_entity": le.name,
                "logical_id": le.id,
                "unmanaged": bool(le.unmanaged),
                "physical": physical,
            }
        )
    return used


def project(model: Model) -> dict:
    sa_by_id = {sa.id: sa for sa in model.subject_areas.values()}
    ce_by_id = model.conceptual_entities

    entities = []
    for le in sorted(model.logical_entities.values(), key=lambda e: e.name):
        ce = ce_by_id.get(le.realises) if le.realises else None
        sa = sa_by_id.get(ce.subject_area) if ce and ce.subject_area else None
        conceptual = None
        if ce:
            conceptual = {
                "id": ce.id,
                "name": ce.name,
                "definition": ce.definition,
                "synonyms": list(ce.synonyms),
                "subject_area": {"id": sa.id, "name": sa.name} if sa else None,
                "ontology": (
                    {
                        "aligns_to": ce.ontology.aligns_to,
                        "alignment": ce.ontology.alignment,
                        "layer": ce.ontology.layer,
                    }
                    if ce.ontology
                    else None
                ),
                "stewardship": (
                    {"owner": ce.stewardship.owner, "steward": ce.stewardship.steward}
                    if ce.stewardship
                    else None
                ),
            }
        entities.append(
            {
                "id": le.id,
                "name": le.name,
                "pattern": le.pattern,
                "conceptual": conceptual,
                "attributes": [
                    {
                        "id": a.id,
                        "name": a.name,
                        "domain": a.domain,
                        "role": a.role,
                        "nullable": a.nullable,
                        "ontology_iri": a.ontology.aligns_to if a.ontology else None,
                        "enum_values": _enum_values(model, a.domain),
                    }
                    for a in le.attributes
                ],
                "key_groups": [
                    {
                        "id": kg.id,
                        "name": kg.name,
                        "type": kg.type,
                        "members": list(kg.members),
                    }
                    for kg in model.key_groups.values()
                    if kg.entity == le.id
                ],
            }
        )

    attr_owner = {}
    for le in model.logical_entities.values():
        for a in le.attributes:
            attr_owner[a.id] = le.id

    relationships = []
    for rel in sorted(model.relationships.values(), key=lambda r: r.name):
        relationships.append(
            {
                "id": rel.id,
                "name": rel.name,
                "from": {
                    "entity": rel.from_.entity,
                    "attributes": list(rel.from_.attributes),
                },
                "to": {"entity": rel.to.entity, "attributes": list(rel.to.attributes)},
                "cardinality": rel.cardinality,
                "identifying": rel.identifying,
                "optionality": rel.optionality,
            }
        )

    physical = [
        {
            "id": pt.id,
            "target": pt.target,
            "realises": pt.realises,
            "name": pt.name,
            "materialization": pt.materialization,
        }
        for pt in sorted(model.physical_tables.values(), key=lambda p: p.name)
    ]

    return {
        "project": {
            "name": model.config.name,
            "dbt_target": model.config.dbt_target,
            "platform_targets": list(model.config.platform_targets),
        },
        "subject_areas": [
            {"id": sa.id, "name": sa.name, "definition": sa.definition}
            for sa in sorted(model.subject_areas.values(), key=lambda s: s.name)
        ],
        "entities": entities,
        "relationships": relationships,
        "physical": physical,
        "counts": {
            "entities": len(entities),
            "relationships": len(relationships),
            "attributes": sum(len(e["attributes"]) for e in entities),
        },
    }
