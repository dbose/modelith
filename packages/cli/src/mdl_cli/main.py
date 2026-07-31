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
    path: Path = typer.Argument(Path("."), help="Directory to scaffold in"),
    name: str = typer.Option("modelith_model", help="Project name"),
    workspace: bool = typer.Option(
        False,
        "--workspace",
        help="Scaffold the full collaboration topology: model/ + transform/warehouse "
        "siblings, CODEOWNERS, .code-workspace, merge driver (collab model §2.1)",
    ),
    git_hooks: bool = typer.Option(
        False, "--git-hooks", help="Only wire the semantic merge driver (§6.1)"
    ),
) -> None:
    """Scaffold a model repo, or a full collaboration workspace."""
    from mdl_cli.collab import ensure_git_hooks, scaffold_workspace

    if git_hooks:
        for line in ensure_git_hooks(path):
            typer.secho(f"  {line}", fg=typer.colors.GREEN)
        return
    if workspace:
        for line in scaffold_workspace(path, name, scaffold):
            typer.secho(f"  {line}", fg=typer.colors.GREEN)
        return
    files = scaffold(path, project_name=name)
    typer.secho(f"Scaffolded {len(files)} files under {path}", fg=typer.colors.GREEN)


@app.command()
def validate(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    severity: str = typer.Option("error", help="Gate severity: error|warn"),
    fmt: str = typer.Option("text", "--format", help="text|json (json maps ULIDs to files)"),
) -> None:
    """Validate the model (schema, refs, ontology, naming)."""
    repo = _load(model_dir)
    diags = run_validate(repo.model)

    if fmt == "json":
        import json as _json

        # ULID -> owning file, including attribute ULIDs (they live in their
        # entity's file) so editors can map diagnostics to locations.
        ulid_file: dict[str, str] = dict(
            (u, rel) for rel, u in repo.file_ulid.items()
        )
        for le in repo.model.logical_entities.values():
            owner = repo.path_for_ulid(le.id)
            if owner:
                for a in le.attributes:
                    ulid_file.setdefault(a.id, owner)
        typer.echo(
            _json.dumps(
                {
                    "diagnostics": [
                        {
                            "code": d.code,
                            "severity": d.severity.value,
                            "message": d.message,
                            "path": d.path,
                            "file": ulid_file.get(d.path or "", None),
                        }
                        for d in diags.items
                    ],
                    "has_errors": diags.has(Severity.error),
                }
            )
        )
    else:
        for d in diags.items:
            color = {
                Severity.error: typer.colors.RED,
                Severity.warning: typer.colors.YELLOW,
                Severity.info: typer.colors.BLUE,
            }[d.severity]
            typer.secho(f"{d.code} [{d.severity.value}] {d.message}", fg=color)

    gate = Severity.error if severity == "error" else Severity.warning
    if diags.has(gate):
        if fmt != "json":
            typer.secho("validation failed", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if fmt != "json":
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


@app.command("merge-driver")
def merge_driver(
    base: Path = typer.Argument(...),
    ours: Path = typer.Argument(...),
    theirs: Path = typer.Argument(...),
    state: bool = typer.Option(False, "--state", help="Generation-state mode: take ours"),
) -> None:
    """Git merge driver: structural, ULID-keyed merge of model YAML (§6.1).
    Two people adding different attributes merge clean; editing the same field
    conflicts. Wire it with `mdl init --git-hooks`."""
    from mdl_core.merge_driver import run_merge_driver

    raise typer.Exit(run_merge_driver(base, ours, theirs, state=state))


@app.command()
def classify(
    base: str = typer.Option("origin/main", "--base", help="Base ref for the diff"),
    files: list[str] = typer.Option(None, "--files", help="Explicit paths (skip git diff)"),
    model_root: str = typer.Option("model", "--model-root"),
    transform_root: str = typer.Option("transform", "--transform-root"),
    fmt: str = typer.Option("text", "--format", help="text|json"),
) -> None:
    """Classify a change set into collaboration routes A-E (§4) and print the
    required gates + reviewers. Runs first in CI."""
    import json as _json

    from mdl_cli.collab import changed_paths, classify_paths

    paths = list(files) if files else changed_paths(base)
    c = classify_paths(paths, model_root=model_root, transform_root=transform_root)
    if fmt == "json":
        typer.echo(_json.dumps(c.to_dict(), indent=2))
        return
    if not c.routes:
        typer.secho("no route-relevant changes", fg=typer.colors.GREEN)
        return
    from mdl_cli.collab import _ROUTE_META

    names = {r: f"{r} ({_ROUTE_META[r]['name']})" for r in c.routes}
    route_list = ", ".join(names[r] for r in c.routes)
    typer.secho(f"routes: {route_list}  ->  primary {c.primary}", fg=typer.colors.CYAN)
    typer.echo("gates:     " + "; ".join(c.gates))
    typer.echo("reviewers: " + ", ".join(f"@{r}" for r in c.reviewers))
    for u in c.unmatched:
        typer.secho(f"  unmatched: {u}", fg=typer.colors.YELLOW)


@app.command()
def unmanage(
    entity: str = typer.Argument(..., help="Logical entity name"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    reason: str = typer.Option(..., "--reason", help="Why (e.g. 'hotfix INC-4821')"),
    expires: str = typer.Option("14d", "--expires", help="Debt expiry, e.g. 14d"),
) -> None:
    """The debt valve (§7): hand an entity's SQL to engineers NOW, on the record.
    Writes .mdl/debt.yaml; after expiry, drift --check escalates to breaking."""
    from mdl_cli.collab import add_debt
    from mdl_core.commands import CommandError, apply_command

    repo = _load(model_dir)
    le = next((e for e in repo.model.logical_entities.values() if e.name == entity), None)
    if le is None:
        typer.secho(f"no logical entity {entity!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    days = int(expires.rstrip("d") or "14")
    try:
        apply_command(model_dir, "set_unmanaged", {"id": le.id, "unmanaged": True})
    except CommandError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    entry = add_debt(model_dir, entity, reason, days)
    typer.secho(
        f"{entity!r} unmanaged; debt recorded (expires {entry['expires']}) — "
        f"file a tracking issue for the owning architect",
        fg=typer.colors.YELLOW,
    )


debt_app = typer.Typer(help="The committed debt ledger (§7).")
app.add_typer(debt_app, name="debt")


@debt_app.command("list")
def debt_list(model_dir: Path = typer.Option(Path("."), "--model-dir", "-m")) -> None:
    import datetime as _dt

    from mdl_cli.collab import load_debt

    entries = load_debt(model_dir)
    if not entries:
        typer.secho("no modeling debt", fg=typer.colors.GREEN)
        return
    today = _dt.date.today().isoformat()
    for d in entries:
        expired = str(d.get("expires", "")) < today
        mark = "EXPIRED" if expired else "open"
        color = typer.colors.RED if expired else typer.colors.YELLOW
        typer.secho(
            f"  [{mark}] {d['entity']}: {d['reason']} "
            f"(since {d['created']}, expires {d['expires']})",
            fg=color,
        )


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

    # §7: expired modeling debt escalates from warn to error in --check
    from mdl_cli.collab import expired_debt
    from mdl_reverse.drift import DriftItem, DriftKind
    from mdl_reverse.drift import DriftSeverity as _DS

    for d in expired_debt(model_dir):
        report.add(
            DriftItem(
                severity=_DS.breaking,
                kind=DriftKind.unmanaged_model,
                model=str(d.get("entity")),
                detail=(
                    f"modeling debt EXPIRED on {d.get('expires')}: {d.get('entity')} "
                    f"({d.get('reason')}) — re-manage or renew via the modeling review"
                ),
            )
        )

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


@ontology_app.command("promote")
def ontology_promote(
    name: str = typer.Argument(..., help="Conceptual entity or term name"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Promote a proposed ontology alignment to accepted (§5.1: SMEs propose,
    architects promote)."""
    from mdl_core.commands import CommandError, apply_command

    repo = _load(model_dir)
    candidates = [*repo.model.conceptual_entities.values(), *repo.model.terms.values()]
    objs = {o.name: o for o in candidates}
    obj = objs.get(name)
    if obj is None:
        typer.secho(f"no conceptual entity or term {name!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        apply_command(model_dir, "promote_alignment", {"id": obj.id})
    except CommandError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"alignment on {name!r} promoted to accepted", fg=typer.colors.GREEN)


@ontology_app.command("vendor")
def ontology_vendor(
    source: str = typer.Argument("fibo", help="Vocabulary to vendor (fibo)"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    url: str = typer.Option(
        "https://spec.edmcouncil.org/fibo/ontology/master/latest/prod.fibo-quickstart.ttl",
        "--url",
        help="Override the download URL",
    ),
) -> None:
    """Download and pin a full industry vocabulary (spec §3.2).

    FIBO ships as the QuickFIBO single-file production release (MIT licence).
    The download is recorded in .mdl/lock.yaml; declare it in mdl-project.yaml's
    ontology_stack with `path: ontologies/industry/<name>`."""
    import urllib.request

    from mdl_ontology.lock import Lock

    dest = model_dir / "ontologies" / "industry" / source
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{source}.ttl"
    typer.secho(f"downloading {url} …", fg=typer.colors.CYAN)
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        out.write_bytes(resp.read())
    lock = Lock.load(model_dir)
    lock.vocabularies[source] = url
    lock.save(model_dir)
    typer.secho(
        f"vendored {source} -> {out} ({out.stat().st_size // 1024} KB); pinned in .mdl/lock.yaml",
        fg=typer.colors.GREEN,
    )


new_app = typer.Typer(help="Scaffold model objects (mints ULIDs for you).")
app.add_typer(new_app, name="new")


@new_app.command("entity")
def new_entity(
    name: str = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    subject_area: str = typer.Option(None, "--subject-area", help="Subject area ULID"),
    definition: str = typer.Option(None, "--definition"),
    layer: str = typer.Option(None, "--layer", help="industry|core|domain|specialised"),
) -> None:
    """Create a conceptual + logical entity pair."""
    from mdl_server.commands import CommandError, apply_command

    try:
        result = apply_command(
            model_dir,
            "create_entity",
            {"name": name, "subject_area": subject_area, "definition": definition, "layer": layer},
        )
    except CommandError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"created entity {name!r} ({result.created_id})", fg=typer.colors.GREEN)


@new_app.command("subject-area")
def new_subject_area(
    name: str = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    definition: str = typer.Option(None, "--definition"),
) -> None:
    from mdl_core.commands import apply_command

    result = apply_command(
        model_dir, "create_subject_area", {"name": name, "definition": definition}
    )
    typer.secho(f"created subject area {name!r} ({result.created_id})", fg=typer.colors.GREEN)


@new_app.command("term")
def new_term(
    name: str = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    definition: str = typer.Option(None, "--definition"),
    layer: str = typer.Option(
        "domain", "--layer", help="industry|core|domain|specialised"
    ),
    aligns_to: str = typer.Option(None, "--aligns-to", help="Ontology IRI (e.g. fibo-...:X)"),
    alignment: str = typer.Option("skos:closeMatch", "--alignment"),
) -> None:
    """Create a glossary term (the SME's primary object, route A)."""
    from mdl_core.commands import CommandError, apply_command

    try:
        result = apply_command(
            model_dir,
            "create_term",
            {
                "name": name,
                "definition": definition,
                "layer": layer,
                "aligns_to": aligns_to,
                "alignment": alignment,
            },
        )
    except CommandError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"created term {name!r} ({result.created_id})", fg=typer.colors.GREEN)


decisions_app = typer.Typer(help="Review the reverse-engineering decision ledger (§6.2).")
app.add_typer(decisions_app, name="decisions")


@decisions_app.command("list")
def decisions_list(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    pending: bool = typer.Option(False, "--pending", help="Only unreviewed proposals"),
) -> None:
    ledger = DecisionLedger.load(model_dir)
    items = ledger.pending() if pending else list(ledger.decisions.values())
    for d in items:
        mark = {"accepted": "✓", "rejected": "✗", "proposed": "?"}[d.verdict.value]
        typer.echo(f"  {mark} [{d.confidence.value:11}] {d.signal_key}  {d.subject}")
    if not items:
        typer.secho("no decisions", fg=typer.colors.GREEN)


@decisions_app.command("accept")
def decisions_accept(
    signal_key: str = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    _set_decision(model_dir, signal_key, Verdict.accepted)


@decisions_app.command("reject")
def decisions_reject(
    signal_key: str = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    _set_decision(model_dir, signal_key, Verdict.rejected)


def _set_decision(model_dir: Path, signal_key: str, verdict: Verdict) -> None:
    ledger = DecisionLedger.load(model_dir)
    if signal_key not in ledger.decisions:
        typer.secho(f"no decision {signal_key!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    ledger.set_verdict(signal_key, verdict)
    ledger.save(model_dir)
    typer.secho(f"{verdict.value}: {ledger.decisions[signal_key].subject}", fg=typer.colors.GREEN)


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


@export_app.command("json-schema")
def export_json_schema(
    out: Path = typer.Option(Path("schemas"), "--out", "-o", help="Output directory"),
) -> None:
    """Export JSON Schemas for model YAML files (one per object kind), generated
    from the pydantic IR. Editors map them via yaml.schemas for completion and
    inline validation."""
    import json as _json

    from mdl_core.ir import (
        ConceptualEntity,
        Domain,
        LogicalEntity,
        PhysicalTable,
        ProjectConfig,
        Relationship,
        SubjectArea,
        Term,
    )

    kinds = {
        "conceptual_entity": ConceptualEntity,
        "subject_area": SubjectArea,
        "term": Term,
        "logical_entity": LogicalEntity,
        "domain": Domain,
        "relationship": Relationship,
        "physical_table": PhysicalTable,
        "project": ProjectConfig,
    }
    out.mkdir(parents=True, exist_ok=True)
    for name, cls in kinds.items():
        schema = cls.model_json_schema()
        schema["$schema"] = "http://json-schema.org/draft-07/schema#"
        (out / f"{name}.schema.json").write_text(_json.dumps(schema, indent=2))
    typer.secho(f"wrote {len(kinds)} schemas to {out}", fg=typer.colors.GREEN)


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


@app.command()
def lsp() -> None:
    """Start the Modelith language server (stdio). One server for VS Code,
    Cursor, Windsurf, JetBrains, and CI — same engine as the CLI."""
    from mdl_lsp.server import main as lsp_main

    lsp_main()


@app.command()
def serve(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(4800, "--port", "-p"),
    read_only: bool = typer.Option(
        False, "--read-only", help="Disable editing (viewer only, for shared deployments)"
    ),
) -> None:
    """Serve the web canvas + API. Editing writes the working tree; git owns state."""
    from mdl_server.app import serve as run_server

    mode = "read-only" if read_only else "editable"
    typer.secho(
        f"Modelith canvas ({mode}): http://{host}:{port}  (model: {model_dir})",
        fg=typer.colors.CYAN,
    )
    run_server(model_dir, host=host, port=port, read_only=read_only)


@app.command()
def glossary(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(4810, "--port", "-p"),
    read_only: bool = typer.Option(
        False, "--read-only", help="Browse only — the onboarding week-3 gate"
    ),
) -> None:
    """Serve the SME glossary app (git-native; edits become a PR). The SME never
    sees git or a CLI (collaboration model §5.1)."""
    from mdl_server.app import serve as run_server

    mode = "read-only" if read_only else "propose-as-PR"
    typer.secho(
        f"Modelith glossary ({mode}): http://{host}:{port}/sme  (model: {model_dir})",
        fg=typer.colors.CYAN,
    )
    run_server(model_dir, host=host, port=port, read_only=read_only)


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


def _source_of_truth(model) -> str:
    g = getattr(model.config, "glossary", None)
    return getattr(g, "source_of_truth", "git") if g else "git"


@gov_app.command("publish")
def gov_publish(
    profile: Path = typer.Option(..., "--profile"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    base_url: str = typer.Option(None, "--base-url"),
    token: str = typer.Option(None, "--token"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; do not apply"),
    force: bool = typer.Option(
        False, "--force", help="Publish even when glossary.source_of_truth != git"
    ),
) -> None:
    """Mirror the git-mastered model out to the catalog (plan + apply in one step).

    This is the git->catalog direction: run it on merge to `main` so Collibra
    becomes a published, read-only mirror. Refuses to run when the catalog is the
    source of truth (that would push into the master), unless --force."""
    repo = _load(model_dir)
    sot = _source_of_truth(repo.model)
    if sot != "git" and not force:
        typer.secho(
            f"refusing to publish: glossary.source_of_truth is {sot!r}, so the catalog "
            f"is the master — publishing would overwrite it. Use `mdl gov import` to pull "
            f"catalog edits in, or --force to override.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(4)

    prof = Profile.load(profile)
    graph = build_graph(repo.model)
    adapter = _gov_adapter(True, base_url, token)
    try:
        plan = adapter.plan(graph, prof)
    except Exception as e:  # noqa: BLE001
        typer.secho(f"plan failed: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(4) from e

    typer.secho(
        f"plan: {len(plan.creates())} create, {len(plan.updates())} update", fg=typer.colors.CYAN
    )
    if dry_run:
        typer.secho("dry-run: not applied", fg=typer.colors.YELLOW)
        return
    result = adapter.apply(plan)
    typer.secho(
        f"published {result.applied} to catalog "
        f"({result.created} created, {result.updated} updated)",
        fg=typer.colors.GREEN,
    )


@gov_app.command("import")
def gov_import(
    profile: Path = typer.Option(..., "--profile"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    base_url: str = typer.Option(None, "--base-url"),
    token: str = typer.Option(None, "--token"),
    branch: str = typer.Option("catalog/import", "--branch", help="bot branch for the import PR"),
    commit: bool = typer.Option(
        False, "--commit", help="Commit the writeback to a branch (default: working-tree only)"
    ),
) -> None:
    """Pull catalog-mastered glossary fields back into git as a reviewable change.

    This is the catalog->git direction for when Collibra is the source of truth:
    it never silently overwrites — every imported value is applied through the
    mutation engine (comment-preserving) and, with --commit, lands on a bot branch
    so a steward reviews the diff before it reaches `main`."""
    repo = _load(model_dir)
    sot = _source_of_truth(repo.model)
    prof = Profile.load(profile)
    adapter = _gov_adapter(True, base_url, token)
    wb = adapter.pull(prof)
    if not wb.values:
        typer.secho("nothing to import (no writeback values from catalog)", fg=typer.colors.YELLOW)
        return

    from mdl_core.commands import CommandError, apply_command
    from mdl_core.governance_import import writeback_to_commands

    cmds = writeback_to_commands(repo.model, wb)
    if not cmds:
        typer.secho(
            "catalog returned values but none mapped to importable glossary fields "
            "(definition/synonyms/stewardship). Nothing to do.",
            fg=typer.colors.YELLOW,
        )
        return

    applied = 0
    for op, payload in cmds:
        try:
            apply_command(model_dir, op, payload)
            applied += 1
            typer.echo(f"  {op} {payload.get('id', '')}")
        except CommandError as e:
            typer.secho(f"skip {op}: {e}", fg=typer.colors.YELLOW)

    note = "" if sot == "collibra" else " (note: source_of_truth is git; this is a one-off import)"
    typer.secho(f"imported {applied} field(s) into the working tree{note}", fg=typer.colors.GREEN)

    if commit:
        import subprocess

        subprocess.run(["git", "-C", str(model_dir), "checkout", "-B", branch], check=False)
        subprocess.run(["git", "-C", str(model_dir), "add", "--", "."], check=False)
        msg = (
            f"Import glossary edits from {prof.profile}\n\n"
            f"{applied} field(s) pulled from catalog."
        )
        subprocess.run(["git", "-C", str(model_dir), "commit", "-m", msg, "--", "."], check=False)
        typer.secho(
            f"committed to branch {branch} — open a PR for steward review",
            fg=typer.colors.GREEN,
        )


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
