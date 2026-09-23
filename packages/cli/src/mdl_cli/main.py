"""`mdl` CLI (spec §10).

Exit codes (spec §10): 0 ok, 1 validation error, 2 drift breaking,
3 merge conflict, 4 adapter/plan failure.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC
from pathlib import Path

import typer
from mdl_adapter_collibra import CollibraAdapter, CollibraTransport, MockTransport
from mdl_emit_contract import emit_datacontract
from mdl_emit_graph import emit_cypher
from mdl_emit_pydantic import emit_pydantic_models
from mdl_emit_semantic import emit_metricflow, emit_osi, import_osi, validate_joinability
from mdl_governance import Profile, build_graph, emit_openlineage, run_conformance

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
from mdl_reverse.reverse import apply_accepted_relationships
from mdl_reverse.reverse import reverse as run_reverse
from mdl_reverse.schema_reader import read_schema_yml
from mdl_reverse.writer import write_model as write_reversed

app = typer.Typer(help="Modelith: ontology-anchored, git-native data modeling for dbt.")
ontology_app = typer.Typer(help="Ontology stack: search, layer/alignment checks, coverage.")
emit_app = typer.Typer(help="Emit semantic-layer artifacts.")
export_app = typer.Typer(help="Export the model to interchange formats.")
import_app = typer.Typer(help="Import external models into the IR.")
gov_app = typer.Typer(help="Governance: plan/apply/pull/conformance against a catalog.")
catalog_app = typer.Typer(
    help="Cross-repo model catalog: publish/browse model repos (base-tier, no governance)."
)
app.add_typer(ontology_app, name="ontology")
app.add_typer(emit_app, name="emit")
app.add_typer(export_app, name="export")
app.add_typer(import_app, name="import")
app.add_typer(gov_app, name="gov")
app.add_typer(catalog_app, name="catalog")


def _mdl_version() -> str:
    """The installed modelith-dbt version, from package metadata (works for a wheel and an
    editable install). Falls back to 'unknown' rather than raising if metadata is absent."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("modelith-dbt")
    except PackageNotFoundError:  # pragma: no cover - only if run from a non-installed tree
        return "unknown"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mdl {_mdl_version()}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the installed mdl version and exit.",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    """Modelith: ontology-anchored, git-native data modeling for dbt."""


def _load(model_dir: Path) -> ModelRepo:
    try:
        return ModelRepo.load(model_dir)
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e


def _ontology():
    """Import the ontology stack, or exit with an install hint.

    The RDF stack (rdflib today, pyoxigraph after the backend swap) ships only with
    the ``ontology`` extra, so the core install stays free of it — some enterprise
    package mirrors quarantine the ``rdflib`` coordinate, which would otherwise block
    every ``mdl`` command. Ontology/export-RDF commands call this first; everything
    else (validate, reverse, generate, emit dbt/pydantic/contract/graph) never imports
    it. Mirrors the audit/OpenTelemetry optional-dependency pattern
    (``mdl_server.audit._try_build_otel_logger``).
    """
    try:
        import mdl_ontology

        return mdl_ontology
    except ImportError as e:
        typer.secho(
            "This command needs the ontology stack (RDF/OWL/SHACL/R2RML). "
            "Install it with:  uv tool install 'modelith-dbt[ontology]'  "
            "(or: pip install 'modelith-dbt[ontology]').",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(4) from e


def _project_name_from_path(path: Path) -> str:
    """Derive a valid project name from the target directory.

    ``mdl init my-model`` -> ``my_model``; ``mdl init .`` -> the resolved current
    directory name. Non-identifier characters collapse to underscores so the name
    passes naming validation; an empty or unusable result falls back to a default.
    """
    import re

    raw = path.resolve().name or "modelith_model"
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", raw).strip("_").lower()
    return slug or "modelith_model"


@app.command()
def init(
    path: Path = typer.Argument(
        None,
        help="Directory to scaffold in. Defaults to the current directory, except "
        "`--demo`, which defaults to ~/modelith-demo so it never writes into your repo.",
    ),
    name: str = typer.Option(
        None,
        help="Project name. Defaults to the target directory name.",
    ),
    workspace: bool = typer.Option(
        False,
        "--workspace",
        help="Scaffold the full collaboration topology: model/ + transform/warehouse "
        "siblings, CODEOWNERS, .code-workspace, merge driver (collab model §2.1)",
    ),
    git_hooks: bool = typer.Option(
        False, "--git-hooks", help="Only wire the semantic merge driver (§6.1)"
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Scaffold a populated 7-entity example model + a DuckDB dbt project, "
        "so the first `mdl serve` shows a real ERD (spec §4.2). Runs fully offline.",
    ),
) -> None:
    """Scaffold a model repo, or a full collaboration workspace.

    The project name defaults to the target directory (``mdl init my-model`` names
    the project ``my_model``); pass ``--name`` to override. ``--demo`` instead lays
    down a ready-made example you can `serve`, `validate`, `generate`, and `dbt build`
    with no configuration; with no path it writes to ``~/modelith-demo`` so it never
    touches the repo you are standing in.
    """
    from mdl_cli.collab import ensure_git_hooks, scaffold_workspace

    if demo:
        from mdl_cli.demo import scaffold_demo

        # The demo must never land in the engineer's own repo. With no explicit path
        # it goes to ~/modelith-demo (portable across mac/Linux/Windows via Path.home);
        # an explicit `mdl init --demo <path>` still wins.
        target = path if path is not None else Path.home() / "modelith-demo"
        written = scaffold_demo(target)
        typer.secho(
            f"Scaffolded a 7-entity demo model ({len(written)} files) under {target}",
            fg=typer.colors.GREEN,
        )
        typer.secho(
            f"  Next: mdl serve -m {target}/model   (populated ERD)\n"
            f"        mdl validate -m {target}/model\n"
            f"        mdl generate -m {target}/model -o {target}/transform/warehouse\n"
            f"        cd {target}/transform/warehouse && dbt build   (needs dbt-duckdb)",
            fg=typer.colors.CYAN,
        )
        return

    # Plain init (no --demo): default to the current directory, as before.
    if path is None:
        path = Path(".")

    if name is None:
        name = _project_name_from_path(path)

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
    emit_contract: bool = typer.Option(
        False, "--emit-contract", help="Also write an ODCS datacontract.yaml at the model root"
    ),
    emit_pydantic: bool = typer.Option(
        False, "--emit-pydantic", help="Also write Pydantic models.py at the model root"
    ),
    emit_graph: bool = typer.Option(
        False, "--emit-graph", help="Also write a Neo4j schema.cypher at the model root"
    ),
    emit_r2rml: bool = typer.Option(
        False, "--emit-r2rml", help="Also write a W3C R2RML mapping.r2rml.ttl at the model root"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Generate the dbt project with protected regions and three-way merge.

    The optional --emit-* flags make Modelith a multi-target compiler: the same
    model also produces an ODCS data contract, Pydantic models, a Neo4j Cypher
    schema, and/or a W3C R2RML knowledge-graph mapping at the model root (respecting
    --dry-run).
    """
    repo = _load(model_dir)
    tgt = target or repo.model.config.dbt_target or "duckdb_dev"
    emitter = DbtEmitter(repo.model, tgt, inline=inline)
    result = emitter.generate(out, write=not dry_run)

    for flag, fn, fname, label in (
        (emit_contract, emit_datacontract, "datacontract.yaml", "contract"),
        (emit_pydantic, emit_pydantic_models, "models.py", "pydantic"),
        (emit_graph, emit_cypher, "schema.cypher", "graph"),
    ):
        if not flag:
            continue
        text = fn(repo.model)
        dest = model_dir / fname
        if not dry_run:
            dest.write_text(text, encoding="utf-8")
        verb = "would write" if dry_run else "wrote"
        typer.secho(f"  {verb} {label} to {dest}", fg=typer.colors.GREEN)

    if emit_r2rml:
        mo = _ontology()
        reg = mo.build_registry(model_dir, repo.model.config.ontology_stack)
        reg.load()
        # generate is a bulk "produce everything" flow, so it mints fallback IRIs for
        # unmapped objects rather than failing — but warns so the gap is visible. Use
        # `mdl export r2rml` (fail-loud) when coverage must be enforced.
        cov = mo.r2rml_coverage(repo.model, reg)
        if not cov.ok:
            typer.secho(f"  r2rml: {cov.summary()}", fg=typer.colors.YELLOW)
        text = mo.serialize(
            mo.export_r2rml(repo.model, target=tgt, registry=reg, allow_unmapped=True),
            "turtle",
        )
        dest = model_dir / "mapping.r2rml.ttl"
        if not dry_run:
            dest.write_text(text, encoding="utf-8")
        verb = "would write" if dry_run else "wrote"
        typer.secho(f"  {verb} r2rml to {dest}", fg=typer.colors.GREEN)

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


def _read_ddl_source(ddl: Path) -> str:
    """Return the DDL text for --ddl, whether it points at one .sql file or a directory.

    A directory is reversed as a single warehouse: every *.sql under it (recursively,
    sorted for determinism) is concatenated into one script, so CREATE TABLEs split
    across files and foreign keys that cross files resolve — parse_sql_ddl sees them all
    in one pass. A `-- file: <name>` banner precedes each file so parse warnings are
    traceable to their source. An empty directory is an error (nothing to reverse)."""
    if ddl.is_dir():
        files = sorted(ddl.rglob("*.sql"))
        if not files:
            typer.secho(f"no .sql files found under {ddl}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        parts = []
        for f in files:
            parts.append(f"-- file: {f.relative_to(ddl)}\n{f.read_text(encoding='utf-8')}")
        typer.secho(
            f"  reversing {len(files)} DDL file(s) under {ddl} as one warehouse",
            fg=typer.colors.CYAN,
        )
        return "\n\n".join(parts)
    return ddl.read_text(encoding="utf-8")


def _init_profile(adapter: str, dest: Path) -> None:
    """`mdl reverse --init-profile <adapter>`: scaffold a profiles.yml template and print
    the env vars to set + the dbt adapter to install. Never writes a credential."""
    from mdl_reverse.connect import ADAPTER_PACKAGES, ConnectError, scaffold_profile

    try:
        res = scaffold_profile(adapter, dest)
    except ConnectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"wrote {res.path}", fg=typer.colors.GREEN)
    if res.env_vars:
        typer.secho(
            "  fill in the connection fields, then set these env vars:", fg=typer.colors.CYAN
        )
        for v in res.env_vars:
            typer.echo(f"    export {v}=…")
    pkg = ADAPTER_PACKAGES.get(adapter.lower(), f"dbt-{adapter.lower()}")
    typer.secho(
        f"  install the adapter in your dbt env:  pip install {pkg}", fg=typer.colors.CYAN
    )
    typer.secho(
        f"  then:  mdl reverse --connect --schema <schema> --profiles-dir {res.path.parent}",
        fg=typer.colors.CYAN,
    )


def _live_projection(
    *,
    profiles_dir: Path | None,
    schema: str | None,
    database: str | None,
    select: str | None,
    target: str | None,
    with_constraints: bool,
):
    """Introspect a live datastore into a ManifestProjection (spec R2). Runs `dbt debug`
    to validate the connection, then the mdl_get_constraints macro (columns+types+keys)
    through a throwaway dbt project against the user's profiles.yml."""
    from mdl_reverse.connect import ConnectError, dbt_debug, introspect_constraints
    from mdl_reverse.schema_reader import constraints_to_projection

    if not schema:
        typer.secho(
            "--connect needs --schema <schema> to introspect", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)
    pdir = Path(profiles_dir) if profiles_dir else Path.cwd()

    typer.secho("  testing the connection (dbt debug)…", fg=typer.colors.CYAN)
    dbg = dbt_debug(pdir, target=target)
    if not dbg.ok:
        typer.secho("connection test failed:", fg=typer.colors.RED, err=True)
        typer.secho(dbg.message, err=True)
        raise typer.Exit(4)
    typer.secho("  connection ok", fg=typer.colors.GREEN)

    if not with_constraints:
        typer.secho(
            "  --no-constraints: columns/types only, keys will be inferred",
            fg=typer.colors.YELLOW,
        )
    typer.secho(f"  introspecting schema {schema!r}…", fg=typer.colors.CYAN)
    try:
        constraints = introspect_constraints(
            pdir, schema=schema, database=database, select=select, target=target
        )
    except ConnectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(4) from e
    if not with_constraints:
        # keep columns/types + nullability, drop declared keys so they're inferred instead
        for tc in constraints.values():
            if isinstance(tc, dict):
                tc["primary_key"] = []
                tc["foreign_keys"] = []
    if not constraints:
        typer.secho(
            f"no tables found in schema {schema!r} (check --schema/--database and privileges)",
            fg=typer.colors.YELLOW,
        )
    proj = constraints_to_projection(constraints)
    n_fk = sum(
        len(t.get("foreign_keys") or []) for t in constraints.values() if isinstance(t, dict)
    )
    typer.secho(
        f"  introspected {len(proj.models)} table(s), {n_fk} declared foreign key(s)",
        fg=typer.colors.GREEN,
    )
    return proj


def _nonclobbering_out(out: Path) -> Path:
    """If `out` already contains a Modelith model (mdl-project.yaml), return a fresh
    suffixed sibling (model-reversed-v1, -v2, …) so a reverse never overwrites an
    existing model, and print where it diverted. If `out` is empty or absent, use it."""
    if not (out / "mdl-project.yaml").exists():
        return out
    base = out.parent
    stem = f"{out.name}-reversed"
    n = 1
    while (cand := base / f"{stem}-v{n}").exists():
        n += 1
    typer.secho(
        f"  {out} already holds a model; reversing into {cand} instead (not overwriting)",
        fg=typer.colors.YELLOW,
    )
    return cand


@app.command()
def reverse(
    project: Path = typer.Option(
        None, "--project", help="Path to a dbt project (manifest.json or an emitted schema.yml)"
    ),
    ddl: Path = typer.Option(
        None,
        "--ddl",
        help="Path to a raw SQL DDL script (CREATE TABLE …), or a directory of them. "
        "A directory is reversed as one warehouse: every *.sql under it (recursively) is "
        "concatenated so foreign keys across files resolve. Reversed through the same "
        "engine as a dbt project: surrogate stripping, SCD2/staging classification, and "
        "the review ledger all apply. Nullability and keys come from the DDL.",
    ),
    dialect: str = typer.Option(
        None, "--dialect", help="SQL dialect for --ddl (postgres|snowflake|mysql|duckdb|…)"
    ),
    connect: bool = typer.Option(
        False,
        "--connect",
        help="Reverse from a LIVE datastore via dbt. Introspects the warehouse's own "
        "catalog (columns, types, and declared PK/FK/unique) through your dbt adapter and "
        "profiles.yml — no dbt models needed, so a warehouse-only team can reverse a live "
        "schema. Pass --schema (and --database for BigQuery/Snowflake). Runs `dbt debug` "
        "first; declared keys become high-confidence relationships, the rest is inferred.",
    ),
    init_profile: str = typer.Option(
        None,
        "--init-profile",
        help="Scaffold a profiles.yml template for an adapter "
        "(duckdb|postgres|snowflake|bigquery|redshift|databricks) and exit. Secrets are "
        "env_var() placeholders you set yourself; Modelith never stores a credential.",
    ),
    profiles_dir: Path = typer.Option(
        None, "--profiles-dir", help="Directory holding profiles.yml (default: dbt's own search)."
    ),
    schema: str = typer.Option(
        None, "--schema", help="Schema to introspect (with --connect). Required for a live reverse."
    ),
    database: str = typer.Option(
        None,
        "--database",
        help="Database/catalog to introspect (with --connect; BigQuery/Snowflake).",
    ),
    select: str = typer.Option(
        None,
        "--select",
        help="Comma list of tables to reverse (with --connect); scopes a big warehouse.",
    ),
    no_constraints: bool = typer.Option(
        False,
        "--no-constraints",
        help="With --connect, skip constraint introspection (columns/types only, keys "
        "inferred). An escape hatch for a connection without catalog privileges.",
    ),
    out: Path = typer.Option(Path("model"), "--out", "-o", help="Where to write the model"),
    target: str = typer.Option("duckdb_dev", "--target", "-t"),
    name: str = typer.Option("reversed_model", "--name"),
    interactive: bool = typer.Option(
        False, "--interactive", help="Prompt to accept/reject medium-confidence proposals"
    ),
    naming: Path = typer.Option(
        None,
        "--naming",
        help="YAML of reverse naming overrides (rollup/staging/surrogate/scd2 prefixes) "
        "merged with the built-in defaults; also read from out/mdl-project.yaml if present",
    ),
    review: bool = typer.Option(
        True,
        "--review/--no-review",
        help="Print a classification summary (what was excluded / marked rollup / "
        "stripped) so misclassifications on non-standard names are visible",
    ),
    auto_accept: str = typer.Option(
        None,
        "--auto-accept",
        help="Confidence FLOOR for auto-accepting inferences: high | medium-high | "
        "medium | low | none. At or above the floor is accepted; the rest wait for "
        "manual review. 'none' reviews everything regardless of score. Overrides the "
        "`reverse.auto_accept` config; default is medium-high.",
    ),
    review_all: bool = typer.Option(
        False,
        "--review-all",
        help="Review every inference manually — nothing is auto-accepted (alias for "
        "--auto-accept none).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Reverse into --out even if it already holds a model, overwriting it. By "
        "default reverse diverts to a fresh model-reversed-v<N> sibling to protect an "
        "existing model.",
    ),
) -> None:
    """Reverse-engineer a dbt project into a Modelith model (spec §6).

    Tip: run `dbt docs generate` first so a catalog.json sits next to the manifest.
    The manifest only lists columns documented in .yml; the catalog has the real
    warehouse columns, which is what lets reverse spot surrogate keys and SCD2
    tracking columns to strip.

    If your project uses non-default naming (medallion `gold_`, `f_`/`d_`, non-English),
    pass --naming <file.yaml> with the conventions to add. Overrides are additive:

        reverse:
          rollup_prefixes: [gold_, ber_]     # kept: mart_/rpt_/... + these
          staging_prefixes: [bronze_, silver_]

    Auto-accept policy: reverse accepts inferences at or above a confidence FLOOR and
    leaves the rest for manual review in the decision ledger (the VS Code Reverse Review
    panel, or `mdl decisions`). The default floor is medium-high. To review everything
    manually regardless of score, pass --review-all (or --auto-accept none), or set it in
    config so it sticks:

        reverse:
          auto_accept: none        # high | medium-high | medium | low | none
    """
    # --init-profile is a one-shot scaffold that exits before any reverse.
    if init_profile:
        _init_profile(init_profile, profiles_dir or out)
        return

    # Exactly one source: a dbt project (manifest/schema.yml), a SQL DDL script, or a live
    # datastore (--connect).
    sources = [bool(project), bool(ddl), bool(connect)]
    if sum(sources) != 1:
        typer.secho(
            "pass exactly one source: --project (a dbt manifest/schema.yml), --ddl (a SQL "
            "script), or --connect (a live datastore via profiles.yml)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    # Never clobber an existing model unless --force. If --out already holds one
    # (mdl-project.yaml), divert to a fresh, suffixed sibling (model-reversed-v1, -v2, …)
    # and say so — a reverse into a populated model dir would otherwise overwrite
    # hand-authored work. --force is the explicit opt-in to overwrite in place.
    if not force:
        out = _nonclobbering_out(out)

    if connect:
        proj = _live_projection(
            profiles_dir=profiles_dir,
            schema=schema,
            database=database,
            select=select,
            target=target if target != "duckdb_dev" else None,
            with_constraints=not no_constraints,
        )
    elif ddl:
        from mdl_reverse.ddl_projection import ddl_projection

        proj = ddl_projection(_read_ddl_source(ddl), dialect=dialect)
    elif project.name.endswith(".yml") or project.name.endswith(".yaml"):
        # a dbt schema.yml (warehouse-free path)
        proj = read_schema_yml(project)
    else:
        from mdl_reverse.manifest import read_manifest as _rm

        proj = _rm(project)

    for w in proj.warnings:
        typer.secho(f"  {w}", fg=typer.colors.YELLOW)

    reverse_naming = _load_reverse_naming(naming, out)
    floor = _resolve_auto_accept(auto_accept, review_all, naming, out)
    reverse_config = _load_reverse_config(naming, out)
    # Fold reverse.conventions (+ staging-layer prefixes) into the naming the pipeline
    # uses, so a project's declared prefixes/suffixes drive the legacy classifiers too.
    if reverse_config is not None:
        from mdl_reverse.mapping import naming_from_config

        folded = naming_from_config(reverse_config)
        # keep any top-level --naming overrides winning by unioning both
        if reverse_naming is not None:
            from mdl_reverse.lifting import ReverseNaming

            reverse_naming = ReverseNaming.merged(
                {**(reverse_config.conventions or {}),
                 "staging_prefixes": list(folded.staging_prefixes)}
            )
        else:
            reverse_naming = folded

    ledger = DecisionLedger.load(out)
    result = run_reverse(
        proj, project_name=name, target=target, ledger=ledger,
        interactive=interactive, naming=reverse_naming, auto_accept=floor,
        reverse_config=reverse_config,
    )

    if interactive:
        _prompt_proposals(ledger, result, out, target)
        # Materialise relationships the user just accepted (proposed at build time,
        # so they weren't added to the model yet).
        added = apply_accepted_relationships(result.model, ledger)
        if added:
            typer.secho(f"added {added} accepted relationship(s)", fg=typer.colors.GREEN)

    # Refuse to write an entity-less ("hollow") model. This happens when every source
    # model is staging/intermediate (correctly excluded) — e.g. a dbt project whose marts
    # aren't generated yet, so only stg_* exist. Writing an empty mdl-project.yaml + .mdl/
    # there is useless and (in the VS Code flow) later reads as "a model already exists".
    # Fail loud with the remedy instead. --force means "overwrite an existing model", never
    # "write an empty one", so it does not suppress this.
    if result.logical_count() == 0:
        if result.excluded:
            sample = ", ".join(sorted(result.excluded)[:5])
            more = "" if len(result.excluded) <= 5 else ", …"
            typer.secho(
                f"reversed 0 entities — all {len(result.excluded)} models were excluded as "
                f"staging/intermediate ({sample}{more}). This warehouse has no mart/entity "
                "models to reverse. Run `mdl generate`, or point reverse at a manifest that "
                "includes mart models.",
                fg=typer.colors.RED,
                err=True,
            )
        else:
            typer.secho(
                "reversed 0 entities — the source contained no reversible tables. Check "
                "that the manifest/DDL actually defines models.",
                fg=typer.colors.RED,
                err=True,
            )
        raise typer.Exit(1)

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

    if review:
        _render_classification(result)


def _render_classification(result) -> None:
    """Show what reverse classified, grouped by the rule that fired, so an engineer can
    spot a misfire (a `gold_` mart treated as an entity, a real dim demoted to a rollup,
    a natural key stripped) without a prompt-per-item wall."""
    from mdl_reverse import classification_summary

    s = classification_summary(result)

    def _group(title: str, items: list[str], color) -> None:
        if not items:
            return
        typer.secho(f"\n{title} ({len(items)})", fg=color, bold=True)
        shown = items[:12]
        typer.echo("  " + ", ".join(shown) + ("  …" if len(items) > 12 else ""))

    typer.secho("\nClassification review", fg=typer.colors.CYAN, bold=True)
    _group("kept as business entities", s.entities_kept, typer.colors.GREEN)
    _group("managed but no business key (check these)", s.entities_keyless, typer.colors.YELLOW)
    _group("marked reporting rollup (unmanaged)", s.rollups_unmanaged, typer.colors.BLUE)
    _group("excluded as staging/intermediate", s.excluded_staging, typer.colors.BLUE)
    _group("SCD2 pattern detected", s.scd2_detected, typer.colors.BLUE)
    _group("Data Vault detected", s.data_vault, typer.colors.BLUE)
    _group("surrogate keys stripped", s.surrogate_keys_stripped, typer.colors.BLUE)
    typer.secho(
        "\nIf a model was mis-classified (non-standard naming), re-run with "
        "`--naming <file>` to teach reverse your conventions.",
        fg=typer.colors.CYAN,
    )


def _load_reverse_naming(naming_file: Path | None, out: Path):
    """Build the reverse naming config from a --naming file and/or the target project's
    mdl-project.yaml `naming.reverse` block, merged into the built-in defaults. Returns
    None (defaults) when nothing is declared."""
    from mdl_core.yaml_io import load_file
    from mdl_reverse.lifting import ReverseNaming

    overrides: dict = {}

    def _reverse_block(doc: dict) -> dict:
        # accept either a top-level `reverse:` block or `naming.reverse:`
        if not isinstance(doc, dict):
            return {}
        if isinstance(doc.get("reverse"), dict):
            return doc["reverse"]
        nm = doc.get("naming")
        if isinstance(nm, dict) and isinstance(nm.get("reverse"), dict):
            return nm["reverse"]
        return {}

    # existing project config first, explicit --naming file wins on top
    proj_cfg = out / "mdl-project.yaml"
    if proj_cfg.exists():
        try:
            overrides.update(_reverse_block(load_file(proj_cfg)))
        except Exception:  # noqa: BLE001 - a bad config must not block reverse
            pass
    if naming_file is not None:
        if not naming_file.exists():
            typer.secho(f"--naming file not found: {naming_file}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        overrides.update(_reverse_block(load_file(naming_file)) or {})
        if not overrides:
            typer.secho(
                f"--naming {naming_file} has no `reverse:` block; using defaults",
                fg=typer.colors.YELLOW,
            )

    return ReverseNaming.merged(overrides) if overrides else None


def _load_reverse_config(naming_file, out: Path):
    """Build the typed ReverseConfig (layers with roles, exclude, exempt, conventions,
    target_form) from the reverse: block — the target project's mdl-project.yaml, then a
    --naming file on top. Returns a ReverseConfig or None when nothing is configured.
    Used for reverse-time CLASSIFICATION (resolve_layer); drift loads its own copy from
    model.config.reverse."""
    from mdl_core.ir import ReverseConfig
    from mdl_core.yaml_io import load_file

    def _reverse_block(doc) -> dict:
        if not isinstance(doc, dict):
            return {}
        if isinstance(doc.get("reverse"), dict):
            return doc["reverse"]
        nm = doc.get("naming")
        if isinstance(nm, dict) and isinstance(nm.get("reverse"), dict):
            return nm["reverse"]
        return {}

    block: dict = {}
    for src in (out / "mdl-project.yaml", naming_file):
        if src is None or not Path(src).exists():
            continue
        try:
            block.update(_reverse_block(load_file(Path(src))) or {})
        except Exception:  # noqa: BLE001 - a bad config must never block reverse
            pass
    if not block:
        return None
    try:
        return ReverseConfig.model_validate(block)
    except Exception:  # noqa: BLE001 - tolerate a partially-invalid block; drift/classify degrade
        return None


def _resolve_auto_accept(auto_accept: str | None, review_all: bool, naming_file, out: Path):
    """Resolve the auto-accept confidence floor. Precedence: --review-all / --auto-accept
    flag, then a `reverse.auto_accept` in the --naming file, then in the target project's
    mdl-project.yaml, then the default (medium-high). Returns a Confidence floor or None
    (manual-always). A bad level is a hard error so a typo never silently changes policy."""
    from mdl_core.yaml_io import load_file
    from mdl_reverse.ledger import DEFAULT_AUTO_ACCEPT, parse_auto_accept

    def _reverse_block(doc) -> dict:
        if not isinstance(doc, dict):
            return {}
        if isinstance(doc.get("reverse"), dict):
            return doc["reverse"]
        nm = doc.get("naming")
        if isinstance(nm, dict) and isinstance(nm.get("reverse"), dict):
            return nm["reverse"]
        return {}

    raw: object = None
    if review_all:
        raw = "none"
    elif auto_accept is not None:
        raw = auto_accept
    else:
        # config: --naming file wins over the out-dir project config
        for src in (naming_file, out / "mdl-project.yaml"):
            if src is None or not Path(src).exists():
                continue
            try:
                block = _reverse_block(load_file(Path(src)))
            except Exception:  # noqa: BLE001 - bad config must not block reverse
                block = {}
            if "auto_accept" in block:
                raw = block["auto_accept"]
                break
        else:
            return DEFAULT_AUTO_ACCEPT

    try:
        return parse_auto_accept(raw)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e


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
    except (CommandError, FileNotFoundError) as e:
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
    explain: bool = typer.Option(
        False,
        "--explain",
        help="Annotate each item with its reconcile action (safe vs human-gated). "
        "With --format json emits the structured shape the AI surfaces consume; "
        "otherwise a deterministic narrative.",
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

    report = compute_drift(
        repo.model, proj, tgt, reverse=getattr(repo.model.config, "reverse", None)
    )

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

    if explain:
        # The explained shape carries, per item, whether --reconcile would fold it,
        # the concrete action, and the owning YAML file — the single structure the
        # CLI, MCP tool and VS Code drift UI all consume (mdl_reverse.explain).
        from mdl_reverse.explain import explain_report, render_explain_text
        from mdl_reverse.reconcile import model_name_to_ulid

        name_to_le = model_name_to_ulid(repo, tgt)

        def _file_for(model_name: str) -> str | None:
            le_id = name_to_le.get(model_name)
            return repo.path_for_ulid(le_id) if le_id else None

        if fmt == "json":
            typer.echo(json.dumps(explain_report(report, _file_for), indent=2, sort_keys=True))
        else:
            typer.echo(render_explain_text(report))
    elif fmt == "json":
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
    mo = _ontology()
    repo = _load(model_dir)
    reg = mo.build_registry(model_dir, repo.model.config.ontology_stack)
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
    mo = _ontology()
    repo = _load(model_dir)
    reg = mo.build_registry(model_dir, repo.model.config.ontology_stack)
    reg.load()
    diags = mo.check_layers(repo.model, registry=reg)
    for d in diags.items:
        color = typer.colors.RED if d.severity == Severity.error else typer.colors.YELLOW
        typer.secho(f"{d.code} [{d.severity.value}] {d.message}", fg=color)

    if coverage:
        rpt = mo.coverage_report(repo.model)
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
    except (CommandError, FileNotFoundError) as e:
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

    mo = _ontology()
    Lock = mo.Lock

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


@ontology_app.command("add")
def ontology_add(
    file: Path = typer.Argument(..., help="A local .ttl / .jsonld / .owl ontology file"),
    layer: str = typer.Option("core", "--layer", help="industry|core|domain|specialised"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    prefix: str = typer.Option(None, "--prefix", help="Prefix to register (e.g. acme-core)"),
    prefix_iri: str = typer.Option(
        None, "--prefix-iri", help="Namespace IRI for the prefix (inferred if omitted)"
    ),
    name: str = typer.Option(None, "--name", help="Source name (defaults to the file stem)"),
) -> None:
    """Vendor a private ontology file into the repo and wire it into ontology_stack.

    Copies the file to ontologies/<layer>/<name>.<ext> and appends a `local` source
    to mdl-project.yaml, so `mdl serve`/`mdl ontology search` browse it immediately."""
    mo = _ontology()

    if not file.exists():
        typer.secho(f"no such file: {file}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        result = mo.save_ontology_upload(
            model_dir,
            filename=file.name,
            content=file.read_bytes(),
            layer=layer,
            prefix=prefix,
            prefix_iri=prefix_iri,
            name=name,
        )
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(
        f"added {result['name']} ({result['term_count']} terms) as {result['layer']} "
        f"-> {result['path']}; wired into mdl-project.yaml",
        fg=typer.colors.GREEN,
    )


@ontology_app.command("lock")
def ontology_lock(
    layer: str = typer.Argument(..., help="Layer name (industry|core|domain|...)"),
    source: str = typer.Argument(..., help="Artifact URL/path or SPARQL endpoint"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    mode: str = typer.Option(
        "artifact", "--mode", help="artifact | endpoint_snapshot"
    ),
    version: str = typer.Option(None, "--version", help="Artifact version label"),
    snapshot_tag: str = typer.Option(
        None, "--snapshot-tag", help="Endpoint snapshot point-in-time tag"
    ),
    fmt: str = typer.Option("turtle", "--format", help="RDF serialization"),
    prefix: str = typer.Option(
        None, "--prefix", help="Prefix this layer resolves (e.g. fibo-fnd-pty-pty)"
    ),
    prefix_iri: str = typer.Option(
        None, "--prefix-iri", help="Namespace IRI the prefix expands to"
    ),
) -> None:
    """Pin an ontology layer in .mdl/lock.yaml (spec §3): fetch it once, record its
    sha256, and cache it under .mdl/ontology-cache/. No ontology file is committed —
    the lock pins the content, `mdl ontology fetch` reproduces it.

    Pass --prefix/--prefix-iri so the layer's prefixed alignments (e.g.
    fibo-fnd-pty-pty:PartyInRole) resolve offline against the fetched cache."""
    mo = _ontology()
    LOCK_MODES, Lock, compute_lock = mo.LOCK_MODES, mo.Lock, mo.compute_lock

    if mode not in LOCK_MODES:
        typer.secho(
            f"bad --mode {mode!r}; expected one of {', '.join(LOCK_MODES)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if bool(prefix) != bool(prefix_iri):
        typer.secho(
            "pass --prefix and --prefix-iri together, or neither",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    try:
        pin = compute_lock(
            model_dir,
            layer,
            mode,
            source,
            version=version,
            snapshot_tag=snapshot_tag,
            fmt=fmt,
        )
    except Exception as e:  # noqa: BLE001 - surface any fetch failure cleanly
        typer.secho(f"lock failed: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    if prefix and prefix_iri:
        pin.prefixes[prefix] = prefix_iri
    lock = Lock.load(model_dir)
    lock.ontology_layers[layer] = pin
    lock.save(model_dir)
    typer.secho(
        f"pinned {layer} ({mode}) sha256:{pin.sha256[:12]}… in .mdl/lock.yaml; "
        f"cached under .mdl/ontology-cache/{layer}/",
        fg=typer.colors.GREEN,
    )


@ontology_app.command("fetch")
def ontology_fetch(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    no_verify: bool = typer.Option(
        False, "--no-verify", help="Skip sha256 verification (not recommended)"
    ),
) -> None:
    """Fetch + hash-verify every locked ontology layer into .mdl/ontology-cache/
    (spec §3). Fail-closed on a hash mismatch, like `dbt deps` / `npm ci`."""
    mo = _ontology()
    FetchError, Lock, fetch_all = mo.FetchError, mo.Lock, mo.fetch_all

    lock = Lock.load(model_dir)
    if not lock.ontology_layers:
        typer.secho(
            "no ontology layers pinned; add one with `mdl ontology lock <layer> <source>`",
            fg=typer.colors.YELLOW,
        )
        return
    try:
        results = fetch_all(model_dir, lock, verify=not no_verify)
    except FetchError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    for r in results:
        state = "cached" if r.cached else "fetched"
        typer.secho(
            f"  {r.layer}: {state} -> {r.path.relative_to(model_dir)} "
            f"(sha256:{r.sha256[:12]}…)",
            fg=typer.colors.GREEN,
        )
    typer.secho(f"{len(results)} layer(s) ready", fg=typer.colors.GREEN)


@ontology_app.command("align")
def ontology_align(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    threshold: float = typer.Option(
        0.35, "--threshold", help="Minimum match confidence (0..1) to propose"
    ),
    limit: int = typer.Option(5, "--limit", help="Max candidates per object"),
    no_attributes: bool = typer.Option(
        False, "--no-attributes", help="Align entities/terms only, skip attributes"
    ),
) -> None:
    """Alignment pass (spec §2): propose ontology alignments for the model's objects
    and write them to the decision ledger as proposals.

    Non-blocking and non-committing: it matches each entity/attribute against the
    merged closure of every configured ontology source (public + enterprise) and
    records ranked candidates in .mdl/decisions.yaml. A human accepts them through the
    SME app / PR, which is what writes the alignment (and its audit trail) into the
    model YAML. Nothing here changes the model."""
    mo = _ontology()

    from mdl_reverse.ledger import Confidence, Decision, DecisionLedger

    repo = _load(model_dir)
    reg = mo.build_registry(model_dir, repo.model.config.ontology_stack)
    reg.load()
    proposals = mo.align_model(
        repo.model,
        reg,
        threshold=threshold,
        limit=limit,
        include_attributes=not no_attributes,
    )
    if not proposals:
        typer.secho(
            "no alignment candidates found (are ontology sources configured?)",
            fg=typer.colors.YELLOW,
        )
        return

    ledger = DecisionLedger.load(model_dir)
    added = 0
    for p in proposals:
        best = p.best
        d = Decision(
            kind="ontology_alignment",
            signal="lexical_match",
            confidence=Confidence(mo.confidence_band(best.confidence)),
            subject=f"{p.object_name} -> {best.prefixed or best.uri} ({best.source})",
            evidence={
                "object_id": p.object_id,
                "object_kind": p.object_kind,
                "object_name": p.object_name,
                "matched_field": p.matched_field,
                "candidates": [c.to_evidence() for c in p.candidates],
            },
        )
        if ledger.should_propose(d):
            ledger.record(d)
            added += 1
    ledger.save(model_dir)

    for p in proposals:
        best = p.best
        typer.echo(
            f"  {p.object_name:24} -> {best.prefixed or best.uri}  "
            f"({best.confidence:.0%} via {best.source})"
        )
    typer.secho(
        f"{len(proposals)} proposal(s), {added} new -> .mdl/decisions.yaml. "
        f"Review + accept in the SME app or `mdl ontology promote`.",
        fg=typer.colors.GREEN,
    )


# --- subject areas: the erwin Available/Included picker, from the terminal --------

sa_app = typer.Typer(help="Subject areas: scoped views of the model (erwin subject areas).")
app.add_typer(sa_app, name="subject-area")


def _resolve_sa(model_dir: Path, ref: str) -> tuple[str, str]:
    """Accept a ULID or a name, so the terminal is usable without copying ULIDs."""
    from mdl_core.repo import ModelRepo

    model = ModelRepo.load(model_dir).model
    if ref in model.subject_areas:
        return ref, model.subject_areas[ref].name
    matches = [sa for sa in model.subject_areas.values() if sa.name.lower() == ref.lower()]
    if not matches:
        near = [sa.name for sa in model.subject_areas.values() if ref.lower() in sa.name.lower()]
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        typer.secho(f"no subject area {ref!r}.{hint}", fg=typer.colors.RED)
        raise typer.Exit(1)
    return matches[0].id, matches[0].name


def _resolve_objects(model_dir: Path, refs: list[str]) -> list[str]:
    """Accept ULIDs or entity/term names."""
    from mdl_core.repo import ModelRepo

    model = ModelRepo.load(model_dir).model
    by_name: dict[str, str] = {}
    for obj in list(model.conceptual_entities.values()) + list(model.terms.values()):
        by_name[obj.name.lower()] = obj.id
    for le in model.logical_entities.values():
        if le.realises:
            by_name.setdefault(le.name.lower(), le.realises)

    out = []
    for r in refs:
        if r in model.conceptual_entities or r in model.terms or r in model.logical_entities:
            out.append(r)
        elif r.lower() in by_name:
            out.append(by_name[r.lower()])
        else:
            typer.secho(f"no entity or term {r!r}", fg=typer.colors.RED)
            raise typer.Exit(1)
    return out


@sa_app.command("list")
def sa_list(model_dir: Path = typer.Option(Path("."), "--model-dir", "-m")) -> None:
    """List subject areas with their membership counts."""
    from mdl_core.repo import ModelRepo

    model = ModelRepo.load(model_dir).model
    if not model.subject_areas:
        typer.echo("no subject areas yet — mdl new subject-area <name>")
        return
    homed: dict[str, int] = {}
    for ce in model.conceptual_entities.values():
        if ce.subject_area:
            homed[ce.subject_area] = homed.get(ce.subject_area, 0) + 1
    for sa in sorted(model.subject_areas.values(), key=lambda s: s.name):
        typer.echo(
            f"{sa.name:34} {len(sa.members):>3} member(s)  "
            f"{homed.get(sa.id, 0):>3} homed   {sa.id}"
        )


@sa_app.command("show")
def sa_show(
    area: str = typer.Argument(..., help="Subject area name or ULID"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Show what an area contains, and what is homed there but not a member."""
    from mdl_core.repo import ModelRepo

    sa_id, name = _resolve_sa(model_dir, area)
    model = ModelRepo.load(model_dir).model
    sa = model.subject_areas[sa_id]
    named = list(model.conceptual_entities.values()) + list(model.terms.values())
    label = {o.id: o.name for o in named}

    typer.secho(f"{name}", fg=typer.colors.CYAN, bold=True)
    if sa.definition:
        typer.echo(f"  {sa.definition.strip()}")
    typer.echo(f"\n  included ({len(sa.members)})")
    for m in sa.members:
        typer.echo(f"    - {label.get(m, m)}")
    homed = [ce for ce in model.conceptual_entities.values() if ce.subject_area == sa_id]
    inconsistent = [ce.name for ce in homed if ce.id not in set(sa.members)]
    if inconsistent:
        typer.secho(
            f"\n  homed here but not members ({len(inconsistent)}): "
            f"{', '.join(sorted(inconsistent))}",
            fg=typer.colors.YELLOW,
        )


@sa_app.command("add")
def sa_add(
    area: str = typer.Argument(...),
    objects: list[str] = typer.Argument(..., help="Entity or term names, or ULIDs"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Add objects to a subject area (the Included pane)."""
    from mdl_core.commands import apply_command

    sa_id, name = _resolve_sa(model_dir, area)
    members = _resolve_objects(model_dir, objects)
    apply_command(model_dir, "add_subject_area_members", {"id": sa_id, "members": members})
    typer.secho(f"added {len(members)} object(s) to {name!r}", fg=typer.colors.GREEN)


@sa_app.command("remove")
def sa_remove(
    area: str = typer.Argument(...),
    objects: list[str] = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Remove objects from a subject area."""
    from mdl_core.commands import apply_command

    sa_id, name = _resolve_sa(model_dir, area)
    members = _resolve_objects(model_dir, objects)
    apply_command(model_dir, "remove_subject_area_members", {"id": sa_id, "members": members})
    typer.secho(f"removed {len(members)} object(s) from {name!r}", fg=typer.colors.GREEN)


@sa_app.command("expand")
def sa_expand(
    area: str = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    direction: str = typer.Option("both", "--direction", help="ancestors|descendants|both"),
    levels: int = typer.Option(1, "--levels", help="how many hops (1-10)"),
    seeds: list[str] = typer.Option(None, "--seed", help="seed object; defaults to the members"),
    apply: bool = typer.Option(False, "--apply", help="add the results (default: preview only)"),
) -> None:
    """Add related objects — erwin's ancestors/descendants expansion.

    Previews by default and names the relationship each object came through, so a
    deep expansion cannot silently swallow the model. Pass --apply to commit it."""
    from mdl_core.closure import expand
    from mdl_core.commands import apply_command
    from mdl_core.repo import ModelRepo

    sa_id, name = _resolve_sa(model_dir, area)
    model = ModelRepo.load(model_dir).model
    sa = model.subject_areas[sa_id]
    seed_ids = _resolve_objects(model_dir, list(seeds)) if seeds else list(sa.members)
    if not seed_ids:
        typer.secho(
            f"{name!r} has no members yet — add one first, or pass --seed <entity>",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)

    if direction not in ("ancestors", "descendants", "both"):
        typer.secho(f"bad --direction {direction!r}", fg=typer.colors.RED)
        raise typer.Exit(1)

    hops = expand(model, set(seed_ids), direction=direction, levels=levels)
    current = set(sa.members)
    new = [h for h in hops if h.id not in current]
    if not new:
        typer.echo(f"nothing new to add to {name!r} ({direction}, {levels} level(s))")
        return

    typer.secho(f"{len(new)} object(s) related to {name!r}:", bold=True)
    for h in new:
        typer.echo(f"  + {h.name:24} via {h.via_name}  ({h.direction[:-1]}, level {h.level})")
    if not apply:
        typer.echo("\npreview only — re-run with --apply to add them")
        return
    apply_command(
        model_dir, "add_subject_area_members", {"id": sa_id, "members": [h.id for h in new]}
    )
    typer.secho(f"added {len(new)} object(s) to {name!r}", fg=typer.colors.GREEN)


@app.command("diff")
def diff_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    base: str = typer.Option("HEAD", "--base", help="git ref to compare against"),
    head: str = typer.Option(None, "--head", help="git ref (default: the working tree)"),
    fmt: str = typer.Option("text", "--format", help="text|json|markdown"),
) -> None:
    """Semantic diff of the model against a git ref.

    Objects are keyed by ULID, so a rename is one cosmetic change rather than an
    entity removed plus another added."""
    from mdl_server.git_models import RefLoadError, model_at_ref, model_at_working_tree

    from mdl_core.diff import diff_models
    from mdl_core.diff_render import render_json, render_markdown, render_text

    try:
        base_model = model_at_ref(model_dir, base)
        head_model = (
            model_at_working_tree(model_dir) if head is None else model_at_ref(model_dir, head)
        )
    except RefLoadError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(1) from e

    d = diff_models(
        base_model, head_model, base_label=base, head_label=head or "working copy"
    )
    if fmt == "json":
        typer.echo(json.dumps(render_json(d), indent=2))
    elif fmt == "markdown":
        typer.echo(render_markdown(d))
    else:
        typer.echo(render_text(d))
    if d.has_breaking:
        raise typer.Exit(2)  # breaking changes exit 2, as drift does


new_app = typer.Typer(help="Scaffold model objects (mints ULIDs for you).")
app.add_typer(new_app, name="new")

delete_app = typer.Typer(help="Delete model objects (safe: shows impact, confirms).")
app.add_typer(delete_app, name="delete")


@delete_app.command("entity")
def delete_entity(
    name: str = typer.Argument(..., help="Logical entity name (e.g. benchmark)"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    cascade: bool = typer.Option(
        False, "--cascade", help="Also delete relationships and physical tables that reference it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Delete an entity and its dangling references (safe by default).

    Shows exactly what will be removed and asks before touching anything. The
    conceptual entity is dropped only if nothing else realises it. Relationships
    and physical tables that reference the entity block the delete unless
    --cascade. Validates afterwards, so you never end with a broken model."""
    from mdl_core.commands import CommandError, apply_command

    repo = _load(model_dir)
    le = next(
        (e for e in repo.model.logical_entities.values() if e.name == name),
        None,
    )
    if le is None:
        typer.secho(f"no logical entity named {name!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    # Preflight impact: what references this entity, and what will be removed.
    rels = [
        r
        for r in repo.model.relationships.values()
        if r.from_.entity == le.id or r.to.entity == le.id
    ]
    physicals = [pt for pt in repo.model.physical_tables.values() if pt.realises == le.id]
    ce = repo.model.conceptual_entities.get(le.realises) if le.realises else None
    ce_others = (
        [
            x
            for x in repo.model.logical_entities.values()
            if x.realises == le.realises and x.id != le.id
        ]
        if le.realises
        else []
    )

    typer.secho(f"Delete entity {name!r}:", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  - logical entity  {le.name}")
    if ce and not ce_others:
        typer.echo(f"  - conceptual      {ce.name}  (nothing else realises it)")
    elif ce:
        others = ", ".join(x.name for x in ce_others)
        typer.echo(f"  · conceptual      {ce.name}  KEPT (still realised by: {others})")
    for r in rels:
        typer.echo(f"  {'-' if cascade else '!'} relationship    {r.name}")
    for pt in physicals:
        typer.echo(f"  {'-' if cascade else '!'} physical table  {pt.name} ({pt.target})")

    if (rels or physicals) and not cascade:
        typer.secho(
            "\nrefusing: this entity is referenced. Re-run with --cascade to also "
            "delete the flagged (!) objects.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(1)

    if not yes and not typer.confirm("\nProceed?", default=False):
        typer.secho("aborted", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    try:
        apply_command(model_dir, "delete_entity", {"id": le.id, "cascade": cascade})
    except (CommandError, FileNotFoundError) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"deleted entity {name!r}", fg=typer.colors.GREEN)


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
    except (CommandError, FileNotFoundError) as e:
        # FileNotFoundError carries the "no mdl-project.yaml … run from my-model/"
        # guidance (issue #7); show it plainly, not as a traceback.
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"created entity {name!r} ({result.created_id})", fg=typer.colors.GREEN)


@new_app.command("subject-area")
def new_subject_area(
    name: str = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    definition: str = typer.Option(None, "--definition"),
) -> None:
    from mdl_core.commands import CommandError, apply_command

    try:
        result = apply_command(
            model_dir, "create_subject_area", {"name": name, "definition": definition}
        )
    except (CommandError, FileNotFoundError) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
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
    except (CommandError, FileNotFoundError) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(f"created term {name!r} ({result.created_id})", fg=typer.colors.GREEN)


decisions_app = typer.Typer(help="Review the reverse-engineering decision ledger (§6.2).")
app.add_typer(decisions_app, name="decisions")


@decisions_app.command("list")
def decisions_list(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    pending: bool = typer.Option(False, "--pending", help="Only unreviewed proposals"),
    fmt: str = typer.Option(
        "text", "--format", help="text|json (json feeds the VS Code review UI)"
    ),
) -> None:
    ledger = DecisionLedger.load(model_dir)
    items = ledger.pending() if pending else list(ledger.decisions.values())
    if fmt == "json":
        typer.echo(
            json.dumps(
                [
                    {
                        "signal_key": d.signal_key,
                        "kind": d.kind,
                        "signal": d.signal,
                        "confidence": d.confidence.value,
                        "verdict": d.verdict.value,
                        "subject": d.subject,
                        "evidence": d.evidence,
                    }
                    for d in items
                ],
                default=str,
            )
        )
        return
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


@decisions_app.command("add")
def decisions_add(
    src: str = typer.Argument(
        None,
        help="Proposal JSON — one object or a list of "
        '{kind, subject, evidence, confidence?, signal?}. A file path, or omit to read stdin.',
    ),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Record externally-authored proposal(s) into the decision ledger as `proposed`, for
    the user to Accept/Reject in the Reverse Review panel exactly like a built-in proposal.

    The generic ledger-intake doorway: any external proposer — a script, a notebook, or a
    paid AI package — feeds proposals here as JSON. Each becomes a Decision; provenance is
    kept in evidence.source (default "external"). Never auto-accepted: it lands `proposed`,
    and its confidence bands the initial verdict the same way an engine proposal does.
    De-duplicated by signal_key, and a proposal already accepted/rejected is not re-added.
    """
    import sys as _sys

    from mdl_reverse.ledger import Confidence, Decision, DecisionLedger

    raw = Path(src).read_text(encoding="utf-8") if src and Path(src).exists() else (
        src if src else _sys.stdin.read()
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        typer.secho(f"invalid proposal JSON: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    items = data if isinstance(data, list) else [data]

    ledger = DecisionLedger.load(model_dir)
    added = 0
    skipped = 0
    for item in items:
        if not isinstance(item, dict) or "kind" not in item or "subject" not in item:
            typer.secho(
                "each proposal needs at least {kind, subject}", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(1)
        try:
            conf = Confidence(str(item.get("confidence", "low")).lower().replace("_", "-"))
        except ValueError:
            typer.secho(
                f"bad confidence {item.get('confidence')!r} (high|medium-high|medium|low)",
                fg=typer.colors.RED, err=True,
            )
            raise typer.Exit(1) from None
        evidence = dict(item.get("evidence") or {})
        evidence.setdefault("source", item.get("source", "external"))
        d = Decision(
            kind=str(item["kind"]),
            signal=str(item.get("signal", evidence.get("source", "external"))),
            confidence=conf,
            subject=str(item["subject"]),
            evidence=evidence,
        )
        if ledger.should_propose(d):
            ledger.record(d)
            added += 1
        else:
            skipped += 1
    ledger.save(model_dir)
    typer.secho(
        f"added {added} proposal(s)" + (f", skipped {skipped} already decided" if skipped else ""),
        fg=typer.colors.GREEN,
    )


def _set_decision(model_dir: Path, signal_key: str, verdict: Verdict) -> None:
    ledger = DecisionLedger.load(model_dir)
    if signal_key not in ledger.decisions:
        typer.secho(f"no decision {signal_key!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    ledger.set_verdict(signal_key, verdict)
    ledger.save(model_dir)
    typer.secho(f"{verdict.value}: {ledger.decisions[signal_key].subject}", fg=typer.colors.GREEN)


# --- entity -> dbt model mapping (reverse.model_map) --------------------------
#
# The `reverse:` block of mdl-project.yaml is the single source of truth for how this
# warehouse's dbt models map to entities. These commands author `reverse.model_map`
# (comment-preserving round-trip) so the Drift-UI "Map to dbt model…" action, hand
# editing, and future AI discovery all converge on the same YAML.

mapping_app = typer.Typer(help="Map entities to dbt models for drift (reverse.model_map).")
app.add_typer(mapping_app, name="mapping")


def _write_model_map(model_dir: Path, entity: str, dbt_model: str | None) -> None:
    """Set (dbt_model given) or remove (None) reverse.model_map[entity] in
    mdl-project.yaml, preserving comments and formatting."""
    from mdl_core.yaml_io import dump_file, load_file

    proj_path = model_dir / "mdl-project.yaml"
    if not proj_path.exists():
        typer.secho(f"no mdl-project.yaml in {model_dir}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    doc = load_file(proj_path)
    rev = doc.get("reverse")
    if not isinstance(rev, dict):
        rev = {}
        doc["reverse"] = rev
    mm = rev.get("model_map")
    if not isinstance(mm, dict):
        mm = {}
        rev["model_map"] = mm
    if dbt_model is None:
        if entity in mm:
            del mm[entity]
            typer.secho(f"unmapped {entity}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"{entity} was not mapped", fg=typer.colors.YELLOW)
    else:
        mm[entity] = dbt_model
        typer.secho(f"mapped {entity} -> {dbt_model}", fg=typer.colors.GREEN)
    dump_file(proj_path, doc)


@mapping_app.command("set")
def mapping_set(
    entity: str = typer.Argument(..., help="Modelith logical entity name"),
    dbt_model: str = typer.Argument(..., help="Exact dbt model name it materialises as"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Map an entity to the dbt model that materialises it (an explicit override that
    wins over any layer pattern). Written to reverse.model_map in mdl-project.yaml."""
    _write_model_map(model_dir, entity, dbt_model)


@mapping_app.command("unset")
def mapping_unset(
    entity: str = typer.Argument(...),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Remove an entity's explicit mapping (falls back to layers / bare name)."""
    _write_model_map(model_dir, entity, None)


@mapping_app.command("list")
def mapping_list(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    fmt: str = typer.Option("text", "--format", help="text|json (json feeds the Drift UI)"),
    manifest: Path = typer.Option(
        None, "--manifest", help="Manifest to compute the unmatched entity/model sets from"
    ),
    rank_for: str = typer.Option(
        None, "--rank-for", help="Rank unmatched dbt models by similarity to this entity"
    ),
) -> None:
    """Show current mappings and, with --manifest, the entities that still don't match a
    dbt model plus the unclaimed dbt models. With --rank-for <entity>, the unmatched
    models come back best-match-first (e.g. stg_price on top for price) — the candidate
    list the Drift-UI "Map to dbt model…" picker shows."""
    from mdl_reverse.mapping import resolve_model_name
    from mdl_reverse.projection import project_model

    repo = _load(model_dir)
    reverse = getattr(repo.model.config, "reverse", None)
    current = dict(getattr(reverse, "model_map", {}) or {})
    target = repo.model.config.dbt_target or "duckdb_dev"

    unmatched_entities: list[str] = []
    unclaimed_models: list[str] = []
    if manifest is not None:
        try:
            proj = read_manifest(manifest)
        except FileNotFoundError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(4) from e
        manifest_names = proj.model_names()
        expected = project_model(repo.model, target, reverse=reverse, available=manifest_names)
        expected_names = set(expected)
        unmatched_entities = sorted(
            le.name
            for le in repo.model.logical_entities.values()
            if not le.unmanaged
            and resolve_model_name(le.name, target, reverse, manifest_names) not in manifest_names
        )
        unclaimed_models = sorted(manifest_names - expected_names)
        if rank_for:
            unclaimed_models = _rank_by_similarity(rank_for, unclaimed_models)

    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "model_map": current,
                    "unmatched_entities": unmatched_entities,
                    "unclaimed_models": unclaimed_models,
                },
                default=str,
            )
        )
        return
    if current:
        typer.secho("mappings:", bold=True)
        for k, v in current.items():
            typer.echo(f"  {k} -> {v}")
    else:
        typer.secho("no explicit mappings", fg=typer.colors.YELLOW)
    if manifest is not None:
        typer.secho(f"unmatched entities ({len(unmatched_entities)}):", bold=True)
        for e in unmatched_entities:
            typer.echo(f"  {e}")
        typer.secho(f"unclaimed dbt models ({len(unclaimed_models)}):", bold=True)
        for m in unclaimed_models:
            typer.echo(f"  {m}")


def _rank_by_similarity(entity: str, candidates: list[str]) -> list[str]:
    """Order candidate dbt models best-match-first for an entity name, using a stdlib
    ratio (no dependency). A candidate that CONTAINS the entity (stg_price for price)
    is boosted so the obvious staging/dim match sorts to the top."""
    import difflib

    el = entity.lower()

    def score(c: str) -> float:
        cl = c.lower()
        base = difflib.SequenceMatcher(None, el, cl).ratio()
        return base + (0.5 if el in cl else 0.0)

    return sorted(candidates, key=lambda c: (-score(c), c))


# --- reverse config authoring (suggest / explain / import / apply) -----------
#
# The `reverse:` block of mdl-project.yaml is the single, git-committed source of truth
# for how a warehouse's dbt models map to entities. These commands let a team DISCOVER a
# starting config from the warehouse, SEE how it classifies before reversing, and IMPORT
# a shared standard from another team — all through one comment-preserving writer, so the
# config is a first-class, shareable, reviewable artifact.

reverse_cfg_app = typer.Typer(help="Author, inspect and share the reverse: config block.")
app.add_typer(reverse_cfg_app, name="reverse-config")


def _write_reverse_block(
    model_dir: Path, incoming: dict, *, replace: bool = False, source: str | None = None
) -> dict:
    """Merge a partial `reverse:` block into mdl-project.yaml, preserving comments. Returns
    the merged block. Validates the result through ReverseConfig before writing so a
    malformed merge is rejected, not half-applied. `source` adds a provenance comment."""
    from mdl_core.ir import ReverseConfig
    from mdl_core.yaml_io import dump_file, load_file
    from mdl_reverse.config_io import merge_reverse_block

    proj_path = model_dir / "mdl-project.yaml"
    if not proj_path.exists():
        typer.secho(f"no mdl-project.yaml in {model_dir}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    doc = load_file(proj_path)
    existing = doc.get("reverse") if isinstance(doc.get("reverse"), dict) else {}
    merged = merge_reverse_block(existing, incoming, replace=replace)
    # validate before writing — reject a malformed block wholesale.
    try:
        ReverseConfig.model_validate(merged)
    except Exception as e:  # noqa: BLE001 - surface the validation error, don't half-write
        typer.secho(f"resulting reverse: block is invalid — not written ({e})",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    doc["reverse"] = merged
    dump_file(proj_path, doc)
    if source:
        typer.secho(f"  (merged from {source})", fg=typer.colors.CYAN)
    return merged


@reverse_cfg_app.command("apply")
def reverse_config_apply(
    src: Path = typer.Argument(
        None, help="A file with a partial reverse: block (YAML/JSON). Omit to read stdin."
    ),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    replace: bool = typer.Option(
        False, "--replace", help="Overwrite each provided key wholesale (default: merge additively)"
    ),
) -> None:
    """Merge a partial reverse: block into mdl-project.yaml (comment-preserving). The
    generic write doorway suggest/import and external tools use. Reads a `reverse:` block
    or a bare block body from a file or stdin."""
    import sys as _sys

    from mdl_core.yaml_io import load_str

    text = src.read_text(encoding="utf-8") if src is not None else _sys.stdin.read()
    data = load_str(text) or {}
    # accept either a wrapping {reverse: {...}} or the bare block body.
    block = data.get("reverse") if isinstance(data, dict) and "reverse" in data else data
    if not isinstance(block, dict):
        typer.secho(
            "input is not a reverse: block (expected a mapping)", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)
    _write_reverse_block(model_dir, block, replace=replace)
    typer.secho("applied reverse: config", fg=typer.colors.GREEN)


@reverse_cfg_app.command("suggest")
def reverse_config_suggest(
    manifest: Path = typer.Option(..., "--manifest", help="Path to target/manifest.json"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    fmt: str = typer.Option("yaml", "--format", help="yaml|json"),
    apply: bool = typer.Option(
        False, "--apply", help="Merge the suggestion into mdl-project.yaml (review the diff)"
    ),
) -> None:
    """Discover a starting reverse: config from the warehouse — folder/prefix/tag analysis
    proposes layers (role classification) and likely exclusions, deterministically (no AI).
    Prints it to review; --apply merges it (comment-preserving) so you refine from a real
    draft, not a blank page."""
    from mdl_core.yaml_io import dump_str
    from mdl_reverse.suggest import suggest_config

    try:
        proj = read_manifest(manifest)
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(4) from e
    report = suggest_config(proj)
    if not report.block:
        typer.secho(
            "no clear folder/prefix conventions found — nothing to suggest", fg=typer.colors.YELLOW
        )
        return
    if fmt == "json":
        typer.echo(
            json.dumps({"reverse": report.block, "rationale": report.rationale}, default=str)
        )
    else:
        for line in report.rationale:
            typer.secho(f"# {line}", fg=typer.colors.CYAN)
        typer.echo(dump_str({"reverse": report.block}))
    if apply:
        _write_reverse_block(model_dir, report.block, source="suggest-config")
        typer.secho("applied suggested reverse: config", fg=typer.colors.GREEN)


@reverse_cfg_app.command("explain")
def reverse_config_explain(
    manifest: Path = typer.Option(..., "--manifest", help="Path to target/manifest.json"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    fmt: str = typer.Option("text", "--format", help="text|json"),
) -> None:
    """Show how the CURRENT reverse: config classifies every dbt model — role, matched
    layer, exclusion/exemption, effective target_form — WITHOUT running a reverse or
    writing anything. Tweak the config, re-run, see the effect, then commit."""
    from mdl_reverse.mapping import naming_from_config, resolve_layer

    try:
        proj = read_manifest(manifest)
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(4) from e

    # load the reverse config from mdl-project.yaml (the same source reverse uses).
    reverse_config = _load_reverse_config(None, model_dir)
    naming = naming_from_config(reverse_config)
    default_form = getattr(reverse_config, "target_form", None) or "denormalized"

    rows = []
    for name in sorted(proj.models):
        mm = proj.models[name]
        v = resolve_layer(name, mm.tags, getattr(mm, "path", None), reverse_config, naming)
        rows.append({
            "model": name,
            "role": v.role,
            "layer": v.layer_name,
            "exempt": v.exempt,
            "excluded": v.role in ("staging", "intermediate", "exclude"),
            "target_form": v.target_form or default_form,
            "pattern": v.pattern,
        })

    if fmt == "json":
        typer.echo(json.dumps(rows, default=str))
        return
    # text table
    kept = [r for r in rows if not r["excluded"]]
    dropped = [r for r in rows if r["excluded"]]
    typer.secho(f"entities kept ({len(kept)}):", bold=True)
    for r in kept:
        tag = f" [{r['layer']}]" if r["layer"] else ""
        form = "" if r["target_form"] == "denormalized" else f"  target_form={r['target_form']}"
        pat = f"  pattern={r['pattern']}" if r["pattern"] else ""
        ex = "  (exempt)" if r["exempt"] else ""
        typer.echo(f"  {r['model']:32} {r['role']}{tag}{form}{pat}{ex}")
    typer.secho(f"excluded ({len(dropped)}):", bold=True)
    for r in dropped:
        typer.echo(f"  {r['model']:32} {r['role']}")


@reverse_cfg_app.command("import")
def reverse_config_import(
    src: str = typer.Argument(
        ...,
        help="A reverse: config to import — a local file, an https:// URL, a GitHub/GitLab "
        "blob URL (auto-converted to raw), or a `github:owner/repo/path@ref` shorthand.",
    ),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    replace: bool = typer.Option(
        False, "--replace", help="Overwrite each imported key wholesale (default: merge additively)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the fetched config without writing (preview before apply)"
    ),
    token: str = typer.Option(
        None,
        "--token",
        help="Bearer token for a private repo (or $MODELITH_IMPORT_TOKEN / $GITHUB_TOKEN)",
    ),
    allow_insecure: bool = typer.Option(
        False, "--allow-insecure", help="Permit a plain http:// source (not recommended)"
    ),
) -> None:
    """Import a shared reverse: config (a team standard, a starter pack) from a file, URL,
    or git host and merge it into mdl-project.yaml, comment-preserving — the git-native way
    to inherit modeling standards. --dry-run previews without writing. A malformed source
    is rejected wholesale, never half-applied. Private repos: pass --token."""
    from mdl_core.yaml_io import dump_str, load_str
    from mdl_reverse.remote_config import ImportError_, fetch_config_text

    # One dispatch for every source kind (file / https / github: / a plugin scheme like
    # mdl://) — the resolver registry picks the handler for the source's scheme.
    try:
        text = fetch_config_text(src, token=token, allow_insecure=allow_insecure)
    except ImportError_ as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e

    data = load_str(text) or {}
    block = data.get("reverse") if isinstance(data, dict) and "reverse" in data else data
    if not isinstance(block, dict):
        typer.secho("source is not a reverse: config (expected a mapping)",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if dry_run:
        # validate the merged result so the preview reflects exactly what would be written,
        # then print it without touching the file.
        from mdl_core.ir import ReverseConfig
        from mdl_core.yaml_io import load_file
        from mdl_reverse.config_io import merge_reverse_block

        proj_path = model_dir / "mdl-project.yaml"
        existing = {}
        if proj_path.exists():
            doc = load_file(proj_path)
            existing = doc.get("reverse") if isinstance(doc.get("reverse"), dict) else {}
        merged = merge_reverse_block(existing, block, replace=replace)
        try:
            ReverseConfig.model_validate(merged)
        except Exception as e:  # noqa: BLE001
            typer.secho(f"config is invalid — would not be written ({e})",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from e
        typer.secho(f"# preview of the merged reverse: block from {src} (nothing written)",
                    fg=typer.colors.CYAN)
        typer.echo(dump_str({"reverse": merged}))
        return

    _write_reverse_block(model_dir, block, replace=replace, source=src)
    typer.secho(f"imported reverse: config from {src}", fg=typer.colors.GREEN)


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
    mo = _ontology()
    repo = _load(model_dir)
    reg = mo.build_registry(model_dir, repo.model.config.ontology_stack)
    reg.load()
    g = mo.export_rdf(repo.model, layer=layer, registry=reg)
    _emit_text(mo.serialize(g, fmt), out, "rdf")


@export_app.command("shacl")
def export_shacl_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    fmt: str = typer.Option("turtle", "--format"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Export SHACL shapes generated from the logical model (spec §3.3)."""
    mo = _ontology()
    repo = _load(model_dir)
    g = mo.export_shacl(repo.model)
    _emit_text(mo.serialize(g, fmt), out, "shacl")


@export_app.command("r2rml")
def export_r2rml_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    target: str = typer.Option(None, "--target", "-t", help="Physical target to map"),
    fmt: str = typer.Option("turtle", "--format", help="turtle|xml"),
    out: Path = typer.Option(None, "--out", "-o"),
    allow_unmapped: bool = typer.Option(
        False,
        "--allow-unmapped",
        help="Mint fallback IRIs for unmapped entities/attrs instead of failing",
    ),
) -> None:
    """Export a W3C R2RML mapping: the logical model as a first-class knowledge graph.

    Emits the mapping (the deterministic term-map from warehouse rows to typed,
    ontology-aligned graph nodes), not triples. Feed it to Ontop for a virtual SPARQL
    endpoint over your warehouse, or to Morph-KGC to materialize a triple store.

    Precondition (spec §5): every managed entity/attribute must carry a resolved
    ontology mapping. By default this fails loudly listing what's unmapped; pass
    --allow-unmapped to mint fallback IRIs on the project base instead.
    """
    mo = _ontology()

    repo = _load(model_dir)
    tgt = target or repo.model.config.dbt_target
    reg = mo.build_registry(model_dir, repo.model.config.ontology_stack)
    reg.load()
    try:
        g = mo.export_r2rml(
            repo.model, target=tgt, registry=reg, allow_unmapped=allow_unmapped
        )
    except mo.UnmappedError as e:
        typer.secho(e.report.summary(), fg=typer.colors.RED, err=True)
        for line in e.report.report_lines():
            typer.secho(line, fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1) from e
    _emit_text(mo.serialize(g, fmt), out, "r2rml")


@export_app.command("contract")
def export_contract_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(Path("datacontract.yaml"), "--out", "-o"),
) -> None:
    """Export an Open Data Contract Standard (ODCS v3) data contract.

    Modelith as a Data Contract Factory: snapshot the model and emit a pristine,
    valid datacontract.yaml (schema + keys + valid-values + ownership).
    """
    repo = _load(model_dir)
    _emit_text(emit_datacontract(repo.model), out, "contract")


@export_app.command("graph")
def export_graph_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Export a Neo4j / Cypher schema (node key/unique/existence constraints)."""
    repo = _load(model_dir)
    _emit_text(emit_cypher(repo.model), out, "graph")


@emit_app.command("pydantic")
def emit_pydantic_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(Path("models.py"), "--out", "-o"),
) -> None:
    """Emit Pydantic v2 data models (one BaseModel per managed entity)."""
    repo = _load(model_dir)
    _emit_text(emit_pydantic_models(repo.model), out, "pydantic")


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


# --- ER interchange: SQL DDL / Mermaid / DBML / CSV export + import --------------


@export_app.command("sql")
def export_sql_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(None, "--out", "-o"),
    dialect: str = typer.Option("postgres", "--dialect", help="postgres|snowflake|duckdb|..."),
) -> None:
    """Export CREATE TABLE DDL (PK/FK/UNIQUE/NOT NULL) for a chosen SQL dialect."""
    from mdl_emit_erd import emit_sql_ddl

    repo = _load(model_dir)
    _emit_text(emit_sql_ddl(repo.model, dialect=dialect), out, "sql")


@export_app.command("mermaid")
def export_mermaid_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Export a Mermaid erDiagram (renders in GitHub/GitLab/markdown)."""
    from mdl_emit_erd import emit_mermaid

    repo = _load(model_dir)
    _emit_text(emit_mermaid(repo.model), out, "mermaid")


@export_app.command("dbml")
def export_dbml_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Export DBML (opens in dbdiagram.io / dbdocs / ChartDB)."""
    from mdl_emit_erd import emit_dbml

    repo = _load(model_dir)
    _emit_text(emit_dbml(repo.model), out, "dbml")


@export_app.command("csv")
def export_csv_cmd(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    out: Path = typer.Option(None, "--out", "-o"),
) -> None:
    """Export a flat attributes CSV (entity, attribute, type, PK/FK, nullable)."""
    from mdl_emit_erd import emit_csv

    repo = _load(model_dir)
    _emit_text(emit_csv(repo.model), out, "csv")


def _apply_imported(model_dir: Path, imported, source: str) -> None:
    """Apply a parsed ImportedModel to the model dir via the mutation engine, so the
    import goes through the same validated path as manual editing."""
    from mdl_emit_erd.imports.model import to_commands

    from mdl_core.commands import CommandError, apply_command

    for w in imported.warnings:
        typer.secho(f"  note: {w}", fg=typer.colors.YELLOW)
    cmds = to_commands(imported)
    applied = 0
    for c in cmds:
        try:
            apply_command(model_dir, c["op"], c["payload"])
            applied += 1
        except (CommandError, FileNotFoundError) as e:
            typer.secho(f"  skipped {c['op']}: {e}", fg=typer.colors.YELLOW)
    typer.secho(
        f"imported {len(imported.tables)} table(s) from {source} "
        f"({applied} change(s) applied)",
        fg=typer.colors.GREEN,
    )


@import_app.command("sql")
def import_sql_cmd(
    file: Path = typer.Argument(..., help="SQL DDL file (CREATE TABLE ...)"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    dialect: str = typer.Option("postgres", "--dialect", help="postgres|snowflake|mysql|..."),
) -> None:
    """Import a SQL DDL script into the model (parsed via a real SQL AST)."""
    from mdl_emit_erd.imports import parse_sql_ddl

    _apply_imported(model_dir, parse_sql_ddl(file.read_text(encoding="utf-8"), dialect=dialect),
                    "SQL DDL")


@import_app.command("mermaid")
def import_mermaid_cmd(
    file: Path = typer.Argument(..., help="Mermaid erDiagram (.mmd / markdown)"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Import a Mermaid erDiagram (structural: entities, attributes, relationships)."""
    from mdl_emit_erd.imports import parse_mermaid

    _apply_imported(model_dir, parse_mermaid(file.read_text(encoding="utf-8")), "Mermaid")


@import_app.command("json-schema")
def import_json_schema_cmd(
    file: Path = typer.Argument(..., help="JSON Schema ($defs of object schemas, or one object)"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Import a JSON Schema; each object definition becomes an entity."""
    from mdl_emit_erd.imports import parse_json_schema

    _apply_imported(model_dir, parse_json_schema(file.read_text(encoding="utf-8")), "JSON Schema")


@app.command()
def lsp() -> None:
    """Start the Modelith language server (stdio). One server for VS Code,
    Cursor, Windsurf, JetBrains, and CI — same engine as the CLI."""
    from mdl_lsp.server import main as lsp_main

    lsp_main()


model_app = typer.Typer(
    help="Read the model as JSON (the shared query layer behind the AI surfaces)."
)
app.add_typer(model_app, name="model")


@model_app.command("context")
def model_context(model_dir: Path = typer.Option(Path("."), "--model-dir", "-m")) -> None:
    """Print a condensed model summary (project, counts, subject areas, relationships)
    as JSON — sized for a chat context window. Backs `@modelith` in VS Code Ask mode
    and the MCP `get_model_context` tool."""
    from mdl_core.query import get_model_context

    typer.echo(json.dumps(get_model_context(_load(model_dir).model), default=str))


@model_app.command("entities")
def model_entities(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    subject_area: str = typer.Option(None, "--subject-area", help="Scope by area name or ULID"),
) -> None:
    """List logical entities (name, definition, attribute count, subject area) as JSON."""
    from mdl_core.query import list_entities

    typer.echo(json.dumps(list_entities(_load(model_dir).model, subject_area), default=str))


@model_app.command("entity")
def model_entity(
    name: str = typer.Argument(..., help="Entity name or ULID"),
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
) -> None:
    """Print one entity's full detail (attributes, keys, conceptual layer,
    relationships) as JSON. Exits 1 with an error object if no entity matches."""
    from mdl_core.query import get_entity

    hit = get_entity(_load(model_dir).model, name)
    if hit is None:
        typer.echo(json.dumps({"error": f"no entity named {name!r}"}))
        raise typer.Exit(1)
    typer.echo(json.dumps(hit, default=str))


@model_app.command("detail")
def model_detail(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    limit: int = typer.Option(40, "--limit", help="Max entities (alphabetical) to include"),
) -> None:
    """Print compact detail for EVERY entity (attributes, keys, relationships) as JSON,
    for grounding a model-wide question. Terse by design and capped at --limit, with a
    `truncated` flag so a large model doesn't overflow a chat context window."""
    from mdl_core.query import entities_detail

    typer.echo(json.dumps(entities_detail(_load(model_dir).model, limit=limit), default=str))


@app.command()
def mcp(
    repo: Path = typer.Option(
        Path("."), "--repo", "-m", help="Model repo directory (contains mdl-project.yaml)"
    ),
) -> None:
    """Start the Modelith MCP server (stdio) — model + ontology tools for an AI
    agent (VS Code Copilot Chat in agent mode, Claude Desktop, Cursor).

    Reads (list_entities, get_entity, search_ontology, get_model_context, validate)
    and writes (create_entity, update_entity) share the same core query + command
    engine as the CLI and canvas. Writes go straight to this checkout — the
    engineer's own trust boundary, not the SME propose-as-PR flow."""
    from mdl_mcp.server import run as mcp_run

    mcp_run(repo)


@app.command()
def serve(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(4800, "--port", "-p"),
    read_only: bool = typer.Option(
        False, "--read-only", help="Disable editing (viewer only, for shared deployments)"
    ),
) -> None:
    """Serve the architect ER canvas + API. Editing writes the working tree.

    Kept for the VS Code extension and existing scripts; `mdl studio --direct`
    is the same thing with the modeler app's chrome."""
    from mdl_server.app import serve as run_server

    mode = "read-only" if read_only else "editable"
    typer.secho(
        f"Modelith canvas ({mode}): http://{host}:{port}  (model: {model_dir})",
        fg=typer.colors.CYAN,
    )
    # The "wow" moment. Emit at STARTUP, before the blocking server loop — serve only
    # "exits" when killed, so the main() wrapper's record() would fire late/unreliably.
    from mdl_cli import telemetry

    telemetry.emit("canvas_opened", {"surface": "serve"})
    run_server(model_dir, host=host, port=port, read_only=read_only)


@app.command()
def studio(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(4810, "--port", "-p"),
    read_only: bool = typer.Option(
        False, "--read-only", help="Browse only — the onboarding week-3 gate"
    ),
    with_canvas: bool = typer.Option(
        False,
        "--with-canvas",
        help="Also serve the architect ER canvas at / (off by default)",
    ),
    export: Path = typer.Option(
        None,
        "--export",
        help="Write the app's static files to a directory and exit (no server)",
    ),
    catalog: bool = typer.Option(
        False,
        "--catalog",
        help="Open on a list of published models instead of one model dir",
    ),
    direct: bool = typer.Option(
        False,
        "--direct",
        help="Engineer mode: edits write the working tree instead of staging a PR",
    ),
) -> None:
    """Modelith Studio: the model, in a browser.

    Two ways to work, because two people need different things from the same model:

    \b
      default    modeler/steward — edits are staged and land as ONE pull request.
                 Git is never in the way; the review screen is.
      --direct   engineer/architect — edits write the working tree, and you commit
                 from the git panel. The same surface the VS Code canvas gives you.

    A self-contained application, not a view of the canvas: by default the architect
    ER canvas is NOT served at /, so handing someone this URL hands them one app
    rather than two. Pass --with-canvas to serve both from one process."""
    from mdl_server.app import serve as run_server

    if export is not None:
        _export_sme_app(export)
        return

    if catalog:
        # Browse every published model and open one to edit, instead of being bound
        # to a single model dir. The catalog stays a pointer index: opening a model
        # checks its own repo out on a proposal branch, so edits land as a PR there.
        from mdl_catalog.backend import CatalogConfig
        from mdl_catalog.git_backend import make_backend
        from mdl_server.catalog_app import serve_catalog

        cfg = CatalogConfig.resolve(Path.cwd())
        be = make_backend(cfg, _catalog_cache_dir())
        typer.secho(
            f"Modelith Studio (catalog): http://{host}:{port}/catalog",
            fg=typer.colors.CYAN,
        )
        serve_catalog(be, host=host, port=port)
        return

    if read_only:
        mode = "read-only"
    elif direct:
        mode = "direct — writes the working tree"
    else:
        mode = "propose-as-PR"
    extra = " + canvas at /" if with_canvas else ""
    typer.secho(
        f"Modelith Studio ({mode}{extra}): http://{host}:{port}/sme  (model: {model_dir})",
        fg=typer.colors.CYAN,
    )
    from mdl_cli import telemetry

    telemetry.emit("canvas_opened", {"surface": "studio"})  # activation wow, at startup
    run_server(
        model_dir,
        host=host,
        port=port,
        read_only=read_only,
        sme_only=not with_canvas,
        direct=direct,
    )


@app.command(hidden=True)
def glossary(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(4810, "--port", "-p"),
    read_only: bool = typer.Option(False, "--read-only"),
    with_canvas: bool = typer.Option(False, "--with-canvas"),
    export: Path = typer.Option(None, "--export"),
    catalog: bool = typer.Option(False, "--catalog"),
) -> None:
    """Deprecated alias for `mdl studio` (the app outgrew the name)."""
    typer.secho("note: `mdl glossary` is now `mdl studio`", fg=typer.colors.YELLOW)
    studio(
        model_dir=model_dir,
        host=host,
        port=port,
        read_only=read_only,
        with_canvas=with_canvas,
        export=export,
        catalog=catalog,
        direct=False,
    )


def _export_sme_app(dest: Path) -> None:
    """Write just the modeler app's static files, for hosting it anywhere.

    The app is a separate Vite entry, so its JS never pulls in the architect
    canvas bundle: what lands here is the SPA, React, the shared API module and one
    stylesheet. It still needs an `mdl glossary` (or `mdl serve`) somewhere to talk
    to — the API is the model, and the model lives in git."""
    import shutil

    from mdl_server.app import STATIC_DIR

    src_html = STATIC_DIR / "sme.html"
    if not src_html.exists():
        typer.secho(
            "no built app found — this install has no canvas bundle", fg=typer.colors.RED
        )
        raise typer.Exit(1)

    html = src_html.read_text(encoding="utf-8")
    wanted = set(re.findall(r'/assets/([A-Za-z0-9_.-]+\.(?:js|css))', html))
    # follow one level of chunk imports (the SPA imports React + the api module)
    seen: set[str] = set()
    queue = list(wanted)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        f = STATIC_DIR / "assets" / name
        if f.suffix == ".js" and f.is_file():
            for dep in re.findall(r'"\./([A-Za-z0-9_.-]+\.js)"', f.read_text(encoding="utf-8")):
                if dep not in seen:
                    queue.append(dep)

    dest = Path(dest)
    (dest / "assets").mkdir(parents=True, exist_ok=True)
    (dest / "index.html").write_text(html, encoding="utf-8")
    total = len(html.encode())
    for name in sorted(seen):
        f = STATIC_DIR / "assets" / name
        if f.is_file():
            shutil.copy2(f, dest / "assets" / name)
            total += f.stat().st_size

    (dest / "README.md").write_text(
        "# Modelith modeler app\n\n"
        "Static build of the modeler app (`/sme`). Serve this directory from any\n"
        "static host.\n\n"
        "It talks to a Modelith API for the model itself, so point it at one:\n\n"
        "    mdl glossary -m <model-dir> --port 4810\n\n"
        "and serve this directory behind the same origin (or proxy `/api/` to it).\n"
        "The model lives in git; this app is a client over it.\n",
        encoding="utf-8",
    )
    typer.secho(
        f"wrote {len(seen) + 2} files ({total // 1024} KB) to {dest}", fg=typer.colors.GREEN
    )
    typer.echo("serve that directory statically, and proxy /api/ to `mdl glossary`")


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
        except (CommandError, FileNotFoundError) as e:
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


# --- catalog (cross-repo model discovery, base-tier) -----------------------------


def _git_metadata(model_dir: Path) -> tuple[str | None, str | None]:
    """(remote, commit) from the model repo's git, or (None, None) if not a git repo.
    Used only by the git backend; overridable via --remote/--commit for odd CI."""
    import subprocess

    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(  # noqa: S603,S607 - trusted git argv
                ["git", *args], cwd=str(model_dir), capture_output=True, text=True
            )
            return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
        except Exception:  # noqa: BLE001
            return None

    return _git("remote", "get-url", "origin"), _git("rev-parse", "HEAD")


def _catalog_cache_dir() -> Path:
    """Local, disposable working clone of the catalog repo (gitignored / user cache).
    The catalog repo is the rebuildable index; this clone is throwaway."""
    return Path.home() / ".modelith" / "catalog-cache"


@catalog_app.command("publish")
def catalog_publish(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    backend: str = typer.Option(
        None, "--backend", help="Override backend (default: config, else git)"
    ),
    remote: str = typer.Option(None, "--remote", help="Override the model repo git remote"),
    commit: str = typer.Option(None, "--commit", help="Override the model repo commit SHA"),
    published_at: str = typer.Option(None, "--published-at", help="ISO 8601 UTC timestamp"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the entry; do not publish"),
) -> None:
    """Publish this model repo's summary to the cross-repo catalog (spec §1).

    Independent of `mdl gov publish` and the governance adapter — needs NO governance
    profile. Reads the model's config + git metadata, writes one manifest entry via the
    configured backend (default: a git manifest repo). Idempotent: republishing the same
    commit is a no-op. Run it in CI on merge to main.
    """
    from mdl_catalog import CatalogConfig, entry_from_repo, make_backend

    cfg = CatalogConfig.resolve(model_dir)
    if backend:
        cfg.backend = backend

    r, c = _git_metadata(model_dir)
    entry = entry_from_repo(
        model_dir,
        remote=remote or r,
        commit=commit or c,
        published_at=published_at or _now_iso(),
    )

    if dry_run:
        from mdl_core.yaml_io import dump_str

        typer.secho(f"catalog entry ({cfg.backend} backend, dry-run):", fg=typer.colors.CYAN)
        typer.echo(dump_str(entry.to_doc()))
        return

    if cfg.backend == "git" and not cfg.remote:
        typer.secho(
            "no catalog.remote configured in .modelith/catalog.yaml — writing to the "
            "local catalog cache only (set catalog.remote to publish to a shared repo).",
            fg=typer.colors.YELLOW,
        )
    try:
        be = make_backend(cfg, _catalog_cache_dir())
        be.publish(entry)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.secho(
        f"published {entry.model} @ {entry.commit or '?'} to the {cfg.backend} catalog",
        fg=typer.colors.GREEN,
    )


@catalog_app.command("list")
def catalog_list(
    model_dir: Path = typer.Option(Path("."), "--model-dir", "-m"),
    query: str = typer.Option("", "--search", "-q", help="Filter by free-text query"),
) -> None:
    """List models in the catalog (optionally filtered)."""
    from mdl_catalog import CatalogConfig, make_backend

    cfg = CatalogConfig.resolve(model_dir)
    be = make_backend(cfg, _catalog_cache_dir())
    if cfg.backend == "git" and hasattr(be, "ensure_clone"):
        be.ensure_clone()
    entries = be.search(query) if query else be.list()
    if not entries:
        typer.secho("catalog is empty (publish a model with `mdl catalog publish`)",
                    fg=typer.colors.YELLOW)
        return
    for e in entries:
        layers = f"  [{', '.join(e.ontology_layers)}]" if e.ontology_layers else ""
        typer.echo(f"  {e.model:28} {e.commit or '':10}{layers}")


@catalog_app.command("serve")
def catalog_serve(
    config_dir: Path = typer.Option(
        Path("."), "--config-dir", "-c",
        help="Dir to resolve .modelith/catalog.yaml from (default: cwd)",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(4811, "--port", "-p"),
) -> None:
    """Browse the cross-repo catalog (spec §4). Runs ONE LEVEL ABOVE any model repo:
    reads the configured backend (default: a git catalog repo, cloned into a local
    cache), and serves a read-only, searchable list of published models. Each entry
    links out to its source repo@commit — model content is never embedded."""
    from mdl_catalog import CatalogConfig, make_backend
    from mdl_server.catalog_app import serve_catalog

    cfg = CatalogConfig.resolve(config_dir)
    be = make_backend(cfg, _catalog_cache_dir())
    if cfg.backend == "git" and hasattr(be, "ensure_clone"):
        be.ensure_clone()  # freshen the local clone before serving
    typer.secho(
        f"Modelith catalog ({cfg.backend}): http://{host}:{port}/catalog",
        fg=typer.colors.CYAN,
    )
    serve_catalog(be, host=host, port=port)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def main() -> None:
    """Console entry point. Wraps the Typer app so anonymous, opt-in telemetry can
    record the command + exit code exactly once, WITHOUT changing exit behavior.

    `app(standalone_mode=False)` returns instead of raising SystemExit, so we can
    read the true exit code (including from sub-app commands, which a root callback
    cannot see) and re-raise it faithfully. Telemetry is entirely fail-soft; it
    never alters the documented exit codes (0/1/2/3/4).
    """
    # Catch Typer's control-flow exceptions by the classes Typer ACTUALLY raises. This is
    # version-sensitive: older Typer vendors its own Click, so a parse error like
    # `NoSuchOption` derives from `typer._click.exceptions.UsageError` — a DIFFERENT class
    # from `click.exceptions.UsageError`, so catching the click one would miss it and the
    # error would fall through to the generic handler as a traceback (exit 1) instead of a
    # clean usage message (exit 2). Newer Typer dropped the private module and uses plain
    # Click. Resolve each class from typer._click first, then click — so the wrapper is
    # correct on both, and a private-module rename can't crash the import (no bare-except
    # hides a real exit code).
    import click.exceptions as _click_base

    try:
        from typer._click import exceptions as _typer_exc  # type: ignore
    except Exception:  # noqa: BLE001 - private module absent on newer Typer
        _typer_exc = _click_base

    def _exc(name: str):
        return getattr(_typer_exc, name, None) or getattr(_click_base, name)

    class click_exc:  # namespace shim: click_exc.Exit / .Abort / .UsageError
        Exit = _exc("Exit")
        Abort = _exc("Abort")
        UsageError = _exc("UsageError")

    # Ergonomic alias: accept `mdl reverse config <sub>` as a spelling of the
    # `mdl reverse-config <sub>` group. `reverse` is a leaf command (it reverse-engineers
    # a warehouse) so it can't ALSO be a Typer group without breaking `mdl reverse
    # --project …`; instead we rewrite argv here, before Typer parses. Only the exact
    # `reverse config` pair is rewritten — `mdl reverse --project` (no `config` token) is
    # untouched and keeps working.
    if len(sys.argv) >= 3 and sys.argv[1] == "reverse" and sys.argv[2] == "config":
        sys.argv[1:3] = ["reverse-config"]

    from mdl_cli import telemetry

    command = telemetry.sanitize_command(sys.argv[1:])
    # Resolve consent BEFORE running, so a first-run "yes" lets this very command
    # (e.g. the user's first `mdl init`) emit. Non-interactive runs stay silent.
    telemetry.ensure_consent()

    code = 0
    try:
        # In standalone_mode=False, Click RETURNS the command's exit code (an int)
        # rather than raising SystemExit — so a `typer.Exit(1)` surfaces as a return
        # value of 1, not an exception. None means success. Only genuine edge cases
        # (Abort, UsageError, a bare sys.exit) still raise, handled below.
        rv = app(standalone_mode=False)
        if isinstance(rv, int):
            code = rv
    except click_exc.Exit as e:  # belt-and-suspenders: an explicitly raised Exit
        code = e.exit_code
    except click_exc.Abort:
        typer.echo("Aborted.", err=True)
        code = 1
    except click_exc.UsageError as e:
        # standalone_mode=False suppresses Click's own message — echo it ourselves.
        e.show()
        code = e.exit_code if e.exit_code is not None else 2
    except SystemExit as e:  # a command that called sys.exit directly
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception:
        telemetry.record(command, 1)
        raise  # keep the traceback for genuine bugs
    telemetry.record(command, code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
