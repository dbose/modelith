"""Import an ER model from an interchange format into Modelith command lists.

Each parser turns a source document (SQL DDL, Mermaid erDiagram, JSON Schema) into the
same neutral `ImportedTable` view, and `to_commands()` turns that into the exact
`{op, payload}` change list the editing engine already applies — so an import goes
through the same validated, ULID-minting `create_entity`/`add_attribute`/
`create_relationship`/`create_key_group` path as manual editing, and it can be
previewed and proposed as a PR just like any other change (no direct disk write
required).

Fidelity note: SQL DDL and JSON Schema carry real structure and round-trip well;
Mermaid is a diagram language whose key markers are cosmetic, so a Mermaid import
recovers entities/attributes/relationships but not domains or true constraints.
"""

from mdl_emit_erd.imports.model import ImportedColumn, ImportedFk, ImportedModel, ImportedTable
from mdl_emit_erd.imports.parse_ddl import parse_sql_ddl
from mdl_emit_erd.imports.parse_jsonschema import parse_json_schema
from mdl_emit_erd.imports.parse_mermaid import parse_mermaid

__all__ = [
    "ImportedModel",
    "ImportedTable",
    "ImportedColumn",
    "ImportedFk",
    "parse_sql_ddl",
    "parse_mermaid",
    "parse_json_schema",
]
