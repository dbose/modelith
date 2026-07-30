"""Git integration for the editor's diff + commit panel (E2).

Commands write the working tree; committing stays a deliberate act. All git
operations are scoped to the model directory pathspec, so a model living inside
a larger repo (model/ next to transform/) never touches sibling files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel


def _git(model_dir: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(model_dir), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = proc.stdout + (("\n" + proc.stderr) if proc.returncode != 0 else "")
    return proc.returncode, out.strip()


class CommitBody(BaseModel):
    message: str


def git_router(model_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/git")
    model_dir = Path(model_dir).resolve()

    @router.get("/status")
    def status() -> JSONResponse:
        code, out = _git(model_dir, "status", "--porcelain", "--", ".")
        if code != 0:
            return JSONResponse({"git": False, "dirty": [], "error": out})
        dirty = []
        for line in out.splitlines():
            if not line.strip():
                continue
            state, path = line[:2].strip() or "??", line[3:].strip()
            dirty.append({"state": state, "path": path})
        return JSONResponse({"git": True, "dirty": dirty, "clean": not dirty})

    @router.get("/diff")
    def diff() -> JSONResponse:
        # tracked changes + untracked file contents, so new entities show too
        _, tracked = _git(model_dir, "diff", "--", ".")
        _, untracked_list = _git(
            model_dir, "ls-files", "--others", "--exclude-standard", "--", "."
        )
        untracked = []
        for rel in untracked_list.splitlines():
            if not rel.strip():
                continue
            p = model_dir / rel
            if p.is_file():
                untracked.append(f"--- /dev/null\n+++ b/{rel}\n" + "".join(
                    f"+{line}\n" for line in p.read_text(encoding="utf-8").splitlines()
                ))
        return JSONResponse({"diff": tracked, "untracked": untracked})

    @router.post("/commit")
    def commit(body: CommitBody) -> JSONResponse:
        code, out = _git(model_dir, "add", "--", ".")
        if code != 0:
            return JSONResponse({"ok": False, "error": out}, status_code=500)
        msg = body.message.strip() or "canvas edits"
        code, out = _git(model_dir, "commit", "-m", msg, "--", ".")
        if code != 0:
            return JSONResponse({"ok": False, "error": out}, status_code=409)
        _, sha = _git(model_dir, "rev-parse", "--short", "HEAD")
        return JSONResponse({"ok": True, "sha": sha})

    @router.post("/discard")
    def discard() -> JSONResponse:
        # revert tracked edits + remove untracked files, model dir only
        code1, out1 = _git(model_dir, "checkout", "--", ".")
        code2, out2 = _git(model_dir, "clean", "-fd", "--", ".")
        ok = code1 == 0 and code2 == 0
        return JSONResponse({"ok": ok, "error": None if ok else f"{out1}\n{out2}"})

    return router
