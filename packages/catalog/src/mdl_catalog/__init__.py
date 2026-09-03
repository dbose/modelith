"""Modelith cross-repo model catalog (base-tier discovery).

Catalog indexing is decoupled from governance sync: governance is optional and
org-specific, the catalog is base-tier and works identically with zero configuration.
The catalog never imports `packages/governance` or any adapter — a governance adapter
may optionally consume the same manifest, never the reverse.

The default backend is a git-native manifest repo (one YAML entry per model), which is
**rebuildable, not authoritative**: if it's lost, replaying `mdl catalog publish` from
every model repo's CI reconstructs it. That property is what makes the zero-infra
default safe rather than fragile.
"""

from mdl_catalog.backend import (
    CATALOG_CONFIG_REL,
    CatalogBackend,
    CatalogConfig,
    MaterializeNotSupported,
)
from mdl_catalog.entry import (
    CATALOG_SCHEMA_VERSION,
    CatalogEntry,
    entry_from_repo,
)
from mdl_catalog.git_backend import (
    GitBackend,
    GitRunner,
    MockGitRunner,
    RealGitRunner,
    make_backend,
)

__all__ = [
    "CatalogEntry",
    "CatalogBackend",
    "CatalogConfig",
    "MaterializeNotSupported",
    "CATALOG_SCHEMA_VERSION",
    "CATALOG_CONFIG_REL",
    "entry_from_repo",
    "GitBackend",
    "GitRunner",
    "MockGitRunner",
    "RealGitRunner",
    "make_backend",
]
