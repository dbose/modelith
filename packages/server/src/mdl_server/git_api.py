"""Git integration for the editor's diff + commit panel (E2).

Commands write the working tree; committing stays a deliberate act. All git
operations are scoped to the model directory pathspec, so a model living inside
a larger repo (model/ next to transform/) never touches sibling files.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mdl_server.identity import (
    Identity,
    IdentityPolicy,
    effective_author,
    resolve_identity,
)


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
    # Optional: a proxy- or git-established identity (spec §17) overrides it, and it
    # is only used as the author when nothing else is known (the anonymous fallback,
    # today's zero-config behaviour). Kept so a solo user with no git config still
    # gets attribution.
    user: str = ""
    slug: str = ""
    title: str
    body: str = ""
    changes: list[ChangeOp]


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")
    return s or "change"


def _branch_user(ident: Identity, client_user: str) -> str:
    """The name that seeds the `sme/<slug>/` branch prefix. A trusted identity wins
    over the query-string `user`, so "your proposals" filters by the real user rather
    than a typed guess; otherwise fall back to the client string (today's behaviour)."""
    if ident.is_trusted:
        return ident.email or ident.name
    return client_user


# Mutation ops that write the catalog-owned meaning-fields. When the glossary's
# source of truth is Collibra, /propose refuses these — the UI hides them, but the
# server is the real boundary (a scripted client can't smuggle a definition edit in).
_MEANING_OPS = {"set_definition", "update_synonyms", "set_stewardship"}

# Ops a proposal may contain. This is an ALLOW-list, unlike _MEANING_OPS above,
# which is a deny-list that only activates when a catalog masters meaning.
#
# The propose endpoint applies whatever it is handed through the one mutation
# engine, so the UI hiding a control is not a boundary — a scripted client can post
# anything. That was tolerable while the modeler app could emit four ops; it is not
# now that it can emit the whole editing surface.
_PROPOSABLE_OPS = frozenset(
    {
        # meaning
        "set_definition",
        "set_object_definition",
        "update_synonyms",
        "set_stewardship",
        "set_alignment",
        "clear_alignment",
        "create_term",
        # structure
        "create_entity",
        "rename_entity",
        "delete_entity",
        "add_attribute",
        "update_attribute",
        "delete_attribute",
        "create_relationship",
        "rename_relationship",
        "update_relationship",
        "delete_relationship",
        "set_pattern",
        "set_unmanaged",
        "set_term_map",
        "clear_term_map",
        "create_key_group",
        "update_key_group",
        "delete_key_group",
        "create_category",
        "update_category",
        "delete_category",
        # subject areas
        "create_subject_area",
        "update_subject_area",
        "set_subject_area",
        "set_subject_area_members",
        "add_subject_area_members",
        "remove_subject_area_members",
        # reference data: create and update, but see the exclusions below
        "create_domain",
        "update_domain",
        "create_code_set",
        "update_code_set",
    }
)

# Deliberately NOT proposable, each for its own reason:
#
#   promote_alignment   an architect's verdict. Accepting an alignment in the same
#                       PR that proposes it defeats the proposed/accepted state
#                       machine the whole ontology workflow rests on.
#   set_kg_base_iri     project-wide config, not a model edit — it rewrites the IRI
#                       every downstream consumer resolves against.
#   delete_domain       blast radius beyond the proposer's subject area: a domain or
#   delete_code_set     value set is referenced by name from anywhere in the model,
#   delete_subject_area and an area may be someone else's working view. Create and
#                       update are proposable; deletion needs a wider conversation.
_NOT_PROPOSABLE_REASON = {
    "promote_alignment": (
        "accepting an alignment is an architect's decision, not part of a proposal"
    ),
    "set_kg_base_iri": "the knowledge-graph base IRI is project-wide configuration",
    "delete_domain": "deleting shared reference data affects the whole model",
    "delete_code_set": "deleting shared reference data affects the whole model",
    "delete_subject_area": "deleting a subject area may remove someone else's working view",
}


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


# Opening a proposal is a per-provider URL, and the shape is stable and documented
# for each. Building it from the remote is deterministic: it works on a re-push
# (where the host prints no link at all — the amend-a-proposal case), under a
# non-English locale, and with push output captured or quiet.
#
# The host's own "create a pull request" line is still read when present, because a
# host that tells us the exact URL beats our reconstruction of it — but it is a
# confirmation, not the mechanism.

_REMOTE_URL = re.compile(r"^remote:\s*(https?://\S+)", re.MULTILINE)
_PR_HINT = ("pull", "merge_request", "compare", "pullrequest")


def _pr_url_from_push(output: str) -> str | None:
    """The 'open a pull request' link a git host printed, if it printed one.

    Absent on a re-push, so this can only ever be an optimisation over building the
    URL ourselves."""
    for m in _REMOTE_URL.finditer(output):
        url = m.group(1).rstrip(".,;")
        if any(k in url for k in _PR_HINT):
            return url
    return None


@dataclass(frozen=True)
class RemoteRef:
    """A git remote decomposed into the parts a PR URL needs."""

    host: str
    path: str  # owner/repo, or org/project/_git/repo on Azure DevOps

    @property
    def web(self) -> str:
        return f"https://{self.host}/{self.path}"


def parse_remote(remote: str) -> RemoteRef | None:
    """Decompose a git remote URL. Handles ssh, https, and the scp-like form, and
    strips any credentials the user embedded so they cannot leak into a link."""
    r = (remote or "").strip()
    if not r:
        return None

    if r.startswith("ssh://"):
        r = r[len("ssh://") :]
    elif r.startswith(("http://", "https://")):
        r = r.split("://", 1)[1]
    elif r.startswith("git@") or (":" in r and "/" not in r.split(":", 1)[0]):
        pass  # scp-like: user@host:path
    else:
        return None  # local path, or something we should not guess about

    r = re.sub(r"^[^/@]+@", "", r)  # drop user[:token]@
    if ":" in r.split("/", 1)[0]:  # scp-like host:path, or host:port
        host, path = r.split(":", 1)
        if path.split("/", 1)[0].isdigit():  # it was a port, not a path
            host = f"{host}:{path.split('/', 1)[0]}"
            path = path.split("/", 1)[1] if "/" in path else ""
    elif "/" in r:
        host, path = r.split("/", 1)
    else:
        return None

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or not path:
        return None
    return RemoteRef(host=host, path=path)


def _azure_parts(ref: RemoteRef) -> tuple[str, str, str] | None:
    """(org, project, repo) for an Azure DevOps remote, whose path is org/project/
    _git/repo over https but v3/org/project/repo over ssh."""
    parts = [p for p in ref.path.split("/") if p]
    if parts and parts[0] == "v3":  # ssh://ssh.dev.azure.com/v3/org/project/repo
        parts = parts[1:]
    if "_git" in parts:
        i = parts.index("_git")
        if i >= 2 and len(parts) > i + 1:
            return parts[i - 2], parts[i - 1], parts[i + 1]
        if i == 1 and len(parts) > i + 1:  # org.visualstudio.com/project/_git/repo
            org = ref.host.split(".")[0]
            return org, parts[0], parts[i + 1]
    if len(parts) == 3:  # ssh form, already stripped of v3
        return parts[0], parts[1], parts[2]
    return None


def _provider_pr_url(remote: str, branch: str) -> str | None:
    """The provider's documented 'create a pull request' URL, built from the remote.

    Host matching is on the DOMAIN, so a self-hosted GitLab or Bitbucket is served
    by an explicit `git.provider` setting rather than a guess — inventing a URL for
    an unknown host would send someone somewhere that does not exist."""
    ref = parse_remote(remote)
    if ref is None:
        return None
    return _build_pr_url(_provider_of(ref.host), ref, branch)


def _provider_of(host: str) -> str | None:
    h = host.lower()
    if "github" in h:
        return "github"
    if "gitlab" in h:
        return "gitlab"
    if "bitbucket" in h:
        return "bitbucket"
    if "dev.azure.com" in h or "visualstudio.com" in h:
        return "azure"
    return None


def _build_pr_url(provider: str | None, ref: RemoteRef, branch: str) -> str | None:
    from urllib.parse import quote

    # GitHub carries the branch in the PATH, so its slashes must stay literal —
    # `sme/a.hough/x` is three path segments there. Everyone else puts it in a query
    # parameter, where they must be encoded.
    b = quote(branch, safe="")
    if provider == "github":
        return f"{ref.web}/pull/new/{quote(branch, safe='/')}"
    if provider == "gitlab":
        return (
            f"{ref.web}/-/merge_requests/new"
            f"?merge_request%5Bsource_branch%5D={b}"
        )
    if provider == "bitbucket":
        return f"{ref.web}/pull-requests/new?source={b}"
    if provider == "azure":
        parts = _azure_parts(ref)
        if parts is None:
            return None
        org, project, repo = parts
        host = "dev.azure.com" if "visualstudio.com" not in ref.host else ref.host
        base = (
            f"https://{host}/{org}/{project}/_git/{repo}"
            if host == "dev.azure.com"
            else f"https://{ref.host}/{project}/_git/{repo}"
        )
        return f"{base}/pullrequestcreate?sourceRef={b}"
    return None


def _compare_url(model_dir: Path, branch: str) -> str | None:
    """A link that opens a proposal for `branch`.

    Order: an explicit template (self-hosted, or a provider we do not know), then
    the provider's documented URL built from the remote."""
    code, url = _git(model_dir, "remote", "get-url", "origin")
    if code != 0 or not url:
        return None

    ref = parse_remote(url)
    template, configured = _git_settings(model_dir)
    if template and ref:
        return template.replace("{repo}", ref.web).replace("{branch}", branch)
    if configured and ref:
        return _build_pr_url(configured, ref, branch)
    return _provider_pr_url(url, branch)


def _git_settings(model_dir: Path) -> tuple[str | None, str | None]:
    """(pr_url_template, provider) from mdl-project.yaml's `git:` block. `provider`
    names the software for a self-hosted install whose domain we cannot recognise:

        git:
          provider: gitlab          # self-hosted at git.acme.internal
    """
    try:
        from mdl_core.repo import PROJECT_FILE
        from mdl_core.yaml_io import load_file

        cfg = load_file(model_dir / PROJECT_FILE) or {}
    except Exception:
        return None, None
    git_cfg = cfg.get("git") if isinstance(cfg, dict) else None
    if not isinstance(git_cfg, dict):
        return None, None
    t = git_cfg.get("pr_url_template")
    p = git_cfg.get("provider")
    return (
        t.strip() if isinstance(t, str) and t.strip() else None,
        p.strip().lower() if isinstance(p, str) and p.strip() else None,
    )


# --- helpers for the diff / classify / conflicts / proposals endpoints ------------


def _current_branch(model_dir: Path) -> str:
    code, out = _git(model_dir, "rev-parse", "--abbrev-ref", "HEAD")
    return out if code == 0 else ""


def _base_branch(model_dir: Path) -> str:
    """The repo's default branch, from origin's HEAD, falling back to `main`.
    Deliberately NOT a GitHub API call for branch protection: that needs auth and
    fails offline, and CODEOWNERS plus the PR flow already encode the intent."""
    code, out = _git(model_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if code == 0 and out:
        return out.split("/", 1)[-1]
    for cand in ("main", "master"):
        if _git(model_dir, "rev-parse", "--verify", "--quiet", cand)[0] == 0:
            return cand
    return "main"


def _model_pair(model_dir: Path, base: str, head: str | None):
    """(base_model, head_model). `head=None` means the live working tree."""
    from mdl_server.git_models import model_at_ref, model_at_working_tree

    base_model = model_at_ref(model_dir, base)
    head_model = model_at_working_tree(model_dir) if head is None else model_at_ref(model_dir, head)
    return base_model, head_model


def _diff_response(model_dir: Path, base: str, head: str | None) -> JSONResponse:
    from mdl_core.diff import diff_models
    from mdl_core.diff_render import render_json
    from mdl_server.git_models import RefLoadError, resolve_sha

    if not _is_repo(model_dir):
        return JSONResponse({"ok": False, "error": "not a git repository", "git": False})
    try:
        base_model, head_model = _model_pair(model_dir, base, head)
    except RefLoadError as e:
        # a branch with unparsable YAML must say so, not blank the screen
        return JSONResponse({"ok": False, "error": str(e), "side": "base"}, status_code=422)

    head_label = "your working copy" if head is None else head
    diff = diff_models(base_model, head_model, base_label=base, head_label=head_label)
    doc = render_json(diff)
    doc["ok"] = True
    doc["base"] = {"ref": base, "sha": (resolve_sha(model_dir, base) or "")[:12], "label": base}
    doc["head"] = {"ref": head, "label": head_label}
    _attach_paths(model_dir, doc)
    _attach_breaks(head_model, doc)
    return JSONResponse(doc)


def _attach_paths(model_dir: Path, doc: dict) -> None:
    """Fill each object's source file. Done here rather than in core so that
    diff_models stays a pure Model -> Model function."""
    from mdl_core.repo import ModelRepo

    try:
        repo = ModelRepo.load(model_dir)
    except Exception:  # pragma: no cover - unreadable tree
        return
    for obj in doc.get("objects", []):
        obj["path"] = repo.path_for_ulid(obj["ulid"])


def _attach_breaks(head_model, doc: dict) -> None:
    """For a breaking change, name the dbt models it will break.

    This is the sentence that makes a model-driven tool worth having, and it
    appears at the moment the SME is about to do the damage."""
    if head_model is None:
        return
    from mdl_server.projection import where_used

    for obj in doc.get("objects", []):
        if obj["object_kind"] != "logical_entity":
            continue
        le = head_model.logical_entities.get(obj["ulid"])
        if le is None or not le.realises:
            continue
        used = where_used(head_model, le.realises)
        physical = [p for u in used for p in u.get("physical", [])]
        if not physical:
            continue
        for child in obj.get("children", []):
            for f in child.get("fields", []):
                if f["severity"] == "breaking":
                    f["breaks"] = physical
        for f in obj.get("fields", []):
            if f["severity"] == "breaking":
                f["breaks"] = physical


def _is_repo(model_dir: Path) -> bool:
    return _git(model_dir, "rev-parse", "--git-dir")[0] == 0


def _changed_paths(model_dir: Path, base: str) -> list[str]:
    """Repo-root-relative paths changed against `base`, including untracked.

    Root-relative matters: classify_paths matches on `model/conceptual/...`
    prefixes. Passing model-dir-relative paths puts every one in `unmatched` and
    the SME sees "no route". The two git commands differ here, which is easy to
    miss: `diff --name-only` is already root-relative, while `ls-files` needs
    `--full-name` to be."""
    code, out = _git(model_dir, "diff", "--name-only", base, "--", ".")
    paths = [ln.strip() for ln in out.splitlines() if ln.strip()] if code == 0 else []
    code, out = _git(
        model_dir, "ls-files", "--others", "--exclude-standard", "--full-name", "--", "."
    )
    if code == 0:
        paths += [ln.strip() for ln in out.splitlines() if ln.strip()]
    return sorted(set(paths))


def _codeowners_reviewers(model_dir: Path, paths: list[str]) -> list[str]:
    """Real owners from .github/CODEOWNERS when the repo has one.

    Telling an SME "data-stewards will review this" when the org actually uses
    @acme/glossary-council is worse than saying nothing."""
    from fnmatch import fnmatch

    from mdl_server.git_models import repo_root

    root = repo_root(model_dir)
    for rel in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        f = root / rel
        if f.is_file():
            break
    else:
        return []

    rules: list[tuple[str, list[str]]] = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rules.append((parts[0], parts[1:]))

    owners: list[str] = []
    for path in paths:
        for pattern, who in rules:  # last matching rule wins, as git does it
            pat = pattern.lstrip("/")
            if fnmatch(path, pat) or fnmatch(path, pat.rstrip("/") + "/*") or path.startswith(
                pat.rstrip("/") + "/"
            ):
                owners = [o for o in who]
    seen: list[str] = []
    for o in owners:
        if o not in seen:
            seen.append(o)
    return seen


def _classify_response(model_dir: Path, base: str) -> dict:
    from mdl_core.routes import classify_paths

    if not _is_repo(model_dir):
        return {"ok": False, "error": "not a git repository", "git": False}
    paths = _changed_paths(model_dir, base)
    cl = classify_paths(paths).to_dict()
    cl["ok"] = True
    cl["paths"] = paths
    actual = _codeowners_reviewers(model_dir, paths)
    cl["reviewers_actual"] = actual or None
    return cl


def _conflicts_response(model_dir: Path, base: str) -> dict:
    """Dry-run the SAME semantic merge driver git would run, so the answer is not
    an approximation. merge_model_files is pure over three strings; nothing is
    written and the index is untouched."""
    from mdl_core.merge_driver import merge_model_files
    from mdl_server.git_models import ahead_behind, file_at_ref, merge_base, repo_prefix

    if not _is_repo(model_dir):
        return {"ok": False, "error": "not a git repository", "git": False}

    head = _current_branch(model_dir)
    mb = merge_base(model_dir, base, "HEAD")
    ahead, behind = ahead_behind(model_dir, base, "HEAD")
    prefix = repo_prefix(model_dir)

    def rel_to_model(root_rel: str) -> str:
        """diff --name-only is root-relative; reading the working copy needs a
        model-dir-relative path."""
        return root_rel[len(prefix):] if prefix and root_rel.startswith(prefix) else root_rel

    def changed(a: str, b: str) -> set[str]:
        code, out = _git(model_dir, "diff", "--name-only", a, b, "--", ".")
        return {ln.strip() for ln in out.splitlines() if ln.strip()} if code == 0 else set()

    files: list[dict] = []
    if mb:
        ours = changed(mb, "HEAD")
        theirs = changed(mb, base)
        for root_rel in sorted(ours & theirs):
            if not root_rel.endswith((".yaml", ".yml")):
                continue
            rel = rel_to_model(root_rel)
            b_text = file_at_ref(model_dir, mb, rel) or ""
            live = model_dir / rel
            o_text = (
                live.read_text(encoding="utf-8")
                if live.is_file()
                else (file_at_ref(model_dir, "HEAD", rel) or "")
            )
            t_text = file_at_ref(model_dir, base, rel) or ""
            _, conflicts = merge_model_files(b_text, o_text, t_text)
            files.append({"path": root_rel, "clean": not conflicts, "conflicts": conflicts})

    return {
        "ok": True,
        "base": base,
        "head": head,
        "merge_base": (mb or "")[:12],
        "stale": behind > 0,
        "ahead": ahead,
        "behind": behind,
        "clean": all(f["clean"] for f in files),
        "files": [f for f in files if not f["clean"]],
    }


def _context_response(model_dir: Path, user: str, *, read_only: bool) -> dict:
    """One call for the SME view's git banner: what branch, is it the base, is the
    tree dirty, where would a proposal go."""
    if not _is_repo(model_dir):
        return {"ok": True, "git": False, "can_propose": False}

    branch = _current_branch(model_dir)
    base = _base_branch(model_dir)
    code, dirty_out = _git(model_dir, "status", "--porcelain", "--", ".")
    dirty = bool(dirty_out.strip()) if code == 0 else False
    has_remote, remote = _git(model_dir, "remote", "get-url", "origin")
    ahead, behind = (0, 0)
    if branch and branch != base:
        from mdl_server.git_models import ahead_behind

        ahead, behind = ahead_behind(model_dir, base, "HEAD")

    slug = _slugify(user) if user else "<you>"
    return {
        "ok": True,
        "git": True,
        "branch": branch,
        "base_branch": base,
        "on_base": branch == base,
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "remote": remote if has_remote == 0 else None,
        "sme_branch_prefix": f"sme/{slug}/",
        "read_only": read_only,
        # a dirty tree makes propose 409 later; say so up front instead
        "can_propose": (not read_only) and not dirty,
    }


def _gh_prs(model_dir: Path) -> dict[str, dict]:
    """PR state per branch, best effort. `gh` is a network call: a hung or absent
    binary must never hang the endpoint, so it is timeout-guarded and any failure
    degrades to 'no PR information'."""
    if not shutil.which("gh"):
        return {}
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", "50", "--json",
             "number,url,state,headRefName,reviewDecision"],
            cwd=str(model_dir), capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    return {r["headRefName"]: r for r in rows if r.get("headRefName")}


def _proposals_response(model_dir: Path, user: str) -> dict:
    """Open proposal branches. Everything except the PR row comes from plain git,
    so the screen is fully populated with `gh` absent — only the review state is
    unknown."""
    if not _is_repo(model_dir):
        return {"ok": True, "git": False, "proposals": [], "gh": False}

    base = _base_branch(model_dir)
    prefix = f"sme/{_slugify(user)}/" if user else "sme/"
    code, out = _git(
        model_dir, "for-each-ref", "--format=%(refname:short)%09%(committerdate:relative)",
        "refs/heads/sme", "--sort=-committerdate",
    )
    rows = [ln.split("\t") for ln in out.splitlines() if ln.strip()] if code == 0 else []

    merged = set()
    code, out = _git(model_dir, "branch", "--merged", base, "--format=%(refname:short)")
    if code == 0:
        merged = {ln.strip() for ln in out.splitlines() if ln.strip()}

    prs = _gh_prs(model_dir)
    proposals = []
    for row in rows:
        branch = row[0]
        created = row[1] if len(row) > 1 else ""
        if not branch.startswith(prefix):
            continue
        title = _git(model_dir, "log", "-1", "--format=%s", branch)[1]
        upstream = f"{branch}@{{upstream}}"
        pushed = _git(model_dir, "rev-parse", "--verify", "--quiet", upstream)[0] == 0
        ahead, behind = _ahead_behind_branch(model_dir, base, branch)
        pr = prs.get(branch)
        proposals.append(
            {
                "branch": branch,
                "title": title or branch,
                "created": created,
                "pushed": pushed,
                "merged": branch in merged,
                "ahead": ahead,
                "behind": behind,
                "compare_url": _compare_url(model_dir, branch),
                "pr": (
                    {
                        "number": pr.get("number"),
                        "url": pr.get("url"),
                        "state": pr.get("state"),
                        "reviews": pr.get("reviewDecision") or None,
                    }
                    if pr
                    else None
                ),
            }
        )
    return {"ok": True, "git": True, "base": base, "gh": bool(prs), "proposals": proposals}


def _ahead_behind_branch(model_dir: Path, base: str, branch: str) -> tuple[int, int]:
    code, out = _git(model_dir, "rev-list", "--left-right", "--count", f"{base}...{branch}")
    if code != 0 or len(out.split()) != 2:
        return 0, 0
    behind, ahead = out.split()
    return int(ahead), int(behind)


def git_router(
    model_dir: Path,
    *,
    read_only: bool = False,
    identity_policy: IdentityPolicy | None = None,
) -> APIRouter:
    """Git operations for the canvas and the SME app.

    READS are always registered — including under `mdl glossary --read-only`,
    which is exactly the "SME just looking" and catalog-browse case. The SME view
    has to be able to say which branch it is on and whether a proposal would
    merge cleanly, and a diff is a read. Only the mutating routes (commit,
    discard, propose) sit behind `read_only`, matching ontology_router.

    `identity_policy` (spec §17) decides how the acting user is established for the
    propose author and the "your proposals" branch prefix. None means the solo
    default (git config / anonymous), which is what stand-alone callers and the
    existing tests get."""
    router = APIRouter(prefix="/api/git")
    model_dir = Path(model_dir).resolve()
    policy = identity_policy if identity_policy is not None else IdentityPolicy.from_env({})

    def _identity(request: Request) -> Identity:
        return resolve_identity(request.headers, policy, model_dir)

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

    @router.get("/branch")
    def branch() -> JSONResponse:
        code, name = _git(model_dir, "rev-parse", "--abbrev-ref", "HEAD")
        return JSONResponse({"branch": name if code == 0 else None})

    # --- semantic model diff (plan §L) --------------------------------------------
    # All reads, so they stay outside the read_only guard.

    @router.get("/diff/model")
    def diff_model(base: str = "HEAD") -> JSONResponse:
        """The working tree compared to a ref, as an object-keyed semantic diff."""
        return _diff_response(model_dir, base, None)

    @router.get("/diff/refs")
    def diff_refs(base: str = "main", head: str = "") -> JSONResponse:
        """Two refs compared — powers reviewing an existing proposal branch."""
        return _diff_response(model_dir, base, head or _current_branch(model_dir))

    @router.get("/classify")
    def classify(base: str = "HEAD") -> JSONResponse:
        """Which review route the change takes, who reviews it, which gates run."""
        return JSONResponse(_classify_response(model_dir, base))

    @router.get("/conflicts")
    def conflicts(base: str = "main") -> JSONResponse:
        """Would this merge cleanly onto `base`? A dry run of the real merge
        driver — three `git show`s into merge_model_files, writing nothing."""
        return JSONResponse(_conflicts_response(model_dir, base))

    @router.get("/context")
    def context(user: str = "", ident: Identity = Depends(_identity)) -> JSONResponse:
        """Everything the SME view's git banner needs, in one call."""
        return JSONResponse(
            _context_response(model_dir, _branch_user(ident, user), read_only=read_only)
        )

    @router.get("/proposals")
    def proposals(user: str = "", ident: Identity = Depends(_identity)) -> JSONResponse:
        """Open proposal branches with their state. Everything except the PR row
        is derived from plain git, so the list is never empty without `gh`."""
        return JSONResponse(_proposals_response(model_dir, _branch_user(ident, user)))

    if not read_only:

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

        @router.post("/propose")
        def propose(
            body: ProposeBody, ident: Identity = Depends(_identity)
        ) -> JSONResponse:
            """The SME PR flow (§5.1), atomic: branch -> apply commands through the
            shared mutation engine -> commit with Co-authored-by -> push -> open a PR.
            Degrades gracefully with no remote / no gh so the SME always gets a result.

            The commit author is the SERVER-resolved identity (spec §17): a
            proxy-authenticated user or the git config identity OVERRIDES the
            self-asserted body.user, so the attribution is trustworthy. Only when
            nothing is established (anonymous) do we fall back to body.user."""
            # The server is the real boundary, not the UI: reject anything outside
            # the allow-list BEFORE branching, so a refused proposal leaves no stray
            # branch behind.
            rejected = sorted({c.op for c in body.changes if c.op not in _PROPOSABLE_OPS})
            if rejected:
                reasons = [
                    f"{op}: {_NOT_PROPOSABLE_REASON.get(op, 'not allowed in a proposal')}"
                    for op in rejected
                ]
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "these changes cannot be proposed — " + "; ".join(reasons),
                        "rejected_ops": rejected,
                    },
                    status_code=422,
                )

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

            # The effective author drives BOTH the branch prefix and the commit
            # trailer, so the two never disagree.
            author = effective_author(ident, body.user)
            author_key = author.email or author.name or "you"

            slug = _slugify(body.slug or body.title)
            branch_name = f"sme/{_slugify(author_key)}/{slug}"
            # Remember where we started. Without this the server is left sitting on
            # the SME branch: the next SME browses an un-merged proposal as if it
            # were truth, and a second propose branches off the first, stacking
            # unrelated changes into one PR.
            starting_branch = _current_branch(model_dir) or "main"
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
                    # a failed propose must be a complete no-op
                    _git(model_dir, "checkout", starting_branch)
                    _git(model_dir, "branch", "-D", branch_name)
                    return JSONResponse(
                        {"ok": False, "error": f"change {ch.op!r}: {e}"}, status_code=422
                    )

            _git(model_dir, "add", "--", ".")
            message = body.body.strip() or body.title.strip()
            if author.source != "anonymous" or author.name:
                trailer_name = author.name or author_key
                message += f"\n\nCo-authored-by: {trailer_name} <{author.email}>"
            code, out = _git(model_dir, "commit", "-m", message, "--", ".")
            if code != 0:
                return JSONResponse({"ok": False, "error": out}, status_code=409)

            def _finish(payload: dict) -> JSONResponse:
                """Return to the branch we started on. The proposal branch and its
                commit survive (locally and on the remote); only the working tree
                goes back, so the next reader sees the base branch."""
                _git(model_dir, "checkout", starting_branch)
                payload["returned_to"] = starting_branch
                return JSONResponse(payload)

            result: dict = {"ok": True, "branch": branch_name, "applied": applied}

            # push (if a remote exists) + open a PR (if gh is available and authed).
            has_remote, _ = _git(model_dir, "remote", "get-url", "origin")
            if has_remote != 0:
                result["pushed"] = False
                result["message"] = "committed to branch; no `origin` remote to push"
                return _finish(result)

            code, out = _git(model_dir, "push", "-u", "origin", branch_name, timeout=60)
            result["pushed"] = code == 0
            if code != 0:
                result["message"] = f"branch committed; push failed: {out}"
                return _finish(result)

            # Build the provider's documented create-PR URL from the remote. The
            # host also prints one on a FIRST push, and where it does we prefer it —
            # it is authoritative. But it is absent on a re-push (amending a
            # proposal), so it cannot be the mechanism.
            result["compare_url"] = _pr_url_from_push(out) or _compare_url(
                model_dir, branch_name
            )
            if result["compare_url"]:
                result["message"] = "pushed — open the pull request from the link"
                return _finish(result)

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
                result["message"] = "pushed — open the pull request from your git host"
            return _finish(result)

    return router
