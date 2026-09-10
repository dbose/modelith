"""Emit a Mermaid erDiagram from the neutral ER view.

Mermaid renders natively in GitHub, GitLab, and most markdown, so this is the
zero-friction documentation export for a git-native tool. It carries PK/FK markers and
crow's-foot cardinality, but it is a diagram language: it does not encode domains or
enforce constraints, so it is a hand-off format, not a lossless round-trip source."""

from __future__ import annotations

import re

from mdl_core.ir import Model
from mdl_emit_erd.model_view import build_tables


def _safe(name: str) -> str:
    """Mermaid entity/attribute identifiers are unquoted; sanitise to word chars."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return s or "_"


def emit_mermaid(model: Model) -> str:
    """A ```mermaid erDiagram block. `type_map` is cosmetic (Mermaid shows a type
    token per attribute); we use the abstract base type so it reads logically."""
    from mdl_emit_dbt.platforms import get_adapter

    tables = build_tables(model, get_adapter("duckdb"))
    lines = ["erDiagram"]

    for t in tables:
        lines.append(f"  {_safe(t.name)} {{")
        for c in t.columns:
            key = "PK" if c.pk else ("UK" if c.unique else "")
            # attribute line: <type> <name> [PK|FK|UK] "<comment>"
            frag = f"    {_safe(c.base_type)} {_safe(c.name)}"
            marks = key
            if any(c.name in fk.columns for fk in t.foreign_keys):
                marks = (marks + ",FK").strip(",") if marks else "FK"
            if marks:
                frag += f" {marks}"
            lines.append(frag)
        lines.append("  }")

    # relationships: parent ||--o{ child (one-to-many) or ||--|| (one-to-one),
    # labelled with the relationship name. Mermaid draws parent first.
    for t in tables:
        for fk in t.foreign_keys:
            parent = _safe(fk.ref_table)
            child = _safe(t.name)
            # parent side is always "one"; child side "one" if the FK is unique else "many"
            child_card = "||" if fk.child_unique else "o{"
            lines.append(f'  {parent} ||--{child_card} {child} : "{_safe(fk.name)}"')

    return "\n".join(lines) + "\n"
