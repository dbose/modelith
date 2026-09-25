import * as vscode from "vscode";
import type { StatusModel } from "./statusModel";

/** The Documentation frame: warehouse-global model docs (dbt-docs style), separate from
 * the reverse-engineering review workflow. Shows whether docs are generated / stale and
 * the number of models, with Generate / Open actions. Driven by the shared StatusModel
 * (`mdl status`), so its state stays in step with the rest of the panel. */

type Node = ActionNode | StatusNode;
/** A clickable next-step row (generate / open). */
interface ActionNode {
  kind: "action";
  label: string;
  detail: string;
  icon: vscode.ThemeIcon;
  command: string;
}
/** A non-clickable informational/resting row. */
interface StatusNode {
  kind: "status";
  label: string;
  icon: vscode.ThemeIcon;
  description?: string;
}

export class DocsProvider implements vscode.TreeDataProvider<Node> {
  private readonly emitter = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;

  constructor(private readonly statusModel: StatusModel) {
    // Redraw whenever the shared assessment changes (docs generated, model edited, ...).
    this.statusModel.onDidChange(() => this.emitter.fire(undefined));
  }

  getTreeItem(node: Node): vscode.TreeItem {
    const ti = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
    ti.iconPath = node.icon;
    if (node.kind === "action") {
      ti.tooltip = node.detail;
      ti.description = node.detail;
      ti.command = { command: node.command, title: node.label };
      ti.contextValue = "docsAction";
    } else {
      if (node.description) ti.description = node.description;
      ti.contextValue = "docsStatus";
    }
    return ti;
  }

  getChildren(node?: Node): Node[] {
    if (node) return [];
    const s = this.statusModel.status;
    if (!s) {
      return [
        {
          kind: "status",
          label: "Open a model to generate documentation",
          icon: new vscode.ThemeIcon("circle-slash"),
        },
      ];
    }
    if (s.entity_count === 0) {
      return [
        {
          kind: "status",
          label: "No models yet — reverse a warehouse first",
          icon: new vscode.ThemeIcon("circle-outline"),
        },
      ];
    }

    const rows: Node[] = [];
    if (!s.docs_generated) {
      rows.push({
        kind: "status",
        label: "Not generated yet",
        description: `${s.entity_count} models`,
        icon: new vscode.ThemeIcon("book"),
      });
      rows.push({
        kind: "action",
        label: "Generate documentation",
        detail: "Produce a shareable docs site for these models.",
        icon: new vscode.ThemeIcon("run", new vscode.ThemeColor("charts.green")),
        command: "modelith.docsGenerate",
      });
    } else {
      const stale = s.docs_stale;
      rows.push({
        kind: "status",
        label: stale ? "Docs out of date" : "Docs up to date",
        description: `${s.entity_count} models`,
        icon: stale
          ? new vscode.ThemeIcon("warning", new vscode.ThemeColor("charts.yellow"))
          : new vscode.ThemeIcon("check", new vscode.ThemeColor("charts.green")),
      });
      rows.push({
        kind: "action",
        label: "Open documentation",
        detail: "View the generated docs site.",
        icon: new vscode.ThemeIcon("book"),
        command: "modelith.docsOpen",
      });
      if (stale) {
        rows.push({
          kind: "action",
          label: "Regenerate documentation",
          detail: "The docs are older than the model. Regenerate to refresh them.",
          icon: new vscode.ThemeIcon("refresh"),
          command: "modelith.docsGenerate",
        });
      }
    }
    return rows;
  }
}
