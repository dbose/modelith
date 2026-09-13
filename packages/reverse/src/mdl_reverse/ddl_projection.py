"""Project a raw SQL DDL script into the reverse engine's ManifestProjection.

`mdl reverse` was dbt-artifact-only; a `.sql` schema dump could enter only via
`mdl import sql`, which bypasses the classification/confidence/ledger machinery. This
adapter routes DDL through the SAME `reverse()` engine as a dbt manifest, so a plain
SQL dump gains surrogate-key stripping, SCD2 detection, staging exclusion, confidence-
banded relationship inference, and the review ledger.

The heavy lifting already exists: `mdl_emit_erd.imports.parse_sql_ddl` (sqlglot,
multi-dialect) parses CREATE TABLE into an `ImportedModel` with columns (name, type,
nullable, pk) and foreign keys. This module is a thin bridge to `ManifestProjection`.

A declared DDL foreign key maps to a `relationship_test` — the same channel a dbt
`relationships` test uses — so the engine treats an explicit FK as HIGH confidence, as
it should. Nullability from the DDL (`NOT NULL` / PK) is carried through (the manifest
path can't recover it and defaults to nullable; DDL knows the truth)."""

from __future__ import annotations

from mdl_emit_erd.imports.parse_ddl import parse_sql_ddl

from mdl_reverse.manifest import ManifestColumn, ManifestModel, ManifestProjection


def ddl_projection(ddl: str, *, dialect: str | None = None) -> ManifestProjection:
    """Parse a SQL DDL script and project it into a ManifestProjection the reverse
    engine consumes. Parse warnings (e.g. an unparseable statement, an FK to an unknown
    table) are threaded onto the projection so the CLI can surface them."""
    imported = parse_sql_ddl(ddl, dialect=dialect)

    known = {t.name for t in imported.tables}
    models: dict[str, ManifestModel] = {}
    for t in imported.tables:
        columns = {
            c.name: ManifestColumn(
                name=c.name,
                data_type=c.base_type,
                # nullability and PK membership are authoritative in DDL — carry both.
                # `pk` lets the engine use the DECLARED key (incl. a composite table-level
                # PRIMARY KEY) instead of falling back to name heuristics.
                meta={"nullable": c.nullable, "pk": c.pk},
            )
            for c in t.columns
        }
        # A DDL foreign key is an explicit relationship: map it to a relationship_test
        # (column, target-table) so the engine records it at HIGH confidence, exactly
        # as a dbt `relationships` test would be. Skip FKs to tables not in the script.
        rel_tests: list[tuple[str, str]] = []
        for fk in t.foreign_keys:
            if fk.ref_table in known and fk.columns:
                rel_tests.append((fk.columns[0], fk.ref_table))
        models[t.name] = ManifestModel(
            name=t.name,
            unique_id=f"model.ddl.{t.name}",
            columns=columns,
            description=t.definition,
            relationship_tests=rel_tests,
        )

    return ManifestProjection(models=models, warnings=list(imported.warnings))
