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
from mdl_server import commands
from mdl_server.git_api import git_router
from mdl_server.glossary_api import glossary_router
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


def create_app(model_dir: Path, *, read_only: bool = False) -> FastAPI:
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
        doc = project(repo.model)
        doc["fingerprint"] = commands.dir_fingerprint(model_dir)
        doc["read_only"] = read_only
        doc["domains"] = sorted(d.name for d in repo.model.domains.values())
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

    # Mutation + git APIs (E2) — omitted entirely in read-only mode.
    if not read_only:

        @app.post("/api/decisions/{signal_key}/verdict")
        def set_verdict(signal_key: str, body: dict) -> JSONResponse:
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
        def command(body: dict) -> JSONResponse:
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

        app.include_router(git_router(model_dir))

    # Static canvas build. Mounted last so /api/* wins.
    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
        sme_html = STATIC_DIR / "sme.html"

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            candidate = STATIC_DIR / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            # The SME glossary is a second SPA entry served under /sme.
            if (path == "sme" or path.startswith("sme/")) and sme_html.exists():
                return FileResponse(sme_html)
            return FileResponse(STATIC_DIR / "index.html")

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
    model_dir: Path, *, host: str = "127.0.0.1", port: int = 4800, read_only: bool = False
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
            create_app(model_dir, read_only=read_only),
            host=host,
            port=port,
            log_level="warning",
        )
    finally:
        if ols_proc is not None and ols_proc.poll() is None:
            ols_proc.terminate()
