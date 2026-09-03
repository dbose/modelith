"""The CatalogBackend SPI + config resolution.

`CatalogBackend` is deliberately the same shape as the governance adapter SPI
(`GovernanceAdapter` in packages/governance) — parallel, not novel — so the two feel
alike without being coupled. The catalog never imports governance or any adapter; a
governance adapter may *optionally* consume the same manifest, but the dependency never
runs the other way (spec §2).

Backend selection is config-driven and defaults to the git backend (spec §2, §3): a
catalog works with zero configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from mdl_catalog.entry import CatalogEntry

# Where catalog config lives. The catalog sits ABOVE any single model repo, so its
# config is its own file — per-repo first, then user-level (spec §2).
CATALOG_CONFIG_REL = ".modelith/catalog.yaml"


class MaterializeNotSupported(Exception):
    """Raised by a backend that cannot produce a local model dir for an entry (so the
    browse view degrades to the source-repo link instead of an in-app canvas)."""


@runtime_checkable
class CatalogBackend(Protocol):
    """A place catalog entries are published to and read from. The git backend (§3) is
    the reference implementation; S3/DataHub/etc. are separate installable adapters."""

    def publish(self, entry: CatalogEntry) -> None:
        """Idempotently record an entry. Republishing the same commit is a no-op;
        a new commit for a known model updates its entry."""
        ...

    def list(self) -> list[CatalogEntry]:
        """All entries currently in the catalog."""
        ...

    def search(self, query: str) -> list[CatalogEntry]:
        """Entries matching a free-text query (v1: substring over summary fields)."""
        ...

    def materialize(self, entry: CatalogEntry) -> Path:
        """Produce a *local, read-only* model dir for `entry`, so a client (the browse
        server) can mount the LDM canvas over it. HOW is backend-specific and that is the
        point: the git backend checks the source repo out at the pinned commit; an S3 or
        Collibra backend fetches the model bundle its own way. Raise
        `MaterializeNotSupported` when the backend cannot (the caller then falls back to
        the source-repo link). Must be idempotent and cached — repeated calls for the same
        entry return the same dir without re-fetching."""
        ...


@dataclass
class CatalogConfig:
    """Resolved catalog configuration. `backend` selects the implementation; the rest is
    backend-specific (the git backend reads `remote`/`branch`)."""

    backend: str = "git"
    remote: str | None = None  # git backend: the catalog repo remote
    branch: str = "main"  # git backend: branch/entry-branch in the catalog repo
    # A user may also *supply* model-repo paths/remotes directly (offline / mono-repo).
    repos: list[str] | None = None

    @classmethod
    def resolve(cls, start: Path | None = None) -> CatalogConfig:
        """Find and load catalog config: per-repo `.modelith/catalog.yaml` (walking up
        from `start`), else user-level `~/.modelith/catalog.yaml`, else defaults (git
        backend, no remote). Zero-config yields a working git-backend config."""
        for path in _config_search_paths(start or Path.cwd()):
            if path.exists():
                return cls._from_file(path)
        return cls()

    @classmethod
    def _from_file(cls, path: Path) -> CatalogConfig:
        from mdl_core.yaml_io import load_file

        try:
            data = load_file(path) or {}
        except Exception:  # noqa: BLE001 - a broken config degrades to defaults
            return cls()
        cat = data.get("catalog") if isinstance(data.get("catalog"), dict) else data
        cat = cat if isinstance(cat, dict) else {}
        return cls(
            backend=str(cat.get("backend", "git")),
            remote=cat.get("remote"),
            branch=str(cat.get("branch", "main")),
            repos=[str(r) for r in cat["repos"]] if cat.get("repos") else None,
        )


def _config_search_paths(start: Path) -> list[Path]:
    """Per-repo config walking up from `start`, then the user-level fallback."""
    paths: list[Path] = []
    cur = start.resolve()
    for _ in range(40):  # bounded walk to the filesystem root
        paths.append(cur / CATALOG_CONFIG_REL)
        if cur.parent == cur:
            break
        cur = cur.parent
    paths.append(Path.home() / CATALOG_CONFIG_REL)
    return paths
