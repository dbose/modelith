"""Base-type -> ODCS logicalType mapping.

Keys are Modelith's abstract logical base types (spec §2.3 domains), the same
vocabulary the dbt platform adapters map FROM. Values are ODCS v3 logicalTypes
(the platform-agnostic side of ODCS's dual logicalType/physicalType).
"""

from __future__ import annotations

# ODCS v3 logicalType is one of: string, date, number, integer, object, array,
# boolean. Modelith's richer base types collapse onto these.
_BASE_TO_ODCS_LOGICAL = {
    "bigint": "integer",
    "int": "integer",
    "integer": "integer",
    "identifier_bigint": "integer",
    "string": "string",
    "text": "string",
    "lei_code": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "timestamp": "date",
    "decimal": "number",
}


def odcs_logical_type(base_type: str | None) -> str:
    if not base_type:
        return "string"
    return _BASE_TO_ODCS_LOGICAL.get(base_type.lower(), "string")
