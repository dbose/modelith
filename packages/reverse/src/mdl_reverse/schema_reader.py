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
