"""Modelith reverse engineering + drift detection (spec §5.4, §6).

M2 delivers drift detection. Full reverse engineering (§6) lands in M3 and will
share the manifest reader here. Depends only on `modelith-core` (layering §1.3).
"""

from mdl_reverse.drift import (
    DriftItem,
    DriftReport,
    DriftSeverity,
    compute_drift,
)
from mdl_reverse.ledger import Confidence, Decision, DecisionLedger, Verdict
from mdl_reverse.manifest import ManifestColumn, ManifestModel, read_manifest
from mdl_reverse.reverse import ReverseResult, reverse
from mdl_reverse.schema_reader import read_schema_yml

__all__ = [
    "read_manifest",
    "read_schema_yml",
    "ManifestModel",
    "ManifestColumn",
    "compute_drift",
    "DriftReport",
    "DriftItem",
    "DriftSeverity",
    "reverse",
    "ReverseResult",
    "DecisionLedger",
    "Decision",
    "Verdict",
    "Confidence",
]
