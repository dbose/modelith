"""Parse a Mermaid erDiagram into an ImportedModel.

Mermaid is a diagram language: entity blocks list `<type> <name> [PK|FK|UK]`, and
relationship lines carry crow's-foot cardinality. We recover entities, attributes,
key markers, and relationships. We do NOT recover real domains or constraints (the
markers are cosmetic), so the import is structural, and a warning says so."""

from __future__ import annotations

import re

from mdl_emit_erd.imports.model import ImportedColumn, ImportedFk, ImportedModel, ImportedTable

# `parent ||--o{ child : "label"`  — capture both entity names. The crow's-foot tokens
# (||, |o, }o, }|, o{, etc.) sit either side of the -- / .. connector, so the char
# class must include { and }.
_REL = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s+[|}o<>.{}\-]*(?:--|\.\.)[|}o<>.{}\-]*\s+([A-Za-z0-9_]+)\s*:",
    re.MULTILINE,
)
# an attribute line inside an entity block: `<type> <name> [markers] ["comment"]`
_ATTR = re.compile(r"^\s*([A-Za-z0-9_\[\]()]+)\s+([A-Za-z0-9_]+)(?:\s+([A-Za-z0-9_,]+))?")


def parse_mermaid(text: str) -> ImportedModel:
    model = ImportedModel()
    model.warnings.append(
        "Mermaid is a diagram format: attribute types and key markers are approximate, "
        "and column-level foreign keys are inferred from the relationship lines."
    )
    lines = text.splitlines()
    i = 0
    tables: dict[str, ImportedTable] = {}
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.lower().startswith("erdiagram") or line.startswith("%%"):
            continue
        # entity block: `NAME {`
        mblock = re.match(r"^([A-Za-z0-9_]+)\s*\{", line)
        if mblock:
            t = ImportedTable(name=mblock.group(1))
            while i < len(lines) and lines[i].strip() != "}":
                am = _ATTR.match(lines[i])
                if am:
                    dtype, cname, marks = am.group(1), am.group(2), (am.group(3) or "")
                    up = marks.upper()
                    t.columns.append(
                        ImportedColumn(
                            name=cname,
                            base_type=_norm_type(dtype),
                            pk="PK" in up,
                            unique="UK" in up,
                            nullable="PK" not in up,
                        )
                    )
                i += 1
            i += 1  # skip the closing }
            tables[t.name] = t

    # relationships -> FKs (parent ||-- child; child is the FK holder)
    for m in _REL.finditer(text):
        parent, child = m.group(1), m.group(2)
        if child in tables and parent in tables:
            # anchor to the parent's PK column name if the child has a same-named column
            parent_pk = next((c.name for c in tables[parent].columns if c.pk), None)
            cols = (
                [parent_pk]
                if parent_pk and any(c.name == parent_pk for c in tables[child].columns)
                else []
            )
            tables[child].foreign_keys.append(
                ImportedFk(
                    columns=cols,
                    ref_table=parent,
                    ref_columns=[parent_pk] if parent_pk else [],
                )
            )

    model.tables = list(tables.values())
    if not model.tables:
        model.warnings.append("no entity blocks found in the Mermaid erDiagram")
    return model


def _norm_type(t: str) -> str:
    t = re.sub(r"[\[\]()]", "", t).lower()
    known = {"int", "integer", "bigint", "string", "varchar", "text", "decimal",
             "float", "boolean", "bool", "date", "timestamp", "json"}
    if t in ("int", "integer"):
        return "integer"
    if t in ("varchar", "text"):
        return "string"
    if t in ("bool",):
        return "boolean"
    return t if t in known else "string"
