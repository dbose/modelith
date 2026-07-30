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
from mdl_reverse.manifest import ManifestColumn, ManifestModel, read_manifest

__all__ = [
    "read_manifest",
    "ManifestModel",
    "ManifestColumn",
    "compute_drift",
    "DriftReport",
    "DriftItem",
    "DriftSeverity",
]
