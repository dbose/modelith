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
  package?: string | null;
  foreign?: boolean;
  unclassified?: boolean;
}

type Node = GroupNode | PrefixGroupNode | ModelNode;
interface GroupNode {
  kind: "group";
  role: string;
  items: ClassifiedModel[];
}
/** A group of unclassified models sharing a custom prefix (e.g. `pres_art_`), with
 * inline Assign-role / Exclude actions — the discover→assign UX. `prefix` is what an
 * assign/exclude action authors a rule for. */
interface PrefixGroupNode {
  kind: "prefixGroup";
  prefix: string;
  items: ClassifiedModel[];
}
interface ModelNode {
  kind: "model";
  item: ClassifiedModel;
}

/** The leading custom prefix of a model name (first one/two short `_`-tokens), mirroring
 * the CLI's `_unknown_prefix` so the panel groups the same way suggest reports. */
function customPrefix(name: string): string {
  const parts = name.toLowerCase().split("_");
  if (parts.length < 2) return name.toLowerCase();
  if (parts.length >= 3 && parts[0].length <= 4 && parts[1].length <= 4) {
    return `${parts[0]}_${parts[1]}_`;
  }
  return `${parts[0]}_`;
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
    if (node.kind === "prefixGroup") {
      const ti = new vscode.TreeItem(
        `${node.prefix}* (${node.items.length})`,
        vscode.TreeItemCollapsibleState.Expanded,
      );
      ti.iconPath = new vscode.ThemeIcon("question", new vscode.ThemeColor("charts.yellow"));
      ti.description = "unclassified — assign a role or exclude";
      // gates the inline Assign/Exclude actions; `id` carries the prefix for the command
      ti.contextValue = "configUnclassified";
      ti.id = `prefix:${node.prefix}`;
      return ti;
    }
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
      const out: Node[] = [];
      // 1) Unclassified custom prefixes first — the thing needing a decision, grouped by
      // prefix with inline Assign/Exclude. (foreign models are already excluded upstream
      // so they show under their role group as "Tool metadata"; see below.)
      const unclassified = this.models.filter((m) => m.unclassified);
      const byPrefix = new Map<string, ClassifiedModel[]>();
      for (const m of unclassified) {
        const p = customPrefix(m.model);
        (byPrefix.get(p) ?? byPrefix.set(p, []).get(p)!).push(m);
      }
      for (const prefix of [...byPrefix.keys()].sort()) {
        out.push({ kind: "prefixGroup", prefix, items: byPrefix.get(prefix)! });
      }
      // 2) The role groups, excluding the unclassified rows (they're shown above).
      const classified = this.models.filter((m) => !m.unclassified);
      const byRole = new Map<string, ClassifiedModel[]>();
      for (const m of classified) {
        (byRole.get(m.role) ?? byRole.set(m.role, []).get(m.role)!).push(m);
      }
      const roles = [...byRole.keys()].sort(
        (a, b) => (ROLE_ORDER.indexOf(a) + 100) - (ROLE_ORDER.indexOf(b) + 100),
      );
      for (const role of roles) {
        out.push({
          kind: "group",
          role,
          items: (byRole.get(role) ?? []).sort((a, b) => a.model.localeCompare(b.model)),
        });
      }
      return out;
    }
    if (node.kind === "group" || node.kind === "prefixGroup") {
      return node.items
        .slice()
        .sort((a, b) => a.model.localeCompare(b.model))
        .map((item) => ({ kind: "model" as const, item }));
    }
    return [];
  }

  /** All model names carrying a given custom prefix — used by the Assign/Exclude
   * commands to author a rule for the whole group. */
  modelsWithPrefix(prefix: string): string[] {
    return this.models
      .filter((m) => m.unclassified && customPrefix(m.model) === prefix)
      .map((m) => m.model);
  }

  get modelCount(): number {
    return this.models.length;
  }
}
