"""Governance adapter SPI + SyncPlan (spec §9.3).

    class GovernanceAdapter(Protocol):
        def plan(self, graph, profile) -> SyncPlan: ...   # never writes
        def apply(self, plan) -> SyncResult: ...
        def pull(self, profile) -> WritebackSet: ...
        def capabilities(self) -> AdapterCapabilities: ...

`plan` is mandatory and `apply` refuses a plan it did not produce (checked via a
signature over the plan contents). No surprise writes to a production catalog.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Protocol

from mdl_governance.graph import GovernanceGraph
from mdl_governance.profile import MappedAsset, Profile


class ChangeType(str, Enum):
    create = "create"
    update = "update"
    noop = "noop"


@dataclass
class PlannedChange:
    external_id: str
    target_type: str
    change: ChangeType
    name: str
    attributes: dict = field(default_factory=dict)
    relations: list = field(default_factory=list)


@dataclass
class SyncPlan:
    adapter: str
    profile_name: str
    changes: list[PlannedChange] = field(default_factory=list)
    signature: str = ""  # set by the adapter; apply() verifies it

    def creates(self) -> list[PlannedChange]:
        return [c for c in self.changes if c.change == ChangeType.create]

    def updates(self) -> list[PlannedChange]:
        return [c for c in self.changes if c.change == ChangeType.update]

    def compute_signature(self) -> str:
        body = json.dumps(
            [asdict(c) for c in self.changes], sort_keys=True, default=_enum_default
        )
        return "sha256:" + hashlib.sha256((self.adapter + body).encode()).hexdigest()


def _enum_default(o):
    if isinstance(o, Enum):
        return o.value
    raise TypeError(o)


@dataclass
class SyncResult:
    applied: int
    created: int
    updated: int
    errors: list[str] = field(default_factory=list)


@dataclass
class WritebackValue:
    external_id: str
    model_path: str
    value: object


@dataclass
class WritebackSet:
    values: list[WritebackValue] = field(default_factory=list)


@dataclass
class AdapterCapabilities:
    name: str
    supports_writeback: bool = False
    supports_lineage: bool = False
    batch: bool = True


class GovernanceAdapter(Protocol):
    def plan(self, graph: GovernanceGraph, profile: Profile) -> SyncPlan: ...
    def apply(self, plan: SyncPlan) -> SyncResult: ...
    def pull(self, profile: Profile) -> WritebackSet: ...
    def capabilities(self) -> AdapterCapabilities: ...


class ForeignPlanError(Exception):
    """Raised when apply() is handed a plan it did not produce (§9.3)."""


def build_changes(mapped_assets: list[MappedAsset], existing: set[str]) -> list[PlannedChange]:
    """Diff mapped assets against the set of external_ids already in the catalog.
    Deterministic external_id (§9.1) makes this create-vs-update decision idempotent."""
    changes: list[PlannedChange] = []
    for a in sorted(mapped_assets, key=lambda x: x.external_id):
        change = ChangeType.update if a.external_id in existing else ChangeType.create
        changes.append(
            PlannedChange(
                external_id=a.external_id,
                target_type=a.target_type,
                change=change,
                name=a.name,
                attributes=dict(a.attributes),
                relations=list(a.relations),
            )
        )
    return changes
