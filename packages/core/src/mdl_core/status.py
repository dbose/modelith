"""Workspace state assessment and ranked next-actions.

The single source of truth for "where is this modeling workspace, and what should the
user do next". Both the CLI (`mdl status`) and the VS Code panel consume this so the
guidance is identical across surfaces, and the docs generator renders the same summary.

It composes EXISTING state sources only and never mutates anything:
  - is there a compiled dbt manifest to reverse / drift against,
  - are there reversed logical models yet,
  - how many decisions are pending review, and at what confidence,
  - model hygiene: entities/attributes missing a definition, ontology refs still
    proposed (unaligned terms),
  - docs freshness: does a generated site exist and is it up to date.

Everything is derived, so calling it repeatedly is cheap and safe.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from mdl_core.ir import Model

# Where `mdl docs generate` writes by default; used for the docs-freshness signal.
DEFAULT_DOCS_REL = "target/mdl-docs/index.html"


@dataclass
class NextAction:
    """One recommended step. `command` is a VS Code command id the panel can invoke
    (also a stable action id for the CLI); `cli` is the terminal equivalent to show."""

    id: str
    title: str
    detail: str
    severity: str  # "info" | "recommended" | "attention"
    command: str | None = None
    cli: str | None = None


@dataclass
class WorkspaceStatus:
    stage: str  # empty | no-models | needs-review | reviewed | drift-unchecked | ready
    summary: str
    entity_count: int
    pending_count: int
    pending_by_confidence: dict[str, int]
    has_manifest: bool
    docs_generated: bool
    docs_stale: bool
    missing_definitions: int
    unaligned_terms: int
    next_actions: list[NextAction] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _newest_model_mtime(model_dir: Path) -> float:
    """Newest mtime across the model's YAML, or 0.0 if none. Used for docs staleness."""
    newest = 0.0
    for p in model_dir.rglob("*.y*ml"):
        # skip the generated docs dir and any target/ build output
        if "target" in p.parts or ".mdl" in p.parts:
            continue
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def _pending_by_confidence(model_dir: Path) -> dict[str, int]:
    """Pending decisions grouped by confidence band, via the decision ledger. Returns
    an empty dict if the reverse package or ledger is absent (fail-soft)."""
    try:
        from mdl_reverse.ledger import DecisionLedger
    except ImportError:
        return {}
    ledger = DecisionLedger.load(model_dir)
    out: dict[str, int] = {}
    for d in ledger.pending():
        band = d.confidence.value
        out[band] = out.get(band, 0) + 1
    return out


def _hygiene(model: Model) -> tuple[int, int]:
    """(entities/attributes missing a definition, ontology refs still proposed)."""
    missing = 0
    unaligned = 0
    for le in model.logical_entities.values():
        if not (le.definition or "").strip():
            missing += 1
        for a in le.attributes:
            if not (a.definition or "").strip():
                missing += 1
            for ref in a.ontology_refs:
                if getattr(ref, "status", None) == "proposed":
                    unaligned += 1
    for ce in model.conceptual_entities.values():
        for ref in ce.ontology_refs:
            if getattr(ref, "status", None) == "proposed":
                unaligned += 1
    return missing, unaligned


def assess(
    model_dir: Path,
    *,
    model: Model | None = None,
    manifest: Path | None = None,
    docs_rel: str = DEFAULT_DOCS_REL,
) -> WorkspaceStatus:
    """Assess the workspace and rank next-actions. `model` may be passed to avoid a
    reload when the caller already holds one; otherwise it is loaded from `model_dir`."""
    model_dir = Path(model_dir)
    if model is None:
        try:
            from mdl_core.repo import ModelRepo

            model = ModelRepo.load(model_dir).model
        except Exception:  # noqa: BLE001 - a broken/absent model is a valid "empty" state
            model = None

    entity_count = len(model.logical_entities) if model else 0
    has_manifest = bool(manifest and Path(manifest).exists())
    pending_by_conf = _pending_by_confidence(model_dir)
    pending_count = sum(pending_by_conf.values())
    missing_defs, unaligned = _hygiene(model) if model else (0, 0)

    docs_path = model_dir / docs_rel
    docs_generated = docs_path.exists()
    docs_stale = False
    if docs_generated:
        try:
            docs_stale = docs_path.stat().st_mtime < _newest_model_mtime(model_dir)
        except OSError:
            docs_stale = False

    actions: list[NextAction] = []

    if entity_count == 0:
        stage = "no-models"
        summary = "No logical models yet. Reverse a warehouse or a dbt manifest to begin."
        actions.append(
            NextAction(
                id="reverse",
                title="Reverse a warehouse or manifest",
                detail="Point Modelith at a dbt manifest or a live datastore to recover models.",
                severity="recommended",
                command="modelith.reverseEngineer",
                cli="mdl reverse <manifest|--connect>",
            )
        )
    else:
        if pending_count:
            stage = "needs-review"
            summary = (
                f"{entity_count} models, {pending_count} proposal(s) awaiting review."
            )
            actions.append(
                NextAction(
                    id="review",
                    title=f"Review {pending_count} pending proposal(s)",
                    detail="Accept or reject recovered keys and relationships in Reverse Review.",
                    severity="attention",
                    command="modelith.reverseRefresh",
                    cli="mdl decisions list --pending",
                )
            )
        elif not docs_generated or docs_stale:
            stage = "reviewed"
            summary = (
                f"{entity_count} models, all proposals reviewed."
                + (" Docs are out of date." if docs_stale else " No docs generated yet.")
            )
        else:
            stage = "ready"
            summary = f"{entity_count} models, all reviewed, docs up to date."

        # Docs action, offered once models exist and nothing is pending review.
        if not pending_count:
            if not docs_generated:
                actions.append(
                    NextAction(
                        id="docs-generate",
                        title="Generate documentation",
                        detail="Produce a shareable docs site for these models.",
                        severity="recommended",
                        command="modelith.docsGenerate",
                        cli="mdl docs generate",
                    )
                )
            elif docs_stale:
                actions.append(
                    NextAction(
                        id="docs-regenerate",
                        title="Regenerate documentation",
                        detail="The docs are older than the model. Regenerate to refresh them.",
                        severity="recommended",
                        command="modelith.docsGenerate",
                        cli="mdl docs generate",
                    )
                )
            else:
                actions.append(
                    NextAction(
                        id="docs-open",
                        title="Open documentation",
                        detail="View the generated docs site.",
                        severity="info",
                        command="modelith.docsOpen",
                        cli="mdl docs serve",
                    )
                )

        # Drift is checkable once there is a manifest to compare against.
        if has_manifest:
            actions.append(
                NextAction(
                    id="drift",
                    title="Check drift against the warehouse",
                    detail="Compare the committed model to the compiled dbt manifest.",
                    severity="info",
                    command="modelith.driftCheck",
                    cli="mdl drift --manifest target/manifest.json",
                )
            )

    # Hygiene nudges, appended after the primary flow so they never outrank it.
    if entity_count and missing_defs:
        actions.append(
            NextAction(
                id="definitions",
                title=f"Add {missing_defs} missing definition(s)",
                detail="Entities or attributes have no description.",
                severity="info",
                command="modelith.openCanvas",
                cli="mdl model detail <entity>",
            )
        )
    if entity_count and unaligned:
        actions.append(
            NextAction(
                id="align",
                title=f"Review {unaligned} proposed ontology alignment(s)",
                detail="Ontology references are still proposed, not accepted.",
                severity="info",
                command="modelith.openCanvas",
                cli="mdl ontology align",
            )
        )

    return WorkspaceStatus(
        stage=stage,
        summary=summary,
        entity_count=entity_count,
        pending_count=pending_count,
        pending_by_confidence=pending_by_conf,
        has_manifest=has_manifest,
        docs_generated=docs_generated,
        docs_stale=docs_stale,
        missing_definitions=missing_defs,
        unaligned_terms=unaligned,
        next_actions=actions,
    )
