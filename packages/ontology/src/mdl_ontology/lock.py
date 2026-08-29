"""Spec-version lock — .mdl/lock.yaml (spec §2.2, §13.1).

Pins the exact versions of every mutable external spec the model depends on: dbt,
the OSI tag, each vocabulary bundle (FIBO or otherwise), and profiles. Committed
to git. This is what stops a model built today from silently drifting when an
upstream spec changes — especially OSI, whose repo is 0.2.0.dev0 DRAFT while a
tagged 0.1.1 exists (§4.1 version caution).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mdl_core.yaml_io import dump_str, load_str

LOCK_REL = ".mdl/lock.yaml"

# Where fetched ontology layers are materialised. Gitignored — the lock pins the
# content, the cache holds it, like node_modules to package-lock.json (spec §3).
CACHE_REL = ".mdl/ontology-cache"

# The two resolution modes an ontology layer can be pinned in (spec §3):
#   artifact          - an immutable file/URL, sha256 covers the file directly.
#   endpoint_snapshot - a live triple store, sha256 covers a point-in-time export.
LOCK_MODES = ("artifact", "endpoint_snapshot")

# The OSI tag we build against. Pinned per user decision + spec §13.1: target the
# tagged 0.1.1 release, NOT the 0.2.0.dev0 DRAFT on main.
DEFAULT_OSI_TAG = "osi-0.1.1-rc1"
DEFAULT_OSI_VERSION = "0.1.1"


@dataclass
class OntologyLayerLock:
    """One pinned ontology layer (spec §3). `source` is the URL/coordinate/endpoint;
    `mode` is artifact | endpoint_snapshot; `version` (artifact) or `snapshot_tag`
    (endpoint) records what was pinned; `sha256` is the content hash the fetch must
    reproduce. `fmt` is the RDF serialization of the cached copy."""

    mode: str  # artifact | endpoint_snapshot
    source: str
    sha256: str | None = None
    version: str | None = None  # artifact: version label (e.g. 2026Q2)
    snapshot_tag: str | None = None  # endpoint_snapshot: point-in-time tag
    fmt: str = "turtle"
    # prefix -> namespace IRI, so a locked layer can resolve/expand its prefixed
    # alignments offline (e.g. fibo-fnd-pty-pty -> https://spec.edmcouncil.org/...).
    prefixes: dict[str, str] = field(default_factory=dict)

    def to_doc(self) -> dict:
        doc: dict = {"mode": self.mode, "source": self.source}
        if self.version:
            doc["version"] = self.version
        if self.snapshot_tag:
            doc["snapshot_tag"] = self.snapshot_tag
        if self.sha256:
            doc["sha256"] = self.sha256
        if self.fmt and self.fmt != "turtle":
            doc["format"] = self.fmt
        if self.prefixes:
            doc["prefixes"] = dict(sorted(self.prefixes.items()))
        return doc

    @classmethod
    def from_doc(cls, d: dict) -> OntologyLayerLock:
        return cls(
            mode=str(d.get("mode", "artifact")),
            source=str(d.get("source", "")),
            sha256=d.get("sha256"),
            version=d.get("version"),
            snapshot_tag=d.get("snapshot_tag"),
            fmt=str(d.get("format", "turtle")),
            prefixes=dict(d.get("prefixes") or {}),
        )


@dataclass
class Lock:
    dbt: str = "1.9"
    osi_tag: str = DEFAULT_OSI_TAG
    osi_version: str = DEFAULT_OSI_VERSION
    vocabularies: dict[str, str] = field(default_factory=dict)  # name -> version/tag
    profiles: dict[str, str] = field(default_factory=dict)
    # layer name (industry|core|domain|specialised or a custom name) -> pin (spec §3)
    ontology_layers: dict[str, OntologyLayerLock] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> Lock:
        p = root / LOCK_REL
        if not p.exists():
            return cls()
        data = load_str(p.read_text(encoding="utf-8")) or {}
        osi = data.get("osi") or {}
        layers = {
            str(name): OntologyLayerLock.from_doc(dict(entry))
            for name, entry in (data.get("ontology_layers") or {}).items()
            if isinstance(entry, dict)
        }
        return cls(
            dbt=str(data.get("dbt", "1.9")),
            osi_tag=str(osi.get("tag", DEFAULT_OSI_TAG)),
            osi_version=str(osi.get("version", DEFAULT_OSI_VERSION)),
            vocabularies=dict(data.get("vocabularies") or {}),
            profiles=dict(data.get("profiles") or {}),
            ontology_layers=layers,
        )

    def save(self, root: Path) -> None:
        p = root / LOCK_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        doc: dict = {
            "dbt": self.dbt,
            "osi": {"tag": self.osi_tag, "version": self.osi_version},
            "vocabularies": dict(sorted(self.vocabularies.items())),
            "profiles": dict(sorted(self.profiles.items())),
        }
        if self.ontology_layers:
            doc["ontology_layers"] = {
                name: self.ontology_layers[name].to_doc()
                for name in sorted(self.ontology_layers)
            }
        p.write_text(dump_str(doc), encoding="utf-8")
