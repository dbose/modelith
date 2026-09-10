"""Parse SQL DDL into an ImportedModel using the sqlglot AST.

A real parser (not regex) so quoting, dialects, composite keys, inline vs table-level
constraints, and inline REFERENCES all work. sqlglot is multi-dialect: the caller
picks the read dialect (postgres|snowflake|mysql|...); it defaults to a permissive
parse. Types are folded to Modelith's abstract base types so the imported model is
logical, not tied to one warehouse's spelling."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from mdl_emit_erd.imports.model import ImportedColumn, ImportedFk, ImportedModel, ImportedTable

# sqlglot DataType.Type -> Modelith abstract base type
_TYPE_MAP = {
    "INT": "integer",
    "INTEGER": "integer",
    "TINYINT": "integer",
    "SMALLINT": "integer",
    "BIGINT": "bigint",
    "DECIMAL": "decimal",
    "NUMERIC": "decimal",
    "DOUBLE": "float",
    "FLOAT": "float",
    "REAL": "float",
    "VARCHAR": "string",
    "CHAR": "string",
    "TEXT": "string",
    "NVARCHAR": "string",
    "STRING": "string",
    "BOOLEAN": "boolean",
    "BOOL": "boolean",
    "DATE": "date",
    "DATETIME": "timestamp",
    "TIMESTAMP": "timestamp",
    "TIMESTAMPTZ": "timestamp",
    "JSON": "json",
    "UUID": "string",
}


def _base_type(dtype: exp.DataType | None) -> str:
    if dtype is None:
        return "string"
    name = dtype.this.name if hasattr(dtype.this, "name") else str(dtype.this)
    return _TYPE_MAP.get(name.upper(), "string")


def _ref_target(ref: exp.Expression | None) -> tuple[str | None, list[str]]:
    """(referenced table name, referenced column names) from a Reference/FK reference."""
    if ref is None:
        return None, []
    tbl = ref.find(exp.Table)
    schema = ref.find(exp.Schema)  # Schema wraps table + column list
    cols: list[str] = []
    if schema is not None:
        cols = [i.name for i in schema.expressions if isinstance(i, (exp.Identifier, exp.Column))]
    if not cols:
        cols = [c.name for c in ref.find_all(exp.Column)]
    return (tbl.name if tbl else None), cols


def parse_sql_ddl(ddl: str, *, dialect: str | None = None) -> ImportedModel:
    model = ImportedModel()
    read = dialect if dialect and dialect != "postgres" else None
    try:
        statements = sqlglot.parse(ddl, read=read)
    except Exception as e:  # noqa: BLE001 - surface a clean message, not a traceback
        model.warnings.append(f"could not parse SQL: {e}")
        return model

    for stmt in statements:
        if not isinstance(stmt, exp.Create) or (stmt.kind or "").upper() != "TABLE":
            continue
        tbl_node = stmt.find(exp.Table)
        if tbl_node is None:
            continue
        table = ImportedTable(name=tbl_node.name)
        pk_from_cols: list[str] = []

        for cd in stmt.find_all(exp.ColumnDef):
            col = ImportedColumn(name=cd.name, base_type=_base_type(cd.args.get("kind")))
            for con in cd.constraints or []:
                kind = con.args.get("kind")
                if isinstance(kind, exp.PrimaryKeyColumnConstraint):
                    col.pk = True
                    col.nullable = False
                    pk_from_cols.append(col.name)
                elif isinstance(kind, exp.NotNullColumnConstraint):
                    col.nullable = False
                elif isinstance(kind, exp.UniqueColumnConstraint):
                    col.unique = True
                elif isinstance(kind, exp.Reference):
                    rt, rc = _ref_target(kind)
                    if rt:
                        table.foreign_keys.append(
                            ImportedFk(columns=[col.name], ref_table=rt, ref_columns=rc)
                        )
            table.columns.append(col)

        # table-level PRIMARY KEY (...) — composite; wins over per-column markers
        pk = stmt.find(exp.PrimaryKey)
        if pk is not None:
            pk_cols = [c.name for c in pk.expressions]
            _apply_pk(table, pk_cols)
        elif pk_from_cols:
            _apply_pk(table, pk_from_cols)

        # table-level FOREIGN KEY (...) REFERENCES ...
        for fk in stmt.find_all(exp.ForeignKey):
            cols = [c.name for c in fk.expressions]
            rt, rc = _ref_target(fk.args.get("reference"))
            if rt and cols:
                table.foreign_keys.append(ImportedFk(columns=cols, ref_table=rt, ref_columns=rc))

        model.tables.append(table)

    if not model.tables:
        model.warnings.append("no CREATE TABLE statements found")
    return model


def _apply_pk(table: ImportedTable, pk_cols: list[str]) -> None:
    names = set(pk_cols)
    for c in table.columns:
        if c.name in names:
            c.pk = True
            c.nullable = False
