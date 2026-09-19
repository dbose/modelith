import * as vscode from "vscode";
import { findMdl, findModelDir, runMdl } from "./mdl";

/** The Reverse Review tree: pending decision-ledger proposals from `mdl reverse`,
 * grouped by kind (relationship / surrogate strip / SCD2 / rollup) with a confidence
 * badge, and inline Accept / Reject actions that write verdicts via
 * `mdl decisions accept|reject`. Turns the CLI's `--interactive` prompt into a proper
 * review surface. Mirrors the Drift view's shape. */

export interface Decision {
  signal_key: string;
  kind: string;
  signal: string;
  confidence: "high" | "medium-high" | "medium" | "low";
  verdict: "proposed" | "accepted" | "rejected";
  subject: string;
  evidence: Record<string, unknown>;
}

type Node = GroupNode | DecisionNode;
interface GroupNode {
  kind: "group";
  label: string;
  items: Decision[];
}
interface DecisionNode {
  kind: "decision";
  decision: Decision;
}

const KIND_LABEL: Record<string, string> = {
  relationship: "Relationships",
  surrogate_key: "Surrogate keys stripped",
  scd2_pattern: "SCD2 patterns",
  reporting_rollup: "Reporting rollups",
};

const CONFIDENCE_ICON: Record<Decision["confidence"], vscode.ThemeIcon> = {
  high: new vscode.ThemeIcon("verified-filled"),
  "medium-high": new vscode.ThemeIcon("verified"),
  medium: new vscode.ThemeIcon("question"),
  low: new vscode.ThemeIcon("warning"),
};

export class ReverseReviewProvider implements vscode.TreeDataProvider<Node> {
  private readonly emitter = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;
  private decisions: Decision[] = [];

  constructor(private readonly out: vscode.OutputChannel) {}

  /** Re-read the pending proposals from `.mdl/decisions.yaml` and refresh the tree.
   * Pass `modelDir` to read a SPECIFIC model (e.g. the one a right-click reverse just
   * wrote); omit it to auto-discover the workspace model. Being explicit avoids reading
   * the wrong `.mdl/decisions.yaml` when several models live in one workspace. */
  async refresh(modelDir?: string): Promise<void> {
    const dir = modelDir ?? (await findModelDir());
    if (!dir) {
      this.decisions = [];
      this.emitter.fire(undefined);
      return;
    }
    try {
      const bin = await findMdl(dir);
      const r = await runMdl(
        bin,
        ["decisions", "list", "--pending", "--format", "json", "-m", "."],
        dir,
      );
      this.decisions = r.code === 0 && r.stdout.trim() ? (JSON.parse(r.stdout) as Decision[]) : [];
    } catch (e) {
      this.out.appendLine(`[reverse] could not read decisions: ${e}`);
      this.decisions = [];
    }
    this.emitter.fire(undefined);
  }

  get pendingCount(): number {
    return this.decisions.length;
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "group") {
      const ti = new vscode.TreeItem(
        `${node.label} (${node.items.length})`,
        vscode.TreeItemCollapsibleState.Expanded,
      );
      ti.contextValue = "reverseGroup";
      return ti;
    }
    const d = node.decision;
    const ti = new vscode.TreeItem(d.subject, vscode.TreeItemCollapsibleState.None);
    ti.description = d.confidence;
    ti.tooltip = `${d.subject}\n\nconfidence: ${d.confidence}\nsignal: ${d.signal}`;
    ti.iconPath = CONFIDENCE_ICON[d.confidence];
    // context value gates the inline Accept/Reject actions contributed in package.json
    ti.contextValue = "reverseDecision";
    return ti;
  }

  getChildren(node?: Node): Node[] {
    if (!node) {
      // group by kind, kinds with items only
      const kinds = [...new Set(this.decisions.map((d) => d.kind))];
      return kinds.map((k) => ({
        kind: "group" as const,
        label: KIND_LABEL[k] ?? k,
        items: this.decisions.filter((d) => d.kind === k),
      }));
    }
    if (node.kind === "group") {
      return node.items.map((decision) => ({ kind: "decision" as const, decision }));
    }
    return [];
  }

  /** Write a verdict for one proposal and refresh. Used by the Accept/Reject commands. */
  async setVerdict(signalKey: string, verdict: "accept" | "reject"): Promise<void> {
    const dir = await findModelDir();
    if (!dir) return;
    const bin = await findMdl(dir);
    const r = await runMdl(bin, ["decisions", verdict, signalKey, "-m", "."], dir);
    this.out.appendLine(r.stdout + r.stderr);
    await this.refresh();
  }
}
