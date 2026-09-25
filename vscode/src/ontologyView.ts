import * as vscode from "vscode";
import { findMdl, findModelDir, runMdl } from "./mdl";

/** The Ontology tree view: proposed ontology alignments awaiting an architect's
 * review (concept/term -> vocabulary URI, with layer + confidence), each with inline
 * Promote / Reject actions, plus an industry-coverage summary row. Mirrors the Reverse
 * Review view — the review surface ontology alignment previously lacked. Reads
 * `mdl ontology status --format json`; acts via `mdl ontology promote|reject --uri`. */

interface ProposedAlignment {
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
  proposed: ProposedAlignment[];
  coverage: Coverage;
}

type Node = ProposalNode | StatusNode | UncoveredNode;
interface ProposalNode {
  kind: "proposal";
  a: ProposedAlignment;
}
/** A resting/summary row (coverage, or "all reviewed"). */
interface StatusNode {
  kind: "status";
  label: string;
  icon: vscode.ThemeIcon;
  description?: string;
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
    if (node.kind === "status") {
      const ti = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
      ti.iconPath = node.icon;
      if (node.description) ti.description = node.description;
      ti.contextValue = "ontologyStatus";
      return ti;
    }
    if (node.kind === "uncovered") {
      const ti = new vscode.TreeItem(node.name, vscode.TreeItemCollapsibleState.None);
      ti.iconPath = new vscode.ThemeIcon("circle-outline");
      ti.description = "no industry alignment";
      ti.contextValue = "ontologyUncovered";
      return ti;
    }
    const a = node.a;
    const ti = new vscode.TreeItem(`${a.name} → ${prefixed(a.uri)}`, vscode.TreeItemCollapsibleState.None);
    const conf = a.confidence != null ? `${Math.round(a.confidence * 100)}%` : "";
    ti.description = [a.layer, conf].filter(Boolean).join(" · ");
    ti.tooltip = `${a.name} (${a.kind})\n→ ${a.uri}\npredicate: ${a.predicate}${
      a.resolved_via ? `\nvia: ${a.resolved_via}` : ""
    }`;
    ti.iconPath = new vscode.ThemeIcon("lightbulb");
    // gates the inline Promote/Reject actions contributed in package.json
    ti.contextValue = "ontologyProposal";
    return ti;
  }

  getChildren(node?: Node): Node[] {
    if (node) return [];
    const s = this.status;
    if (!s) {
      return [
        {
          kind: "status",
          label: "No ontology data — run in a model",
          icon: new vscode.ThemeIcon("circle-slash"),
        },
      ];
    }
    const rows: Node[] = [];
    if (s.proposed.length === 0) {
      rows.push({
        kind: "status",
        label: "No proposed alignments — all reviewed",
        icon: new vscode.ThemeIcon("check", new vscode.ThemeColor("charts.green")),
      });
    } else {
      for (const a of s.proposed) rows.push({ kind: "proposal", a });
    }
    // coverage summary always shown as the last row(s)
    const c = s.coverage;
    rows.push({
      kind: "status",
      label: `Industry coverage: ${c.coverage_pct}%`,
      description: `${c.core_with_industry + c.core_exempt}/${c.total_core} core terms`,
      icon: coverageIcon(c.coverage_pct),
    });
    for (const name of c.core_uncovered.slice(0, 20)) {
      rows.push({ kind: "uncovered", name });
    }
    return rows;
  }

  /** Promote or reject one proposed alignment via the CLI, then refresh. */
  async act(a: ProposedAlignment, verb: "promote" | "reject"): Promise<void> {
    const dir = await findModelDir();
    if (!dir) return;
    const bin = await findMdl(dir);
    const r = await runMdl(bin, ["ontology", verb, a.name, "--uri", a.uri, "-m", "."], dir);
    this.out.appendLine(r.stdout + r.stderr);
    await this.refresh(dir);
  }

  /** Find a proposal by the node passed to a command (VS Code hands us the tree node). */
  proposalOf(node: unknown): ProposedAlignment | undefined {
    if (node && typeof node === "object" && "kind" in node && (node as Node).kind === "proposal") {
      return (node as ProposalNode).a;
    }
    return undefined;
  }
}

/** Shorten a URI to a readable tail for the row label (full URI stays in the tooltip). */
function prefixed(uri: string): string {
  const tail = uri.split(/[#/]/).filter(Boolean).pop();
  return tail || uri;
}

function coverageIcon(pct: number): vscode.ThemeIcon {
  if (pct >= 80) return new vscode.ThemeIcon("shield", new vscode.ThemeColor("charts.green"));
  if (pct >= 40) return new vscode.ThemeIcon("shield", new vscode.ThemeColor("charts.yellow"));
  return new vscode.ThemeIcon("shield", new vscode.ThemeColor("charts.red"));
}
