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


def _term_map(tm) -> dict | None:
    """Project an R2RML TermMap override to the canvas wire shape, or None."""
    if tm is None:
        return None
    out = {
        "subject_template": tm.subject_template,
        "class_iri": tm.class_iri,
        "predicate_iri": tm.predicate_iri,
        "datatype": tm.datatype,
    }
    return out if any(out.values()) else None


def _ontology_ref(ref) -> dict:
    """Project one OntologyRef to the canvas wire shape (spec §1)."""
    return {
        "predicate": ref.predicate,
        "uri": ref.uri,
        "layer": ref.layer,
        "resolved_via": ref.resolved_via,
        "resolved_by": ref.resolved_by,
        "confidence": ref.confidence,
        "resolved_at": ref.resolved_at,
        "approved_at": ref.approved_at,
        "status": ref.status,
    }


def _legacy_ontology(obj) -> dict | None:
    """Back-compat single-alignment view for older canvas code, from the object's
    primary ref + its own layer. New code should read `ontology_refs`."""
    ref = obj.primary_ref
    if ref is None and obj.ontology_layer is None:
        return None
    return {
        "aligns_to": ref.uri if ref else None,
        "alignment": ref.predicate if ref else None,
        "layer": obj.ontology_layer,
        "status": ref.status if ref else None,
    }


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


def scoped_ulids(model: Model, subject_area: str) -> set[str]:
    """The LOGICAL entities in a subject area: those realising one of its members,
    plus those whose conceptual entity is homed there. Both, because the two answer
    different questions (see SubjectArea's docstring) and a scoped view wants the
    union."""
    sa = model.subject_areas.get(subject_area)
    if sa is None:
        return set()
    ce_ids = set(sa.members)
    ce_ids |= {ce.id for ce in model.conceptual_entities.values() if ce.subject_area == sa.id}
    return {le.id for le in model.logical_entities.values() if le.realises in ce_ids}


def project(model: Model, *, subject_area: str | None = None) -> dict:
    """Serialise the model for the canvas. With `subject_area`, scope it to that
    area: entities in scope, relationships with BOTH ends in scope (a half-edge
    breaks the React Flow render), and the counts recomputed. The subject_areas
    list itself is never filtered — the picker needs all of them."""
    sa_by_id = {sa.id: sa for sa in model.subject_areas.values()}
    in_scope = scoped_ulids(model, subject_area) if subject_area else None
    ce_by_id = model.conceptual_entities

    entities = []
    for le in sorted(model.logical_entities.values(), key=lambda e: e.name):
        if in_scope is not None and le.id not in in_scope:
            continue
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
                # Object layer + the full ontology_refs list (spec §1). A legacy
                # single `ontology` object is kept for back-compat with older canvas
                # code, derived from the primary ref.
                "ontology_layer": ce.ontology_layer,
                "no_industry_equivalent": ce.no_industry_equivalent,
                "ontology_refs": [_ontology_ref(r) for r in ce.ontology_refs],
                "ontology": _legacy_ontology(ce),
                "stewardship": (
                    {"owner": ce.stewardship.owner, "steward": ce.stewardship.steward}
                    if ce.stewardship
                    else None
                ),
            }
        # entity-level UDPs merge conceptual + logical (logical wins on key clash)
        entity_udp = {**((ce.udp or {}) if ce else {}), **(le.udp or {})} or None
        # category role: is this entity a supertype or subtype in a category?
        category_role = None
        for cat in model.categories.values():
            if cat.supertype == le.id:
                category_role = {"role": "supertype", "category": cat.name}
                break
            if le.id in cat.subtypes:
                category_role = {
                    "role": "subtype",
                    "category": cat.name,
                    "materialization": cat.materialization,
                }
                break
        entities.append(
            {
                "id": le.id,
                "name": le.name,
                "definition": le.definition,
                "pattern": le.pattern,
                "conceptual": conceptual,
                "udp": entity_udp,
                "category": category_role,
                "term_map": _term_map(le.term_map),
                "attributes": [
                    {
                        "id": a.id,
                        "name": a.name,
                        "definition": a.definition,
                        "domain": a.domain,
                        "role": a.role,
                        "nullable": a.nullable,
                        "ontology_iri": a.aligned_uri,
                        "ontology_refs": [_ontology_ref(r) for r in a.ontology_refs],
                        "enum_values": _enum_values(model, a.domain),
                        "term_map": _term_map(a.term_map),
                        "udp": a.udp or None,
                    }
                    for a in le.attributes
                ],
                "key_groups": [
                    {
                        "id": kg.id,
                        "name": kg.name,
                        "definition": kg.definition,
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
        if in_scope is not None and not (
            rel.from_.entity in in_scope and rel.to.entity in in_scope
        ):
            continue
        relationships.append(
            {
                "id": rel.id,
                "name": rel.name,
                "definition": rel.definition,
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
        if in_scope is None or pt.realises in in_scope
    ]

    return {
        "project": {
            "name": model.config.name,
            "dbt_target": model.config.dbt_target,
            "platform_targets": list(model.config.platform_targets),
            "kg_base_iri": model.config.kg_base_iri,
        },
        "scope": subject_area,
        "subject_areas": [
            {
                "id": sa.id,
                "name": sa.name,
                "definition": sa.definition,
                "members": list(sa.members),
                "member_count": len(sa.members),
            }
            for sa in sorted(model.subject_areas.values(), key=lambda s: s.name)
        ],
        "entities": entities,
        "relationships": relationships,
        "categories": [
            {
                "id": cat.id,
                "name": cat.name,
                "definition": cat.definition,
                "supertype": cat.supertype,
                "subtypes": list(cat.subtypes),
                "discriminator": cat.discriminator,
                "complete": cat.complete,
                "exclusive": cat.exclusive,
                "materialization": cat.materialization,
            }
            for cat in sorted(model.categories.values(), key=lambda c: c.name)
            if in_scope is None or cat.supertype in in_scope
        ],
        "physical": physical,
        "counts": {
            "entities": len(entities),
            "relationships": len(relationships),
            "attributes": sum(len(e["attributes"]) for e in entities),
            # The whole model, regardless of scope — a "Whole model" row in a picker
            # has to show the real total, not however many survived the filter.
            "entities_total": len(model.logical_entities),
        },
    }
