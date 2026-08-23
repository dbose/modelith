"""Base-type -> Neo4j property type mapping.

Keys are Modelith's abstract logical base types; values are Neo4j property type
names as used in property-type constraints (Neo4j 5+). Only used in comments on the
generated Cypher today, but kept as a first-class map so a future typed-property
constraint mode can consume it.
"""

from __future__ import annotations

_BASE_TO_NEO4J = {
    "bigint": "INTEGER",
    "int": "INTEGER",
    "integer": "INTEGER",
    "identifier_bigint": "INTEGER",
    "string": "STRING",
    "text": "STRING",
    "lei_code": "STRING",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "date": "DATE",
    "timestamp": "ZONED DATETIME",
    "decimal": "FLOAT",
}


def neo4j_type(base_type: str | None) -> str:
    if not base_type:
        return "STRING"
    return _BASE_TO_NEO4J.get(base_type.lower(), "STRING")
