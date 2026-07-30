"""Feature builders: pure functions from workspace state to LSP structures.

The engineer sees a model violation in the same place as a type error:
- model YAML files: validation, ontology-layer rules, joinability/fan-out
- dbt files (.sql / schema.yml): drift, contract mismatch, edited-generated-block
  (via the SAME merge engine, run in dry-run), FK heuristics, manifest-version
  warnings

Every diagnostic carries a machine `code` + a `data` payload that code actions
key off — adopt column, lift model, unmanage block, declare relationship.
"""

from __future__ import annotations

import re
from pathlib import Path

from lsprotocol import types as lsp
from mdl_ontology import check_layers

from mdl_core.diagnostics import Severity
from mdl_core.validate import validate
from mdl_emit_dbt.emitter import DbtEmitter
from mdl_lsp.workspace import ModelWorkspace
from mdl_reverse import lifting
from mdl_reverse.drift import DriftKind, DriftSeverity, compute_drift

_SEV = {
    Severity.error: lsp.DiagnosticSeverity.Error,
    Severity.warning: lsp.DiagnosticSeverity.Warning,
    Severity.info: lsp.DiagnosticSeverity.Information,
}
_DRIFT_SEV = {
    DriftSeverity.breaking: lsp.DiagnosticSeverity.Error,
    DriftSeverity.unmanaged: lsp.DiagnosticSeverity.Information,
    DriftSeverity.additive: lsp.DiagnosticSeverity.Warning,
    DriftSeverity.cosmetic: lsp.DiagnosticSeverity.Hint,
}
SOURCE = "modelith"
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _line_of(text: str, needle: str) -> int:
    for i, line in enumerate(text.splitlines()):
        if needle in line:
            return i
    return 0


def _range(text: str, needle: str) -> lsp.Range:
    line = _line_of(text, needle)
    lines = text.splitlines()
    col = lines[line].find(needle) if line < len(lines) and needle in lines[line] else 0
    end = col + len(needle) if col >= 0 else 80
    return lsp.Range(lsp.Position(line, max(col, 0)), lsp.Position(line, end))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# --- model-file diagnostics ----------------------------------------------------


def model_diagnostics(ws: ModelWorkspace) -> dict[Path, list[lsp.Diagnostic]]:
    model = ws.model
    if model is None or ws.model_dir is None:
        return {}
    diags = validate(model)
    # Only check IRI resolvability when a vocabulary is actually declared —
    # otherwise every alignment would error on a repo that simply hasn't
    # vendored its ontology yet (noise, not signal).
    registry = ws.registry
    if registry is not None and not registry.prefixes:
        registry = None
    diags.extend(check_layers(model, registry=registry))
    try:
        from mdl_emit_semantic import validate_joinability

        diags.extend(validate_joinability(model))
    except ImportError:  # emit-semantic optional for the LSP
        pass

    ulid_file = ws.ulid_to_file()
    out: dict[Path, list[lsp.Diagnostic]] = {}
    for d in diags.items:
        rel = ulid_file.get(d.path or "", "mdl-project.yaml")
        path = ws.model_dir / rel
        text = _read(path)
        rng = _range(text, d.path) if d.path and d.path in text else _range(text, "id:")
        out.setdefault(path, []).append(
            lsp.Diagnostic(
                range=rng,
                message=d.message,
                severity=_SEV[d.severity],
                code=d.code,
                source=SOURCE,
            )
        )
    return out


# --- dbt-file diagnostics ------------------------------------------------------


def dbt_diagnostics(ws: ModelWorkspace) -> dict[Path, list[lsp.Diagnostic]]:
    out: dict[Path, list[lsp.Diagnostic]] = {}
    model = ws.model
    if model is None or ws.dbt_dir is None:
        return out

    schema_yml = ws.dbt_dir / "models" / "schema.yml"
    schema_text = _read(schema_yml)

    def _sql_path(name: str) -> Path:
        return ws.dbt_dir / "models" / f"{name}.sql"

    def add(path: Path, rng: lsp.Range, msg: str, sev, code: str, data: dict | None = None):
        out.setdefault(path, []).append(
            lsp.Diagnostic(
                range=rng, message=msg, severity=sev, code=code, source=SOURCE, data=data
            )
        )

    manifest = ws.manifest
    if manifest is not None:
        target = model.config.dbt_target or "duckdb_dev"
        report = compute_drift(model, manifest, target)
        for item in report.items:
            code = f"MDL-DRIFT-{item.kind.value}"
            sev = _DRIFT_SEV[item.severity]
            data = {
                "kind": item.kind.value,
                "model": item.model,
                "column": item.column,
                **item.payload,
            }
            if item.kind == DriftKind.unmanaged_model:
                p = _sql_path(item.model)
                add(p, _range(_read(p), "select") if p.exists() else _zero(),
                    f"{item.detail} — lift it to keep the model current", sev, code, data)
            elif item.kind == DriftKind.column_added and item.column:
                rng = (
                    _range(schema_text, f"name: {item.column}")
                    if f"name: {item.column}" in schema_text
                    else _zero()
                )
                add(schema_yml, rng, item.detail, sev, code, data)
            elif item.column and f"name: {item.column}" in schema_text:
                rng = _range(schema_text, f"name: {item.column}")
                add(schema_yml, rng, item.detail, sev, code, data)
            else:
                p = _sql_path(item.model)
                add(p if p.exists() else schema_yml, _zero(), item.detail, sev, code, data)

        for w in manifest.warnings:
            proj_file = ws.dbt_dir / "dbt_project.yml"
            add(proj_file, _zero(), w, lsp.DiagnosticSeverity.Warning, "MDL-W900")

        _fk_hints(ws, manifest, schema_yml, schema_text, add)

    _merge_engine_diagnostics(ws, model, out)
    return out


def _zero() -> lsp.Range:
    return lsp.Range(lsp.Position(0, 0), lsp.Position(0, 80))


def _fk_hints(ws, manifest, schema_yml: Path, schema_text: str, add) -> None:
    """Name/type FK heuristic (§6.2 medium confidence): propose, never auto."""
    model = ws.model
    declared: set[tuple[str, str]] = set()
    attr_name = {a.id: a.name for le in model.logical_entities.values() for a in le.attributes}
    le_name = {le.id: le.name for le in model.logical_entities.values()}
    for rel in model.relationships.values():
        col = attr_name.get(rel.from_.attributes[0]) if rel.from_.attributes else None
        frm = le_name.get(rel.from_.entity)
        if col and frm:
            declared.add((frm, col))

    managed = {le.name for le in model.logical_entities.values() if not le.unmanaged}
    for name in managed & set(manifest.models):
        mm = manifest.models[name]
        for guess in lifting.foreign_key_candidates(name, list(mm.columns), managed):
            if (name, guess.column) in declared:
                continue
            needle = f"name: {guess.column}"
            if needle not in schema_text:
                continue
            add(
                schema_yml,
                _range(schema_text, needle),
                f"{name}.{guess.column} looks like a foreign key to "
                f"{guess.target_entity!r} — no relationship declared in the model",
                lsp.DiagnosticSeverity.Hint,
                "MDL-H401",
                {"from_entity": name, "column": guess.column, "to_entity": guess.target_entity},
            )


def _merge_engine_diagnostics(ws, model, out) -> None:
    """Live edited-generated-block detection: compare each on-disk generated
    region against the freshly planned content for the same block. Same
    fingerprint + different body = the engineer edited Modelith-owned SQL
    (MDL-E201); different fingerprint + different body = a true conflict is
    waiting on the next generate (MDL-C301). This warns *now*, not at
    regeneration time — squiggles beat a CI email."""
    if ws.dbt_dir is None or not (ws.dbt_dir / ".mdl" / "state").exists():
        return
    from mdl_core.regions import parse as parse_regions

    try:
        target = model.config.dbt_target or "duckdb_dev"
        planned = DbtEmitter(model, target)._plan_files()
    except Exception:
        return
    for rel, spec in planned.items():
        path = ws.dbt_dir / rel
        if not path.exists():
            continue
        text = _read(path)
        prefix = spec.get("prefix", "--")
        try:
            ours = {r.obj_id: r for r in parse_regions(text, prefix).generated_regions()}
            theirs = {
                r.obj_id: r
                for r in parse_regions(spec["content"], prefix).generated_regions()
            }
        except ValueError:
            continue  # malformed markers; the merge engine will handle it at generate
        for obj_id, fresh in theirs.items():
            mine = ours.get(obj_id)
            if mine is None:
                continue
            if mine.content.strip("\n") == fresh.content.strip("\n"):
                continue
            if mine.fingerprint == fresh.fingerprint:
                code, msg = "MDL-E201", (
                    f"{rel}: generated block was edited by hand — it belongs to "
                    f"Modelith. Adopt the change into the model or unmanage the block."
                )
            else:
                code, msg = "MDL-C301", (
                    f"{rel}: generated block changed in both the model and by hand — "
                    f"the next `mdl generate` will write a conflict."
                )
            entity = _entity_of_generated_file(ws, rel)
            out.setdefault(path, []).append(
                lsp.Diagnostic(
                    range=_range(text, f"{prefix} mdl:generated-begin"),
                    message=msg,
                    severity=lsp.DiagnosticSeverity.Warning,
                    code=code,
                    source=SOURCE,
                    data={"entity": entity, "path": rel},
                )
            )


def _entity_of_generated_file(ws, rel: str) -> str | None:
    stem = Path(rel).stem
    le = ws.entity_for_dbt_model(stem)
    return le.name if le else None


# --- hover ----------------------------------------------------------------------


def hover(ws: ModelWorkspace, path: Path, line: int, character: int) -> lsp.Hover | None:
    text = _read(path)
    lines = text.splitlines()
    if line >= len(lines):
        return None
    word = _word_at(lines[line], character)
    if not word:
        return None

    model = ws.model
    if model is None:
        return None

    # dbt SQL: entity = file stem; schema.yml: nearest enclosing model block.
    le = None
    if path.suffix == ".sql":
        le = ws.entity_for_dbt_model(path.stem)
    elif path.name in ("schema.yml", "schema.yaml"):
        le = _enclosing_schema_model(ws, lines, line)
    if le is None and ws.model_dir and ws.model_dir in path.parents:
        le = ws.entity_for_dbt_model(path.stem)

    if le is None:
        return None
    if word == le.name:
        return _hover_md(ws.sme_card(le, None))
    attr = ws.attribute(le, word)
    if attr is not None:
        return _hover_md(ws.sme_card(le, attr))
    return None


def _enclosing_schema_model(ws, lines: list[str], line: int):
    for i in range(line, -1, -1):
        m = re.match(r"\s*- name:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", lines[i])
        if m:
            le = ws.entity_for_dbt_model(m.group(1))
            if le is not None:
                return le
    return None


def _word_at(line: str, character: int) -> str | None:
    for m in _WORD.finditer(line):
        if m.start() <= character <= m.end():
            return m.group(0)
    return None


def _hover_md(md: str) -> lsp.Hover:
    return lsp.Hover(contents=lsp.MarkupContent(kind=lsp.MarkupKind.Markdown, value=md))


# --- code lens -------------------------------------------------------------------


def code_lens(ws: ModelWorkspace, path: Path) -> list[lsp.CodeLens]:
    lenses: list[lsp.CodeLens] = []
    if path.suffix != ".sql" or ws.dbt_dir is None:
        return lenses
    text = _read(path)
    name = path.stem

    marker = "mdl:generated-begin"
    if marker in text:
        le = ws.entity_for_dbt_model(name)
        title = f"◮ Modelith-owned · {le.name}" if le else "◮ Modelith-owned"
        lenses.append(
            lsp.CodeLens(
                range=_range(text, marker),
                command=lsp.Command(title=title, command="modelith.openPreview", arguments=[name]),
            )
        )
    else:
        manifest = ws.manifest
        model = ws.model
        if manifest and model and name in manifest.models:
            known = {le.name for le in model.logical_entities.values()}
            if name not in known and not lifting.is_staging(name):
                lenses.append(
                    lsp.CodeLens(
                        range=_zero(),
                        command=lsp.Command(
                            title="◮ Lift to logical model",
                            command="mdl.lift",
                            arguments=[str(path)],
                        ),
                    )
                )
    return lenses


# --- code actions ----------------------------------------------------------------


def code_actions(
    ws: ModelWorkspace, path: Path, diagnostics: list[lsp.Diagnostic]
) -> list[lsp.CodeAction]:
    actions: list[lsp.CodeAction] = []
    for d in diagnostics:
        if d.source != SOURCE:
            continue
        data = d.data if isinstance(d.data, dict) else {}
        code = str(d.code or "")
        if code == "MDL-DRIFT-column_added" and data.get("model") and data.get("column"):
            actions.append(
                _action(
                    f"Adopt column '{data['column']}' into Modelith model",
                    "mdl.adoptColumn",
                    [data["model"], data["column"], data.get("data_type")],
                    d,
                )
            )
        elif code == "MDL-DRIFT-unmanaged_model" and data.get("model"):
            actions.append(
                _action(
                    f"Lift '{data['model']}' to logical model",
                    "mdl.lift",
                    [str(path)],
                    d,
                )
            )
        elif code in ("MDL-E201", "MDL-C301") and data.get("entity"):
            actions.append(
                _action(
                    f"Unmanage '{data['entity']}' — hand this SQL to engineers permanently",
                    "mdl.unmanage",
                    [data["entity"]],
                    d,
                )
            )
        elif code == "MDL-H401" and data.get("from_entity"):
            actions.append(
                _action(
                    f"Declare relationship {data['from_entity']}.{data['column']} → "
                    f"{data['to_entity']} in the model",
                    "mdl.declareRelationship",
                    [data["from_entity"], data["column"], data["to_entity"]],
                    d,
                )
            )
    return actions


def _action(title: str, command: str, args: list, diag: lsp.Diagnostic) -> lsp.CodeAction:
    return lsp.CodeAction(
        title=title,
        kind=lsp.CodeActionKind.QuickFix,
        diagnostics=[diag],
        command=lsp.Command(title=title, command=command, arguments=args),
    )
