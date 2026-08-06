"""Platform adapters (spec §7.2).

Ships duckdb (local dev/tests), snowflake, redshift, iceberg, trino. Each declares
its type map, constraint capability matrix (enforced vs informational), physical
options (cluster/dist/sort/partition), and sqlglot/OSI dialect name.

Constraint capability matters (§7.2): most warehouses accept FK constraints as
informational only. The adapter declares this and the emitter compensates with a
dbt `relationships` test so the model's intent is actually verified.
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
    unique: bool = False  # alternate/unique keys as enforced constraints


@dataclass
class PhysicalType:
    sql_type: str


class PlatformAdapter(Protocol):
    name: str

    def map_domain(self, domain: Domain) -> PhysicalType: ...
    def map_base_type(self, base_type: str | None) -> str: ...
    def constraint_support(self) -> ConstraintCapabilities: ...
    def physical_options(self, entity: LogicalEntity) -> dict: ...
    def dialect(self) -> str: ...


# Base type maps per platform. Keys are abstract logical base types (spec §2.3
# domains). Values are platform SQL types (uppercased to match the drift reader's
# normalisation, so a reverse->emit->drift loop stays clean).
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
    "decimal": "DECIMAL(38,2)",
    "identifier_bigint": "BIGINT",
    "lei_code": "VARCHAR(20)",
}

_SNOWFLAKE_TYPES = {
    "bigint": "NUMBER(38,0)",
    "int": "NUMBER(38,0)",
    "integer": "NUMBER(38,0)",
    "string": "VARCHAR",
    "text": "VARCHAR",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP_NTZ",
    "decimal": "NUMBER(38,2)",
    "identifier_bigint": "NUMBER(38,0)",
    "lei_code": "VARCHAR(20)",
}

_REDSHIFT_TYPES = {
    "bigint": "BIGINT",
    "int": "INTEGER",
    "integer": "INTEGER",
    "string": "VARCHAR(65535)",
    "text": "VARCHAR(65535)",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "decimal": "NUMERIC(38,2)",
    "identifier_bigint": "BIGINT",
    "lei_code": "VARCHAR(20)",
}

_ICEBERG_TYPES = {
    "bigint": "BIGINT",
    "int": "INT",
    "integer": "INT",
    "string": "STRING",
    "text": "STRING",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "decimal": "DECIMAL(38,2)",
    "identifier_bigint": "BIGINT",
    "lei_code": "STRING",
}

_TRINO_TYPES = {
    "bigint": "BIGINT",
    "int": "INTEGER",
    "integer": "INTEGER",
    "string": "VARCHAR",
    "text": "VARCHAR",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "decimal": "DECIMAL(38,2)",
    "identifier_bigint": "BIGINT",
    "lei_code": "VARCHAR(20)",
}


@dataclass
class _BaseAdapter:
    """Shared type-mapping; subclasses set name, types, caps, options, dialect."""

    name: str = "base"
    domain_types: dict[str, str] = field(default_factory=dict)
    _dialect: str = "ansi"

    def map_domain(self, domain: Domain) -> PhysicalType:
        return PhysicalType(self.map_base_type(domain.base_type))

    def map_base_type(self, base_type: str | None) -> str:
        return self.domain_types.get((base_type or "string").lower(), self._default_type())

    def _default_type(self) -> str:
        return self.domain_types.get("string", "VARCHAR")

    def constraint_support(self) -> ConstraintCapabilities:
        return ConstraintCapabilities()

    def physical_options(self, entity: LogicalEntity) -> dict:
        return {}

    def dialect(self) -> str:
        return self._dialect


@dataclass
class DuckDBAdapter(_BaseAdapter):
    name: str = "duckdb"
    domain_types: dict[str, str] = field(default_factory=lambda: dict(_DUCKDB_TYPES))
    _dialect: str = "duckdb"

    def constraint_support(self) -> ConstraintCapabilities:
        return ConstraintCapabilities(
            primary_key=True, foreign_key=False, not_null=True, check=True, unique=True
        )


@dataclass
class SnowflakeAdapter(_BaseAdapter):
    name: str = "snowflake"
    domain_types: dict[str, str] = field(default_factory=lambda: dict(_SNOWFLAKE_TYPES))
    _dialect: str = "snowflake"

    def constraint_support(self) -> ConstraintCapabilities:
        # Snowflake accepts PK/UNIQUE/FK but enforces none — all informational.
        return ConstraintCapabilities(
            primary_key=False, foreign_key=False, not_null=True, check=False
        )

    def physical_options(self, entity: LogicalEntity) -> dict:
        # Cluster by the business key(s); transient off by default.
        keys = [a.name for a in entity.attributes if a.role == "business_key"]
        opts: dict = {"transient": False}
        if keys:
            opts["cluster_by"] = keys
        return opts


@dataclass
class RedshiftAdapter(_BaseAdapter):
    name: str = "redshift"
    domain_types: dict[str, str] = field(default_factory=lambda: dict(_REDSHIFT_TYPES))
    _dialect: str = "redshift"

    def constraint_support(self) -> ConstraintCapabilities:
        # Redshift: PK/FK are informational (planner hints); NOT NULL enforced.
        return ConstraintCapabilities(
            primary_key=False, foreign_key=False, not_null=True, check=False
        )

    def physical_options(self, entity: LogicalEntity) -> dict:
        keys = [a.name for a in entity.attributes if a.role == "business_key"]
        opts: dict = {"dist": "even"}
        if keys:
            opts["dist"] = keys[0]  # dist key on the primary business key
            opts["sort"] = keys
        return opts


@dataclass
class IcebergAdapter(_BaseAdapter):
    name: str = "iceberg"
    domain_types: dict[str, str] = field(default_factory=lambda: dict(_ICEBERG_TYPES))
    _dialect: str = "trino"  # sqlglot has no dedicated iceberg dialect; trino is closest

    def constraint_support(self) -> ConstraintCapabilities:
        # Iceberg table format: no enforced constraints -> everything via dbt tests.
        return ConstraintCapabilities(
            primary_key=False, foreign_key=False, not_null=False, check=False
        )

    def physical_options(self, entity: LogicalEntity) -> dict:
        keys = [a.name for a in entity.attributes if a.role == "business_key"]
        opts: dict = {"table_format": "iceberg"}
        if keys:
            opts["partition_by"] = keys[:1]
            opts["sort_order"] = keys
        return opts


@dataclass
class TrinoAdapter(_BaseAdapter):
    name: str = "trino"
    domain_types: dict[str, str] = field(default_factory=lambda: dict(_TRINO_TYPES))
    _dialect: str = "trino"

    def constraint_support(self) -> ConstraintCapabilities:
        # Trino capability depends on the connector; default to informational.
        return ConstraintCapabilities(
            primary_key=False, foreign_key=False, not_null=False, check=False
        )


_ADAPTERS: dict[str, PlatformAdapter] = {
    "duckdb": DuckDBAdapter(),
    "snowflake": SnowflakeAdapter(),
    "redshift": RedshiftAdapter(),
    "iceberg": IcebergAdapter(),
    "trino": TrinoAdapter(),
}


def get_adapter(name: str) -> PlatformAdapter:
    if name not in _ADAPTERS:
        raise KeyError(
            f"no platform adapter {name!r}; available: {sorted(_ADAPTERS)}"
        )
    return _ADAPTERS[name]


def available_adapters() -> list[str]:
    return sorted(_ADAPTERS)
