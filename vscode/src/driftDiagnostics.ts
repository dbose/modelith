import * as path from "node:path";
import * as vscode from "vscode";
import { findManifestPath, findMdl, findModelDir, runMdl } from "./mdl";

/** Drift as first-class VS Code diagnostics + code actions.
 *
 * One `mdl drift --explain --format json` run is the single source of truth (the
 * same shape the CLI narrative and the MCP tool consume). This module parses it into
 * `DriftItem`s, publishes them to a DiagnosticCollection keyed by the owning model
 * YAML file (so drift shows as squiggles + Problems entries), and offers code actions
 * that mirror the engine's reconcile boundary: additive/cosmetic can be reconciled,
 * breaking is only ever explained. The parsed report is cached so the Drift tree view
 * and the `@modelith /drift` chat command reuse it without re-running drift. */

export interface DriftItem {
  severity: "breaking" | "additive" | "cosmetic" | "unmanaged";
  kind: string;
  model: string;
  column: string | null;
  detail: string;
  payload: Record<string, unknown>;
  reconcilable: boolean;
  reconcile_action: string | null;
  file: string | null;
}

export interface DriftReport {
  target: string;
  max_severity: string | null;
  has_breaking: boolean;
  counts: Record<string, number>;
  safe_count: number;
  breaking_count: number;
  items: DriftItem[];
  reconcilable: DriftItem[];
}

const SEVERITY_ORDER = ["breaking", "unmanaged", "additive", "cosmetic"] as const;

function toVSCodeSeverity(sev: DriftItem["severity"]): vscode.DiagnosticSeverity {
  switch (sev) {
    case "breaking":
      return vscode.DiagnosticSeverity.Error;
    case "additive":
    case "unmanaged":
      return vscode.DiagnosticSeverity.Warning;
    default:
      return vscode.DiagnosticSeverity.Information;
  }
}

/** Manages the drift diagnostics + the cached last report. A single instance is
 * created in activate() and shared with the tree view and the chat command. */
export class DriftManager {
  private readonly collection: vscode.DiagnosticCollection;
  private readonly emitter = new vscode.EventEmitter<DriftReport | undefined>();
  /** Fires whenever a drift check completes (or is cleared); the tree view listens. */
  readonly onDidChange = this.emitter.event;
  private lastReport: DriftReport | undefined;

  constructor(private readonly out: vscode.OutputChannel) {
    this.collection = vscode.languages.createDiagnosticCollection("modelith-drift");
  }

  get report(): DriftReport | undefined {
    return this.lastReport;
  }

  dispose(): void {
    this.collection.dispose();
    this.emitter.dispose();
  }

  /** Run drift and publish diagnostics. Returns the report, or undefined when no
   * manifest is found (the caller surfaces the "run dbt compile" hint). */
  async check(dir: string): Promise<DriftReport | undefined> {
    const manifest = await findManifestPath();
    if (!manifest) {
      void vscode.window.showWarningMessage(
        "Modelith: no manifest.json found — run `dbt parse`/`dbt compile` (or set modelith.manifestPath).",
      );
      return undefined;
    }
    const bin = await findMdl(dir);
    const r = await runMdl(
      bin,
      ["drift", "--manifest", manifest, "-m", ".", "--explain", "--format", "json"],
      dir,
    );
    if (r.code !== 0 && !r.stdout.trim()) {
      this.out.appendLine(`[drift] failed: ${r.stderr}`);
      void vscode.window.showErrorMessage(`Modelith drift: ${r.stderr.trim() || "failed"}`);
      return undefined;
    }
    let report: DriftReport;
    try {
      report = JSON.parse(r.stdout) as DriftReport;
    } catch {
      this.out.appendLine(`[drift] unparseable output: ${r.stdout}\n${r.stderr}`);
      void vscode.window.showErrorMessage("Modelith drift: could not parse the report.");
      return undefined;
    }
    this.publish(dir, report);
    return report;
  }

  /** Publish the report's items as diagnostics grouped by file, and cache it. */
  private publish(dir: string, report: DriftReport): void {
    this.collection.clear();
    const byFile = new Map<string, vscode.Diagnostic[]>();
    for (const item of report.items) {
      // Items resolve to their owning model YAML; those that don't (e.g. a removed
      // model) attach to mdl-project.yaml so they're never silently dropped.
      const rel = item.file ?? "mdl-project.yaml";
      const abs = path.join(dir, rel);
      const diag = new vscode.Diagnostic(
        new vscode.Range(0, 0, 0, 0),
        item.column ? `${item.detail} (column: ${item.column})` : item.detail,
        toVSCodeSeverity(item.severity),
      );
      diag.source = "modelith-drift";
      diag.code = item.kind;
      const list = byFile.get(abs) ?? [];
      list.push(diag);
      byFile.set(abs, list);
    }
    for (const [abs, diags] of byFile) {
      this.collection.set(vscode.Uri.file(abs), diags);
    }
    this.lastReport = report;
    this.emitter.fire(report);
  }

  clear(): void {
    this.collection.clear();
    this.lastReport = undefined;
    this.emitter.fire(undefined);
  }
}

/** Code actions on a drift diagnostic: reconcile a safe change, or explain a breaking
 * one. Mirrors the engine — never offers auto-apply for breaking drift. */
export class DriftCodeActionProvider implements vscode.CodeActionProvider {
  static readonly kinds = [vscode.CodeActionKind.QuickFix];

  constructor(private readonly manager: DriftManager) {}

  provideCodeActions(
    document: vscode.TextDocument,
    _range: vscode.Range | vscode.Selection,
    context: vscode.CodeActionContext,
  ): vscode.CodeAction[] {
    const report = this.manager.report;
    if (!report) return [];
    const actions: vscode.CodeAction[] = [];
    let offeredReconcileAll = false;
    for (const diag of context.diagnostics) {
      if (diag.source !== "modelith-drift") continue;
      // Match the diagnostic back to a report item by kind + this file.
      const item = report.items.find(
        (i) => i.kind === diag.code && document.fileName.endsWith(i.file ?? "mdl-project.yaml"),
      );
      if (!item) continue;
      if (item.reconcilable && !offeredReconcileAll) {
        // Reconcile is whole-report (drift --reconcile folds every safe change), so
        // one action per file is enough; label it for clarity.
        const fix = new vscode.CodeAction(
          "Modelith: Reconcile safe drift into the model",
          vscode.CodeActionKind.QuickFix,
        );
        fix.command = { command: "modelith.driftReconcile", title: "Reconcile safe drift" };
        fix.diagnostics = [diag];
        actions.push(fix);
        offeredReconcileAll = true;
      } else if (item.severity === "breaking") {
        const explain = new vscode.CodeAction(
          "Modelith: Explain this breaking drift (@modelith)",
          vscode.CodeActionKind.QuickFix,
        );
        explain.command = {
          command: "modelith.driftExplain",
          title: "Explain drift",
          arguments: [item.model],
        };
        explain.diagnostics = [diag];
        actions.push(explain);
      }
    }
    return actions;
  }
}

export { SEVERITY_ORDER, toVSCodeSeverity };
export { findModelDir };
