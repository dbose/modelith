import * as path from "node:path";
import * as vscode from "vscode";
import { CanvasManager } from "./canvasPanel";
import { ModelDiagnostics } from "./diagnostics";
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

export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
  const out = vscode.window.createOutputChannel("Modelith");
  ctx.subscriptions.push(out);

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
  status.text = "$(circle-outline) Modelith";
  status.command = "modelith.validate";
  status.show();
  ctx.subscriptions.push(status);

  const diagnostics = new ModelDiagnostics(status);
  diagnostics.register(ctx);

  canvas = new CanvasManager(out);
  ctx.subscriptions.push({ dispose: () => canvas.dispose() });

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

  cmd("modelith.validate", () => withModelDir(async () => diagnostics.run()));

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
      // generate into the dbt project when one exists next to the model
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
      } else {
        void vscode.window
          .showErrorMessage("Modelith: drift check failed.", "Show Output")
          .then((a) => a && out.show());
      }
    }),
  );

  cmd("modelith.lintFix", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      const r = await runMdl(bin, ["lint", "-m", ".", "--fix"], dir);
      out.appendLine(r.stdout + r.stderr);
      diagnostics.schedule();
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
        diagnostics.schedule();
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

  // React to setting changes that invalidate detection.
  ctx.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("modelith.mdlPath")) resetMdlCache();
    }),
  );

  // First-run niceties: schema registration + initial validation, both async
  // and non-blocking. In a devcontainer this runs inside the container.
  const modelDir = await findModelDir();
  if (modelDir) {
    void registerSchemas(ctx, modelDir);
    diagnostics.schedule();
  }
}

export function deactivate(): void {
  canvas?.dispose();
}
