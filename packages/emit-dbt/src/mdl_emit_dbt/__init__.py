"""Modelith dbt emitter (spec §7).

Depends only on `modelith-core` (layering rule §1.3).
"""

from mdl_emit_dbt.emitter import DbtEmitter, EmitResult
from mdl_emit_dbt.platforms import (
    DuckDBAdapter,
    IcebergAdapter,
    PlatformAdapter,
    RedshiftAdapter,
    SnowflakeAdapter,
    TrinoAdapter,
    available_adapters,
    get_adapter,
)

__all__ = [
    "DbtEmitter",
    "EmitResult",
    "PlatformAdapter",
    "DuckDBAdapter",
    "SnowflakeAdapter",
    "RedshiftAdapter",
    "IcebergAdapter",
    "TrinoAdapter",
    "get_adapter",
    "available_adapters",
]

EMITTER_VERSION = "0.1.0"
