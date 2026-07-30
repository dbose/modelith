"""Ontology read API for the canvas (E1: make the ontology visible).

Endpoints:
  GET /api/ontology/search?q=      ranked term cards from loaded vocabularies
  GET /api/ontology/term?ref=      full card: definition, broader/narrower
  GET /api/ontology/stack          four-layer view: model terms per layer +
                                   alignment edges (upward) + external targets
  GET /api/ontology/coverage       the CDO coverage report

The registry is cached against a fingerprint of the vocabulary files + the
declared ontology_stack, mirroring the model cache: git stays the source of
truth, vendored vocab changes show on refresh.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from mdl_ontology import build_registry, coverage_report
from mdl_ontology.registry import OntologyRegistry

from mdl_core.ir import Model

_VOCAB_EXTS = {".ttl", ".rdf", ".owl", ".jsonld", ".nt", ".n3"}


def _vocab_fingerprint(model_dir: Path, stack: list[dict]) -> tuple:
    import json

    n, mtime, size = 0, 0.0, 0
    for entry in stack or []:
        path = entry.get("path")
        if not path:
            continue
        base = model_dir / path
        files = [base] if base.is_file() else (
            [p for p in base.rglob("*") if p.suffix.lower() in _VOCAB_EXTS]
            if base.exists()
            else []
        )
        for f in files:
            st = f.stat()
            n += 1
            mtime = max(mtime, st.st_mtime)
            size += st.st_size
    return (json.dumps(stack, sort_keys=True, default=str), n, mtime, size)


class RegistryCache:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self._fp: tuple | None = None
        self._registry: OntologyRegistry | None = None

    def get(self, model: Model) -> OntologyRegistry:
        stack = model.config.ontology_stack
        fp = _vocab_fingerprint(self.model_dir, stack)
        if self._registry is None or self._fp != fp:
            reg = build_registry(self.model_dir, stack)
            reg.load()
            self._registry = reg
            self._fp = fp
        return self._registry


def ontology_router(model_dir: Path, load_model) -> APIRouter:
    """`load_model` is a callable returning the current (cached) Model."""
    router = APIRouter(prefix="/api/ontology")
    cache = RegistryCache(model_dir)

    @router.get("/search")
    def search(q: str = "", limit: int = 20) -> JSONResponse:
        model = load_model()
        reg = cache.get(model)
        hits = reg.search(q, limit=limit) if q.strip() else []
        return JSONResponse(
            {
                "query": q,
                "vocabularies": sorted(reg.sources),
                "loaded_terms": len(set(reg.graph.subjects())),
                "results": [
                    {
                        "iri": h.iri,
                        "prefixed": h.prefixed,
                        "label": h.label,
                        "definition": h.definition,
                        "source": h.source,
                    }
                    for h in hits
                ],
            }
        )

    @router.get("/term")
    def term(ref: str) -> JSONResponse:
        model = load_model()
        reg = cache.get(model)
        card = reg.describe(ref)
        if card is None:
            return JSONResponse({"error": f"unknown term {ref!r}"}, status_code=404)
        return JSONResponse(card)

    @router.get("/stack")
    def stack() -> JSONResponse:
        """The four-layer view: every model object that declares an ontology
        layer, grouped, with its upward alignment (resolved against loaded
        vocabularies where possible)."""
        model = load_model()
        reg = cache.get(model)
        layers: dict[str, list[dict]] = {
            "industry": [],
            "core": [],
            "domain": [],
            "specialised": [],
        }
        external: dict[str, dict] = {}

        def _add(obj, obj_kind: str) -> None:
            ont = obj.ontology
            if ont is None or ont.layer is None:
                return
            aligned = None
            if ont.aligns_to:
                card = reg.describe(ont.aligns_to)
                aligned = {
                    "ref": ont.aligns_to,
                    "predicate": ont.alignment,
                    "resolved": card is not None,
                    "label": card["label"] if card else ont.aligns_to.split(":")[-1],
                }
                if card:
                    external[card["iri"]] = {
                        "iri": card["iri"],
                        "prefixed": card["prefixed"],
                        "label": card["label"],
                        "definition": card["definition"],
                        "source": card["source"],
                    }
            layers.setdefault(ont.layer, []).append(
                {
                    "id": obj.id,
                    "name": obj.name,
                    "kind": obj_kind,
                    "definition": obj.definition,
                    "aligned_to": aligned,
                    "no_industry_equivalent": ont.no_industry_equivalent,
                }
            )

        for ce in model.conceptual_entities.values():
            _add(ce, "conceptual_entity")
        for t in model.terms.values():
            _add(t, "term")

        return JSONResponse(
            {
                "layers": layers,
                "external_terms": sorted(external.values(), key=lambda t: t["label"]),
                "vocabularies": sorted(cache.get(model).sources),
            }
        )

    @router.get("/coverage")
    def coverage() -> JSONResponse:
        model = load_model()
        rpt = coverage_report(model)
        return JSONResponse(
            {
                "coverage_pct": rpt.coverage_pct,
                "total_core": rpt.total_core,
                "core_with_industry": rpt.core_with_industry,
                "core_exempt": rpt.core_exempt,
                "core_uncovered": rpt.core_uncovered,
                "by_layer": rpt.by_layer,
            }
        )

    return router
