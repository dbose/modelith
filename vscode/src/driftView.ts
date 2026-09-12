import * as path from "node:path";
import * as vscode from "vscode";
import { type DriftItem, type DriftManager, SEVERITY_ORDER } from "./driftDiagnostics";

/** The Drift tree view: findings grouped Breaking / Additive / Cosmetic, each
 * clickable to open the owning model file. Reads the cached report from the shared
 * DriftManager (no extra drift run) and refreshes when it changes. */

type Node = GroupNode | ItemNode;

interface GroupNode {
  kind: "group";
  severity: DriftItem["severity"];
  items: DriftItem[];
}

interface ItemNode {
  kind: "item";
  item: DriftItem;
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
    // reconcilable items get a context value so a per-item "Reconcile" inline action
    // can be contributed in package.json; breaking items get "explain".
    ti.contextValue = item.reconcilable ? "driftItem:reconcilable" : "driftItem:breaking";
    return ti;
  }

  async getChildren(node?: Node): Promise<Node[]> {
    const report = this.manager.report;
    if (!report) return [];
    if (!node) {
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
