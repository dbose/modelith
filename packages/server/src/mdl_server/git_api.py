"""Git integration for the editor's diff + commit panel (E2).

Commands write the working tree; committing stays a deliberate act. All git
operations are scoped to the model directory pathspec, so a model living inside
a larger repo (model/ next to transform/) never touches sibling files.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel


def _git(model_dir: Path, *args: str, timeout: int = 30) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(model_dir), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = proc.stdout + (("\n" + proc.stderr) if proc.returncode != 0 else "")
    return proc.returncode, out.strip()


class CommitBody(BaseModel):
    message: str


class ChangeOp(BaseModel):
    op: str
    payload: dict


class ProposeBody(BaseModel):
    user: str
    slug: str = ""
    title: str
    body: str = ""
    changes: list[ChangeOp]


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")
    return s or "change"


# Mutation ops that write the catalog-owned meaning-fields. When the glossary's
# source of truth is Collibra, /propose refuses these — the UI hides them, but the
# server is the real boundary (a scripted client can't smuggle a definition edit in).
_MEANING_OPS = {"set_definition", "update_synonyms", "set_stewardship"}


def _catalog_owns_meaning(model_dir: Path) -> tuple[bool, str]:
    """(catalog_masters?, catalog_name) from mdl-project.yaml, loaded fresh."""
    try:
        from mdl_core.repo import ModelRepo

        cfg = ModelRepo.load(model_dir).model.config
        g = getattr(cfg, "glossary", None)
        sot = getattr(g, "source_of_truth", "git") if g else "git"
        name = getattr(g, "catalog_name", "Collibra") if g else "Collibra"
        return sot != "git", name
    except Exception:  # noqa: BLE001 — never let config loading break the PR flow
        return False, "Collibra"


def _compare_url(model_dir: Path, branch: str) -> str | None:
    """Derive a GitHub compare URL from the origin remote, for the no-gh fallback."""
    code, url = _git(model_dir, "remote", "get-url", "origin")
    if code != 0 or not url:
        return None
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/compare/{branch}?expand=1"


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

    @router.get("/branch")
    def branch() -> JSONResponse:
        code, name = _git(model_dir, "rev-parse", "--abbrev-ref", "HEAD")
        return JSONResponse({"branch": name if code == 0 else None})

    @router.post("/propose")
    def propose(body: ProposeBody) -> JSONResponse:
        """The SME PR flow (§5.1), atomic: branch -> apply commands through the
        shared mutation engine -> commit with Co-authored-by -> push -> open a PR.
        Degrades gracefully with no remote / no gh so the SME always gets a result."""
        # Enforce the source-of-truth boundary. When the catalog masters meaning,
        # refuse definition/synonym/stewardship edits here (they belong in Collibra);
        # alignment proposals still pass. This is the server-side guard behind the UI.
        catalog_owns, catalog_name = _catalog_owns_meaning(model_dir)
        if catalog_owns:
            blocked = sorted({c.op for c in body.changes if c.op in _MEANING_OPS})
            if blocked:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            f"{', '.join(blocked)} are mastered in {catalog_name}; "
                            f"edit them there. This glossary is a read-only mirror for "
                            f"those fields (alignment proposals are still accepted)."
                        ),
                    },
                    status_code=409,
                )

        # Refuse to run on a dirty tree — we branch from a clean base.
        code, dirty = _git(model_dir, "status", "--porcelain", "--", ".")
        if code == 0 and dirty:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "working tree has uncommitted changes; commit or discard first",
                },
                status_code=409,
            )

        slug = _slugify(body.slug or body.title)
        branch_name = f"sme/{_slugify(body.user)}/{slug}"
        code, out = _git(model_dir, "checkout", "-B", branch_name)
        if code != 0:
            return JSONResponse({"ok": False, "error": out}, status_code=500)

        # Apply each change through the ONE mutation engine (no duplicated logic).
        from mdl_core.commands import CommandError, apply_command

        applied = 0
        for ch in body.changes:
            try:
                apply_command(model_dir, ch.op, ch.payload)
                applied += 1
            except CommandError as e:
                _git(model_dir, "checkout", "--", ".")
                _git(model_dir, "clean", "-fd", "--", ".")
                return JSONResponse(
                    {"ok": False, "error": f"change {ch.op!r}: {e}"}, status_code=422
                )

        _git(model_dir, "add", "--", ".")
        message = body.body.strip() or body.title.strip()
        email = f"{_slugify(body.user)}@users.noreply.github.com"
        message += f"\n\nCo-authored-by: {body.user} <{email}>"
        code, out = _git(model_dir, "commit", "-m", message, "--", ".")
        if code != 0:
            return JSONResponse({"ok": False, "error": out}, status_code=409)

        result: dict = {"ok": True, "branch": branch_name, "applied": applied}

        # push (if a remote exists) + open a PR (if gh is available and authed).
        has_remote, _ = _git(model_dir, "remote", "get-url", "origin")
        if has_remote != 0:
            result["pushed"] = False
            result["message"] = "committed to branch; no `origin` remote to push"
            return JSONResponse(result)

        code, out = _git(model_dir, "push", "-u", "origin", branch_name, timeout=60)
        result["pushed"] = code == 0
        if code != 0:
            result["message"] = f"branch committed; push failed: {out}"
            return JSONResponse(result)

        if shutil.which("gh"):
            proc = subprocess.run(
                ["gh", "pr", "create", "--head", branch_name,
                 "--title", body.title, "--body", body.body or body.title],
                cwd=str(model_dir), capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                result["pr_url"] = proc.stdout.strip()
                result["message"] = "pull request opened for steward review"
            else:
                result["pr_url"] = None
                result["compare_url"] = _compare_url(model_dir, branch_name)
                result["message"] = f"pushed; open the PR: {proc.stderr.strip()}"
        else:
            result["compare_url"] = _compare_url(model_dir, branch_name)
            result["message"] = "pushed; `gh` not installed — open the PR from the compare link"
        return JSONResponse(result)

    return router
