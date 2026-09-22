import * as vscode from "vscode";
import { findManifestPath, findMdl, findModelDir, runMdl } from "./mdl";

/** The Warehouse Config tree: a live picture of how the current `reverse:` config
 * classifies every dbt model — grouped by role, each row showing its matched layer,
 * target_form, or the rule that excluded it. It answers "how does Modelith see my
 * warehouse, and what do my conventions do?" at a glance, and hosts the config workflow
 * (Suggest / Import) in its title bar. Fed by `mdl reverse config explain --format json`,
 * so it always reflects the committed config, never a stale in-memory copy. */

export interface ClassifiedModel {
  model: string;
  role: string;
  layer: string | null;
  exempt: boolean;
  excluded: boolean;
  target_form: string;
  pattern: string | null;
}

type Node = GroupNode | ModelNode;
interface GroupNode {
  kind: "group";
  role: string;
  items: ClassifiedModel[];
}
interface ModelNode {
  kind: "model";
  item: ClassifiedModel;
}

// Role -> a friendly group label + an icon. Ordered so the governed entity layers sit
// above the excluded ones (the reading order a modeler wants).
const ROLE_LABEL: Record<string, string> = {
  dimension: "Dimensions",
  fact: "Facts",
  mart: "Marts",
  hub: "Hubs",
  link: "Links",
  satellite: "Satellites",
  bridge: "Bridges",
  business: "Entities",
  staging: "Staging (excluded)",
  intermediate: "Intermediate (excluded)",
  exclude: "Excluded",
};
const ROLE_ORDER = [
  "dimension", "fact", "mart", "hub", "link", "satellite", "bridge", "business",
  "intermediate", "staging", "exclude",
];
const ROLE_ICON: Record<string, vscode.ThemeIcon> = {
  dimension: new vscode.ThemeIcon("symbol-class"),
  fact: new vscode.ThemeIcon("symbol-event"),
  mart: new vscode.ThemeIcon("symbol-structure"),
  hub: new vscode.ThemeIcon("circle-large-outline"),
  link: new vscode.ThemeIcon("link"),
  satellite: new vscode.ThemeIcon("broadcast"),
  bridge: new vscode.ThemeIcon("git-merge"),
  business: new vscode.ThemeIcon("symbol-class"),
  staging: new vscode.ThemeIcon("circle-slash"),
  intermediate: new vscode.ThemeIcon("circle-slash"),
  exclude: new vscode.ThemeIcon("circle-slash"),
};

export class ConfigTreeProvider implements vscode.TreeDataProvider<Node> {
  private readonly emitter = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;
  private models: ClassifiedModel[] = [];
  private hasManifest = true;

  constructor(private readonly out: vscode.OutputChannel) {}

  /** Re-read the classification from `mdl reverse config explain --format json`. */
  async refresh(): Promise<void> {
    const dir = await findModelDir();
    if (!dir) {
      this.models = [];
      this.emitter.fire(undefined);
      return;
    }
    const manifest = await findManifestPath();
    this.hasManifest = !!manifest;
    if (!manifest) {
      this.models = [];
      this.emitter.fire(undefined);
      return;
    }
    try {
      const bin = await findMdl(dir);
      const r = await runMdl(
        bin,
        ["reverse-config", "explain", "--manifest", manifest, "-m", ".", "--format", "json"],
        dir,
      );
      this.models = r.code === 0 && r.stdout.trim() ? (JSON.parse(r.stdout) as ClassifiedModel[]) : [];
    } catch (e) {
      this.out.appendLine(`[config] could not read classification: ${e}`);
      this.models = [];
    }
    // drives the viewsWelcome empty-state (no manifest vs no models)
    void vscode.commands.executeCommand(
      "setContext",
      "modelith.configHasManifest",
      this.hasManifest,
    );
    this.emitter.fire(undefined);
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "group") {
      const ti = new vscode.TreeItem(
        `${ROLE_LABEL[node.role] ?? node.role} (${node.items.length})`,
        vscode.TreeItemCollapsibleState.Expanded,
      );
      ti.iconPath = ROLE_ICON[node.role];
      ti.contextValue = `configGroup:${node.role}`;
      return ti;
    }
    const m = node.item;
    const ti = new vscode.TreeItem(m.model, vscode.TreeItemCollapsibleState.None);
    // a compact right-aligned description: the layer name, target_form, pattern, exempt
    const bits: string[] = [];
    if (m.layer) bits.push(m.layer);
    if (m.target_form && m.target_form !== "denormalized") bits.push(m.target_form);
    if (m.pattern) bits.push(m.pattern);
    if (m.exempt) bits.push("exempt");
    ti.description = bits.join(" · ");
    // a legible tooltip explaining WHY this model classified as it did
    const md = new vscode.MarkdownString();
    md.appendMarkdown(`**${m.model}** — ${m.role}\n\n`);
    if (m.layer) md.appendMarkdown(`Matched layer: \`${m.layer}\`\n\n`);
    if (m.excluded) md.appendMarkdown(`Excluded from the model (not a governed entity).\n\n`);
    if (m.pattern) md.appendMarkdown(`Data Vault pattern: \`${m.pattern}\`\n\n`);
    if (m.target_form !== "denormalized") md.appendMarkdown(`Target form: \`${m.target_form}\`\n\n`);
    ti.tooltip = md;
    ti.iconPath = ROLE_ICON[m.role] ?? new vscode.ThemeIcon("symbol-field");
    ti.contextValue = m.excluded ? "configModel:excluded" : "configModel:entity";
    // clicking a model opens mdl-project.yaml so the user can tweak the rule that classified it
    ti.command = { command: "modelith.configOpenProject", title: "Open mdl-project.yaml" };
    return ti;
  }

  getChildren(node?: Node): Node[] {
    if (!node) {
      const byRole = new Map<string, ClassifiedModel[]>();
      for (const m of this.models) {
        const list = byRole.get(m.role) ?? [];
        list.push(m);
        byRole.set(m.role, list);
      }
      const roles = [...byRole.keys()].sort(
        (a, b) => (ROLE_ORDER.indexOf(a) + 100) - (ROLE_ORDER.indexOf(b) + 100),
      );
      return roles.map((role) => ({
        kind: "group" as const,
        role,
        items: (byRole.get(role) ?? []).sort((a, b) => a.model.localeCompare(b.model)),
      }));
    }
    if (node.kind === "group") {
      return node.items.map((item) => ({ kind: "model" as const, item }));
    }
    return [];
  }

  get modelCount(): number {
    return this.models.length;
  }
}
