import * as path from "path";
import * as vscode from "vscode";
import { findMdl, findModelDir, runMdl } from "./mdl";

/** The Model tree: the LOGICAL model as an entity → attribute drill-down. Each entity
 * expands to its attributes (PK marked), and clicking an entity or attribute opens the
 * entity's `logical/entities/<name>.yaml` at the right line. This is the "what is my
 * model" view — distinct from the Warehouse Mapping tree, which shows how dbt models
 * classify into entities. Fed by `mdl model tree --format json`, so it always reflects
 * the committed model, never a stale copy. */

export interface TreeAttribute {
  name: string;
  physical_name: string | null;
  domain: string | null;
  role: string;
  nullable: boolean;
  is_pk: boolean;
}
export interface TreeEntity {
  name: string;
  physical_name: string | null;
  subject_area: string | null;
  definition: string | null;
  pattern: string | null;
  file: string | null;
  attributes: TreeAttribute[];
}

type Node = EntityNode | AttributeNode | StatusNode;
interface EntityNode {
  kind: "entity";
  entity: TreeEntity;
}
interface AttributeNode {
  kind: "attribute";
  entity: TreeEntity;
  attr: TreeAttribute;
}
/** A resting row when the model is empty / unreadable, so the pane explains itself
 * instead of rendering blank. */
interface StatusNode {
  kind: "status";
  label: string;
  icon: vscode.ThemeIcon;
}

export class ModelTreeProvider implements vscode.TreeDataProvider<Node> {
  private readonly emitter = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;
  private entities: TreeEntity[] = [];
  private modelDir: string | undefined;

  constructor(private readonly out: vscode.OutputChannel) {}

  /** Re-read the model from `mdl model tree --format json`. */
  async refresh(): Promise<void> {
    const dir = await findModelDir();
    this.modelDir = dir;
    if (!dir) {
      this.entities = [];
      this.emitter.fire(undefined);
      return;
    }
    try {
      const bin = await findMdl(dir);
      const r = await runMdl(bin, ["model", "tree", "-m", ".", "--format", "json"], dir);
      this.entities =
        r.code === 0 && r.stdout.trim()
          ? ((JSON.parse(r.stdout) as { entities?: TreeEntity[] }).entities ?? [])
          : [];
    } catch (e) {
      this.out.appendLine(`[model] could not read the model tree: ${e}`);
      this.entities = [];
    }
    this.emitter.fire(undefined);
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "status") {
      const ti = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.None);
      ti.iconPath = node.icon;
      return ti;
    }
    if (node.kind === "entity") {
      const e = node.entity;
      const ti = new vscode.TreeItem(
        e.name,
        e.attributes.length
          ? vscode.TreeItemCollapsibleState.Collapsed
          : vscode.TreeItemCollapsibleState.None,
      );
      ti.iconPath = new vscode.ThemeIcon("symbol-class");
      const bits: string[] = [`${e.attributes.length} attr`];
      if (e.subject_area) bits.push(e.subject_area);
      if (e.pattern) bits.push(e.pattern);
      ti.description = bits.join(" · ");
      const md = new vscode.MarkdownString();
      md.appendMarkdown(`**${e.name}**${e.physical_name ? ` · \`${e.physical_name}\`` : ""}\n\n`);
      if (e.definition) md.appendMarkdown(`${e.definition}\n\n`);
      if (e.subject_area) md.appendMarkdown(`Subject area: ${e.subject_area}\n\n`);
      ti.tooltip = md;
      ti.contextValue = "modelEntity";
      // click opens the entity's logical YAML
      ti.command = this.openCommand(e, e.name);
      return ti;
    }
    // attribute row
    const a = node.attr;
    const ti = new vscode.TreeItem(a.name, vscode.TreeItemCollapsibleState.None);
    ti.iconPath = new vscode.ThemeIcon(a.is_pk ? "key" : "symbol-field");
    const bits: string[] = [];
    if (a.domain) bits.push(a.domain);
    if (a.is_pk) bits.push("pk");
    if (!a.nullable) bits.push("not null");
    ti.description = bits.join(" · ");
    if (a.physical_name) {
      const md = new vscode.MarkdownString();
      md.appendMarkdown(`**${a.name}** · \`${a.physical_name}\`\n\n${a.domain ?? ""}`);
      ti.tooltip = md;
    }
    ti.contextValue = "modelAttribute";
    // click opens the owning entity's YAML at the attribute line
    ti.command = this.openCommand(node.entity, a.name);
    return ti;
  }

  /** A command that opens the entity's source YAML, revealing the given search term
   * (the entity or attribute name) so the cursor lands near it. */
  private openCommand(entity: TreeEntity, reveal: string): vscode.Command | undefined {
    if (!entity.file || !this.modelDir) return undefined;
    const uri = vscode.Uri.file(path.join(this.modelDir, entity.file));
    return {
      command: "modelith.openModelObject",
      title: "Open in the model YAML",
      arguments: [uri, reveal],
    };
  }

  getChildren(node?: Node): Node[] {
    if (!node) {
      if (!this.modelDir) {
        return [
          {
            kind: "status",
            label: "No model in this workspace",
            icon: new vscode.ThemeIcon("info"),
          },
        ];
      }
      if (!this.entities.length) {
        return [
          {
            kind: "status",
            label: "No entities yet — run mdl init or import a model",
            icon: new vscode.ThemeIcon("info"),
          },
        ];
      }
      return this.entities.map((entity) => ({ kind: "entity" as const, entity }));
    }
    if (node.kind === "entity") {
      return node.entity.attributes.map((attr) => ({
        kind: "attribute" as const,
        entity: node.entity,
        attr,
      }));
    }
    return [];
  }
}

/** Open a model object's YAML and move the cursor to the first line mentioning `reveal`
 * (the entity or attribute name), so a click lands where the user expected. */
export async function openModelObject(uri: vscode.Uri, reveal: string): Promise<void> {
  const doc = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(doc);
  const text = doc.getText();
  // match `name: <reveal>` first (the authored field), else a bare mention
  const idx =
    text.indexOf(`: ${reveal}\n`) >= 0
      ? text.indexOf(`: ${reveal}\n`)
      : text.indexOf(reveal);
  if (idx >= 0) {
    const pos = doc.positionAt(idx);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
  }
}
