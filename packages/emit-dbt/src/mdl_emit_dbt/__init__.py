"""Modelith dbt emitter (spec §7).

Depends only on `modelith-core` (layering rule §1.3).
"""

from mdl_emit_dbt.emitter import DbtEmitter, EmitResult
from mdl_emit_dbt.platforms import DuckDBAdapter, PlatformAdapter

__all__ = ["DbtEmitter", "EmitResult", "PlatformAdapter", "DuckDBAdapter"]

EMITTER_VERSION = "0.1.0"
