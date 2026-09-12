"""The shared read/query layer for the AI surfaces (MCP server + chat participant).

Both the VS Code MCP server (agent mode) and the `@modelith` chat participant
answer questions from the same functions here, so the two frontends never drift.
The shapes are deliberately CONDENSED — sized for a chat context window — not the
full canvas projection (`mdl_server.projection`), which pre-joins everything the
React graph needs. A grounding tool wants "what exists and how it connects" in a
few hundred tokens, not the whole wire model.

Everything here is a pure read over an in-memory `Model`; nothing loads or writes.
The CLI/MCP wrappers do the `ModelRepo.load` and hand the model in.
"""

from __future__ import annotations

from mdl_core.ir import ConceptualEntity, LogicalEntity, Model, Term


def _entity_by_name(model: Model, name: str) -> LogicalEntity | None:
    """Resolve a logical entity by name or ULID. There is no name index in the IR
    (only ULID-keyed dicts and domain/code-set name lookups), so match by name
    case-insensitively, then fall back to a ULID hit."""
    lname = name.lower()
    for le in model.logical_entities.values():
        if le.name.lower() == lname:
            return le
    obj = model.logical_entities.get(name)
    return obj


def _ontology_summary(obj: ConceptualEntity | Term | LogicalEntity) -> str | None:
    """The object's canonical alignment as a single prefixed IRI, or None. Reads the
    `primary_ref` accessor (source of truth post-migration)."""
    ref = getattr(obj, "primary_ref", None)
    if ref is None:
        return None
    return ref.uri or None


def list_entities(model: Model, subject_area: str | None = None) -> list[dict]:
    """Grounding: every logical entity as {name, definition, attribute_count,
    subject_area}, sorted by name. With `subject_area` (name or ULID), scope to
    entities homed in / realising that area."""
    scope: set[str] | None = None
    if subject_area:
        sa_id = _resolve_subject_area_id(model, subject_area)
        # An unknown area name scopes to NOTHING (empty set), not to everything —
        # returning the whole model on a typo'd filter would silently mislead.
        if sa_id is None:
            return []
        sa = model.subject_areas.get(sa_id)
        ce_ids = set(sa.members) if sa else set()
        ce_ids |= {
            ce.id for ce in model.conceptual_entities.values() if ce.subject_area == sa_id
        }
        scope = {le.id for le in model.logical_entities.values() if le.realises in ce_ids}

    out: list[dict] = []
    for le in sorted(model.logical_entities.values(), key=lambda e: e.name):
        if scope is not None and le.id not in scope:
            continue
        ce = model.conceptual_entities.get(le.realises) if le.realises else None
        sa = (
            model.subject_areas.get(ce.subject_area)
            if ce and ce.subject_area
            else None
        )
        out.append(
            {
                "name": le.name,
                "definition": (le.definition or (ce.definition if ce else None)),
                "attribute_count": len(le.attributes),
                "subject_area": sa.name if sa else None,
            }
        )
    return out


def _keys_for(model: Model, le: LogicalEntity) -> list[dict]:
    """The entity's key groups (erwin Key Groups) — pk / unique / alternate / index —
    with member attribute names in key order. This is what lets a caller reason about
    normalization (candidate keys, functional dependencies) and identity.

    When no explicit `pk` KeyGroup is present, fall back to the legacy per-attribute
    `role: business_key` convention (which the IR documents as still supported): the
    business-key attributes form an inferred primary key, flagged `inferred: True` so
    a caller knows it came from the convention, not a declared KeyGroup."""
    attr_name = {a.id: a.name for a in le.attributes}
    keys: list[dict] = []
    for kg in sorted(model.key_groups.values(), key=lambda k: (k.type, k.name)):
        if kg.entity != le.id:
            continue
        keys.append(
            {
                "name": kg.name,
                "type": kg.type,
                "columns": [attr_name.get(m, m) for m in kg.members],
            }
        )
    # Legacy fallback: no declared pk KeyGroup, but business_key attributes exist.
    if not any(k["type"] == "pk" for k in keys):
        bk = [a.name for a in le.attributes if a.role == "business_key"]
        if bk:
            keys.insert(0, {"name": "pk (from business_key)", "type": "pk",
                            "columns": bk, "inferred": True})
    return keys


def get_entity(model: Model, name: str) -> dict | None:
    """Full detail for one entity by name or ULID: attributes (with types + ontology
    alignment), key groups (pk/unique/alternate), the conceptual layer, and the
    relationships it participates in. Returns None when no entity matches."""
    le = _entity_by_name(model, name)
    if le is None:
        return None
    ce = model.conceptual_entities.get(le.realises) if le.realises else None

    attributes = [
        {
            "name": a.name,
            "definition": a.definition,
            "domain": a.domain,
            "nullable": a.nullable,
            "ontology": _ontology_summary(a),
        }
        for a in le.attributes
    ]
    keys = _keys_for(model, le)

    rels: list[dict] = []
    for r in sorted(model.relationships.values(), key=lambda x: x.name):
        from_e = model.logical_entities.get(r.from_.entity)
        to_e = model.logical_entities.get(r.to.entity)
        if le.id not in (r.from_.entity, r.to.entity):
            continue
        rels.append(
            {
                "name": r.name,
                "from": from_e.name if from_e else r.from_.entity,
                "to": to_e.name if to_e else r.to.entity,
                "definition": r.definition,
            }
        )

    return {
        "name": le.name,
        "definition": le.definition or (ce.definition if ce else None),
        "pattern": le.pattern,
        "unmanaged": bool(le.unmanaged),
        "conceptual": (
            {
                "name": ce.name,
                "definition": ce.definition,
                "synonyms": list(ce.synonyms),
                "ontology": _ontology_summary(ce),
                "ontology_layer": ce.ontology_layer,
            }
            if ce
            else None
        ),
        "keys": keys,
        "attributes": attributes,
        "relationships": rels,
    }


def get_model_context(model: Model) -> dict:
    """A condensed model summary sized for a chat context window: the project name,
    counts, subject areas with their entity lists, and every relationship as a
    from->to sentence. Enough to ground an answer without shipping the whole model."""
    ce_by_id = model.conceptual_entities

    # subject areas -> the logical entities homed there
    areas: list[dict] = []
    for sa in sorted(model.subject_areas.values(), key=lambda s: s.name):
        ce_ids = set(sa.members)
        ce_ids |= {ce.id for ce in ce_by_id.values() if ce.subject_area == sa.id}
        names = sorted(
            le.name for le in model.logical_entities.values() if le.realises in ce_ids
        )
        areas.append({"name": sa.name, "definition": sa.definition, "entities": names})

    unhomed = sorted(
        le.name
        for le in model.logical_entities.values()
        if not le.realises or ce_by_id.get(le.realises) is None
    )

    relationships = []
    for r in sorted(model.relationships.values(), key=lambda x: x.name):
        from_e = model.logical_entities.get(r.from_.entity)
        to_e = model.logical_entities.get(r.to.entity)
        relationships.append(
            {
                "name": r.name,
                "from": from_e.name if from_e else r.from_.entity,
                "to": to_e.name if to_e else r.to.entity,
            }
        )

    return {
        "project": model.config.name,
        "counts": {
            "entities": len(model.logical_entities),
            "relationships": len(model.relationships),
            "subject_areas": len(model.subject_areas),
            "terms": len(model.terms),
        },
        "subject_areas": areas,
        "unassigned_entities": unhomed,
        "relationships": relationships,
    }


def entities_detail(model: Model, limit: int = 40) -> dict:
    """Compact detail for EVERY entity — attributes (name:domain, nullability), key
    groups, and relationship names — for grounding a model-wide question ("are all
    entities in BCNF?", "which lack a primary key?"). Deliberately terse: a real
    model can have 150+ entities, so each is one small record, not the full nested
    `get_entity` shape, keeping the whole thing within a chat context window.

    Capped at `limit` entities (alphabetical). `truncated` is True when the model has
    more, with `total` and `shown` so the caller can tell the user the answer covers
    only the first N and to narrow the question."""
    all_le = sorted(model.logical_entities.values(), key=lambda e: e.name)
    shown = all_le[:limit]
    ce_by_id = model.conceptual_entities

    # relationship names per entity, one pass
    rels_by_entity: dict[str, list[str]] = {}
    for r in model.relationships.values():
        from_e = model.logical_entities.get(r.from_.entity)
        to_e = model.logical_entities.get(r.to.entity)
        label = f"{from_e.name if from_e else '?'}→{to_e.name if to_e else '?'}"
        for end in (r.from_.entity, r.to.entity):
            rels_by_entity.setdefault(end, []).append(label)

    records = []
    for le in shown:
        ce = ce_by_id.get(le.realises) if le.realises else None
        records.append(
            {
                "name": le.name,
                # "col:domain" (+ "?" when nullable) — enough to reason about
                # attributes and dependencies without the full attribute objects.
                "attributes": [
                    f"{a.name}:{a.domain or '?'}{'?' if a.nullable else ''}"
                    for a in le.attributes
                ],
                "keys": [
                    {"type": kg["type"], "columns": kg["columns"]}
                    for kg in _keys_for(model, le)
                ],
                "relationships": sorted(set(rels_by_entity.get(le.id, []))),
                "ontology": _ontology_summary(ce) if ce else None,
            }
        )

    return {
        "project": model.config.name,
        "total": len(all_le),
        "shown": len(shown),
        "truncated": len(all_le) > limit,
        "entities": records,
    }


def _resolve_subject_area_id(model: Model, ref: str) -> str | None:
    """Accept a subject-area name or ULID; return its ULID, or None if unknown."""
    if ref in model.subject_areas:
        return ref
    lref = ref.lower()
    for sa in model.subject_areas.values():
        if sa.name.lower() == lref:
            return sa.id
    return None
