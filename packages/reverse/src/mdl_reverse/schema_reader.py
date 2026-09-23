"""Read an emitted dbt project's schema.yml into a ManifestProjection.

`dbt parse` turns models + schema.yml into manifest.json; that requires a dbt
install and a project context. For the warehouse-free round-trip path (spec §6.1
"must work against dbt compile --empty") and for tests, we read the emitted
`schema.yml` directly into the same ManifestProjection the manifest reader
produces. This exercises the real reverse pipeline end-to-end without dbt.

Crucially, the emitter writes `meta.mdl_ulid` on each model and column, and the
`relationships` info; we surface those so reverse recovers ULID identity and the
generate->reverse->generate diff is semantically empty (property 2, §12).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from mdl_core.yaml_io import load_str
from mdl_reverse.manifest import ManifestColumn, ManifestModel, ManifestProjection
from mdl_reverse.regions_strip import strip_regions

# The sentinel `_mdl_emit` prints before the JSON payload, so connect.py can find the
# constraint blob among dbt's run-operation output (kept in sync with the macro).
CONSTRAINTS_SENTINEL = "MDL_CONSTRAINTS_JSON "


def constraint_macro_sql() -> str:
    """The `mdl_get_constraints` dbt macro source (shipped as a package resource).

    `mdl reverse --connect` writes this into the user's dbt project `macros/` dir before
    `dbt run-operation mdl_get_constraints`, so declared PK/FK/unique/nullable come back
    from the warehouse's own constraint catalog. Read via importlib.resources so it
    resolves in both an editable checkout and the shipped wheel."""
    return (resources.files("mdl_reverse") / "resources" / "mdl_get_constraints.sql").read_text(
        encoding="utf-8"
    )


def parse_constraints_output(stdout: str) -> dict:
    """Extract the constraint JSON the macro printed (the `MDL_CONSTRAINTS_JSON <json>`
    line) from a `dbt run-operation` stdout blob. Returns {} when the sentinel is absent
    (macro didn't run, or the warehouse had no readable catalog) — the graceful path."""
    import json

    for line in stdout.splitlines():
        idx = line.find(CONSTRAINTS_SENTINEL)
        if idx != -1:
            payload = line[idx + len(CONSTRAINTS_SENTINEL):].strip()
            try:
                return json.loads(payload) or {}
            except (ValueError, json.JSONDecodeError):
                return {}
    return {}


def read_schema_yml(path: str | Path) -> ManifestProjection:
    text = Path(path).read_text(encoding="utf-8")
    # schema.yml is wrapped in mdl region markers (comment prefix '#'); strip them
    # to get the plain YAML the reader parses.
    yaml_text = strip_regions(text, prefix="#")
    data = load_str(yaml_text) or {}
    return _project(data)


def read_schema_dict(data: dict) -> ManifestProjection:
    return _project(data)


def read_sources_yml(path: str | Path) -> ManifestProjection:
    """Read a dbt `sources.yml` (as emitted by dbt-codegen's `generate_source`) into a
    ManifestProjection — the live-database front door.

    `generate_source` introspects a live warehouse via the dbt adapter and prints a
    `sources:` block: one entry per schema, each with `tables:`, each table carrying
    `columns:` with `data_type` (when run with `include_data_types: true`). That is a
    different shape from an emitted model's `models:` block (which `read_schema_yml`
    walks), so this is its own reader — but it normalises to the SAME ManifestProjection
    every other front door produces, so the reverse engine, classification, confidence
    ledger and writer are all reused unchanged.

    What the live source does and does NOT carry, and how the engine treats it:
    - columns + types -> `ManifestColumn.data_type` (folded to a base domain in the engine).
    - nullability -> `meta["nullable"]` when the adapter reports it (honoured at
      reverse.py:330); absent otherwise, so it keeps the historical `True` default.
    - NO primary/foreign keys: warehouses rarely enforce them and information_schema FK
      metadata is inconsistent across adapters, so `generate_source` omits them. Keys and
      relationships therefore come from the engine's name-based inference (business-key
      candidates, the `*_id` FK heuristic -> medium-confidence proposals a human triages),
      not from this reader. `meta["pk"]` is left absent and `relationship_tests` empty.
    """
    text = Path(path).read_text(encoding="utf-8")
    data = load_str(text) or {}
    return read_sources_dict(data)


def read_sources_dict(data: dict) -> ManifestProjection:
    """Project an already-parsed `sources.yml` dict. Split from the file reader so tests
    (and the live-connect path, which captures generate_source on stdout) can pass a dict
    directly."""
    models: dict[str, ManifestModel] = {}
    warnings: list[str] = []
    for source in data.get("sources", []) or []:
        database = source.get("database")
        for table in source.get("tables", []) or []:
            name = table.get("name")
            if not name:
                continue
            if name in models:
                # Two schemas exposing the same table name would collide on the model key;
                # keep the first and warn rather than silently overwrite.
                warnings.append(
                    f"duplicate table name {name!r} across sources; keeping the first"
                )
                continue
            cols: dict[str, ManifestColumn] = {}
            for col in table.get("columns", []) or []:
                cname = col.get("name")
                if not cname:
                    continue
                cmeta: dict = {}
                # generate_source can carry nullability when the adapter reports it; honour
                # it so the engine infers NOT NULL instead of defaulting to nullable.
                if "nullable" in col:
                    cmeta["nullable"] = bool(col.get("nullable"))
                cols[cname] = ManifestColumn(
                    name=cname,
                    data_type=(col.get("data_type") or col.get("type") or None),
                    description=col.get("description") or None,
                    meta=cmeta,
                )
            meta: dict = {}
            if database:
                meta["source_database"] = database
            models[name] = ManifestModel(
                name=name,
                unique_id=f"model.live.{name}",
                columns=cols,
                description=table.get("description") or None,
                meta=meta,
            )
    return ManifestProjection(models=models, warnings=warnings)


def constraints_to_projection(constraints: dict) -> ManifestProjection:
    """Build a ManifestProjection straight from the `mdl_get_constraints` macro output —
    the LIVE-database front door. The macro returns columns + types + nullability (the
    spine) AND declared PK / FK / unique (the keys) in one payload, so the live path needs
    no dbt-codegen: this one reader produces the whole projection.

    Per table it sets, on each `ManifestColumn`: `data_type` from the column's `type`,
    `meta["nullable"]` from its `nullable`, and `meta["pk"]=True` for PK members; and per
    model it appends each FK (whose referenced table is also present) as a
    `relationship_tests` `(column, ref_table)` tuple — the DDL path's HIGH-confidence
    contract. So a declared FK is an auto-accepted relationship, a declared PK the
    authoritative business key, exactly as erwin would. An empty payload -> empty
    projection (the caller falls back / warns).

    JSON shape (per table): {"columns": {"c": {"type": "...", "nullable": bool}},
    "primary_key": ["c"], "unique": [["c"]], "foreign_keys": [{"columns": ["c"],
    "ref_table": "t", "ref_columns": ["c"]}]}.
    """
    models: dict[str, ManifestModel] = {}
    known = set(constraints)
    for table_name, tc in constraints.items():
        if not isinstance(tc, dict):
            continue
        cols: dict[str, ManifestColumn] = {}
        for cname, cinfo in (tc.get("columns") or {}).items():
            meta: dict = {}
            if isinstance(cinfo, dict) and "nullable" in cinfo:
                meta["nullable"] = bool(cinfo["nullable"])
            cols[cname] = ManifestColumn(
                name=cname,
                data_type=(cinfo.get("type") if isinstance(cinfo, dict) else None) or None,
                meta=meta,
            )
        for pk_col in tc.get("primary_key") or []:
            if pk_col in cols:
                cols[pk_col].meta["pk"] = True
                cols[pk_col].meta.setdefault("nullable", False)
        rel_tests: list[tuple[str, str]] = []
        for fk in tc.get("foreign_keys") or []:
            if not isinstance(fk, dict) or fk.get("ref_table") not in known:
                continue
            for col_name in fk.get("columns") or []:
                if col_name in cols and (col_name, fk["ref_table"]) not in rel_tests:
                    rel_tests.append((col_name, fk["ref_table"]))
        models[table_name] = ManifestModel(
            name=table_name,
            unique_id=f"model.live.{table_name}",
            columns=cols,
            relationship_tests=rel_tests,
        )
    return ManifestProjection(models=models)


def apply_constraints(projection: ManifestProjection, constraints: dict) -> ManifestProjection:
    """Overlay real warehouse constraints (from the `mdl_get_constraints` dbt macro) onto a
    projection built from the column/type spine (a catalog, or a legacy generate_source
    sources.yml). Retained for the catalog-fallback path (a built dbt project); the live
    default uses `constraints_to_projection` since the macro now carries the spine too.

    This is what makes the live-database reverse erwin-grade: `generate_source` and
    `catalog.json` carry columns + types but no keys, so on their own the reverse can only
    *infer* keys and relationships (medium-confidence proposals). The dispatched constraint
    macro reads the warehouse's own constraint catalog (information_schema /
    pg_constraint / SHOW ... KEYS depending on the adapter) and returns declared PK / FK /
    unique / nullability. Overlaying it here lands:
      - PK columns as `meta["pk"] = True` -> the engine takes them as the business key
        authoritatively (reverse.py:297-303), a composite PK included.
      - nullability as `meta["nullable"]` -> honoured at reverse.py:330 (always present when
        the macro ran; information_schema `is_nullable` is universal across warehouses).
      - each FK as a `relationship_tests` `(column, ref_table)` tuple -> the HIGH-confidence
        relationship channel (reverse.py:410-427), the SAME contract the DDL path fills.
    So a declared FK becomes an auto-accepted relationship, not a proposal.

    The overlay is additive and fail-soft: an empty/absent `constraints` dict is a no-op
    (the projection keeps its inferred behaviour), a constraint on an unknown table or
    column is skipped, and only FKs whose referenced table is also in the projection are
    kept (a dangling FK can't become a graph edge). Constraint JSON shape, per table:
        {"<table>": {
            "primary_key": ["col", ...],
            "unique": [["col", ...], ...],
            "foreign_keys": [{"columns": ["col"], "ref_table": "t", "ref_columns": ["c"]}],
            "columns": {"col": {"nullable": bool}}}}
    Mutates and returns the same projection (the projection is freshly built per reverse).
    """
    if not constraints:
        return projection
    known = set(projection.models)
    for table_name, tc in constraints.items():
        model = projection.models.get(table_name)
        if model is None or not isinstance(tc, dict):
            continue
        # nullability + pk are per-column meta overlays
        for cname, cinfo in (tc.get("columns") or {}).items():
            col = model.columns.get(cname)
            if col is not None and isinstance(cinfo, dict) and "nullable" in cinfo:
                col.meta["nullable"] = bool(cinfo["nullable"])
        for pk_col in tc.get("primary_key") or []:
            col = model.columns.get(pk_col)
            if col is not None:
                col.meta["pk"] = True
                # a PK column is by definition not nullable, even if the columns block
                # didn't say so.
                col.meta.setdefault("nullable", False)
        # foreign keys -> HIGH-confidence relationship tests, one per constrained column,
        # only when the referenced table is present (a dangling FK can't be an edge).
        for fk in tc.get("foreign_keys") or []:
            if not isinstance(fk, dict):
                continue
            ref_table = fk.get("ref_table")
            if ref_table not in known:
                continue
            for col_name in fk.get("columns") or []:
                if col_name in model.columns:
                    edge = (col_name, ref_table)
                    if edge not in model.relationship_tests:
                        model.relationship_tests.append(edge)
    return projection


def _project(data: dict) -> ManifestProjection:
    models: dict[str, ManifestModel] = {}
    for entry in data.get("models", []) or []:
        name = entry.get("name")
        if not name:
            continue
        cols: dict[str, ManifestColumn] = {}
        rel_tests: list[tuple[str, str]] = []
        for col in entry.get("columns", []) or []:
            cname = col.get("name")
            if not cname:
                continue
            cmeta = dict(col.get("meta") or {})
            cols[cname] = ManifestColumn(
                name=cname,
                data_type=(col.get("data_type") or None),
                description=col.get("description") or None,
                meta=cmeta,
            )
            # relationships tests attached at column level. In a dbt relationships
            # test, `field` is the *target* column; the source column is the one the
            # test is attached to (cname).
            for test in col.get("tests", []) or []:
                if isinstance(test, dict) and "relationships" in test:
                    rel = test["relationships"]
                    to = _strip_ref(str(rel.get("to", "")))
                    if to:
                        rel_tests.append((cname, to))
        config = entry.get("config") or {}
        contract = config.get("contract") or {}
        models[name] = ManifestModel(
            name=name,
            unique_id=f"model.reversed.{name}",
            columns=cols,
            contract_enforced=bool(contract.get("enforced", False)),
            description=entry.get("description") or None,
            meta=dict(entry.get("meta") or {}),
            tags=list(entry.get("tags") or []),
            relationship_tests=rel_tests,
        )
    return ManifestProjection(models=models)


def _strip_ref(expr: str) -> str:
    import re

    m = re.search(r"ref\(\s*['\"]([^'\"]+)['\"]", expr)
    return m.group(1) if m else expr
