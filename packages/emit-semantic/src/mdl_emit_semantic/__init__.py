"""Modelith semantic-layer emitters (spec §4, §8).

- MetricFlow semantic_models + metrics
- OSI (version-isolated behind osi/v<version>/)
- OSI import into IR
- joinability / fan-out validation (§8)

Depends only on `modelith-core` (layering §1.3).
"""

from mdl_emit_semantic.import_osi import import_osi
from mdl_emit_semantic.joinability import build_join_graph, validate_joinability
from mdl_emit_semantic.metricflow import emit_metricflow
from mdl_emit_semantic.osi import DEFAULT_VERSION, get_osi


def emit_osi(model, *, version: str = DEFAULT_VERSION, targets=None) -> str:
    return get_osi(version).emit(model, targets=targets)


__all__ = [
    "emit_metricflow",
    "emit_osi",
    "import_osi",
    "validate_joinability",
    "build_join_graph",
    "get_osi",
    "DEFAULT_VERSION",
]
