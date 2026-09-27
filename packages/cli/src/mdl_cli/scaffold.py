"""`mdl init` scaffold: a minimal but valid model repo (spec §2.2)."""

from __future__ import annotations

from pathlib import Path

from mdl_core.ids import new_ulid

_GITIGNORE = """\
# dbt build artifacts (generated projects live here by default)
target/
dbt_packages/
logs/
# Fetched ontology layers — pinned by .mdl/lock.yaml, not committed (spec §3).
# Run `mdl ontology fetch` to repopulate. Like node_modules to package-lock.json.
.mdl/ontology-cache/
# Reverse/generation working state (the committed lock/decisions stay tracked).
.mdl/state/
"""


def scaffold(root: Path, *, project_name: str = "modelith_model") -> dict[str, str]:
    """Create the directory shape and seed a tiny conceptual+logical example.
    Returns {relative_path: content} for everything written."""
    sa_id = new_ulid()
    ce_id = new_ulid()
    le_id = new_ulid()
    attr_id = new_ulid()

    files: dict[str, str] = {}

    files["mdl-project.yaml"] = f"""\
# Modelith project config (spec §2.2)
name: {project_name}
dbt_target: duckdb_dev
platform_targets:
  - duckdb_dev
# Ontology sources plug in by declaration. A source is either a local file
# vocabulary (default type: local) or a remote resolver browsed live for
# autocomplete/search (types: ols, ols-compatible, ontoportal, collibra). FIBO is
# only one example — ACORD / FHIR / ISO 20022 / your own plug in the same way.
# ontology_stack:
#   - name: fibo                       # local file vocabulary
#     layer: industry
#     format: turtle
#     path: ontologies/industry/fibo/2024.03
#     modules: [fnd, fbc]
#     prefixes:
#       fibo-fnd-pty-pty: "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/"
#   - name: ols                        # public OLS4 (no auth) — live search only
#     layer: industry
#     type: ols
#     url: https://www.ebi.ac.uk/ols4/api
#   - name: bioportal                  # OntoPortal/BioPortal (needs an API key)
#     layer: domain
#     type: ontoportal
#     url: https://data.bioontology.org
#     apikey_env: BIOPORTAL_APIKEY
#   - name: collibra                   # Collibra Ontology Domains (bearer token)
#     layer: core
#     type: collibra
#     url: https://acme.collibra.com
#     token_env: COLLIBRA_TOKEN
#     domain_types: [Ontology]
ontology_stack: []
naming:
  logical_case: snake
  physical_case: upper_snake
  abbreviations: {{}}
"""

    files["conceptual/subject-areas/counterparty.yaml"] = f"""\
id: {sa_id}
kind: subject_area
name: Counterparty Management
definition: >
  Everything about the parties the firm transacts with.
"""

    files["conceptual/entities/counterparty.yaml"] = f"""\
id: {ce_id}
kind: conceptual_entity
name: Counterparty
subject_area: {sa_id}
definition: >
  A legal person with whom the firm has or may have a contractual obligation.
stewardship:
  owner: risk-data-office
  steward: a.hough
synonyms: [Counterparty, CPTY]
"""

    files["logical/domains/identifier_bigint.yaml"] = f"""\
id: {new_ulid()}
kind: domain
name: identifier_bigint
base_type: bigint
definition: A surrogate or business identifier stored as a 64-bit integer.
"""

    files["logical/entities/counterparty.yaml"] = f"""\
id: {le_id}
kind: logical_entity
name: counterparty
realises: {ce_id}
attributes:
  - id: {attr_id}
    name: counterparty_id
    domain: identifier_bigint
    role: business_key
    nullable: false
"""

    files[".gitignore"] = _GITIGNORE

    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    files.update(_scaffold_skeleton(root))
    return files


def find_dbt_project(root: Path) -> Path | None:
    """The directory of an existing dbt project under `root` (the one holding
    dbt_project.yml), or None. Skips build/vendor dirs. Shallowest match wins, so a
    project at the root or a top-level dir beats one nested in a package. Used so an
    import into a workspace that ALREADY has a dbt project never scaffolds a second,
    throwaway DuckDB project on top of it."""
    skip = {"node_modules", "dbt_packages", "target", ".venv", ".git", "__pycache__"}
    hits = [p for p in root.rglob("dbt_project.yml") if not (set(p.parts) & skip)]
    if not hits:
        return None
    hits.sort(key=lambda p: len(p.parts))
    return hits[0].parent


def _scaffold_skeleton(root: Path) -> dict[str, str]:
    """The tool-owned skeleton every project needs, WITHOUT any seed model objects:
    the .gitignore, the empty .mdl/state/ dir, and a pinned .mdl/lock.yaml. Shared by
    `scaffold` (which adds a seed example) and `bootstrap_project` (which lets an import
    supply the objects). Idempotent — safe to call on a dir that already has some of it."""
    written: dict[str, str] = {}
    root.mkdir(parents=True, exist_ok=True)

    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(_GITIGNORE, encoding="utf-8")
    written[".gitignore"] = _GITIGNORE

    # Empty state dir the tool expects.
    (root / ".mdl" / "state").mkdir(parents=True, exist_ok=True)

    # Pin spec versions (spec §2.2, §13.1). Import lazily so `core`/scaffold stay
    # free of an ontology dependency at module load.
    from mdl_ontology.lock import Lock

    lock = root / ".mdl" / "lock.yaml"
    if not lock.exists():
        Lock().save(root)
    written[".mdl/lock.yaml"] = lock.read_text(encoding="utf-8")
    return written


class Bootstrap:
    """Result of ``bootstrap_project``.

    - ``model_root`` — where the importer writes its object YAMLs.
    - ``written`` — human-readable scaffolded paths, for the CLI summary.
    - ``dbt_project_dir`` — the dbt project the model config should point at
      (relative to ``root``), or None when there is no dbt project. Set to a
      DISCOVERED existing project when one is present (so no second, throwaway DuckDB
      project is created), else the freshly scaffolded ``transform/warehouse``.
    """

    def __init__(
        self, model_root: Path, written: list[str], dbt_project_dir: str | None
    ) -> None:
        self.model_root = model_root
        self.written = written
        self.dbt_project_dir = dbt_project_dir


def bootstrap_project(
    root: Path,
    *,
    project_name: str,
    layout: str = "model",
    dbt_target: str = "duckdb_dev",
    existing: bool = False,
) -> Bootstrap:
    """Lay down the project skeleton an IMPORT (erwin, etc.) writes its objects into,
    without any seed example objects.

    - ``existing`` True means the target already holds a Modelith project (an
      ``mdl-project.yaml`` or a ``.mdl/`` dir): scaffold NOTHING structural and RESPECT
      what is there — the caller merges objects and the writer preserves the user's config.
    - ``layout == "none"`` writes no skeleton (model objects only, pre-scaffold behaviour).
    - ``layout == "model"`` writes the shared skeleton (.gitignore, .mdl/state, .mdl/lock.yaml).
    - ``layout == "workspace"`` also lays down transform/warehouse + CODEOWNERS + the
      .code-workspace + merge drivers (collab §2.1), with the model under ``root/model``.
      BUT if a dbt project already exists anywhere under ``root``, that project is reused
      (config points at it) and NO throwaway DuckDB dbt project / profiles.yml is written.
    """
    if existing or layout == "none":
        nested = existing and (root / "model" / "mdl-project.yaml").exists()
        model_root = root / "model" if nested else root
        # Respect any dbt project the workspace already has (don't invent a target).
        found = find_dbt_project(root)
        dbt_dir = _rel(found, root) if found else None
        return Bootstrap(model_root, [], dbt_dir)

    if layout == "workspace":
        from mdl_cli.collab import scaffold_workspace_skeleton

        model_root = root / "model"
        # A dbt project already in the workspace wins — reuse it, don't scaffold a dumb
        # DuckDB one on top. Only when there is none do we lay down transform/warehouse.
        found = find_dbt_project(root)
        dbt_dir = _rel(found, root) if found else "transform/warehouse"
        written = scaffold_workspace_skeleton(
            root,
            project_name,
            dbt_target=dbt_target,
            existing_dbt=found is not None,
            dbt_project_dir=dbt_dir,
        )
        written.extend(f"model/{p}" for p in _scaffold_skeleton(model_root))
        return Bootstrap(model_root, written, dbt_dir)

    # layout == "model" (default): a plain model repo skeleton at root. Point at an
    # existing dbt project if there is one; a plain model doesn't scaffold its own.
    found = find_dbt_project(root)
    dbt_dir = _rel(found, root) if found else None
    return Bootstrap(root, list(_scaffold_skeleton(root)), dbt_dir)


def _rel(target: Path, root: Path) -> str:
    """`target` relative to `root` as a forward-slash string, or the path as-is when it
    is not under `root` (e.g. a sibling dbt project)."""
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return target.as_posix()
