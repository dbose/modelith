import * as path from "node:path";
import * as vscode from "vscode";
import { findMdl, findModelDir, runMdl } from "./mdl";

interface MdlDiagnostic {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
  path: string | null;
  file: string | null;
}

const SEVERITY: Record<string, vscode.DiagnosticSeverity> = {
  error: vscode.DiagnosticSeverity.Error,
  warning: vscode.DiagnosticSeverity.Warning,
  info: vscode.DiagnosticSeverity.Information,
};

export class ModelDiagnostics {
  private collection = vscode.languages.createDiagnosticCollection("modelith");
  private timer: NodeJS.Timeout | undefined;

  constructor(private status: vscode.StatusBarItem) {}

  register(ctx: vscode.ExtensionContext): void {
    ctx.subscriptions.push(this.collection);
    ctx.subscriptions.push(
      vscode.workspace.onDidSaveTextDocument((doc) => {
        const cfg = vscode.workspace.getConfiguration("modelith");
        if (!cfg.get<boolean>("validateOnSave")) return;
        if (!doc.fileName.endsWith(".yaml") && !doc.fileName.endsWith(".yml")) return;
        this.schedule();
      }),
    );
  }

  schedule(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.run(), 300);
  }

  async run(): Promise<void> {
    const modelDir = await findModelDir();
    if (!modelDir) return;
    this.status.text = "$(sync~spin) Modelith";
    try {
      const bin = await findMdl(modelDir);
      const r = await runMdl(bin, ["validate", "-m", ".", "--format", "json"], modelDir);
      // exit 1 with JSON on stdout = validation errors; parse either way
      const parsed = JSON.parse(r.stdout || "{}") as { diagnostics?: MdlDiagnostic[] };
      const diags = parsed.diagnostics ?? [];

      this.collection.clear();
      const byFile = new Map<string, vscode.Diagnostic[]>();
      for (const d of diags) {
        const rel = d.file ?? "mdl-project.yaml";
        const uri = vscode.Uri.file(path.join(modelDir, rel));
        const range = await rangeFor(uri, d.path);
        const vd = new vscode.Diagnostic(range, d.message, SEVERITY[d.severity]);
        vd.code = d.code;
        vd.source = "modelith";
        const list = byFile.get(uri.fsPath) ?? [];
        list.push(vd);
        byFile.set(uri.fsPath, list);
      }
      for (const [file, list] of byFile) {
        this.collection.set(vscode.Uri.file(file), list);
      }

      const errors = diags.filter((d) => d.severity === "error").length;
      const warnings = diags.filter((d) => d.severity === "warning").length;
      this.status.text = errors
        ? `$(error) Modelith ${errors}`
        : warnings
          ? `$(warning) Modelith ${warnings}`
          : "$(check) Modelith";
      this.status.tooltip = errors || warnings ? "Open Problems for MDL-* details" : "model valid";
    } catch (e) {
      this.status.text = "$(question) Modelith";
      this.status.tooltip = String(e);
    }
  }
}

/** Highlight the line declaring the offending ULID where possible, else line 0. */
async function rangeFor(uri: vscode.Uri, ulid: string | null): Promise<vscode.Range> {
  const zero = new vscode.Range(0, 0, 0, 80);
  if (!ulid) return zero;
  try {
    const doc = await vscode.workspace.openTextDocument(uri);
    const text = doc.getText();
    const idx = text.indexOf(ulid);
    if (idx < 0) return zero;
    const pos = doc.positionAt(idx);
    return doc.lineAt(pos.line).range;
  } catch {
    return zero;
  }
}
