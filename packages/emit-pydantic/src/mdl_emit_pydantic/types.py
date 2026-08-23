"""Base-type -> Python type mapping for the Pydantic emitter.

Keys are Modelith's abstract logical base types; values are Python type
expressions used in the generated model source. Types needing an import
(date, datetime, Decimal) are declared here so the emitter can emit the imports.
"""

from __future__ import annotations

_BASE_TO_PYTHON = {
    "bigint": "int",
    "int": "int",
    "integer": "int",
    "identifier_bigint": "int",
    "string": "str",
    "text": "str",
    "lei_code": "str",
    "boolean": "bool",
    "bool": "bool",
    "date": "date",
    "timestamp": "datetime",
    "decimal": "Decimal",
}

# Python types that require an import in the generated module. Direct name imports
# (not `import datetime`) so Pydantic resolves the annotations without a rebuild.
_IMPORT_FOR = {
    "date": "from datetime import date",
    "datetime": "from datetime import datetime",
    "Decimal": "from decimal import Decimal",
}


def python_type(base_type: str | None) -> str:
    if not base_type:
        return "str"
    return _BASE_TO_PYTHON.get(base_type.lower(), "str")


def imports_for(python_types: set[str]) -> list[str]:
    """Distinct import lines needed for the given set of python type expressions."""
    lines = {_IMPORT_FOR[t] for t in python_types if t in _IMPORT_FOR}
    return sorted(lines)
