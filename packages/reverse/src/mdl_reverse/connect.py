"""Live-database reverse engineering via dbt as a connection layer (spec R2).

Modelith writes NO database drivers. dbt already has a mature, warehouse-covering
adapter ecosystem, so the live-DB path shells out to the user's dbt to run Modelith's own
dispatched `mdl_get_constraints` macro, which reads information_schema (and the per-adapter
constraint catalog) for the WHOLE picture in one call: column names + types + nullability
AND real declared PK / FK / unique (erwin parity). No dbt-codegen dependency. Modelith
normalises the result to a ManifestProjection through the same reverse() engine a dbt
manifest or DDL uses.

Design notes that matter:
- **No user-project mutation, no macros/ discovery.** Every `dbt run-operation` runs
  against a THROWAWAY dbt project we build in a temp dir, whose `dbt_project.yml` points
  `macro-paths` at our copied macros and whose `profile:` matches the user's. The user's
  `profiles.yml` is passed with `--profiles-dir`; nothing in their repo changes. A team
  with only a warehouse (no dbt project) works identically.
- **Credentials never touch Modelith.** We only ever pass `--profiles-dir`; secrets live
  in the user's profiles.yml as `env_var(...)` and resolve inside dbt.
- **The user's dbt, not ours.** `mdl` is an isolated tool install; dbt + the adapter live
  in the user's environment. So we shell out to `dbt` on PATH (never import dbt in-process).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mdl_core.yaml_io import dump_file, load_file
from mdl_reverse.schema_reader import constraint_macro_sql, parse_constraints_output

# A live warehouse connect can be slow (cold Snowflake/BigQuery); allow generously.
DEFAULT_TIMEOUT = 300.0

# The adapters we scaffold a profile template for. Value = the pip package to install.
ADAPTER_PACKAGES = {
    "duckdb": "dbt-duckdb",
    "postgres": "dbt-postgres",
    "snowflake": "dbt-snowflake",
    "bigquery": "dbt-bigquery",
    "redshift": "dbt-redshift",
    "databricks": "dbt-databricks",
}


class ConnectError(RuntimeError):
    """A live-connect step failed with a message safe to show the user."""


@dataclass
class RunResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class DbtProbe:
    dbt_found: bool
    adapter_found: bool
    install_cmd: str | None  # the exact `pip install …` to run, or None if all present

    @property
    def ready(self) -> bool:
        return self.dbt_found and self.adapter_found


@dataclass
class ConnectResult:
    ok: bool
    message: str


@dataclass
class ScaffoldResult:
    path: Path
    env_vars: list[str] = field(default_factory=list)  # env vars the user must set


# --- subprocess boundary -----------------------------------------------------


def _run(argv: list[str], cwd: Path | None = None, timeout: float = DEFAULT_TIMEOUT) -> RunResult:
    """Run a command, capturing output, never raising on a non-zero exit (callers branch
    on `.ok`). A missing binary or a timeout becomes a RunResult with a clear stderr."""
    try:
        proc = subprocess.run(  # noqa: S603 - trusted argv (we build it), no shell
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return RunResult(proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError:
        return RunResult(127, "", f"command not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        return RunResult(124, "", f"timed out after {timeout:.0f}s: {' '.join(argv[:2])}")


def _dbt() -> str:
    """The dbt executable. Honours MDL_DBT (for tests / an explicit path), else `dbt`."""
    return os.environ.get("MDL_DBT", "dbt")


# --- probe -------------------------------------------------------------------


def probe_dbt(adapter: str) -> DbtProbe:
    """Check that `dbt` and the chosen `dbt-<adapter>` are available in the USER's
    environment (`dbt --version` lists installed adapters). Returns the exact pip command
    to run for whatever is missing — Modelith never auto-installs into its own isolated
    tool env. No dbt-codegen needed: our self-contained macro reads information_schema
    directly for both columns and constraints."""
    ver = _run([_dbt(), "--version"], timeout=30.0)
    dbt_found = ver.ok or "installed" in (ver.stdout + ver.stderr).lower()
    blob = (ver.stdout + ver.stderr).lower()
    adapter_found = dbt_found and adapter.lower() in blob
    missing: list[str] = []
    if not dbt_found:
        missing.append("dbt-core")
    # suggest the adapter whenever it isn't confirmed present (installing dbt-<adapter>
    # pulls dbt-core too, so a single line covers a machine with no dbt at all).
    if not adapter_found:
        missing.append(ADAPTER_PACKAGES.get(adapter, f"dbt-{adapter}"))
    install_cmd = ("pip install " + " ".join(missing)) if missing else None
    return DbtProbe(dbt_found=dbt_found, adapter_found=adapter_found, install_cmd=install_cmd)


# --- profiles.yml scaffold ---------------------------------------------------

# Minimal, non-secret connection templates. Every secret is an env_var placeholder — the
# user sets the env var; dbt resolves it at connect time. Modelith never sees a credential.
_PROFILE_TEMPLATES = {
    "duckdb": lambda f: {"type": "duckdb", "path": f.get("path", "warehouse.duckdb")},
    "postgres": lambda f: {
        "type": "postgres",
        "host": f.get("host", "localhost"),
        "port": int(f.get("port", 5432)),
        "user": f.get("user", "{{ env_var('MDL_POSTGRES_USER') }}"),
        "password": "{{ env_var('MDL_POSTGRES_PASSWORD') }}",
        "dbname": f.get("database", "postgres"),
        "schema": f.get("schema", "public"),
    },
    "snowflake": lambda f: {
        "type": "snowflake",
        "account": f.get("account", "{{ env_var('MDL_SNOWFLAKE_ACCOUNT') }}"),
        "user": f.get("user", "{{ env_var('MDL_SNOWFLAKE_USER') }}"),
        "password": "{{ env_var('MDL_SNOWFLAKE_PASSWORD') }}",
        "role": f.get("role", "{{ env_var('MDL_SNOWFLAKE_ROLE') }}"),
        "database": f.get("database", "{{ env_var('MDL_SNOWFLAKE_DATABASE') }}"),
        "warehouse": f.get("warehouse", "{{ env_var('MDL_SNOWFLAKE_WAREHOUSE') }}"),
        "schema": f.get("schema", "PUBLIC"),
    },
    "bigquery": lambda f: {
        "type": "bigquery",
        "method": "oauth",
        "project": f.get("database", "{{ env_var('MDL_BIGQUERY_PROJECT') }}"),
        "dataset": f.get("schema", "{{ env_var('MDL_BIGQUERY_DATASET') }}"),
        "location": f.get("location", "US"),
    },
    "redshift": lambda f: {
        "type": "redshift",
        "host": f.get("host", "{{ env_var('MDL_REDSHIFT_HOST') }}"),
        "port": int(f.get("port", 5439)),
        "user": f.get("user", "{{ env_var('MDL_REDSHIFT_USER') }}"),
        "password": "{{ env_var('MDL_REDSHIFT_PASSWORD') }}",
        "dbname": f.get("database", "dev"),
        "schema": f.get("schema", "public"),
    },
    "databricks": lambda f: {
        "type": "databricks",
        "host": f.get("host", "{{ env_var('MDL_DATABRICKS_HOST') }}"),
        "http_path": f.get("http_path", "{{ env_var('MDL_DATABRICKS_HTTP_PATH') }}"),
        "token": "{{ env_var('MDL_DATABRICKS_TOKEN') }}",
        "catalog": f.get("catalog", "main"),
        "schema": f.get("schema", "default"),
    },
}


def _env_vars_in(obj) -> list[str]:
    """Collect the env_var names referenced in a profile output block, in order."""
    import re

    out: list[str] = []
    for v in obj.values():
        m = re.search(r"env_var\('([^']+)'\)", str(v))
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def scaffold_profile(
    adapter: str,
    dest: Path,
    *,
    profile_name: str = "warehouse",
    target: str = "dev",
    fields: dict | None = None,
) -> ScaffoldResult:
    """Write a `profiles.yml` for an adapter with the caller's NON-SECRET fields filled and
    every secret as an `env_var(...)` placeholder. Returns the path + the env vars the user
    must set. Never writes a literal credential. Used by `mdl reverse --init-profile` and
    the wizard."""
    adapter = adapter.lower()
    if adapter not in _PROFILE_TEMPLATES:
        raise ConnectError(
            f"unknown adapter {adapter!r}; supported: {', '.join(sorted(_PROFILE_TEMPLATES))}"
        )
    output = _PROFILE_TEMPLATES[adapter](fields or {})
    profiles = {profile_name: {"target": target, "outputs": {target: output}}}
    dest = Path(dest)
    if dest.is_dir():
        dest = dest / "profiles.yml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dump_file(dest, profiles)
    return ScaffoldResult(path=dest, env_vars=_env_vars_in(output))


# --- the throwaway dbt project ----------------------------------------------


def _profile_name(profiles_dir: Path) -> str:
    """The top-level profile name in the user's profiles.yml (the throwaway project's
    `profile:` must match it). Falls back to 'warehouse' if it can't be read."""
    p = Path(profiles_dir) / "profiles.yml"
    try:
        data = load_file(p) or {}
        names = [k for k in data if k != "config"]
        return names[0] if names else "warehouse"
    except (OSError, ValueError):
        return "warehouse"


@contextmanager
def _throwaway_project(profiles_dir: Path):
    """Build a minimal throwaway dbt project pointed at OUR introspection macro and the
    user's profile, yield its dir, and remove it after. This is why we never touch the
    user's repo or hunt for their macros/ dir — dbt discovers our macro because we own the
    project it runs in. `profile:` is read from the user's profiles.yml so `run-operation`
    resolves a connection. The macro is self-contained (reads information_schema directly),
    so the project needs no packages and no `dbt deps`."""
    proj = Path(tempfile.mkdtemp(prefix="mdl-connect-"))
    try:
        macros = proj / "macros"
        macros.mkdir()
        (macros / "mdl_get_constraints.sql").write_text(constraint_macro_sql(), encoding="utf-8")
        dump_file(
            proj / "dbt_project.yml",
            {
                "name": "mdl_connect",
                "version": "1.0",
                "config-version": 2,
                "profile": _profile_name(profiles_dir),
                "macro-paths": ["macros"],
            },
        )
        yield proj
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def _base_args(profiles_dir: Path, target: str | None) -> list[str]:
    args = ["--profiles-dir", str(Path(profiles_dir).resolve())]
    if target:
        args += ["--target", target]
    return args


# --- connection test ---------------------------------------------------------


def dbt_debug(profiles_dir: Path, *, target: str | None = None) -> ConnectResult:
    """Run `dbt debug` against a throwaway project + the user's profiles.yml — dbt's own
    connection validator (the wizard's "test connection"). Surfaces adapter/credential/
    network errors in dbt's own words."""
    with _throwaway_project(profiles_dir) as proj:
        r = _run([_dbt(), "debug", *_base_args(profiles_dir, target)], cwd=proj, timeout=120.0)
    msg = (r.stdout + r.stderr).strip()
    ok = r.ok and "All checks passed" in r.stdout
    if not ok and not msg:
        msg = "dbt debug failed with no output — is dbt installed and the adapter present?"
    return ConnectResult(ok=ok, message=msg)


# --- introspection -----------------------------------------------------------


def _constraints_args(schema: str | None, database: str | None) -> dict:
    """Args for our mdl_get_constraints macro (`schema` / `database`)."""
    args: dict = {}
    if schema:
        args["schema"] = schema
    if database:
        args["database"] = database
    return args


def introspect_constraints(
    profiles_dir: Path,
    *,
    schema: str,
    database: str | None = None,
    select: str | None = None,
    target: str | None = None,
) -> dict:
    """Shell Modelith's `mdl_get_constraints` macro → parsed columns+types+PK/FK/unique/
    nullable JSON. This is the WHOLE live introspection: the macro reads
    information_schema.columns for the spine (names + types + nullability) AND the
    constraint catalog for keys, so no dbt-codegen is needed. `select` (a comma list) maps
    to a table filter the macro applies. Raises ConnectError on a hard failure so the CLI
    can surface it; an empty/failed catalog returns {} (the reverse then has nothing to
    lift, and the CLI warns)."""
    with _throwaway_project(profiles_dir) as proj:
        args = _constraints_args(schema, database)
        r = _run(
            [
                _dbt(), "--quiet", "run-operation", "mdl_get_constraints",
                "--args", json.dumps(args), *_base_args(profiles_dir, target),
            ],
            cwd=proj,
        )
    if not r.ok:
        raise ConnectError(
            "Live introspection failed. Check the connection (mdl reverse "
            "runs `dbt debug`) and that the schema exists.\n"
            f"{(r.stderr or r.stdout).strip()}"
        )
    out = parse_constraints_output(r.stdout)
    # `--select` filters tables client-side (the macro reads the whole schema). A comma
    # list of table names; keep only those, so a large warehouse can be scoped.
    if select and out:
        wanted = {t.strip() for t in select.split(",") if t.strip()}
        out = {k: v for k, v in out.items() if k in wanted}
    return out


def introspect_catalog(
    dbt_project_dir: Path, profiles_dir: Path, *, target: str | None = None
) -> Path:
    """Fallback for teams with a BUILT dbt project: `dbt docs generate` → target/catalog.json
    (the existing catalog-merge path). Runs in the user's real project (it needs their
    models), not the throwaway one. Carries no keys — the constraint macro still overlays
    them. Raises ConnectError on failure."""
    dbt_project_dir = Path(dbt_project_dir)
    r = _run(
        [_dbt(), "docs", "generate", *_base_args(profiles_dir, target)],
        cwd=dbt_project_dir,
    )
    catalog = dbt_project_dir / "target" / "catalog.json"
    if not r.ok or not catalog.exists():
        detail = (r.stderr or r.stdout).strip()
        raise ConnectError(f"dbt docs generate failed or produced no catalog.json.\n{detail}")
    return catalog
