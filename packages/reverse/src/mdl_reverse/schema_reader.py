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

from pathlib import Path

from mdl_core.yaml_io import load_str
from mdl_reverse.manifest import ManifestColumn, ManifestModel, ManifestProjection
from mdl_reverse.regions_strip import strip_regions


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
