"""The Modelith MCP server (stdio).

Seven tools, mirroring the AI spec. Reads (`list_entities`, `get_entity`,
`search_ontology`, `get_model_context`, `validate`) come from the shared query
layer; writes (`create_entity`, `update_entity`) go through
`mdl_core.commands.apply_command` so they are validated and fingerprint-guarded
exactly like a canvas or CLI edit.

Direct local write is intentional (spec §1): this runs against the engineer's own
checkout, a different trust boundary than the SME app's propose-as-PR flow.

The server is bound to ONE model directory, passed at startup (`--repo`). A
programmatically registered MCP server has no implicit repo scope the way a
committed `.vscode/mcp.json` would, so VS Code passes the workspace folder through.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from mdl_core import query
from mdl_core.commands import CommandError, StaleModelError, apply_command
from mdl_core.diagnostics import Severity
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate as run_validate


def build_server(repo_dir: Path) -> FastMCP:
    """Assemble the MCP server bound to `repo_dir`. Split out from `run()` so tests
    can drive the tools without stdio."""
    repo_dir = repo_dir.resolve()
    mcp = FastMCP("modelith")

    def _model():
        # Reload per call: an agent's own writes (and a human editing alongside it)
        # must be visible to the next read. Cheap on the demo; the LSP/server carry
        # their own caches for the hot paths.
        return ModelRepo.load(repo_dir).model

    # --- reads ------------------------------------------------------------------

    @mcp.tool()
    def list_entities(subject_area: str | None = None) -> str:
        """List the model's logical entities (name, definition, attribute count,
        subject area). Grounding: call this first to see what already exists.
        Optionally scope to a subject area by name or ULID."""
        return _json(query.list_entities(_model(), subject_area))

    @mcp.tool()
    def get_entity(name: str) -> str:
        """Full detail for one entity by name (or ULID): its attributes with types
        and ontology alignment, its key groups (pk / unique / alternate), the
        conceptual layer, and the relationships it takes part in. Returns an error
        object if no entity matches."""
        hit = query.get_entity(_model(), name)
        if hit is None:
            return _json({"error": f"no entity named {name!r}"})
        return _json(hit)

    @mcp.tool()
    def get_model_context() -> str:
        """A condensed summary of the whole model — project, counts, subject areas
        with their entities, and every relationship — sized for a chat context
        window. Use it to orient before answering a broad question."""
        return _json(query.get_model_context(_model()))

    @mcp.tool()
    def search_ontology(text: str, within: str | None = None, limit: int = 10) -> str:
        """Search the model's configured industry/enterprise vocabularies for terms
        matching `text` (e.g. to find a FIBO class to align an entity to). `within`
        scopes to a single vocabulary id (see the source's ontology list). Returns
        ranked hits with their prefixed IRI, label and definition."""
        reg = _registry(repo_dir)
        if reg is None:
            return _json({"warning": "no ontology sources configured", "results": []})
        results = reg.search(text, within=within, limit=limit)
        return _json(
            {
                "results": [
                    {
                        "prefixed": r.prefixed,
                        "iri": r.iri,
                        "label": r.label,
                        "definition": r.definition,
                        "source": r.source,
                    }
                    for r in results
                ]
            }
        )

    @mcp.tool()
    def validate() -> str:
        """Validate the model (schema, references, ontology rules, naming) and return
        the diagnostics. Use this before writing, or to check the model's health."""
        diags = run_validate(_model())
        return _json(
            {
                "ok": not diags.has(Severity.error),
                "diagnostics": [
                    {"code": d.code, "severity": d.severity.value, "message": d.message}
                    for d in diags.items
                ],
            }
        )

    @mcp.tool()
    def explain_drift(manifest: str | None = None, target: str | None = None) -> str:
        """Compare the committed model to a compiled dbt manifest and return the drift,
        annotated per item with whether it is safely reconcilable and the concrete
        action. Severity (breaking / additive / cosmetic) is authoritative — it comes
        from the engine, not from you; never re-derive it. Breaking items are never
        auto-reconcilable and need a human decision.

        `manifest` defaults to <repo>/target/manifest.json; `target` defaults to the
        project's dbt_target. Returns an error object if no manifest is found."""
        from mdl_reverse.drift import compute_drift
        from mdl_reverse.explain import explain_report
        from mdl_reverse.manifest import read_manifest
        from mdl_reverse.reconcile import model_name_to_ulid

        repo = ModelRepo.load(repo_dir)
        tgt = target or repo.model.config.dbt_target or "duckdb_dev"
        man_path = Path(manifest) if manifest else repo_dir / "target" / "manifest.json"
        if not man_path.is_absolute():
            man_path = repo_dir / man_path
        try:
            proj = read_manifest(man_path)
        except FileNotFoundError:
            return _json(
                {"error": f"no dbt manifest at {man_path} — run `dbt compile` first"}
            )
        report = compute_drift(repo.model, proj, tgt)
        name_to_le = model_name_to_ulid(repo, tgt)

        def _file_for(model_name: str) -> str | None:
            le_id = name_to_le.get(model_name)
            return repo.path_for_ulid(le_id) if le_id else None

        return _json(explain_report(report, _file_for))

    # --- writes (validated + fingerprint-guarded via apply_command) -------------

    @mcp.tool()
    def create_entity(
        name: str,
        definition: str | None = None,
        subject_area: str | None = None,
        layer: str | None = None,
    ) -> str:
        """Create a conceptual + logical entity pair. Writes to the local model on
        disk (validated before it lands). `subject_area` is a subject-area ULID;
        `layer` is one of industry|core|domain|specialised. Returns the new entity's
        ULID or an error object."""
        return _write(
            repo_dir,
            "create_entity",
            {
                "name": name,
                "definition": definition,
                "subject_area": subject_area,
                "layer": layer,
            },
        )

    @mcp.tool()
    def update_entity(name: str, changes: dict[str, Any]) -> str:
        """Update an existing entity by name (or ULID). `changes` may contain:
        `definition` (str), `rename` (str, the new name), `add_attribute`
        ({name, domain?, definition?, nullable?}), or `subject_area` (ULID). Each is
        applied through the validated command engine; the whole update is rejected
        (nothing written) if any step is invalid. Returns ok or an error object."""
        le = query._entity_by_name(_model(), name)  # noqa: SLF001 - shared helper
        if le is None:
            return _json({"error": f"no entity named {name!r}"})
        return _apply_updates(repo_dir, le.id, le.realises, changes)

    return mcp


# --- helpers -------------------------------------------------------------------


def _apply_updates(
    repo_dir: Path, entity_id: str, conceptual_id: str | None, changes: dict[str, Any]
) -> str:
    """Map the `update_entity` change bag onto field-scoped commands. Applied in a
    fixed order; each goes through apply_command, so validation runs after every
    step and a bad change stops the sequence. Definition and subject area live on the
    CONCEPTUAL node, so those ops take the conceptual ULID; rename and attributes are
    logical."""
    applied: list[str] = []
    try:
        if "definition" in changes:
            if conceptual_id is None:
                return _json({"error": "entity has no conceptual layer to set a definition on"})
            apply_command(
                repo_dir,
                "set_definition",
                {"id": conceptual_id, "definition": changes["definition"]},
            )
            applied.append("definition")
        if "rename" in changes:
            apply_command(repo_dir, "rename_entity", {"id": entity_id, "name": changes["rename"]})
            applied.append("rename")
        if "subject_area" in changes:
            if conceptual_id is None:
                return _json({"error": "entity has no conceptual layer to home in a subject area"})
            apply_command(
                repo_dir,
                "set_subject_area",
                {"id": conceptual_id, "subject_area": changes["subject_area"]},
            )
            applied.append("subject_area")
        if "add_attribute" in changes:
            attr = changes["add_attribute"]
            if not isinstance(attr, dict) or "name" not in attr:
                return _json({"error": "add_attribute must be an object with at least a name"})
            apply_command(repo_dir, "add_attribute", {"entity_id": entity_id, **attr})
            applied.append(f"add_attribute:{attr['name']}")
    except StaleModelError as e:
        return _json({"error": f"model changed on disk mid-update: {e}", "applied": applied})
    except (CommandError, FileNotFoundError, ValueError) as e:
        return _json({"error": str(e), "applied": applied})
    if not applied:
        return _json({"error": "no recognised keys in changes", "applied": []})
    return _json({"ok": True, "applied": applied})


def _write(repo_dir: Path, op: str, payload: dict) -> str:
    """Run one write command and shape the result (created id + diagnostics), or an
    error object the agent can read."""
    try:
        result = apply_command(repo_dir, op, {k: v for k, v in payload.items() if v is not None})
    except StaleModelError as e:
        return _json({"error": f"model changed on disk: {e}"})
    except (CommandError, FileNotFoundError, ValueError) as e:
        return _json({"error": str(e)})
    return _json(
        {
            "ok": result.ok,
            "created_id": result.created_id,
            # CommandResult.diagnostics is already a list of {code, severity, message}
            # dicts (validated on the reload apply_command does after each write).
            "diagnostics": result.diagnostics,
            "error": result.error,
        }
    )


@lru_cache(maxsize=8)
def _registry(repo_dir: Path):
    """Build + load the ontology registry once per repo dir. Returns None when the
    model has no ontology stack configured (search then returns an empty result)."""
    try:
        from mdl_ontology import build_registry

        model = ModelRepo.load(repo_dir).model
        stack = getattr(model.config, "ontology_stack", None)
        if not stack:
            return None
        reg = build_registry(repo_dir, stack)
        reg.load()
        return reg
    except Exception:  # noqa: BLE001 - a bad ontology config must not crash the server
        return None


def _json(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


def run(repo_dir: Path) -> None:
    """Start the server on stdio (the transport VS Code / Claude Desktop use)."""
    build_server(repo_dir).run()
