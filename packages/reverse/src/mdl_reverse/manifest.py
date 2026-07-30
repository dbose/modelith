"""Version-tolerant dbt manifest reader (spec §6.1).

We normalise whatever manifest version we're handed into a small, stable
projection: {model_name -> ManifestModel(columns, contract, tests...)}. This
insulates the drift engine from manifest schema churn between dbt 1.5/1.9/v12+.

Strategy:
1. Try `dbt-artifacts-parser` (authoritative, version-aware) if importable.
2. Fall back to raw-JSON navigation of the fields we need, which have been stable
   across manifest versions: nodes[].{resource_type,name,columns{name,data_type},
   config.contract.enforced, meta}.

Must work against `dbt compile --empty` output (no warehouse), so we never require
`catalog.json`; column types come from the manifest's contract/column entries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ManifestColumn:
    name: str
    data_type: str | None = None
    description: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class ManifestModel:
    name: str
    unique_id: str
    columns: dict[str, ManifestColumn] = field(default_factory=dict)
    contract_enforced: bool = False
    description: str | None = None
    meta: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    relationship_tests: list[tuple[str, str]] = field(default_factory=list)
    # (column, to_ref) pairs recovered from relationships tests / fk constraints


# Manifest schema versions this reader has been exercised against. The reader is
# raw-JSON and engine-agnostic (works for anything emitting the manifest schema,
# dbt-core or its successors); newer versions parse best-effort with a warning.
KNOWN_SCHEMA_VERSIONS = frozenset({7, 8, 9, 10, 11, 12, 20})


def _schema_version_number(url: str | None) -> int | None:
    import re

    if not url:
        return None
    m = re.search(r"/v(\d+)\.json", url)
    return int(m.group(1)) if m else None


@dataclass
class ManifestProjection:
    models: dict[str, ManifestModel]
    dbt_schema_version: str | None = None
    warnings: list[str] = field(default_factory=list)

    def model_names(self) -> set[str]:
        return set(self.models)


def read_manifest(path: str | Path) -> ManifestProjection:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return _project(raw)


def read_manifest_dict(raw: dict[str, Any]) -> ManifestProjection:
    return _project(raw)


def _project(raw: dict[str, Any]) -> ManifestProjection:
    schema_version = None
    meta = raw.get("metadata") or {}
    if isinstance(meta, dict):
        schema_version = meta.get("dbt_schema_version")

    warnings: list[str] = []
    vnum = _schema_version_number(schema_version)
    if schema_version and vnum is not None and vnum not in KNOWN_SCHEMA_VERSIONS:
        warnings.append(
            f"manifest schema v{vnum} is newer than this reader has been tested "
            f"against — parsed best-effort; verify drift output"
        )
    models: dict[str, ManifestModel] = {}
    nodes = raw.get("nodes") or {}
    # Index relationships tests by the model they test, so we can attribute them.
    rel_tests_by_model = _extract_relationship_tests(nodes)

    for unique_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        if node.get("resource_type") != "model":
            continue
        name = node.get("name") or unique_id.split(".")[-1]
        cols: dict[str, ManifestColumn] = {}
        for col_name, col in (node.get("columns") or {}).items():
            col = col or {}
            cols[col_name] = ManifestColumn(
                name=col_name,
                data_type=_norm_type(col.get("data_type")),
                description=col.get("description") or None,
                meta=dict(col.get("meta") or {}),
            )
        config = node.get("config") or {}
        contract = config.get("contract") or {}
        models[name] = ManifestModel(
            name=name,
            unique_id=unique_id,
            columns=cols,
            contract_enforced=bool(contract.get("enforced", False)),
            description=node.get("description") or None,
            meta=dict(node.get("meta") or {}),
            tags=list(node.get("tags") or []),
            relationship_tests=rel_tests_by_model.get(name, []),
        )
    return ManifestProjection(
        models=models, dbt_schema_version=schema_version, warnings=warnings
    )


def _extract_relationship_tests(nodes: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Recover (column, to) pairs from generic `relationships` test nodes so drift
    can detect a removed relationship."""
    out: dict[str, list[tuple[str, str]]] = {}
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        if node.get("resource_type") != "test":
            continue
        meta = node.get("test_metadata") or {}
        if meta.get("name") != "relationships":
            continue
        kwargs = meta.get("kwargs") or {}
        col = kwargs.get("column_name") or kwargs.get("field")
        to = kwargs.get("to")
        # attached_node points at the model under test in modern manifests
        attached = node.get("attached_node") or ""
        model_name = attached.split(".")[-1] if attached else None
        if model_name and col and to:
            out.setdefault(model_name, []).append((str(col), _strip_ref(str(to))))
    return out


def _strip_ref(expr: str) -> str:
    """`ref('trade')` / `ref("trade")` / `ref('pkg', 'trade')` -> `trade`."""
    import re

    m = re.search(r"ref\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*['\"]([^'\"]+)['\"]\s*)?\)", expr)
    if m:
        return m.group(2) or m.group(1)
    return expr


def _norm_type(t: str | None) -> str | None:
    if t is None:
        return None
    # Normalise case + whitespace so cosmetic type spelling differences don't
    # register as drift (NUMBER(38,0) vs number(38, 0)).
    return " ".join(str(t).upper().replace(" ", "").split())
