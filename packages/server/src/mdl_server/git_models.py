"""Materialise a Model as of a git ref (plan §K).

The diff engine takes two `Model` objects; this is where the second one comes
from. Server layer, because core must not shell out to git.

**`git archive`, not `git worktree add`.** A worktree writes to `.git/worktrees/`
in the user's real repo — unacceptable in read-only serve mode, it leaks
administrative state if the process dies, and it refuses a ref that is already
checked out (exactly the HEAD case). It also cannot be scoped to a subdirectory,
and the model dir is usually `model/` beside a full dbt project. `git archive`
touches nothing under `.git` and extracts precisely the subtree.

Caching is keyed on the resolved commit SHA, not the ref name, so `main` moving
yields a new key automatically. The extracted files are thrown away — the parsed
`Model` is the artifact worth keeping.
"""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
from functools import lru_cache
from pathlib import Path

from mdl_core.ir import Model
from mdl_core.repo import PROJECT_FILE, ModelRepo


class RefLoadError(Exception):
    """The ref could not be resolved or its model could not be read."""


def _git(model_dir: Path, *args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(model_dir), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:  # pragma: no cover - env dependent
        return 1, str(e)
    return p.returncode, (p.stdout or p.stderr).strip()


def _git_bytes(model_dir: Path, *args: str, timeout: int = 60) -> tuple[int, bytes, str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(model_dir), *args],
            capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:  # pragma: no cover
        return 1, b"", str(e)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace").strip()


def is_git_repo(model_dir: Path) -> bool:
    code, _ = _git(model_dir, "rev-parse", "--git-dir")
    return code == 0


def repo_prefix(model_dir: Path) -> str:
    """The model dir's path relative to the repo root ('model/', or '' at the root).
    `git archive` needs this to scope the extraction."""
    code, out = _git(model_dir, "rev-parse", "--show-prefix")
    return out if code == 0 else ""


def repo_root(model_dir: Path) -> Path:
    """The repo's top level. `git archive`'s pathspec resolves against the repo
    ROOT, not the -C directory, so a nested model dir must archive from here."""
    code, out = _git(model_dir, "rev-parse", "--show-toplevel")
    return Path(out) if code == 0 and out else Path(model_dir)


def resolve_sha(model_dir: Path, ref: str) -> str | None:
    code, out = _git(model_dir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return out or None if code == 0 else None


def merge_base(model_dir: Path, a: str, b: str) -> str | None:
    code, out = _git(model_dir, "merge-base", a, b)
    return out if code == 0 and out else None


def ahead_behind(model_dir: Path, base: str, head: str) -> tuple[int, int]:
    """(ahead, behind) of `head` relative to `base`."""
    code, out = _git(model_dir, "rev-list", "--left-right", "--count", f"{base}...{head}")
    if code != 0 or not out:
        return 0, 0
    parts = out.split()
    if len(parts) != 2:
        return 0, 0
    behind, ahead = int(parts[0]), int(parts[1])
    return ahead, behind


def file_at_ref(model_dir: Path, ref: str, rel_path: str) -> str | None:
    """One file's contents at a ref, repo-root-relative. None when absent there."""
    prefix = repo_prefix(model_dir)
    code, out = _git(model_dir, "show", f"{ref}:{prefix}{rel_path}")
    return out if code == 0 else None


def model_at_working_tree(model_dir: Path) -> Model:
    return ModelRepo.load(model_dir).model


def model_at_ref(model_dir: Path, ref: str) -> Model | None:
    """The model as of `ref`, or None when the model dir did not exist there.

    Raises RefLoadError for an unresolvable ref or an unparsable model."""
    sha = resolve_sha(model_dir, ref)
    if sha is None:
        raise RefLoadError(f"cannot resolve ref {ref!r}")
    return _model_at_sha(str(model_dir), sha)


@lru_cache(maxsize=8)
def _model_at_sha(model_dir_str: str, sha: str) -> Model | None:
    """Cached on the resolved SHA: a moving ref simply produces a new key."""
    model_dir = Path(model_dir_str)
    prefix = repo_prefix(model_dir)
    args = ["archive", "--format=tar", sha]
    if prefix:
        args += ["--", prefix]
    # run from the repo root: an archive pathspec is root-relative, so `-C model/`
    # with pathspec `model/` matches nothing
    code, blob, err = _git_bytes(repo_root(model_dir), *args)
    if code != 0:
        # a path that did not exist at this commit is "no model here", not an error
        if "did not match any files" in err or "path not found" in err:
            return None
        raise RefLoadError(f"git archive failed at {sha[:12]}: {err[:200]}")
    if not blob:
        return None

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        try:
            with tarfile.open(fileobj=_BytesReader(blob)) as tf:
                _safe_extract(tf, dest)
        except tarfile.TarError as e:  # pragma: no cover - malformed archive
            raise RefLoadError(f"could not read the archive at {sha[:12]}: {e}") from e
        root = dest / prefix if prefix else dest
        if not (root / PROJECT_FILE).exists():
            return None
        try:
            return ModelRepo.load(root).model
        except Exception as e:  # unparsable YAML, unknown kind, ...
            raise RefLoadError(f"could not read the model at {sha[:12]}: {e}") from e


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract, refusing any member that would escape `dest`. git archive output is
    already well-formed; this guards against a hostile or corrupt archive."""
    root = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if root not in target.parents and target != root:
            raise RefLoadError(f"archive member escapes the extraction root: {member.name}")
    tf.extractall(dest)  # noqa: S202 - members validated above


class _BytesReader:
    """Minimal file-like wrapper so tarfile can stream the in-memory archive."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def seek(self, pos: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = pos
        elif whence == 1:
            self._pos += pos
        else:
            self._pos = len(self._data) + pos
        return self._pos

    def tell(self) -> int:
        return self._pos

    def close(self) -> None:
        return None


def clear_cache() -> None:
    """Drop the SHA cache (tests, and after a fetch that rewrote history)."""
    _model_at_sha.cache_clear()
