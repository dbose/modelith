"""Git-native catalog backend — the default, reference implementation (spec §3).

The catalog is a dedicated git repo (or a branch) holding one human-readable YAML entry
per model under `entries/`. No database, no long-running server for the base case.

- `publish()` = ensure a local working clone, write/update `entries/<slug>.yaml`, and if
  it changed, commit + push (retry on push rejection, like any CI commit bot).
- `list()`/`search()` = read all entries from the local clone.

**Rebuildable, not authoritative.** The catalog repo is an index over the model repos,
never their source of truth. If it's lost, replaying `mdl catalog publish` from every
model repo's CI reconstructs it fully. So `publish` is idempotent (same commit for a
model => byte-identical entry => no commit) and the local clone is a disposable cache.

Git access goes through an injectable `GitRunner` (a callable over argv in a cwd),
mirroring the governance adapter's `Transport` seam: `MockGitRunner` records commands for
tests and runs a filesystem-only fake; the real runner shells out to `git`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from mdl_catalog.backend import MaterializeNotSupported
from mdl_catalog.entry import CatalogEntry

_ENTRIES_DIR = "entries"
# Checked-out source repos for the browse-view canvas live here (siblings of the catalog
# clone), keyed by entry slug + short commit so distinct commits never collide.
_SOURCES_DIR = "sources"


class GitRunner(Protocol):
    """Run a git command in `cwd`; return (returncode, stdout). Never raises for a
    non-zero exit — the caller inspects the code (push rejection is expected flow)."""

    def run(self, args: list[str], cwd: Path) -> tuple[int, str]: ...


@dataclass
class RealGitRunner:
    """Shells out to the system `git`."""

    def run(self, args: list[str], cwd: Path) -> tuple[int, str]:
        proc = subprocess.run(  # noqa: S603,S607 - trusted git argv, no shell
            ["git", *args], cwd=str(cwd), capture_output=True, text=True
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


@dataclass
class MockGitRunner:
    """A filesystem-only git fake for tests: `clone` copies a seed dir (or makes an empty
    working dir), `add`/`commit`/`pull` are no-ops that record the call, `push` succeeds
    unless `reject_pushes` is set for the first N attempts. Records every argv."""

    calls: list[list[str]] = field(default_factory=list)
    reject_pushes: int = 0  # fail the first N pushes to exercise retry
    _pushes: int = 0

    def run(self, args: list[str], cwd: Path) -> tuple[int, str]:
        self.calls.append(list(args))
        if args and args[0] == "push":
            self._pushes += 1
            if self._pushes <= self.reject_pushes:
                return 1, "! [rejected] (fetch first)"
            return 0, ""
        # clone / init / pull / add / commit / config all succeed as filesystem no-ops
        return 0, ""


@dataclass
class GitBackend:
    """Publish/list/search over a git manifest repo checked out at `work_dir`.

    `work_dir` is the local working clone (a disposable cache). `remote` is the catalog
    repo URL used to (re)establish that clone; when None, `work_dir` is treated as an
    already-present local catalog (offline / test)."""

    work_dir: Path
    remote: str | None = None
    branch: str = "main"
    runner: GitRunner = field(default_factory=RealGitRunner)
    author_name: str = "modelith-catalog"
    author_email: str = "catalog@modelith.local"
    max_push_retries: int = 3

    # --- lifecycle ----------------------------------------------------------

    def ensure_clone(self) -> None:
        """Make sure `work_dir` is a usable clone of the catalog repo. Clones when
        absent (and a remote is configured), pulls when present."""
        self.work_dir = Path(self.work_dir)
        git_dir = self.work_dir / ".git"
        if git_dir.exists():
            self.runner.run(["pull", "--ff-only"], self.work_dir)
            return
        if self.remote:
            self.work_dir.parent.mkdir(parents=True, exist_ok=True)
            self.runner.run(
                ["clone", "--branch", self.branch, self.remote, str(self.work_dir)],
                self.work_dir.parent,
            )
        else:
            # local-only catalog: init an empty repo so commits work
            self.work_dir.mkdir(parents=True, exist_ok=True)
            if not git_dir.exists():
                self.runner.run(["init", "-q"], self.work_dir)
        (self.work_dir / _ENTRIES_DIR).mkdir(parents=True, exist_ok=True)

    # --- CatalogBackend -----------------------------------------------------

    def publish(self, entry: CatalogEntry) -> None:
        """Idempotently write `entries/<slug>.yaml`, then commit + push if it changed.
        Republishing the identical entry (same commit for a model) is a no-op."""
        from mdl_core.yaml_io import dump_str

        self.ensure_clone()
        dest = self.work_dir / _ENTRIES_DIR / f"{entry.slug()}.yaml"
        new_text = dump_str(entry.to_doc())
        if dest.exists() and dest.read_text(encoding="utf-8") == new_text:
            return  # idempotent: nothing changed for this model+commit
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_text, encoding="utf-8")

        rel = f"{_ENTRIES_DIR}/{entry.slug()}.yaml"
        self.runner.run(["add", rel], self.work_dir)
        self.runner.run(
            ["-c", f"user.name={self.author_name}",
             "-c", f"user.email={self.author_email}",
             "commit", "-m", f"catalog: publish {entry.model} @ {entry.commit or '?'}"],
            self.work_dir,
        )
        if self.remote:
            self._push_with_retry()

    def _push_with_retry(self) -> None:
        for _ in range(self.max_push_retries):
            code, _out = self.runner.run(["push"], self.work_dir)
            if code == 0:
                return
            # push rejected (someone else published concurrently): pull + retry
            self.runner.run(["pull", "--rebase"], self.work_dir)
        # give up silently after retries — CI will re-run; the entry is on disk

    def list(self) -> list[CatalogEntry]:
        from mdl_core.yaml_io import load_str

        entries_dir = Path(self.work_dir) / _ENTRIES_DIR
        if not entries_dir.exists():
            return []
        out: list[CatalogEntry] = []
        for p in sorted(entries_dir.glob("*.yaml")):
            try:
                doc = load_str(p.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001 - a bad entry file is skipped, not fatal
                continue
            if isinstance(doc, dict) and doc.get("model"):
                out.append(CatalogEntry.from_doc(dict(doc)))
        return out

    def search(self, query: str) -> list[CatalogEntry]:
        return [e for e in self.list() if e.matches(query)]

    # --- materialize (browse-view canvas) -----------------------------------

    def materialize(self, entry: CatalogEntry) -> Path:
        """Check the entry's source repo out at its pinned commit into a local cache and
        return the model dir inside it (the dir holding `mdl-project.yaml`). Idempotent:
        an already-materialised checkout for the same slug@commit is reused as-is.

        The checkout is a disposable, read-only cache — never a working branch. The browse
        server mounts a read-only canvas over it; edits happen in each model's own repo."""
        if not entry.remote:
            raise MaterializeNotSupported(
                f"catalog entry {entry.model!r} has no source remote to check out"
            )
        short = (entry.commit or "head")[:12]
        dest = Path(self.work_dir).parent / _SOURCES_DIR / f"{entry.slug()}-{short}"
        marker = dest / ".git"
        if not marker.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Full clone (not shallow): a pinned commit may be unreachable by a shallow
            # tip fetch; correctness beats a few MB of cache here.
            code, out = self.runner.run(
                ["clone", "--quiet", entry.remote, str(dest)], dest.parent
            )
            if code != 0:
                raise MaterializeNotSupported(
                    f"could not clone {entry.remote}: {out.strip()[:200]}"
                )
            if entry.commit:
                code, out = self.runner.run(
                    ["checkout", "--quiet", entry.commit], dest
                )
                if code != 0:
                    raise MaterializeNotSupported(
                        f"could not check out {entry.commit[:12]} of {entry.model}: "
                        f"{out.strip()[:200]}"
                    )
        model_dir = _find_model_dir(dest)
        if model_dir is None:
            raise MaterializeNotSupported(
                f"no Modelith model (mdl-project.yaml) found in {entry.model}'s repo"
            )
        return model_dir


def _find_model_dir(root: Path) -> Path | None:
    """Locate the Modelith model dir inside a checked-out repo: the dir containing
    `mdl-project.yaml`. Prefer the repo root, then a shallow search (a workspace layout
    puts the model under `model/`). Returns None when the repo has no model."""
    if (root / "mdl-project.yaml").is_file():
        return root
    # Shallowest match wins (avoid descending into vendored/nested copies).
    best: Path | None = None
    best_depth = 1 << 30
    for p in root.rglob("mdl-project.yaml"):
        if ".git" in p.parts:
            continue
        depth = len(p.relative_to(root).parts)
        if depth < best_depth:
            best, best_depth = p.parent, depth
    return best


def make_backend(config, cache_dir: Path, runner: GitRunner | None = None):
    """Construct a backend from a resolved CatalogConfig. Only the git backend ships
    here; other backends live in separate installable adapter packages and are resolved
    by name when present (spec §2)."""
    if config.backend in ("git", "", None):
        return GitBackend(
            work_dir=cache_dir,
            remote=config.remote,
            branch=config.branch,
            runner=runner or RealGitRunner(),
        )
    raise ValueError(
        f"unknown catalog backend {config.backend!r}; install its adapter package "
        f"or use the default git backend"
    )
