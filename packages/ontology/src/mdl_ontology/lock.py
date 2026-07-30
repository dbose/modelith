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

# The OSI tag we build against. Pinned per user decision + spec §13.1: target the
# tagged 0.1.1 release, NOT the 0.2.0.dev0 DRAFT on main.
DEFAULT_OSI_TAG = "osi-0.1.1-rc1"
DEFAULT_OSI_VERSION = "0.1.1"


@dataclass
class Lock:
    dbt: str = "1.9"
    osi_tag: str = DEFAULT_OSI_TAG
    osi_version: str = DEFAULT_OSI_VERSION
    vocabularies: dict[str, str] = field(default_factory=dict)  # name -> version/tag
    profiles: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> Lock:
        p = root / LOCK_REL
        if not p.exists():
            return cls()
        data = load_str(p.read_text(encoding="utf-8")) or {}
        osi = data.get("osi") or {}
        return cls(
            dbt=str(data.get("dbt", "1.9")),
            osi_tag=str(osi.get("tag", DEFAULT_OSI_TAG)),
            osi_version=str(osi.get("version", DEFAULT_OSI_VERSION)),
            vocabularies=dict(data.get("vocabularies") or {}),
            profiles=dict(data.get("profiles") or {}),
        )

    def save(self, root: Path) -> None:
        p = root / LOCK_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "dbt": self.dbt,
            "osi": {"tag": self.osi_tag, "version": self.osi_version},
            "vocabularies": dict(sorted(self.vocabularies.items())),
            "profiles": dict(sorted(self.profiles.items())),
        }
        p.write_text(dump_str(doc), encoding="utf-8")
