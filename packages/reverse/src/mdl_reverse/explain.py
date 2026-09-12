"""Explain a drift report: the one structured shape every surface consumes.

`mdl drift --explain`, the `explain_drift()` MCP tool, and the VS Code drift UI all
need the same thing — the report, plus, per item, whether it is safely reconcilable
and (if so) the concrete action reconcile would take. Rather than each surface
re-deriving that from `DriftKind`, this module produces it once. The deterministic
engine (`compute_drift`, severity) stays the source of truth; this only annotates.

Nothing here calls an LLM. An LLM surface (chat, MCP client) grounds on this JSON;
the CLI renders it directly.
"""

from __future__ import annotations

from collections.abc import Callable

from mdl_reverse.drift import DriftItem, DriftReport, DriftSeverity
from mdl_reverse.render import RECONCILABLE_KINDS, counts_by_severity


def _reconcile_action(item: DriftItem) -> str | None:
    """The concrete action `mdl drift --reconcile` would take for this item, or None
    when it is not safely reconcilable (breaking, or a kind reconcile can't fold).

    Mirrors `reconcile.reconcile` exactly — additive/cosmetic only, and only the
    kinds it actually handles — so the explanation never promises a fix the engine
    won't perform."""
    if item.severity == DriftSeverity.breaking:
        return None
    if item.kind not in RECONCILABLE_KINDS:
        return None
    from mdl_reverse.drift import DriftKind

    if item.kind == DriftKind.column_added:
        base = item.payload.get("data_type") or "string"
        return f"add attribute `{item.column}` ({base}) to `{item.model}`"
    if item.kind == DriftKind.description_changed:
        return f"update the definition of `{item.model}` from the warehouse description"
    return None  # unreachable given RECONCILABLE_KINDS, but keeps the mapping explicit


def explain_item(
    item: DriftItem, file_for_model: Callable[[str], str | None] | None = None
) -> dict:
    """One drift item, annotated with its reconcile action and safety. `file_for_model`
    (optional) resolves the model name to the YAML file the drift is about, so a UI can
    open it without re-deriving the mapping."""
    action = _reconcile_action(item)
    return {
        "severity": item.severity.value,
        "kind": item.kind.value,
        "model": item.model,
        "column": item.column,
        "detail": item.detail,
        "payload": item.payload,
        # True when `--reconcile` will fold this in automatically; breaking is never
        # auto-applied and carries reconcilable=False + reconcile_action=None.
        "reconcilable": action is not None,
        "reconcile_action": action,
        "file": file_for_model(item.model) if file_for_model else None,
    }


def explain_report(
    report: DriftReport, file_for_model: Callable[[str], str | None] | None = None
) -> dict:
    """The whole report as the structured shape every surface consumes: the summary
    counts, the breaking/reconcilable split, and every item annotated.

    `safe_count` / `breaking_count` are what a UI shows as "🟡 N safe / 🔴 N breaking";
    `reconcilable` lists the items `--reconcile` would apply, so a surface can offer a
    one-click "reconcile safe changes" without re-deriving the set."""
    items = [explain_item(i, file_for_model) for i in report.items]
    reconcilable = [i for i in items if i["reconcilable"]]
    breaking = [i for i in items if i["severity"] == DriftSeverity.breaking.value]
    return {
        "target": report.target,
        "max_severity": report.max_severity.value if report.max_severity else None,
        "has_breaking": report.has_breaking,
        "counts": counts_by_severity(report),
        "safe_count": len(reconcilable),
        "breaking_count": len(breaking),
        "items": items,
        "reconcilable": reconcilable,
    }


def render_explain_text(report: DriftReport) -> str:
    """A deterministic human narrative of the explained report — the LLM-free
    rendering `mdl drift --explain` prints, and the fallback the chat surface uses
    when no language model is available. Groups by severity and, per item, states the
    concrete reconcile action or that a human must decide."""
    ex = explain_report(report)
    if not ex["items"]:
        return "✅ no drift — the model matches the warehouse."

    lines = [
        f"Drift vs target {ex['target']!r}: "
        f"🔴 {ex['breaking_count']} breaking, 🟢 {ex['safe_count']} safe to reconcile.",
        "",
    ]
    for sev in (
        DriftSeverity.breaking,
        DriftSeverity.unmanaged,
        DriftSeverity.additive,
        DriftSeverity.cosmetic,
    ):
        group = [i for i in ex["items"] if i["severity"] == sev.value]
        if not group:
            continue
        lines.append(f"[{sev.value}] ({len(group)})")
        for i in group:
            where = f"{i['model']}.{i['column']}" if i["column"] else i["model"]
            lines.append(f"  - {where}: {i['detail']}")
            if i["reconcile_action"]:
                lines.append(f"      fix: {i['reconcile_action']}")
            elif sev == DriftSeverity.breaking:
                lines.append("      breaking — needs a human decision, not auto-reconciled")
        lines.append("")
    return "\n".join(lines).rstrip()
