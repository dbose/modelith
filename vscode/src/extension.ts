import * as path from "node:path";
import * as vscode from "vscode";
import type { LanguageClient } from "vscode-languageclient/node";
import { CanvasManager } from "./canvasPanel";
import { executeLspCommand, startLsp } from "./lspClient";
import {
  findDbtProjectDir,
  findManifestPath,
  findMdl,
  findModelDir,
  resetMdlCache,
  runMdl,
} from "./mdl";
import { registerSchemas } from "./schemas";

let canvas: CanvasManager;
let client: LanguageClient | undefined;

export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
  const out = vscode.window.createOutputChannel("Modelith");
  ctx.subscriptions.push(out);

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
  status.text = "$(circle-outline) Modelith";
  status.command = "modelith.openPreview";
  status.show();
  ctx.subscriptions.push(status);

  canvas = new CanvasManager(out);
  ctx.subscriptions.push({ dispose: () => canvas.dispose() });

  // Status bar reflects the LSP-published diagnostics (source: "modelith").
  const refreshStatus = () => {
    let errors = 0;
    let warnings = 0;
    for (const [, diags] of vscode.languages.getDiagnostics()) {
      for (const d of diags) {
        if (d.source !== "modelith") continue;
        if (d.severity === vscode.DiagnosticSeverity.Error) errors++;
        else if (d.severity === vscode.DiagnosticSeverity.Warning) warnings++;
      }
    }
    status.text = errors
      ? `$(error) Modelith ${errors}`
      : warnings
        ? `$(warning) Modelith ${warnings}`
        : "$(check) Modelith";
  };
  ctx.subscriptions.push(vscode.languages.onDidChangeDiagnostics(refreshStatus));

  const withModelDir = async (fn: (dir: string) => Promise<void>) => {
    const dir = await findModelDir();
    if (!dir) {
      void vscode.window.showWarningMessage(
        "Modelith: no mdl-project.yaml found in this workspace (set modelith.modelDir).",
      );
      return;
    }
    await fn(dir);
  };

  const cmd = (id: string, fn: (...a: unknown[]) => unknown) =>
    ctx.subscriptions.push(vscode.commands.registerCommand(id, fn));

  // --- language server (diagnostics, hover, lens, actions, lift/adopt/unmanage)

  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (root) {
    // Make `mdl` available in every integrated terminal (standalone AND
    // devcontainer — the collection applies wherever the extension host runs).
    // If detection resolved to a concrete binary (.venv or modelith.mdlPath),
    // its directory is prepended to PATH; `uv run mdl` needs nothing extra.
    try {
      const bin = await findMdl(root);
      if (path.isAbsolute(bin.cmd)) {
        ctx.environmentVariableCollection.prepend(
          "PATH",
          path.dirname(bin.cmd) + path.delimiter,
        );
        ctx.environmentVariableCollection.description =
          "Adds the Modelith `mdl` CLI to integrated terminals";
      }
    } catch (e) {
      out.appendLine(`[mdl] detection failed: ${e}`);
    }

    try {
      client = await startLsp(root);
      ctx.subscriptions.push({ dispose: () => void client?.stop() });
    } catch (e) {
      out.appendLine(`[lsp] failed to start: ${e}`);
      status.text = "$(question) Modelith";
      status.tooltip = `Language server failed: ${e}`;
    }
  }

  cmd("modelith.restartLsp", async () => {
    resetMdlCache();
    await client?.stop();
    if (root) client = await startLsp(root);
  });

  cmd("modelith.liftModel", async (...args: unknown[]) => {
    const uri = args[0] instanceof vscode.Uri ? args[0] : undefined;
    const target = uri ?? vscode.window.activeTextEditor?.document.uri;
    if (!target || !client) return;
    await executeLspCommand(client, "mdl.lift", [target.fsPath]);
  });

  // --- preview pane: the diagram beside the SQL, following the active editor

  let preview: vscode.WebviewPanel | undefined;
  const previewFocus = (doc: vscode.TextDocument | undefined): string | undefined => {
    if (!doc) return undefined;
    const ext = path.extname(doc.fileName);
    if (ext !== ".sql" && ext !== ".yaml" && ext !== ".yml") return undefined;
    return path.basename(doc.fileName, ext);
  };

  const renderPreview = async (modelDir: string, focus: string | undefined) => {
    const url = await canvas.externalUrl(modelDir);
    const params = new URLSearchParams({ minimal: "1" });
    if (focus) params.set("focus", focus);
    const full = `${url}?${params.toString()}`;
    preview!.webview.html = `<!DOCTYPE html><html><head><style>
      html,body{height:100%;margin:0;background:#0b0f16}
      iframe{border:0;width:100%;height:100%}
    </style></head><body><iframe src="${full}" allow="clipboard-read; clipboard-write"></iframe></body></html>`;
  };

  cmd("modelith.openPreview", () =>
    withModelDir(async (dir) => {
      if (!preview) {
        preview = vscode.window.createWebviewPanel(
          "modelithPreview",
          "◮ Model Preview",
          { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
          { enableScripts: true, retainContextWhenHidden: true },
        );
        preview.onDidDispose(() => (preview = undefined));
        ctx.subscriptions.push(
          vscode.window.onDidChangeActiveTextEditor((ed) => {
            const focus = previewFocus(ed?.document);
            if (preview && focus) void renderPreview(dir, focus);
          }),
        );
      }
      await renderPreview(dir, previewFocus(vscode.window.activeTextEditor?.document));
      preview.reveal(vscode.ViewColumn.Beside, true);
    }),
  );

  // --- CLI-backed commands (unchanged surface) ---------------------------------

  cmd("modelith.validate", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      const r = await runMdl(bin, ["validate", "-m", "."], dir);
      out.appendLine(r.stdout + r.stderr);
      out.show(true);
    }),
  );

  cmd("modelith.openCanvas", () =>
    withModelDir(async (dir) => {
      try {
        await canvas.open(dir);
      } catch (e) {
        void vscode.window.showErrorMessage(`Modelith canvas: ${e}`);
      }
    }),
  );

  cmd("modelith.stopServer", () => canvas.stop());

  cmd("modelith.generate", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      const dbtDir = await findDbtProjectDir();
      const args = ["generate", "-m", "."];
      if (dbtDir) args.push("-o", dbtDir);
      const r = await runMdl(bin, args, dir);
      out.appendLine(r.stdout + r.stderr);
      if (r.code === 3) {
        void vscode.window
          .showWarningMessage("Modelith: merge conflicts written — resolve before proceeding.", "Show Output")
          .then((a) => a && out.show());
      } else if (r.code !== 0) {
        void vscode.window
          .showErrorMessage("Modelith: generate failed.", "Show Output")
          .then((a) => a && out.show());
      } else {
        void vscode.window.setStatusBarMessage("Modelith: dbt project generated ✓", 4000);
      }
    }),
  );

  cmd("modelith.driftCheck", () =>
    withModelDir(async (dir) => {
      const manifest = await findManifestPath();
      if (!manifest) {
        void vscode.window.showWarningMessage(
          "Modelith: no manifest.json found — run `dbt parse` (or set modelith.manifestPath).",
        );
        return;
      }
      const bin = await findMdl(dir);
      const r = await runMdl(bin, ["drift", "--manifest", manifest, "-m", ".", "--check"], dir);
      out.appendLine(r.stdout + r.stderr);
      if (r.code === 2) {
        void vscode.window
          .showErrorMessage("Modelith: BREAKING drift vs dbt manifest.", "Show Report")
          .then((a) => a && out.show());
      } else if (r.code === 0) {
        const clean = r.stdout.includes("no drift detected");
        void vscode.window.setStatusBarMessage(
          clean ? "Modelith: no drift ✓" : "Modelith: non-breaking drift (see output)",
          5000,
        );
        if (!clean) out.show(true);
      }
    }),
  );

  cmd("modelith.lintFix", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      const r = await runMdl(bin, ["lint", "-m", ".", "--fix"], dir);
      out.appendLine(r.stdout + r.stderr);
    }),
  );

  cmd("modelith.newEntity", () =>
    withModelDir(async (dir) => {
      const name = await vscode.window.showInputBox({
        prompt: "New entity name (snake_case)",
        placeHolder: "e.g. custody_account",
        validateInput: (v) => (v.trim() ? undefined : "name required"),
      });
      if (!name) return;
      const bin = await findMdl(dir);
      const r = await runMdl(bin, ["new", "entity", name, "-m", "."], dir);
      out.appendLine(r.stdout + r.stderr);
      if (r.code === 0) {
        const file = vscode.Uri.file(
          path.join(dir, "logical", "entities", `${name.trim().toLowerCase().replace(/ /g, "_")}.yaml`),
        );
        void vscode.window.showTextDocument(file);
      } else {
        void vscode.window.showErrorMessage(`Modelith: ${r.stdout || r.stderr}`);
      }
    }),
  );

  cmd("modelith.vendorFibo", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      void vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Modelith: vendoring FIBO…" },
        async () => {
          const r = await runMdl(bin, ["ontology", "vendor", "fibo", "-m", "."], dir);
          out.appendLine(r.stdout + r.stderr);
          if (r.code !== 0) void vscode.window.showErrorMessage("Modelith: vendor failed (see output).");
        },
      );
    }),
  );

  cmd("modelith.emitSemantic", () =>
    withModelDir(async (dir) => {
      const fmt = await vscode.window.showQuickPick(["metricflow", "osi"], {
        placeHolder: "Semantic format",
      });
      if (!fmt) return;
      const bin = await findMdl(dir);
      const outFile = path.join(dir, "semantic", `${fmt}.yaml`);
      const r = await runMdl(
        bin,
        ["emit", "semantic", "--format", fmt, "-m", ".", "--out", outFile],
        dir,
      );
      out.appendLine(r.stdout + r.stderr);
      if (r.code === 0) void vscode.window.showTextDocument(vscode.Uri.file(outFile));
      else void vscode.window.showErrorMessage("Modelith: emit failed (see output).");
    }),
  );

  ctx.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("modelith.mdlPath")) resetMdlCache();
    }),
  );

  const modelDir = await findModelDir();
  if (modelDir) void registerSchemas(ctx, modelDir);
}

export function deactivate(): Thenable<void> | undefined {
  canvas?.dispose();
  return client?.stop();
}
