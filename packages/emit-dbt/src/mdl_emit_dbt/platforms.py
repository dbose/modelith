"""Platform adapter interface + duckdb adapter (spec §7.2).

Only duckdb ships in M1 (local dev + tests). The Protocol mirrors §7.2 so
snowflake/redshift/iceberg/trino slot in at M3 without touching the emitter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from mdl_core.ir import Domain, LogicalEntity


@dataclass
class ConstraintCapabilities:
    """Which constraints the platform enforces vs treats as informational (§7.2)."""

    primary_key: bool = False
    foreign_key: bool = False  # most warehouses: informational only -> emit dbt test
    not_null: bool = True
    check: bool = False


@dataclass
class PhysicalType:
    sql_type: str


class PlatformAdapter(Protocol):
    name: str

    def map_domain(self, domain: Domain) -> PhysicalType: ...
    def constraint_support(self) -> ConstraintCapabilities: ...
    def physical_options(self, entity: LogicalEntity) -> dict: ...
    def dialect(self) -> str: ...


# Abstract logical base type -> duckdb SQL type.
_DUCKDB_TYPES = {
    "bigint": "BIGINT",
    "int": "INTEGER",
    "integer": "INTEGER",
    "string": "VARCHAR",
    "text": "VARCHAR",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "decimal": "DECIMAL(38,0)",
    "identifier_bigint": "BIGINT",
    "lei_code": "VARCHAR(20)",
}


@dataclass
class DuckDBAdapter:
    name: str = "duckdb"
    domain_types: dict[str, str] = field(default_factory=lambda: dict(_DUCKDB_TYPES))

    def map_domain(self, domain: Domain) -> PhysicalType:
        base = (domain.base_type or "string").lower()
        return PhysicalType(self.domain_types.get(base, "VARCHAR"))

    def map_base_type(self, base_type: str | None) -> str:
        return self.domain_types.get((base_type or "string").lower(), "VARCHAR")

    def constraint_support(self) -> ConstraintCapabilities:
        # duckdb enforces PK/NOT NULL; FKs are informational -> emitter adds dbt test.
        return ConstraintCapabilities(
            primary_key=True, foreign_key=False, not_null=True, check=True
        )

    def physical_options(self, entity: LogicalEntity) -> dict:
        return {}

    def dialect(self) -> str:
        return "duckdb"


_ADAPTERS: dict[str, PlatformAdapter] = {"duckdb": DuckDBAdapter()}


def get_adapter(name: str) -> PlatformAdapter:
    if name not in _ADAPTERS:
        raise KeyError(
            f"no platform adapter {name!r} (M1 ships duckdb; "
            f"snowflake/redshift/iceberg/trino land in M3)"
        )
    return _ADAPTERS[name]
