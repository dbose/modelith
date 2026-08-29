"""Glossary read API for the SME app (collaboration model §5.1).

The canvas projection (`/api/model`) emits only logical entities; `/api/ontology/
stack` only includes objects that declare an ontology *layer*. An SME glossary
needs *every* term — both `ConceptualEntity` and `Term` kinds — with the fields
the SME cares about (definition, synonyms, steward, subject area, alignment) plus
"where used". This router provides exactly that, read-only, available in both
serve modes (git remains the source of truth).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from mdl_core.ir import ConceptualEntity, Model
from mdl_server.projection import where_used


def _term_card(model: Model, obj) -> dict:
    ref = obj.primary_ref
    is_ce = isinstance(obj, ConceptualEntity)
    sa = None
    if is_ce and obj.subject_area:
        sa_obj = model.subject_areas.get(obj.subject_area)
        sa = {"id": sa_obj.id, "name": sa_obj.name} if sa_obj else {"id": obj.subject_area}
    return {
        "id": obj.id,
        "kind": obj.kind.value,
        "name": obj.name,
        "definition": obj.definition,
        "synonyms": list(obj.synonyms),
        "subject_area": sa,
        "stewardship": (
            {"owner": obj.stewardship.owner, "steward": obj.stewardship.steward}
            if is_ce and obj.stewardship
            else None
        ),
        "ontology_layer": obj.ontology_layer,
        "ontology_refs": [
            {
                "predicate": r.predicate,
                "uri": r.uri,
                "layer": r.layer,
                "resolved_via": r.resolved_via,
                "status": r.status,
            }
            for r in obj.ontology_refs
        ],
        "ontology": (
            {
                "aligns_to": ref.uri,
                "alignment": ref.predicate,
                "layer": obj.ontology_layer,
                "status": ref.status,
            }
            if ref
            else None
        ),
        # where-used only applies to conceptual entities (terms aren't realised)
        "where_used": where_used(model, obj.id) if is_ce else [],
    }


# The meaning-fields the catalog masters when it is the source of truth. When
# source_of_truth == "collibra" these are read-only in the /sme app; the alignment
# *proposal* is deliberately NOT in this set — it's Modelith's to own either way.
CATALOG_OWNED_FIELDS = ["definition", "synonyms", "stewardship"]


def _glossary_config(model: Model) -> dict:
    """The source-of-truth switch + catalog deep-link info for the /sme banner."""
    g = getattr(model.config, "glossary", None)
    sot = getattr(g, "source_of_truth", "git") if g else "git"
    catalog_url = getattr(g, "catalog_url", None) if g else None
    catalog_name = getattr(g, "catalog_name", "Collibra") if g else "Collibra"
    catalog_owns = sot != "git"
    return {
        "source_of_truth": sot,
        "catalog_url": catalog_url,
        "catalog_name": catalog_name,
        # fields the /sme editor must render read-only (empty when git masters)
        "catalog_owned_fields": CATALOG_OWNED_FIELDS if catalog_owns else [],
    }


def glossary_router(load_model) -> APIRouter:
    """`load_model` is a callable returning the current (cached) Model."""
    router = APIRouter(prefix="/api/glossary")

    def _all(model: Model):
        return [*model.conceptual_entities.values(), *model.terms.values()]

    @router.get("/config")
    def config() -> JSONResponse:
        return JSONResponse(_glossary_config(load_model()))

    @router.get("/terms")
    def terms(subject_area: str = "", q: str = "") -> JSONResponse:
        model = load_model()
        cards = [_term_card(model, o) for o in _all(model)]
        if subject_area:
            cards = [
                c
                for c in cards
                if (c["subject_area"] or {}).get("id") == subject_area
            ]
        if q.strip():
            ql = q.strip().lower()
            cards = [
                c
                for c in cards
                if ql in c["name"].lower()
                or ql in (c["definition"] or "").lower()
                or any(ql in s.lower() for s in c["synonyms"])
            ]
        cards.sort(key=lambda c: c["name"].lower())
        subject_areas = [
            {"id": sa.id, "name": sa.name, "definition": sa.definition}
            for sa in sorted(model.subject_areas.values(), key=lambda s: s.name)
        ]
        return JSONResponse({"terms": cards, "subject_areas": subject_areas})

    @router.get("/term/{ulid}")
    def term(ulid: str) -> JSONResponse:
        model = load_model()
        obj = model.conceptual_entities.get(ulid) or model.terms.get(ulid)
        if obj is None:
            return JSONResponse({"error": f"no term {ulid}"}, status_code=404)
        return JSONResponse(_term_card(model, obj))

    return router


__all__ = ["glossary_router", "_term_card"]
