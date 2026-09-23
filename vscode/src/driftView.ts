import * as path from "node:path";
import * as vscode from "vscode";
import { type DriftItem, type DriftManager, SEVERITY_ORDER } from "./driftDiagnostics";

/** The Drift tree view: findings grouped Breaking / Additive / Cosmetic, each
 * clickable to open the owning model file. Reads the cached report from the shared
 * DriftManager (no extra drift run) and refreshes when it changes. */

type Node = GroupNode | ItemNode | StatusNode;

interface GroupNode {
  kind: "group";
  severity: DriftItem["severity"];
  items: DriftItem[];
}

interface ItemNode {
  kind: "item";
  item: DriftItem;
}

/** A single non-clickable row shown when there are no findings: a clean "no drift"
 * resting state after a check, or a prompt before one has run. Keeps the focused view
 * from ever being blank — a deliberate "Check Drift" always shows a result. */
interface StatusNode {
  kind: "status";
  label: string;
  icon: vscode.ThemeIcon;
}

const SEVERITY_LABEL: Record<DriftItem["severity"], string> = {
  breaking: "Breaking",
  unmanaged: "Unmanaged",
  additive: "Additive",
  cosmetic: "Cosmetic",
};

const SEVERITY_ICON: Record<DriftItem["severity"], vscode.ThemeIcon> = {
  breaking: new vscode.ThemeIcon("error", new vscode.ThemeColor("errorForeground")),
  unmanaged: new vscode.ThemeIcon("warning"),
  additive: new vscode.ThemeIcon("diff-added"),
  cosmetic: new vscode.ThemeIcon("info"),
};

export class DriftTreeProvider implements vscode.TreeDataProvider<Node> {
  private readonly emitter = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;

  constructor(
    private readonly manager: DriftManager,
    private readonly modelDir: () => Promise<string | undefined>,
  ) {
    manager.onDidChange(() => this.emitter.fire(undefined));
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "status") {
      const ti = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
      ti.iconPath = node.icon;
      ti.contextValue = "driftStatus";
      return ti;
    }
    if (node.kind === "group") {
      const ti = new vscode.TreeItem(
        `${SEVERITY_LABEL[node.severity]} (${node.items.length})`,
        vscode.TreeItemCollapsibleState.Expanded,
      );
      ti.iconPath = SEVERITY_ICON[node.severity];
      ti.contextValue = `driftGroup:${node.severity}`;
      return ti;
    }
    const item = node.item;
    const where = item.column ? `${item.model}.${item.column}` : item.model;
    const ti = new vscode.TreeItem(where, vscode.TreeItemCollapsibleState.None);
    ti.description = item.kind.replace(/_/g, " ");
    ti.tooltip = item.reconcile_action
      ? `${item.detail}\n\nFix: ${item.reconcile_action}`
      : item.detail;
    ti.iconPath = SEVERITY_ICON[item.severity];
    // contextValue gates the per-item inline actions in package.json:
    //  - reconcilable -> "Reconcile"
    //  - model_removed -> "Map to dbt model…" (often the real fix is a missing mapping,
    //    not a dropped model), so it needs its own kind-aware value
    //  - other breaking -> "Explain"
    ti.contextValue = item.reconcilable
      ? "driftItem:reconcilable"
      : item.kind === "model_removed"
        ? "driftItem:modelRemoved"
        : "driftItem:breaking";
    return ti;
  }

  async getChildren(node?: Node): Promise<Node[]> {
    const report = this.manager.report;
    if (!node) {
      // No check has run this session -> a prompt row (not a blank pane) so the view
      // explains itself the first time it's revealed.
      if (!report) {
        return [
          {
            kind: "status" as const,
            label: "No drift check yet — run Check Drift",
            icon: new vscode.ThemeIcon("search"),
          },
        ];
      }
      // Checked and clean -> an explicit resting row, so choosing "Check drift instead"
      // from the reverse modal shows a clear ✓ instead of an empty view.
      if (report.items.length === 0) {
        return [
          {
            kind: "status" as const,
            label: "No drift — model matches the manifest",
            icon: new vscode.ThemeIcon("check", new vscode.ThemeColor("charts.green")),
          },
        ];
      }
      // top level: one group per severity that has items, in severity order
      return SEVERITY_ORDER.filter((sev) => report.items.some((i) => i.severity === sev)).map(
        (severity) => ({
          kind: "group" as const,
          severity,
          items: report.items.filter((i) => i.severity === severity),
        }),
      );
    }
    if (node.kind === "group") {
      return node.items.map((item) => ({ kind: "item" as const, item }));
    }
    return [];
  }

  /** Open the model file an item points at (used by the item's command). */
  async reveal(item: DriftItem): Promise<void> {
    const dir = await this.modelDir();
    if (!dir || !item.file) return;
    const uri = vscode.Uri.file(path.join(dir, item.file));
    await vscode.window.showTextDocument(uri, { preview: true });
  }
}
