"""FastAPI read API + static canvas hosting.

Read-only by design (spec §13.5): the server never writes model files. Each API
call reloads the repo from disk so git is the single source of truth and an
external edit shows up on the next refresh.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mdl_core.diagnostics import Severity
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate
from mdl_server import commands
from mdl_server.git_api import git_router
from mdl_server.glossary_api import glossary_router, subject_area_router
from mdl_server.identity import (
    Identity,
    IdentityPolicy,
    resolve_identity,
    write_denied_reason,
)
from mdl_server.ontology_api import ontology_router
from mdl_server.projection import project

STATIC_DIR = Path(__file__).parent / "static"


def _cache_on_align(model_dir: Path, op: str, payload: dict) -> None:
    """After a successful alignment, snapshot the resolved term into the local cache
    so a term picked from a REMOTE resolver still validates / exports offline
    (spec §4 cache-on-align). Best-effort: never fails the command."""
    if op != "set_alignment":
        return
    ref = payload.get("aligns_to")
    if not ref:
        return
    try:
        from mdl_ontology import build_registry, cache_from_registry

        repo = ModelRepo.load(model_dir)
        reg = build_registry(model_dir, repo.model.config.ontology_stack)
        reg.load()
        cache_from_registry(model_dir, reg, ref)
    except Exception:  # noqa: BLE001 - caching is an optimisation, not a guarantee
        return


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


def create_app(
    model_dir: Path,
    *,
    read_only: bool = False,
    sme_only: bool = False,
    direct: bool = False,
) -> FastAPI:
    model_dir = Path(model_dir)
    app = FastAPI(title="Modelith", docs_url="/api/docs", openapi_url="/api/openapi.json")
    cache: dict = {"fingerprint": None, "repo": None}

    # Identity is resolved per request from a trusted-proxy header (enterprise) or the
    # model dir's git config (solo), or falls back to anonymous — see identity.py. The
    # policy is read from the environment ONCE, here, so serve() and the CLI need no
    # signature change. Nothing is trusted unless the operator opted in.
    policy = IdentityPolicy.from_env()
    if policy.misconfigured_secret():
        import warnings

        warnings.warn(
            "MDL_AUTH_PROXY_SECRET_HEADER is set but MDL_AUTH_PROXY_SECRET is empty; "
            "trusted-header auth is DISABLED (fail-closed). Set the secret value to "
            "enable it.",
            RuntimeWarning,
            stacklevel=2,
        )

    def _identity(request: Request) -> Identity:
        return resolve_identity(request.headers, policy, model_dir)

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
    def get_model(
        subject_area: str = "", ident: Identity = Depends(_identity)
    ) -> JSONResponse:
        """`?subject_area=<ulid>` scopes the model to that area — which also gives
        the main canvas a subject-area filter, not just the SME workspace."""
        repo = _load()
        doc = project(repo.model, subject_area=subject_area or None)
        doc["fingerprint"] = commands.dir_fingerprint(model_dir)
        doc["read_only"] = read_only
        # Which persona this process is serving: staged edits that become a PR, or
        # direct writes to the working tree. The app reads it to pick its mode.
        doc["direct"] = direct
        # Who the server thinks you are (spec §17). The Studio app reads this: when
        # source != "anonymous" it greets you and stops asking for a name. Purely
        # informational on a read — identity gates writes, never reads.
        doc["identity"] = {"name": ident.name, "email": ident.email, "source": ident.source}
        doc["domains"] = sorted(d.name for d in repo.model.domains.values())
        return JSONResponse(doc)

    @app.post("/api/preview")
    def preview(body: dict) -> JSONResponse:
        """Project a set of staged changes without writing anything.

        Registered OUTSIDE the read_only guard: it writes nothing, and the modeler
        app needs it in exactly the modes where /api/command is unavailable."""
        from mdl_server.preview import preview_model

        changes = body.get("changes") or []
        if not isinstance(changes, list):
            return JSONResponse({"ok": False, "error": "changes must be a list"}, status_code=422)
        doc = preview_model(
            _load(), changes, subject_area=body.get("subject_area") or None
        )
        doc["fingerprint"] = commands.dir_fingerprint(model_dir)
        doc["read_only"] = read_only
        return JSONResponse(doc)

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
        return {"status": "ok", "model_dir": str(model_dir), "read_only": read_only}

    # Ontology + glossary read APIs — always available (read-only + edit modes).
    app.include_router(ontology_router(model_dir, lambda: _load().model, read_only=read_only))
    app.include_router(glossary_router(lambda: _load().model))
    app.include_router(subject_area_router(lambda: _load().model))

    @app.get("/api/decisions")
    def decisions() -> JSONResponse:
        from mdl_reverse.ledger import DecisionLedger

        ledger = DecisionLedger.load(model_dir)
        return JSONResponse(
            {
                "decisions": [
                    {
                        "signal_key": d.signal_key,
                        "kind": d.kind,
                        "signal": d.signal,
                        "confidence": d.confidence.value,
                        "subject": d.subject,
                        "verdict": d.verdict.value,
                    }
                    for d in sorted(
                        ledger.decisions.values(), key=lambda x: (x.verdict.value, x.subject)
                    )
                ]
            }
        )

    # Mutation APIs (E2) — omitted entirely in read-only mode. (The git router
    # below is mounted either way; it gates its own writes.)
    if not read_only:

        @app.post("/api/decisions/{signal_key}/verdict")
        def set_verdict(
            signal_key: str, body: dict, ident: Identity = Depends(_identity)
        ) -> JSONResponse:
            denied = write_denied_reason(ident, policy)
            if denied:
                raise HTTPException(status_code=403, detail=denied)
            from mdl_reverse.ledger import DecisionLedger, Verdict

            ledger = DecisionLedger.load(model_dir)
            if signal_key not in ledger.decisions:
                raise HTTPException(status_code=404, detail=f"no decision {signal_key}")
            try:
                verdict = Verdict(body.get("verdict", ""))
            except ValueError as e:
                raise HTTPException(status_code=422, detail="verdict: accepted|rejected") from e
            ledger.set_verdict(signal_key, verdict)
            ledger.save(model_dir)
            return JSONResponse({"ok": True})

        @app.post("/api/command")
        def command(body: dict, ident: Identity = Depends(_identity)) -> JSONResponse:
            denied = write_denied_reason(ident, policy)
            if denied:
                return JSONResponse({"ok": False, "error": denied}, status_code=403)
            op = body.get("op", "")
            payload = body.get("payload") or {}
            base_fp = body.get("fingerprint")
            try:
                result = commands.apply_command(model_dir, op, payload, base_fp)
            except commands.StaleModelError as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
            except commands.CommandError as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
            cache["repo"] = None  # bust the model cache
            _cache_on_align(model_dir, op, payload)
            return JSONResponse(
                {
                    "ok": True,
                    "fingerprint": result.fingerprint,
                    "created_id": result.created_id,
                    "diagnostics": result.diagnostics,
                }
            )

    # git_router is mounted unconditionally: its READS (status, branch, diff,
    # classify, conflicts, context, proposals) must work under --read-only, which
    # is exactly the catalog-browse and "SME just looking" case. Writes are gated
    # inside the router.
    app.include_router(git_router(model_dir, read_only=read_only, identity_policy=policy))

    # Static canvas build. Mounted last so /api/* wins.
    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
        sme_html = STATIC_DIR / "sme.html"
        mocks_html = STATIC_DIR / "mocks.html"

        def _page(path: Path) -> FileResponse:
            """An SPA entry point, explicitly not cached.

            Asset filenames carry a content hash, so they are safe to cache forever
            — but the HTML that NAMES them must not be, or a browser keeps serving
            yesterday's HTML pointing at yesterday's bundle, and a rebuilt app looks
            like it simply did not change."""
            return FileResponse(
                path,
                headers={"Cache-Control": "no-cache, must-revalidate", "Pragma": "no-cache"},
            )

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            # sme_only: this process IS the modeler app. The architect canvas and
            # the design mocks are not part of what was handed out, so every route
            # lands on /sme rather than quietly exposing a second application.
            if sme_only:
                if path.startswith("assets/"):
                    candidate = STATIC_DIR / path
                    if candidate.is_file():
                        return FileResponse(candidate)
                return _page(sme_html)
            candidate = STATIC_DIR / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            # The SME app is a second SPA entry served under /sme.
            if (path == "sme" or path.startswith("sme/")) and sme_html.exists():
                return _page(sme_html)
            # Static UI mocks for screens that aren't built yet (design review only;
            # fixture-driven, no API calls).
            if (path == "mocks" or path.startswith("mocks/")) and mocks_html.exists():
                return _page(mocks_html)
            return _page(STATIC_DIR / "index.html")

    return app


def _spawn_demo_ols(model_dir: Path):
    """If the project declares a `demo_ols:` block in mdl-project.yaml, spawn the
    bundled mock OLS4 server as a child process so `mdl serve` demonstrates remote
    resolution with one command, fully offline. Returns the Popen (or None).

    The block is a demo convenience only — a real deployment points its `type: ols`
    source straight at a live OLS4 URL and omits `demo_ols`. Shape:

        demo_ols:
          script: ols/mock_ols.py    # relative to the model dir
          host: 127.0.0.1
          port: 4901
    """
    import subprocess
    import sys

    from mdl_core.yaml_io import load_str

    project = model_dir / "mdl-project.yaml"
    if not project.exists():
        return None
    try:
        cfg = load_str(project.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a bad config must not stop the server
        return None
    demo = cfg.get("demo_ols")
    if not isinstance(demo, dict) or not demo.get("script"):
        return None
    script = (model_dir / demo["script"]).resolve()
    if not script.exists():
        print(f"demo_ols: script not found at {script}; skipping")
        return None
    host = str(demo.get("host", "127.0.0.1"))
    port = str(demo.get("port", 4901))
    try:
        proc = subprocess.Popen(  # noqa: S603 - trusted demo script from the repo
            [sys.executable, str(script), "--host", host, "--port", port],
        )
    except Exception as e:  # noqa: BLE001
        print(f"demo_ols: could not start mock OLS ({e}); continuing without it")
        return None
    print(f"demo_ols: mock OLS4 spawned on http://{host}:{port}/api (pid {proc.pid})")
    return proc


def serve(
    model_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 4800,
    read_only: bool = False,
    sme_only: bool = False,
    direct: bool = False,
) -> None:
    import atexit

    import uvicorn

    ols_proc = _spawn_demo_ols(model_dir)
    if ols_proc is not None:

        def _stop_ols() -> None:
            if ols_proc.poll() is None:
                ols_proc.terminate()
                try:
                    ols_proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    ols_proc.kill()

        atexit.register(_stop_ols)

    try:
        uvicorn.run(
            create_app(model_dir, read_only=read_only, sme_only=sme_only, direct=direct),
            host=host,
            port=port,
            log_level="warning",
        )
    finally:
        if ols_proc is not None and ols_proc.poll() is None:
            ols_proc.terminate()
