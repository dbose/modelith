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

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from mdl_ontology import build_registry, coverage_report
from mdl_ontology.ingest import save_ontology_upload
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


def ontology_router(model_dir: Path, load_model, *, read_only: bool = False) -> APIRouter:
    """`load_model` is a callable returning the current (cached) Model.

    Read endpoints are always available. When `read_only` is False, the upload
    endpoint (`POST /upload`) is added so the canvas can vendor a private ontology
    file into the repo and wire it into `ontology_stack`.
    """
    router = APIRouter(prefix="/api/ontology")
    cache = RegistryCache(model_dir)

    @router.get("/search")
    def search(q: str = "", limit: int = 20, within: str = "") -> JSONResponse:
        """Ranked term search. `within` scopes to one ontology id (browse phase two,
        spec §4) so a large remote catalog stays usable."""
        model = load_model()
        reg = cache.get(model)
        scope = within or None
        hits = reg.search(q, within=scope, limit=limit) if q.strip() else []
        return JSONResponse(
            {
                "query": q,
                "within": within,
                "vocabularies": sorted(reg.sources),
                "loaded_terms": reg.loaded_term_count(),
                "results": [
                    {
                        "iri": h.iri,
                        "prefixed": h.prefixed,
                        "label": h.label,
                        "definition": h.definition,
                        "source": h.source,
                        "source_kind": h.source_kind,
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
            layer = obj.ontology_layer
            if layer is None:
                return
            aligned_refs = []
            for ref in obj.ontology_refs:
                if not ref.uri:
                    continue
                card = reg.describe(ref.uri)
                aligned_refs.append(
                    {
                        "ref": ref.uri,
                        "predicate": ref.predicate,
                        "resolved": card is not None,
                        "resolved_via": ref.resolved_via,
                        "status": ref.status,
                        "label": card["label"] if card else ref.uri.split(":")[-1],
                    }
                )
                if card:
                    external[card["iri"]] = {
                        "iri": card["iri"],
                        "prefixed": card["prefixed"],
                        "label": card["label"],
                        "definition": card["definition"],
                        "source": card["source"],
                    }
            layers.setdefault(layer, []).append(
                {
                    "id": obj.id,
                    "name": obj.name,
                    "kind": obj_kind,
                    "definition": obj.definition,
                    # primary alignment (back-compat) + the full list
                    "aligned_to": aligned_refs[0] if aligned_refs else None,
                    "aligned_refs": aligned_refs,
                    "no_industry_equivalent": obj.no_industry_equivalent,
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

    @router.get("/ontologies")
    def ontologies() -> JSONResponse:
        """Browse phase one: the vocabularies each configured source indexes."""
        reg = cache.get(load_model())  # cache.get already calls reg.load()
        out = [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "namespace": r.namespace,
                "count": r.count,
                "layer": reg.sources.get(r.id).layer if r.id in reg.sources else None,
            }
            for r in reg.list_ontologies()
        ]
        return JSONResponse({"ontologies": out})

    if not read_only:

        @router.post("/upload")
        async def upload(
            file: UploadFile = File(...),  # noqa: B008 - FastAPI dependency idiom
            layer: str = Form("core"),
            prefix: str = Form(""),
            prefix_iri: str = Form(""),
            name: str = Form(""),
        ) -> JSONResponse:
            """Vendor a private ontology file into the repo and wire it into
            `ontology_stack`. Writes `ontologies/<layer>/<name>.<ext>` and appends a
            local source entry to `mdl-project.yaml` (comment-preserving)."""
            try:
                result = save_ontology_upload(
                    model_dir,
                    filename=file.filename or "ontology.ttl",
                    content=await file.read(),
                    layer=layer,
                    prefix=prefix or None,
                    prefix_iri=prefix_iri or None,
                    name=name or None,
                )
            except ValueError as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
            cache._registry = None  # bust so the new vocab loads on next read
            return JSONResponse(result)

    return router
