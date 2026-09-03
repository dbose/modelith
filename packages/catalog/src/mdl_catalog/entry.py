"""The catalog entry: one manifest record per model repo.

A catalog entry is a POINTER plus summary fields — never model content. It records
where a model lives (git remote + commit), what it is (name, namespace ULID), what
ontology layers it uses, and when it was published. Full model detail is always fetched
from the source repo on demand; the catalog stays a rebuildable index over the repos,
never a second copy of them (spec §3, §5).

Deliberately built from *config-level* fields only (`ModelRepo.load` -> `model.config`),
not the full governance graph — that is the base-tier boundary: cataloguing needs no
profile, no adapter, no governance backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# The Modelith model schema version a catalog entry declares. Bumped when the manifest
# entry shape changes in a way consumers must notice.
CATALOG_SCHEMA_VERSION = "0.2"


@dataclass
class CatalogEntry:
    """One model's manifest record. `remote`/`commit` are backend-shaped pointers — for
    the git backend they are the source repo's git remote + commit SHA."""

    model: str
    namespace_ulid: str | None = None
    remote: str | None = None
    commit: str | None = None
    ontology_layers: list[str] = field(default_factory=list)
    published_at: str | None = None  # ISO 8601 UTC; stamped by the publisher
    modelith_schema_version: str = CATALOG_SCHEMA_VERSION

    def slug(self) -> str:
        """Filename-safe id for the manifest file (`entries/<slug>.yaml`)."""
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in self.model.lower())

    def to_doc(self) -> dict:
        doc: dict = {"model": self.model}
        if self.namespace_ulid:
            doc["namespace_ulid"] = self.namespace_ulid
        if self.remote:
            doc["remote"] = self.remote
        if self.commit:
            doc["commit"] = self.commit
        doc["ontology_layers"] = list(self.ontology_layers)
        if self.published_at:
            doc["published_at"] = self.published_at
        doc["modelith_schema_version"] = self.modelith_schema_version
        return doc

    @classmethod
    def from_doc(cls, d: dict) -> CatalogEntry:
        return cls(
            model=str(d.get("model", "")),
            namespace_ulid=d.get("namespace_ulid"),
            remote=d.get("remote"),
            commit=d.get("commit"),
            ontology_layers=[str(x) for x in (d.get("ontology_layers") or [])],
            published_at=d.get("published_at"),
            modelith_schema_version=str(
                d.get("modelith_schema_version", CATALOG_SCHEMA_VERSION)
            ),
        )

    def matches(self, query: str) -> bool:
        """Cheap substring search across the fields a browser searches on (v1: no query
        language — filename/field grep is sufficient, spec §3)."""
        q = query.lower().strip()
        if not q:
            return True
        hay = " ".join(
            [self.model, self.namespace_ulid or "", self.remote or "",
             *(self.ontology_layers or [])]
        ).lower()
        return q in hay


def _ontology_layers_from_config(config) -> list[str]:
    """The ontology layers a model uses, read from its `ontology_stack` config — the
    `name`s (or `layer`s) declared. Empty when the model isn't ontology-anchored."""
    layers: list[str] = []
    for entry in getattr(config, "ontology_stack", None) or []:
        if isinstance(entry, dict):
            nm = entry.get("name") or entry.get("layer")
            if nm and nm not in layers:
                layers.append(str(nm))
    return layers


def entry_from_repo(
    model_dir: Path,
    *,
    remote: str | None = None,
    commit: str | None = None,
    published_at: str | None = None,
) -> CatalogEntry:
    """Build a CatalogEntry from a model repo's config. `remote`/`commit`/`published_at`
    are supplied by the caller (the CLI fills them from git + the clock), keeping this
    function pure and testable."""
    from mdl_core.repo import ModelRepo

    repo = ModelRepo.load(model_dir)
    cfg = repo.model.config
    # A namespace ULID for the whole model: reuse the first subject area's ULID if one
    # exists (a stable anchor), else leave None — the entry is keyed by model name.
    namespace = None
    sas = getattr(repo.model, "subject_areas", {}) or {}
    if sas:
        namespace = next(iter(sas))
    return CatalogEntry(
        model=cfg.name,
        namespace_ulid=namespace,
        remote=remote,
        commit=commit,
        ontology_layers=_ontology_layers_from_config(cfg),
        published_at=published_at,
    )
