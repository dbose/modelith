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
    ont = obj.ontology
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
        "ontology": (
            {
                "aligns_to": ont.aligns_to,
                "alignment": ont.alignment,
                "layer": ont.layer,
                "status": ont.status,
            }
            if ont
            else None
        ),
        # where-used only applies to conceptual entities (terms aren't realised)
        "where_used": where_used(model, obj.id) if is_ce else [],
    }


def glossary_router(load_model) -> APIRouter:
    """`load_model` is a callable returning the current (cached) Model."""
    router = APIRouter(prefix="/api/glossary")

    def _all(model: Model):
        return [*model.conceptual_entities.values(), *model.terms.values()]

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
