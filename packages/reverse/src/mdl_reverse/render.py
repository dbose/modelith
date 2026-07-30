"""Drift report renderers (spec §11 PR comment format).

Three formats:
- text: human CLI summary
- json: machine-readable, for CI to post structured comments
- markdown: the PR comment — a table of added/dropped/type-changed columns with a
  breaking-change verdict, plus a Mermaid diff of the affected subgraph. Reviewers
  who never open the tool still see the model (§11).
"""

from __future__ import annotations

import json

from mdl_reverse.drift import DriftKind, DriftReport, DriftSeverity

_SEV_EMOJI = {
    DriftSeverity.breaking: "🔴",
    DriftSeverity.unmanaged: "🟠",
    DriftSeverity.additive: "🟡",
    DriftSeverity.cosmetic: "🔵",
}


def render_text(report: DriftReport) -> str:
    if not report.items:
        return "no drift detected"
    lines = [f"drift vs target {report.target!r}:"]
    for sev in (
        DriftSeverity.breaking,
        DriftSeverity.unmanaged,
        DriftSeverity.additive,
        DriftSeverity.cosmetic,
    ):
        items = report.by_severity(sev)
        if not items:
            continue
        lines.append(f"  [{sev.value}] {len(items)}")
        for i in items:
            lines.append(f"    - {i.detail}")
    return "\n".join(lines)


def render_json(report: DriftReport) -> str:
    return json.dumps(
        {
            "target": report.target,
            "max_severity": report.max_severity.value if report.max_severity else None,
            "has_breaking": report.has_breaking,
            "items": [
                {
                    "severity": i.severity.value,
                    "kind": i.kind.value,
                    "model": i.model,
                    "column": i.column,
                    "detail": i.detail,
                    "payload": i.payload,
                }
                for i in report.items
            ],
        },
        indent=2,
        sort_keys=True,
    )


def render_markdown(report: DriftReport) -> str:
    verdict = "🔴 **BREAKING drift**" if report.has_breaking else (
        "🟡 additive/cosmetic drift" if report.items else "✅ no drift"
    )
    out = [f"## Modelith drift — {verdict}", "", f"Target: `{report.target}`", ""]

    if report.items:
        out += [
            "| Severity | Model | Column | Change |",
            "|---|---|---|---|",
        ]
        for i in report.items:
            emoji = _SEV_EMOJI[i.severity]
            out.append(
                f"| {emoji} {i.severity.value} | `{i.model}` | "
                f"`{i.column or '—'}` | {i.kind.value.replace('_', ' ')} |"
            )
        out.append("")
        out.append(_mermaid_subgraph(report))
    return "\n".join(out)


def _mermaid_subgraph(report: DriftReport) -> str:
    """Mermaid diff of affected models, colored by max severity per model."""
    lines = ["```mermaid", "graph TD"]
    worst: dict[str, DriftSeverity] = {}
    for i in report.items:
        cur = worst.get(i.model)
        if cur is None or i.severity.rank > cur.rank:
            worst[i.model] = i.severity
    color = {
        DriftSeverity.breaking: "fill:#f8d7da,stroke:#dc3545",
        DriftSeverity.unmanaged: "fill:#ffe5d0,stroke:#fd7e14",
        DriftSeverity.additive: "fill:#fff3cd,stroke:#ffc107",
        DriftSeverity.cosmetic: "fill:#cfe2ff,stroke:#0d6efd",
    }
    for idx, (model, sev) in enumerate(sorted(worst.items())):
        node = f"m{idx}"
        n_changes = sum(1 for i in report.items if i.model == model)
        lines.append(f'  {node}["{model}<br/>{n_changes} change(s)"]')
        lines.append(f"  style {node} {color[sev]}")
    lines.append("```")
    return "\n".join(lines)


def counts_by_severity(report: DriftReport) -> dict[str, int]:
    return {
        sev.value: len(report.by_severity(sev))
        for sev in DriftSeverity
    }


# Kinds that reconcile knows how to fold into the model (additive/cosmetic only).
RECONCILABLE_KINDS = {
    DriftKind.column_added,
    DriftKind.description_changed,
}
