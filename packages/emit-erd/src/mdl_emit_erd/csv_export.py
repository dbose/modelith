"""Emit a flat attributes CSV from the neutral ER view.

The lowest common denominator: one row per column, with the key/FK facts as columns,
so a data-governance stakeholder can open the model in a spreadsheet or a BI tool.
Lossy for relationships beyond the FK-target columns, but that is the accepted trade
for a universally-openable surface (SqlDBM and Hackolade both do Excel round-trips)."""

from __future__ import annotations

import csv
import io

from mdl_core.ir import Model
from mdl_emit_erd.model_view import build_tables

_HEADER = [
    "entity",
    "attribute",
    "base_type",
    "sql_type",
    "primary_key",
    "unique",
    "nullable",
    "foreign_key_to",
    "definition",
]


def emit_csv(model: Model) -> str:
    from mdl_emit_dbt.platforms import get_adapter

    tables = build_tables(model, get_adapter("duckdb"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    for t in tables:
        # column name -> "target_table.target_col" for FK annotation
        fk_target: dict[str, str] = {}
        for fk in t.foreign_keys:
            for i, cc in enumerate(fk.columns):
                ref = fk.ref_columns[i] if i < len(fk.ref_columns) else ""
                fk_target[cc] = f"{fk.ref_table}.{ref}" if ref else fk.ref_table
        for c in t.columns:
            w.writerow(
                [
                    t.name,
                    c.name,
                    c.base_type,
                    c.sql_type,
                    "yes" if c.pk else "",
                    "yes" if c.unique else "",
                    "yes" if c.nullable else "no",
                    fk_target.get(c.name, ""),
                    (c.definition or "").strip(),
                ]
            )
    return buf.getvalue()
