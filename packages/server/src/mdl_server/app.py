"""FastAPI read API + static canvas hosting.

Read-only by design (spec §13.5): the server never writes model files. Each API
call reloads the repo from disk so git is the single source of truth and an
external edit shows up on the next refresh.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mdl_core.diagnostics import Severity
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate
from mdl_server.projection import project

STATIC_DIR = Path(__file__).parent / "static"


def _dir_fingerprint(model_dir: Path) -> tuple:
    """Cheap change detector: (count, max mtime, total size) over model YAML files.
    Git stays the source of truth — any on-disk edit changes the fingerprint and
    triggers a reload; unchanged trees serve from cache (a 1000-entity model costs
    seconds to parse with round-trip YAML, far too slow per request)."""
    n = 0
    mtime = 0.0
    size = 0
    for p in model_dir.rglob("*.yaml"):
        st = p.stat()
        n += 1
        mtime = max(mtime, st.st_mtime)
        size += st.st_size
    return (n, mtime, size)


def create_app(model_dir: Path) -> FastAPI:
    model_dir = Path(model_dir)
    app = FastAPI(title="Modelith", docs_url="/api/docs", openapi_url="/api/openapi.json")
    cache: dict = {"fingerprint": None, "repo": None}

    def _load() -> ModelRepo:
        try:
            fp = _dir_fingerprint(model_dir)
            if cache["repo"] is None or cache["fingerprint"] != fp:
                cache["repo"] = ModelRepo.load(model_dir)
                cache["fingerprint"] = fp
            return cache["repo"]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.get("/api/model")
    def get_model() -> JSONResponse:
        repo = _load()
        return JSONResponse(project(repo.model))

    @app.get("/api/entities/{ulid}")
    def get_entity(ulid: str) -> JSONResponse:
        repo = _load()
        doc = project(repo.model)
        for e in doc["entities"]:
            if e["id"] == ulid:
                return JSONResponse(e)
        raise HTTPException(status_code=404, detail=f"no entity {ulid}")

    @app.get("/api/diagnostics")
    def get_diagnostics() -> JSONResponse:
        repo = _load()
        diags = validate(repo.model)
        return JSONResponse(
            {
                "items": [
                    {
                        "code": d.code,
                        "severity": d.severity.value,
                        "message": d.message,
                        "path": d.path,
                    }
                    for d in diags.items
                ],
                "has_errors": diags.has(Severity.error),
            }
        )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "model_dir": str(model_dir)}

    # Static canvas build. Mounted last so /api/* wins.
    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            candidate = STATIC_DIR / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


def serve(model_dir: Path, *, host: str = "127.0.0.1", port: int = 4800) -> None:
    import uvicorn

    uvicorn.run(create_app(model_dir), host=host, port=port, log_level="warning")
