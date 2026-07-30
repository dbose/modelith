"""`mdl` CLI (spec §10).

Exit codes (spec §10): 0 ok, 1 validation error, 2 drift breaking,
3 merge conflict, 4 adapter/plan failure.
"""

from __future__ import annotations

from pathlib import Path

import typer
from mdl_adapter_collibra import CollibraAdapter, CollibraTransport, MockTransport
from mdl_emit_semantic import emit_metricflow, emit_osi, import_osi, validate_joinability
from mdl_governance import Profile, build_graph, emit_openlineage, run_conformance
from mdl_ontology import (
    build_registry,
    check_layers,
    coverage_report,
    export_rdf,
    export_shacl,
    serialize,
)

from mdl_cli.scaffold import scaffold
from mdl_core.diagnostics import Severity
from mdl_core.merge import MergeOutcome
from mdl_core.naming import lint as naming_lint
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate as run_validate
from mdl_emit_dbt.emitter import DbtEmitter
from mdl_reverse.drift import DriftSeverity, compute_drift
from mdl_reverse.erwin import import_erwin
from mdl_reverse.ledger import DecisionLedger, Verdict
from mdl_reverse.manifest import read_manifest
from mdl_reverse.reconcile import reconcile
from mdl_reverse.render import render_json, render_markdown, render_text
from mdl_reverse.reverse import reverse as run_reverse
from mdl_reverse.schema_reader import read_schema_yml
from mdl_reverse.writer import write_model as write_reversed

app = typer.Typer(help="Modelith: ontology-anchored, git-native data modeling for dbt.")
ontology_app = typer.Typer(help="Ontology stack: search, layer/alignment checks, coverage.")
emit_app = typer.Typer(help="Emit semantic-layer artifacts.")
export_app = typer.Typer(help="Export the model to interchange formats.")
import_app = typer.Typer(help="Import external models into the IR.")
gov_app = typer.Typer(help="Governance: plan/apply/pull/conformance against a catalog.")
app.add_typer(ontology_app, name="ontology")
app.add_typer(emit_app, name="emit")
app.add_typer(export_app, name="export")
app.add_typer(import_app, name="import")
app.add_typer(gov_app, name="gov")


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


@ontology_app.command("search")
def ontology_search(
    term: str = typer.Argument(..., help="Search term"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Search loaded industry vocabularies for matching classes (spec §3.2)."""
    repo = _load(model_dir)
    reg = build_registry(model_dir, repo.model.config.ontology_stack)
    loaded = reg.load()
    if not loaded:
        typer.secho(
            "no vocabulary files loaded (declare ontology_stack with a `path`)",
            fg=typer.colors.YELLOW,
        )
    for r in reg.search(term, limit=limit):
        typer.echo(f"  {r.prefixed}  [{r.source}]")
        if r.definition:
            typer.echo(f"      {r.definition[:100]}")


@ontology_app.command("check")
def ontology_check(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    coverage: bool = typer.Option(True, "--coverage/--no-coverage"),
) -> None:
    """Layer/alignment rules + industry-coverage report (spec §3.1)."""
    repo = _load(model_dir)
    reg = build_registry(model_dir, repo.model.config.ontology_stack)
    reg.load()
    diags = check_layers(repo.model, registry=reg)
    for d in diags.items:
        color = typer.colors.RED if d.severity == Severity.error else typer.colors.YELLOW
        typer.secho(f"{d.code} [{d.severity.value}] {d.message}", fg=color)

    if coverage:
        rpt = coverage_report(repo.model)
        typer.echo("")
        typer.secho(
            f"industry alignment coverage: {rpt.coverage_pct}% "
            f"({rpt.core_with_industry}+{rpt.core_exempt}/{rpt.total_core} core terms)",
            fg=typer.colors.CYAN,
        )
        for name in rpt.core_uncovered:
            typer.echo(f"  uncovered: {name}")

    if diags.has(Severity.error):
        raise typer.Exit(1)


@emit_app.command("semantic")
def emit_semantic(
    fmt: str = typer.Option("metricflow", "--format", help="metricflow|osi"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    target: str = typer.Option(None, "--target", "-t"),
    out: Path = typer.Option(None, "--out", "-o", help="Write to a file instead of stdout"),
) -> None:
    """Emit MetricFlow or OSI from the logical model (spec §4, §8)."""
    repo = _load(model_dir)
    tgt = target or repo.model.config.dbt_target or "duckdb_dev"

    # Joinability / fan-out validation before emission (spec §8).
    jdiags = validate_joinability(repo.model)
    for d in jdiags.items:
        color = typer.colors.RED if d.severity == Severity.error else typer.colors.YELLOW
        typer.secho(f"{d.code} [{d.severity.value}] {d.message}", fg=color)
    if jdiags.has(Severity.error):
        typer.secho(
            "fan-out / joinability errors — fix before emitting", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)

    if fmt == "osi":
        text = emit_osi(repo.model, targets=repo.model.config.platform_targets or [tgt])
    else:
        text = emit_metricflow(repo.model, tgt)

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.secho(f"wrote {fmt} to {out}", fg=typer.colors.GREEN)
    else:
        typer.echo(text)


@export_app.command("rdf")
def export_rdf_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    layer: str = typer.Option("conceptual", "--layer", help="conceptual|logical|all"),
    fmt: str = typer.Option("turtle", "--format", help="turtle|xml|jsonld|nt"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Export RDF/OWL with SKOS alignments (spec §3.3)."""
    repo = _load(model_dir)
    reg = build_registry(model_dir, repo.model.config.ontology_stack)
    reg.load()
    g = export_rdf(repo.model, layer=layer, registry=reg)
    _emit_text(serialize(g, fmt), out, "rdf")


@export_app.command("shacl")
def export_shacl_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    fmt: str = typer.Option("turtle", "--format"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Export SHACL shapes generated from the logical model (spec §3.3)."""
    repo = _load(model_dir)
    g = export_shacl(repo.model)
    _emit_text(serialize(g, fmt), out, "shacl")


@import_app.command("osi")
def import_osi_cmd(
    file: Path = typer.Argument(..., help="OSI YAML file"),
    out: Path = typer.Option(Path("model"), "--out", "-o"),
    name: str = typer.Option(None, "--name"),
) -> None:
    """Import an OSI model into the IR (spec §4.3)."""
    model = import_osi(file.read_text(encoding="utf-8"), project_name=name)
    write_reversed(model, out)
    typer.secho(
        f"imported {len(model.logical_entities)} entities from OSI to {out}",
        fg=typer.colors.GREEN,
    )


@import_app.command("erwin")
def import_erwin_cmd(
    file: Path = typer.Argument(..., help="erwin XML export"),
    out: Path = typer.Option(Path("model"), "--out", "-o"),
    name: str = typer.Option(None, "--name"),
) -> None:
    """Import an erwin XML export into the IR (spec §6.4)."""
    model = import_erwin(file.read_text(encoding="utf-8"), project_name=name)
    write_reversed(model, out)
    typer.secho(
        f"imported {len(model.logical_entities)} entities, "
        f"{len(model.relationships)} relationships from erwin to {out}",
        fg=typer.colors.GREEN,
    )


def _gov_adapter(sandbox: bool, base_url: str | None, token: str | None):
    """A Collibra adapter. Uses MockTransport for dry/sandbox plans (no live tenant),
    or the real transport when base_url+token are given."""
    if base_url and token:
        return CollibraAdapter(transport=CollibraTransport(base_url, token))
    return CollibraAdapter(transport=MockTransport())


@gov_app.command("conformance")
def gov_conformance(
    profile: Path = typer.Option(..., "--profile", help="governance-profile.yaml"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    strict: bool = typer.Option(False, "--strict", help="Fail if any present kind is unmapped"),
) -> None:
    """Validate a bespoke mapping against a fixture model (spec §9.5)."""
    repo = _load(model_dir)
    prof = Profile.load(profile)
    result = run_conformance(repo.model, prof, strict=strict)
    for w in result.warnings:
        typer.secho(f"warn: {w}", fg=typer.colors.YELLOW)
    for e in result.errors:
        typer.secho(f"error: {e}", fg=typer.colors.RED)
    if result.passed:
        typer.secho(
            f"conformance passed ({result.mapped_assets} assets mapped)", fg=typer.colors.GREEN
        )
    else:
        typer.secho("conformance failed", fg=typer.colors.RED, err=True)
        raise typer.Exit(4)


@gov_app.command("plan")
def gov_plan(
    profile: Path = typer.Option(..., "--profile"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(Path("gov-plan.json"), "--out", "-o"),
    base_url: str = typer.Option(None, "--base-url"),
    token: str = typer.Option(None, "--token"),
) -> None:
    """Produce a human-reviewable sync plan. Never writes to the catalog (§9.3)."""
    import json
    from dataclasses import asdict

    repo = _load(model_dir)
    prof = Profile.load(profile)
    graph = build_graph(repo.model)
    adapter = _gov_adapter(True, base_url, token)
    try:
        plan = adapter.plan(graph, prof)
    except Exception as e:  # noqa: BLE001
        typer.secho(f"plan failed: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(4) from e

    out.write_text(json.dumps(asdict(plan), indent=2, default=str), encoding="utf-8")
    typer.secho(
        f"plan: {len(plan.creates())} create, {len(plan.updates())} update -> {out}",
        fg=typer.colors.GREEN,
    )


@gov_app.command("apply")
def gov_apply(
    plan_file: Path = typer.Argument(..., help="plan.json from `mdl gov plan`"),
    base_url: str = typer.Option(None, "--base-url"),
    token: str = typer.Option(None, "--token"),
) -> None:
    """Execute an approved plan (§9.3). Refuses a plan it did not produce."""
    import json

    from mdl_governance import ForeignPlanError
    from mdl_governance.spi import ChangeType, PlannedChange, SyncPlan

    data = json.loads(plan_file.read_text(encoding="utf-8"))
    plan = SyncPlan(
        adapter=data["adapter"],
        profile_name=data["profile_name"],
        changes=[
            PlannedChange(
                external_id=c["external_id"],
                target_type=c["target_type"],
                change=ChangeType(c["change"]),
                name=c["name"],
                attributes=c.get("attributes", {}),
                relations=[tuple(r) for r in c.get("relations", [])],
            )
            for c in data["changes"]
        ],
        signature=data.get("signature", ""),
    )
    adapter = _gov_adapter(True, base_url, token)
    try:
        result = adapter.apply(plan)
    except ForeignPlanError as e:
        typer.secho(f"refused: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(4) from e
    typer.secho(
        f"applied {result.applied} ({result.created} created, {result.updated} updated)",
        fg=typer.colors.GREEN,
    )


@gov_app.command("pull")
def gov_pull(
    profile: Path = typer.Option(..., "--profile"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    base_url: str = typer.Option(None, "--base-url"),
    token: str = typer.Option(None, "--token"),
) -> None:
    """Pull governance-owned fields back into the model (§9.4 writeback)."""
    prof = Profile.load(profile)
    adapter = _gov_adapter(True, base_url, token)
    wb = adapter.pull(prof)
    typer.secho(f"pulled {len(wb.values)} writeback value(s)", fg=typer.colors.GREEN)
    for v in wb.values:
        typer.echo(f"  {v.external_id} {v.model_path} = {v.value}")


@gov_app.command("lineage")
def gov_lineage(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Emit an OpenLineage payload (spec §9.6)."""
    repo = _load(model_dir)
    _emit_text(emit_openlineage(repo.model), out, "openlineage")


def _emit_text(text: str, out: Path | None, label: str) -> None:
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.secho(f"wrote {label} to {out}", fg=typer.colors.GREEN)
    else:
        typer.echo(text)


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
