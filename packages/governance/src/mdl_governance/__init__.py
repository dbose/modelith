"""Modelith governance framework (spec §9).

Core emits a neutral GovernanceGraph; adapters consume it. This package depends
only on `modelith-core`; adapters depend only on this package (layering §1.3).
"""

from mdl_governance.conformance import ConformanceResult, run_conformance
from mdl_governance.graph import (
    GovernanceAsset,
    GovernanceGraph,
    GovernanceRelation,
    build_graph,
    external_id,
)
from mdl_governance.lineage import emit_openlineage
from mdl_governance.profile import MappedGraph, Profile, map_graph
from mdl_governance.spi import (
    AdapterCapabilities,
    ChangeType,
    ForeignPlanError,
    GovernanceAdapter,
    PlannedChange,
    SyncPlan,
    SyncResult,
    WritebackSet,
    WritebackValue,
    build_changes,
)

__all__ = [
    "external_id",
    "GovernanceAsset",
    "GovernanceRelation",
    "GovernanceGraph",
    "build_graph",
    "Profile",
    "MappedGraph",
    "map_graph",
    "GovernanceAdapter",
    "SyncPlan",
    "PlannedChange",
    "ChangeType",
    "SyncResult",
    "WritebackSet",
    "WritebackValue",
    "AdapterCapabilities",
    "ForeignPlanError",
    "build_changes",
    "run_conformance",
    "ConformanceResult",
    "emit_openlineage",
]
