import * as vscode from "vscode";
import { findMdl, findModelDir, runMdl } from "./mdl";

/** The Ontology tree view: ontology alignment as a visible flow — proposals awaiting
 * review, the accepted (done) side, and industry coverage — in collapsible, counted
 * sections. Proposed rows carry inline Promote / Reject. It is the review surface
 * ontology alignment lacked, mirroring Reverse Review. Reads `mdl ontology status
 * --format json`; acts via `mdl ontology promote|reject --uri`. */

interface Alignment {
  id: string;
  name: string;
  kind: "conceptual_entity" | "term";
  uri: string;
  predicate: string;
  layer: string | null;
  confidence: number | null;
  resolved_via: string | null;
}

interface Coverage {
  coverage_pct: number;
  total_core: number;
  core_with_industry: number;
  core_exempt: number;
  core_uncovered: string[];
}

interface OntologyStatus {
  proposed: Alignment[];
  accepted: Alignment[];
  coverage: Coverage;
}

type Node = SectionNode | AlignmentNode | StatusNode | UncoveredNode;
/** A collapsible section header with a count — the flow's stages. */
interface SectionNode {
  kind: "section";
  id: "review" | "accepted" | "coverage";
  label: string;
  icon: vscode.ThemeIcon;
  count?: number;
  description?: string;
  children: Node[];
  collapsed: boolean;
}
interface AlignmentNode {
  kind: "alignment";
  a: Alignment;
  state: "proposed" | "accepted";
}
/** A non-actionable informational row (resting state, empty section). */
interface StatusNode {
  kind: "status";
  label: string;
  icon: vscode.ThemeIcon;
}
interface UncoveredNode {
  kind: "uncovered";
  name: string;
}

export class OntologyProvider implements vscode.TreeDataProvider<Node> {
  private readonly emitter = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;
  private status: OntologyStatus | undefined;

  constructor(private readonly out: vscode.OutputChannel) {}

  async refresh(modelDir?: string): Promise<void> {
    const dir = modelDir ?? (await findModelDir());
    if (!dir) {
      this.status = undefined;
      this.emitter.fire(undefined);
      return;
    }
    try {
      const bin = await findMdl(dir);
      const r = await runMdl(bin, ["ontology", "status", "--format", "json", "-m", "."], dir);
      this.status =
        r.code === 0 && r.stdout.trim() ? (JSON.parse(r.stdout) as OntologyStatus) : undefined;
      if (r.code !== 0 && r.stderr.trim()) this.out.appendLine(`[ontology] ${r.stderr.trim()}`);
    } catch (e) {
      this.out.appendLine(`[ontology] could not read status: ${e}`);
      this.status = undefined;
    }
    this.emitter.fire(undefined);
  }

  get pendingCount(): number {
    return this.status?.proposed.length ?? 0;
  }

  getTreeItem(node: Node): vscode.TreeItem {
    switch (node.kind) {
      case "section": {
        const label = node.count != null ? `${node.label} (${node.count})` : node.label;
        const ti = new vscode.TreeItem(
          label,
          node.collapsed
            ? vscode.TreeItemCollapsibleState.Collapsed
            : vscode.TreeItemCollapsibleState.Expanded,
        );
        ti.iconPath = node.icon;
        if (node.description) ti.description = node.description;
        ti.contextValue = `ontologySection:${node.id}`;
        return ti;
      }
      case "alignment": {
        const a = node.a;
        const ti = new vscode.TreeItem(
          `${a.name} → ${prefixed(a.uri)}`,
          vscode.TreeItemCollapsibleState.None,
        );
        const conf = a.confidence != null ? `${Math.round(a.confidence * 100)}%` : "";
        ti.description = [a.layer, conf].filter(Boolean).join(" · ");
        ti.tooltip = new vscode.MarkdownString(
          `**${a.name}** _(${a.kind.replace("_", " ")})_\n\n` +
            `${node.state === "accepted" ? "✓ accepted" : "⏳ proposed"} · aligns to\n\n` +
            `\`${a.uri}\`\n\npredicate: \`${a.predicate}\`${
              a.resolved_via ? ` · via ${a.resolved_via}` : ""
            }`,
        );
        ti.iconPath =
          node.state === "accepted"
            ? new vscode.ThemeIcon("pass-filled", new vscode.ThemeColor("charts.green"))
            : confidenceIcon(a.confidence);
        // context value gates the inline Promote/Reject actions (proposed rows only)
        ti.contextValue = node.state === "accepted" ? "ontologyAccepted" : "ontologyProposal";
        return ti;
      }
      case "uncovered": {
        const ti = new vscode.TreeItem(node.name, vscode.TreeItemCollapsibleState.None);
        ti.iconPath = new vscode.ThemeIcon("circle-outline");
        ti.description = "no industry alignment";
        ti.contextValue = "ontologyUncovered";
        return ti;
      }
      case "status": {
        const ti = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
        ti.iconPath = node.icon;
        ti.contextValue = "ontologyStatus";
        return ti;
      }
    }
  }

  getChildren(node?: Node): Node[] {
    if (node) return node.kind === "section" ? node.children : [];
    const s = this.status;
    if (!s) {
      return [
        {
          kind: "status",
          label: "No ontology data — open a model to review alignments",
          icon: new vscode.ThemeIcon("circle-slash"),
        },
      ];
    }

    const sections: Node[] = [];

    // 1) To review — the action stage. Expanded, inline Promote/Reject on each row.
    const reviewChildren: Node[] =
      s.proposed.length > 0
        ? s.proposed.map((a) => ({ kind: "alignment" as const, a, state: "proposed" as const }))
        : [
            {
              kind: "status" as const,
              label: "Nothing to review — all alignments decided",
              icon: new vscode.ThemeIcon("check", new vscode.ThemeColor("charts.green")),
            },
          ];
    sections.push({
      kind: "section",
      id: "review",
      label: "To review",
      icon: new vscode.ThemeIcon("inbox"),
      count: s.proposed.length,
      children: reviewChildren,
      collapsed: false,
    });

    // 2) Accepted — the done side of the flow. Collapsed by default (reassurance, not
    // a to-do). Only shown when there is at least one.
    if (s.accepted.length > 0) {
      sections.push({
        kind: "section",
        id: "accepted",
        label: "Accepted",
        icon: new vscode.ThemeIcon("pass-filled", new vscode.ThemeColor("charts.green")),
        count: s.accepted.length,
        children: s.accepted.map((a) => ({
          kind: "alignment" as const,
          a,
          state: "accepted" as const,
        })),
        collapsed: true,
      });
    }

    // 3) Coverage — the health readout. Collapsed; children are the uncovered terms.
    const c = s.coverage;
    const uncovered: Node[] = c.core_uncovered
      .slice(0, 50)
      .map((name) => ({ kind: "uncovered" as const, name }));
    sections.push({
      kind: "section",
      id: "coverage",
      label: "Industry coverage",
      icon: coverageIcon(c.coverage_pct),
      description: `${c.coverage_pct}% · ${c.core_with_industry + c.core_exempt}/${c.total_core} core`,
      children:
        uncovered.length > 0
          ? uncovered
          : [
              {
                kind: "status" as const,
                label: "All core terms covered",
                icon: new vscode.ThemeIcon("check", new vscode.ThemeColor("charts.green")),
              },
            ],
      collapsed: true,
    });

    return sections;
  }

  /** Promote or reject one proposed alignment via the CLI, then refresh. */
  async act(a: Alignment, verb: "promote" | "reject"): Promise<void> {
    const dir = await findModelDir();
    if (!dir) return;
    const bin = await findMdl(dir);
    const r = await runMdl(bin, ["ontology", verb, a.name, "--uri", a.uri, "-m", "."], dir);
    this.out.appendLine(r.stdout + r.stderr);
    await this.refresh(dir);
  }

  /** The alignment for a node passed to a command (VS Code hands us the tree node). */
  proposalOf(node: unknown): Alignment | undefined {
    if (node && typeof node === "object" && "kind" in node && (node as Node).kind === "alignment") {
      return (node as AlignmentNode).a;
    }
    return undefined;
  }
}

/** Shorten a URI to a readable tail for the row label (full URI stays in the tooltip). */
function prefixed(uri: string): string {
  const tail = uri.split(/[#/]/).filter(Boolean).pop();
  return tail || uri;
}

/** Proposed-row icon graded by confidence, so the review queue reads at a glance. */
function confidenceIcon(confidence: number | null): vscode.ThemeIcon {
  if (confidence == null) return new vscode.ThemeIcon("question");
  if (confidence >= 0.75) return new vscode.ThemeIcon("lightbulb-autofix");
  if (confidence >= 0.4) return new vscode.ThemeIcon("lightbulb");
  return new vscode.ThemeIcon("warning");
}

function coverageIcon(pct: number): vscode.ThemeIcon {
  if (pct >= 80) return new vscode.ThemeIcon("shield", new vscode.ThemeColor("charts.green"));
  if (pct >= 40) return new vscode.ThemeIcon("shield", new vscode.ThemeColor("charts.yellow"));
  return new vscode.ThemeIcon("shield", new vscode.ThemeColor("charts.red"));
}
