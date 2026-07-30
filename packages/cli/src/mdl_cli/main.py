"""`mdl` CLI (spec §10).

Exit codes (spec §10): 0 ok, 1 validation error, 2 drift breaking,
3 merge conflict, 4 adapter/plan failure.
"""

from __future__ import annotations

from pathlib import Path

import typer

from mdl_cli.scaffold import scaffold
from mdl_core.diagnostics import Severity
from mdl_core.merge import MergeOutcome
from mdl_core.naming import lint as naming_lint
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate as run_validate
from mdl_emit_dbt.emitter import DbtEmitter
from mdl_reverse.drift import DriftSeverity, compute_drift
from mdl_reverse.ledger import DecisionLedger, Verdict
from mdl_reverse.manifest import read_manifest
from mdl_reverse.reconcile import reconcile
from mdl_reverse.render import render_json, render_markdown, render_text
from mdl_reverse.reverse import reverse as run_reverse
from mdl_reverse.schema_reader import read_schema_yml
from mdl_reverse.writer import write_model as write_reversed

app = typer.Typer(help="Modelith: ontology-anchored, git-native data modeling for dbt.")


def _load(model_dir: Path) -> ModelRepo:
    try:
        return ModelRepo.load(model_dir)
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e


@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Directory to scaffold the model repo in"),
    name: str = typer.Option("modelith_model", help="Project name"),
) -> None:
    """Scaffold a model repo."""
    files = scaffold(path, project_name=name)
    typer.secho(f"Scaffolded {len(files)} files under {path}", fg=typer.colors.GREEN)


@app.command()
def validate(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    severity: str = typer.Option("error", help="Gate severity: error|warn"),
) -> None:
    """Validate the model (schema, refs, ontology, naming)."""
    repo = _load(model_dir)
    diags = run_validate(repo.model)
    for d in diags.items:
        color = {
            Severity.error: typer.colors.RED,
            Severity.warning: typer.colors.YELLOW,
            Severity.info: typer.colors.BLUE,
        }[d.severity]
        typer.secho(f"{d.code} [{d.severity.value}] {d.message}", fg=color)

    gate = Severity.error if severity == "error" else Severity.warning
    if diags.has(gate):
        typer.secho("validation failed", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("validation passed", fg=typer.colors.GREEN)


@app.command()
def lint(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    fix: bool = typer.Option(False, "--fix", help="Apply naming corrections in place"),
) -> None:
    """Naming-standards lint (spec §2.4)."""
    repo = _load(model_dir)
    diags, fixes = naming_lint(repo.model)
    for d in diags.items:
        typer.secho(f"{d.code} {d.message}", fg=typer.colors.YELLOW)
    if fix and not fixes.empty():
        _apply_naming_fixes(repo, fixes)
        repo.save()
        typer.secho(
            f"applied {len(fixes.entities) + len(fixes.attributes)} fixes",
            fg=typer.colors.GREEN,
        )
    elif not diags.items:
        typer.secho("no naming issues", fg=typer.colors.GREEN)


@app.command()
def generate(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    target: str = typer.Option(None, "--target", "-t", help="Physical target"),
    out: Path = typer.Option(Path("target/dbt"), "--out", "-o", help="dbt project output dir"),
    inline: bool = typer.Option(False, "--inline", help="Inline pattern SQL instead of macros"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Generate the dbt project with protected regions and three-way merge."""
    repo = _load(model_dir)
    tgt = target or repo.model.config.dbt_target or "duckdb_dev"
    emitter = DbtEmitter(repo.model, tgt, inline=inline)
    result = emitter.generate(out, write=not dry_run)

    for mr in result.merges:
        if mr.outcome != MergeOutcome.unchanged:
            typer.echo(f"  {mr.outcome.value:16} {mr.path}")
        for d in mr.diagnostics:
            typer.secho(f"    {d.code} {d.message}", fg=typer.colors.RED)

    if result.has_conflicts:
        typer.secho("merge conflicts — resolve before proceeding", fg=typer.colors.RED, err=True)
        raise typer.Exit(3)
    if result.has_errors:
        typer.secho("generation errors (e.g. MDL-E201)", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    verb = "would write" if dry_run else "wrote"
    typer.secho(f"{verb} {len(result.files)} files to {out}", fg=typer.colors.GREEN)


@app.command()
def reverse(
    project: Path = typer.Option(
        ..., "--project", help="Path to a dbt project (manifest.json or an emitted schema.yml)"
    ),
    out: Path = typer.Option(Path("model"), "--out", "-o", help="Where to write the model"),
    target: str = typer.Option("duckdb_dev", "--target", "-t"),
    name: str = typer.Option("reversed_model", "--name"),
    interactive: bool = typer.Option(
        False, "--interactive", help="Prompt to accept/reject medium-confidence proposals"
    ),
) -> None:
    """Reverse-engineer a dbt project into a Modelith model (spec §6)."""
    # Accept either a manifest.json or a schema.yml (warehouse-free path).
    if project.name.endswith(".yml") or project.name.endswith(".yaml"):
        proj = read_schema_yml(project)
    else:
        from mdl_reverse.manifest import read_manifest as _rm

        proj = _rm(project)

    ledger = DecisionLedger.load(out)
    result = run_reverse(
        proj, project_name=name, target=target, ledger=ledger, interactive=interactive
    )

    if interactive:
        _prompt_proposals(ledger, result, out, target)

    write_reversed(result.model, out)
    ledger.save(out)

    typer.secho(
        f"reversed {result.logical_count()} entities "
        f"({len(result.excluded)} staging/intermediate excluded); "
        f"{len(ledger.pending())} proposals pending review",
        fg=typer.colors.GREEN,
    )
    for d in result.proposals:
        mark = {"accepted": "✓", "rejected": "✗", "proposed": "?"}[d.verdict.value]
        typer.echo(f"  {mark} [{d.confidence.value}] {d.subject}")


def _prompt_proposals(ledger: DecisionLedger, result, out: Path, target: str) -> None:
    for d in list(ledger.pending()):
        ans = typer.prompt(f"Accept? [{d.confidence.value}] {d.subject} (y/n)", default="n")
        d.verdict = Verdict.accepted if ans.lower().startswith("y") else Verdict.rejected


@app.command()
def drift(
    manifest: Path = typer.Option(..., "--manifest", help="Path to target/manifest.json"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    target: str = typer.Option(None, "--target", "-t"),
    check: bool = typer.Option(False, "--check", help="CI gate: exit 2 on breaking drift"),
    reconcile_: bool = typer.Option(
        False, "--reconcile", help="Apply additive/cosmetic deltas to the model"
    ),
    fmt: str = typer.Option("text", "--format", help="text|json|mermaid"),
) -> None:
    """Compare the committed model to a compiled dbt manifest (spec §5.4)."""
    repo = _load(model_dir)
    tgt = target or repo.model.config.dbt_target or "duckdb_dev"
    try:
        proj = read_manifest(manifest)
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(4) from e

    report = compute_drift(repo.model, proj, tgt)

    if fmt == "json":
        typer.echo(render_json(report))
    elif fmt == "mermaid":
        typer.echo(render_markdown(report))
    else:
        typer.echo(render_text(report))

    if reconcile_:
        result = reconcile(repo, report, tgt)
        repo.save()
        typer.secho(
            f"reconciled {len(result.applied)} delta(s); "
            f"skipped {result.skipped_breaking} breaking",
            fg=typer.colors.GREEN,
        )
        for line in result.applied:
            typer.echo(f"  + {line}")

    if check and report.has_breaking:
        n = len(report.by_severity(DriftSeverity.breaking))
        typer.secho(f"{n} breaking drift(s) — failing CI", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)


def _apply_naming_fixes(repo: ModelRepo, fixes) -> None:
    """Mutate raw YAML nodes in place so comments survive (spec §2.2 mergeable)."""
    for ulid, new_name in fixes.entities.items():
        node = repo.raw_for_ulid(ulid)
        if node is not None:
            node["name"] = new_name
    for (entity_ulid, attr_ulid), new_name in fixes.attributes.items():
        node = repo.raw_for_ulid(entity_ulid)
        if node is None:
            continue
        for attr in node.get("attributes", []):
            if attr.get("id") == attr_ulid:
                attr["name"] = new_name


if __name__ == "__main__":
    app()
