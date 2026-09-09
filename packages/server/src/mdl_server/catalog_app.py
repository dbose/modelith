"""Cross-repo catalog browse server (spec §4).

This runs *one level above* a model repo: it reads catalog entries via a
`CatalogBackend` (default: a git manifest repo cloned into a local cache) and serves a
read-only, searchable list. Each entry is a POINTER — name, namespace, ontology layers,
publish time, and a link out to the source repo@commit. Model content is never embedded;
full detail is fetched from the source repo on demand (the same "state lives in git,
every surface is a client" principle the core model follows).

Reuses the packages/server FastAPI + static-canvas infrastructure; the browse UI is a
third canvas SPA entry served at `/catalog`. Deliberately separate from `serve()` (which
is bound to a single model_dir) because the catalog is cross-repo.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"


def _entry_doc(e) -> dict:
    # Compose a source link when the remote looks like a git URL.
    link = _repo_link(e.remote, e.commit)
    return {
        "model": e.model,
        "namespace_ulid": e.namespace_ulid,
        "remote": e.remote,
        "commit": e.commit,
        "ontology_layers": list(e.ontology_layers),
        "published_at": e.published_at,
        "modelith_schema_version": e.modelith_schema_version,
        "source_link": link,
    }


def _repo_link(remote: str | None, commit: str | None) -> str | None:
    """Best-effort web URL for a git remote at a commit (github/gitlab https+ssh)."""
    if not remote:
        return None
    r = remote.strip()
    # git@host:org/repo.git  ->  https://host/org/repo
    if r.startswith("git@") and ":" in r:
        host, path = r[4:].split(":", 1)
        base = f"https://{host}/{path[:-4] if path.endswith('.git') else path}"
    elif r.startswith(("http://", "https://")):
        base = r[:-4] if r.endswith(".git") else r
    else:
        return None
    if commit:
        return f"{base}/tree/{commit}"
    return base


def _slug_user(name: str) -> str:
    """Branch-safe form of a user's name, matching git_api's slugify."""
    out = "".join(c if c.isalnum() else "-" for c in name.strip().lower())
    return "-".join(p for p in out.split("-") if p) or "you"


def create_catalog_app(backend) -> FastAPI:
    """A read-only FastAPI app over a CatalogBackend.

    Beyond the browse list, it can OPEN an entry's LDM canvas in-app: `materialize`
    checks the entry's model out locally (via the backend, so the how is backend-specific)
    and mounts a read-only canvas sub-app at `/view/<slug>`. The catalog stays a pointer
    index — the checkout is a disposable cache, never a second source of truth."""
    from mdl_catalog import MaterializeNotSupported
    from starlette.applications import Starlette

    app = FastAPI(title="Modelith catalog", docs_url=None, redoc_url=None)
    # A dedicated host for per-model canvas apps, mounted at /view BEFORE the SPA
    # catch-all so `/view/<slug>/...` resolves to the canvas, not catalog.html. Per-slug
    # canvas apps are sub-mounted onto it on demand (Starlette matches mounts by path).
    view_host = Starlette()
    app.mount("/view", view_host)
    # slug -> whether it is mounted EDITABLE. A slug opened read-only and then
    # opened for editing must be re-mounted, so this records the mode, not a flag.
    # slug -> whether it is mounted EDITABLE. A slug opened read-only and then
    # reopened for editing needs a new mount, so this records the MODE, not a flag.
    mounted: dict[str, bool] = {}

    def _entry_by_slug(slug: str):
        for e in backend.list():
            if e.slug() == slug:
                return e
        return None

    @app.get("/api/catalog/list")
    def catalog_list(q: str = "") -> JSONResponse:
        entries = backend.search(q) if q.strip() else backend.list()
        return JSONResponse(
            {
                "count": len(entries),
                "query": q,
                "entries": [_entry_doc(e) for e in entries],
            }
        )

    @app.post("/api/catalog/open/{slug}")
    def catalog_open(slug: str, body: dict | None = None) -> JSONResponse:
        """Materialise an entry's model and mount it at `/view/<slug>`.

        With `{"edit": true, "user": "..."}` the checkout is put on a proposal branch
        first, so edits made from the catalog land as a PR on that model's OWN repo.
        The catalog stays a pointer index either way — it never becomes a second
        source of truth."""
        entry = _entry_by_slug(slug)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no catalog entry {slug!r}")
        body = body or {}
        edit = bool(body.get("edit"))
        view_url = f"/view/{slug}/"
        if mounted.get(slug) == edit:
            return JSONResponse({"ok": True, "url": view_url, "model": entry.model, "edit": edit})
        try:
            if edit:
                user = _slug_user(str(body.get("user") or "you"))
                model_dir = backend.checkout_branch(entry, f"sme/{user}/{slug}")
            else:
                model_dir = backend.materialize(entry)
        except MaterializeNotSupported as exc:
            # Degrade to the source link — the UI opens that instead.
            return JSONResponse(
                {
                    "ok": False,
                    "reason": str(exc),
                    "source_link": _repo_link(entry.remote, entry.commit),
                },
                status_code=200,
            )
        # Import here so the catalog app has no hard dependency on the canvas server unless
        # someone actually opens a model (keeps `mdl catalog list` light).
        from mdl_server.app import create_app

        # Mounted onto view_host (already at /view), so its routes live under
        # /view/<slug>/... and are matched ahead of the catalog SPA catch-all.
        # Starlette matches the FIRST route whose prefix fits, so a slug re-opened in
        # a different mode would keep serving the old mount. Drop it first.
        view_host.routes[:] = [
            r for r in view_host.routes if getattr(r, "path", None) != f"/{slug}"
        ]
        # sme_only: a catalog visitor gets the modeler app, not the architect canvas —
        # they came here to look at a MODEL, not to be handed a second application.
        view_host.mount(
            f"/{slug}",
            create_app(model_dir, read_only=not edit, sme_only=True),
        )
        mounted[slug] = edit
        return JSONResponse({"ok": True, "url": view_url, "model": entry.model, "edit": edit})

    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
        catalog_html = STATIC_DIR / "catalog.html"

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            candidate = STATIC_DIR / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            if catalog_html.exists():
                return FileResponse(catalog_html)
            return FileResponse(STATIC_DIR / "index.html")

    return app


def serve_catalog(
    backend, *, host: str = "127.0.0.1", port: int = 4811
) -> None:
    """Boot the catalog browse server. `backend` is a resolved CatalogBackend (the CLI
    builds it from .modelith/catalog.yaml and ensures the local clone is fresh)."""
    import uvicorn

    uvicorn.run(create_catalog_app(backend), host=host, port=port, log_level="warning")
