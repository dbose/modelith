import * as path from "node:path";
import * as vscode from "vscode";
import type { LanguageClient } from "vscode-languageclient/node";
import { CanvasManager } from "./canvasPanel";
import { registerChatParticipant } from "./chatParticipant";
import { DriftCodeActionProvider, DriftManager } from "./driftDiagnostics";
import { DriftTreeProvider } from "./driftView";
import { executeLspCommand, startLsp } from "./lspClient";
import { type Decision, ReverseReviewProvider } from "./reverseView";
import { registerMcpProvider } from "./mcpProvider";
import {
  findDbtProjectDir,
  findManifestPath,
  findMdl,
  findModelDir,
  hasAiCommands,
  isMdlNotFound,
  offerCliInstall,
  resetMdlCache,
  runMdl,
  upgradeHint,
} from "./mdl";
import { registerSchemas } from "./schemas";
import { initTelemetry, track } from "./telemetry";

let canvas: CanvasManager;
let client: LanguageClient | undefined;

/** Extract the Decision from a Reverse-tree node passed to an inline command. */
function decisionOf(node: unknown): Decision | undefined {
  if (node && typeof node === "object" && "decision" in node) {
    return (node as { decision: Decision }).decision;
  }
  return undefined;
}

export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
  const out = vscode.window.createOutputChannel("Modelith");
  ctx.subscriptions.push(out);

  // Seed the telemetry gate and subscribe to mid-session telemetry-setting changes
  // BEFORE the first track() so the enabled state is correct from the start.
  initTelemetry(ctx, out);

  // Editor-funnel: the extension activated (a dbt/model workspace was opened, or a
  // command was invoked). Acquisition/retention signal, keyed on machineId.
  track("extension_activated");

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
  status.text = "$(circle-outline) Modelith";
  status.command = "modelith.openPreview";
  status.show();
  ctx.subscriptions.push(status);

  canvas = new CanvasManager(out);
  ctx.subscriptions.push({ dispose: () => canvas.dispose() });

  // AI surfaces: the bundled MCP server (Copilot Chat agent mode) and the
  // `@modelith` chat participant (ask mode). Both are internally guarded against
  // hosts that lack their API, so a failure here never blocks the LSP or canvas.
  registerMcpProvider(ctx);
  registerChatParticipant(ctx);

  // Drift surfacing: one DriftManager owns the drift diagnostics + the last report;
  // the tree view and code-action provider read from it. A status-bar item reflects
  // the drift state and reveals the view.
  const drift = new DriftManager(out);
  ctx.subscriptions.push({ dispose: () => drift.dispose() });
  const driftStatus = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 89);
  driftStatus.command = "modelith.driftFocusView";
  ctx.subscriptions.push(driftStatus);
  const driftTree = new DriftTreeProvider(drift, findModelDir);
  ctx.subscriptions.push(
    vscode.window.registerTreeDataProvider("modelithDrift", driftTree),
    vscode.languages.registerCodeActionsProvider(
      [
        { scheme: "file", language: "yaml" },
        { scheme: "file", pattern: "**/*.yaml" },
      ],
      new DriftCodeActionProvider(drift),
      { providedCodeActionKinds: DriftCodeActionProvider.kinds },
    ),
    drift.onDidChange((report) => {
      if (!report || report.items.length === 0) {
        driftStatus.text = "$(check) Drift: clean";
        driftStatus.tooltip = "No drift vs the dbt manifest";
      } else {
        const b = report.breaking_count;
        const safe = report.safe_count;
        driftStatus.text = b
          ? `$(error) Drift: ${b} breaking`
          : `$(warning) Drift: ${safe} safe`;
        driftStatus.tooltip = `${b} breaking, ${safe} safe to reconcile — click to view`;
      }
      driftStatus.show();
    }),
  );

  // Reverse Review: the decision-ledger proposals from `mdl reverse`, in a tree with
  // Accept/Reject actions — the editor form of `mdl reverse --interactive`.
  const reverse = new ReverseReviewProvider(out);
  ctx.subscriptions.push(vscode.window.registerTreeDataProvider("modelithReverse", reverse));
  void reverse.refresh();

  // After a window reload VS Code restores our webview panels, but the `mdl serve`
  // child died with the old extension host, so the restored iframe points at a
  // dead port (blank). Re-hydrate the canvas (restart server + rewrite iframe);
  // drop the restored preview pane (it re-opens cheaply and follows the editor).
  ctx.subscriptions.push(
    vscode.window.registerWebviewPanelSerializer("modelithCanvas", {
      async deserializeWebviewPanel(panel) {
        const dir = await findModelDir();
        if (dir) await canvas.restore(panel, dir);
        else panel.dispose();
      },
    }),
    vscode.window.registerWebviewPanelSerializer("modelithPreview", {
      async deserializeWebviewPanel(panel) {
        panel.dispose(); // stale; user reopens via the command
      },
    }),
  );

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

  // Every CLI-backed command flows through cmd(). Wrap it once so that if any of
  // them hits a missing `mdl`, the user gets the one-click installer (doc §4.1)
  // instead of an opaque error — the single highest first-run bounce point.
  const cmd = (id: string, fn: (...a: unknown[]) => unknown) =>
    ctx.subscriptions.push(
      vscode.commands.registerCommand(id, async (...a: unknown[]) => {
        try {
          return await fn(...a);
        } catch (e) {
          if (isMdlNotFound(e)) {
            await offerCliInstall();
            return;
          }
          throw e;
        }
      }),
    );

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
      // Version-skew handshake. The extension and the `mdl` CLI install separately,
      // so a stale CLI can lack commands the AI surfaces (chat participant, MCP
      // server) call. Probe once; if it's too old, tell the user how to upgrade
      // THEIR install (inferred from the resolved path) — once, dismissibly, and
      // without blocking anything. The per-call guards remain the backstop.
      void hasAiCommands(bin, root).then((ok) => {
        if (ok) return;
        out.appendLine(`[mdl] ${bin.label} is missing the 'model'/'mcp' commands (out of date)`);
        void vscode.window.showWarningMessage(
          `Your Modelith CLI (${bin.label}) is out of date — it lacks commands the ` +
            `chat and MCP features need. To fix, ${upgradeHint(bin)}, then reload VS Code.`,
          "Reload Window",
        ).then((pick) => {
          if (pick === "Reload Window") {
            void vscode.commands.executeCommand("workbench.action.reloadWindow");
          }
        });
      });
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
        // Activation wow (editor surface) — the model preview beside the YAML.
        // Fired on first open of a session, however reached (status bar, walkthrough,
        // the demo hand-off). `surface` distinguishes it from the full Open Canvas.
        track("canvas_opened_in_editor", { surface: "preview" });
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
          // Re-render the preview when a model YAML is saved, so it reflects edits
          // (from the editor, or a `mdl new`/CLI change reopened in the editor).
          vscode.workspace.onDidSaveTextDocument((doc) => {
            if (preview && doc.uri.fsPath.endsWith(".yaml")) {
              void renderPreview(dir, previewFocus(vscode.window.activeTextEditor?.document));
            }
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
      const r = await runMdl(bin, ["validate", "-m", "."], dir, out);
      out.appendLine(r.stdout + r.stderr);
      out.show(true);
    }),
  );

  cmd("modelith.openCanvas", () =>
    withModelDir(async (dir) => {
      try {
        await canvas.open(dir);
        track("canvas_opened_in_editor"); // activation wow, editor surface
      } catch (e) {
        if (isMdlNotFound(e)) throw e; // let cmd() offer the one-click installer
        void vscode.window.showErrorMessage(`Modelith canvas: ${e}`);
      }
    }),
  );

  cmd("modelith.import", () =>
    withModelDir(async (dir) => {
      if (vscode.workspace.getConfiguration("modelith").get<boolean>("canvas.readOnly")) {
        void vscode.window.showWarningMessage(
          "Modelith: the canvas is in read-only mode — turn off modelith.canvas.readOnly to import.",
        );
        return;
      }
      try {
        // Reveal the canvas with the Import wizard open. Import writes to the
        // working tree (direct-write, like the CLI) — you review the git diff.
        await canvas.open(dir, "import=1");
      } catch (e) {
        if (isMdlNotFound(e)) throw e; // let cmd() offer the one-click installer
        void vscode.window.showErrorMessage(`Modelith canvas: ${e}`);
      }
    }),
  );

  cmd("modelith.stopServer", () => canvas.stop());

  // Palette-discoverable one-click CLI install (also offered automatically when a
  // command finds no `mdl`). Registered directly, not via cmd(), so it never
  // recurses into the not-found handler.
  ctx.subscriptions.push(
    vscode.commands.registerCommand("modelith.installCli", () => offerCliInstall()),
  );

  // Scaffold the bundled demo (walkthrough step 2). `mdl init --demo` with no path
  // writes to ~/modelith-demo — NEVER the user's own repo — so this does not need
  // an existing model dir; it runs from the workspace root (or home) purely so
  // `mdl` resolves. Goes through cmd() so a missing CLI offers the installer.
  cmd("modelith.initDemo", async () => {
    track("walkthrough_try_demo_clicked");
    const os = require("node:os");
    const path = require("node:path");
    const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? os.homedir();
    const bin = await findMdl(cwd);
    const r = await runMdl(bin, ["init", "--demo"], cwd, out);
    out.appendLine(r.stdout + r.stderr);
    if (r.code !== 0) {
      out.show(true);
      return;
    }
    const demoDir = path.join(os.homedir(), "modelith-demo");
    // The user clicked "Create the demo model" — that IS the consent, so open the
    // new window directly rather than making them chase a corner toast. Hand off via
    // globalState (shared across windows) so the new window's activate() auto-runs
    // the walkthrough + canvas + YAML. The new window itself is the feedback.
    await ctx.globalState.update("modelith.pendingDemoAutoOpen", demoDir);
    await vscode.commands.executeCommand(
      "vscode.openFolder",
      vscode.Uri.file(demoDir),
      { forceNewWindow: true },
    );
  });

  // Re-open the Getting Started walkthrough on demand (the native auto-open only
  // fires once, on install). Registered directly — it touches no `mdl`. The id is
  // <publisher>.<extension>#<walkthroughId>; it must match the PUBLISHED publisher
  // exactly or VS Code silently no-ops.
  ctx.subscriptions.push(
    vscode.commands.registerCommand("modelith.openWalkthrough", () => {
      track("walkthrough_opened");
      return vscode.commands.executeCommand(
        "workbench.action.openWalkthrough",
        "BosonResearch.modelith-vscode#modelith.gettingStarted",
        false,
      );
    }),
  );

  cmd("modelith.generate", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      const dbtDir = await findDbtProjectDir();
      const args = ["generate", "-m", "."];
      if (dbtDir) args.push("-o", dbtDir);
      const r = await runMdl(bin, args, dir, out);
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
      // Populate the Problems panel + the Drift tree + the status bar from one
      // `mdl drift --explain --format json` run (in DriftManager), replacing the old
      // text-dump-to-output behaviour.
      const report = await drift.check(dir);
      if (!report) return;
      if (report.items.length === 0) {
        void vscode.window.setStatusBarMessage("Modelith: no drift ✓", 4000);
      } else {
        void vscode.commands.executeCommand("modelithDrift.focus");
      }
    }),
  );

  cmd("modelith.driftReconcile", () =>
    withModelDir(async (dir) => {
      const report = drift.report;
      const safe = report?.safe_count ?? 0;
      if (safe === 0) {
        void vscode.window.showInformationMessage(
          "Modelith: no safe (additive/cosmetic) drift to reconcile. Breaking drift needs a human decision.",
        );
        return;
      }
      const ok = await vscode.window.showWarningMessage(
        `Reconcile ${safe} safe drift change(s) into the model? Breaking changes are left untouched.`,
        { modal: true },
        "Reconcile",
      );
      if (ok !== "Reconcile") return;
      const manifest = await findManifestPath();
      if (!manifest) return;
      const bin = await findMdl(dir);
      const r = await runMdl(
        bin,
        ["drift", "--manifest", manifest, "-m", ".", "--reconcile"],
        dir,
      );
      out.appendLine(r.stdout + r.stderr);
      if (r.code === 0 || r.code === 2) {
        void vscode.window.setStatusBarMessage("Modelith: reconciled safe drift ✓", 4000);
        await drift.check(dir); // refresh diagnostics + tree from the new state
      } else {
        void vscode.window
          .showErrorMessage("Modelith: reconcile failed.", "Show Output")
          .then((a) => a && out.show());
      }
    }),
  );

  cmd("modelith.driftExplain", (...args: unknown[]) => {
    // Hand off to the @modelith chat participant, optionally scoped to one model.
    const model = typeof args[0] === "string" ? ` ${args[0]}` : "";
    void vscode.commands.executeCommand("workbench.action.chat.open", {
      query: `@modelith /drift${model}`,
    });
  });

  cmd("modelith.driftFocusView", () =>
    vscode.commands.executeCommand("modelithDrift.focus"),
  );

  // --- reverse engineering -----------------------------------------------------

  cmd("modelith.reverse", () =>
    withModelDir(async (dir) => {
      const source = await vscode.window.showQuickPick(
        [
          { label: "$(package) dbt project", detail: "manifest.json or an emitted schema.yml", id: "dbt" },
          { label: "$(file-code) SQL DDL file", detail: "a CREATE TABLE … script", id: "ddl" },
          { label: "$(database) Live database connection", detail: "coming soon (Phase 2)", id: "live" },
        ],
        { placeHolder: "Reverse-engineer from which source?" },
      );
      if (!source) return;
      if (source.id === "live") {
        void vscode.window.showInformationMessage(
          "Live-database reverse (via a dbt profile) is coming in a later release. For now, reverse from a dbt project or a SQL DDL file.",
        );
        return;
      }
      const picked = await vscode.window.showOpenDialog({
        canSelectMany: false,
        openLabel: source.id === "ddl" ? "Reverse this DDL" : "Reverse this dbt artifact",
        filters:
          source.id === "ddl"
            ? { "SQL DDL": ["sql"] }
            : { "dbt artifacts": ["json", "yml", "yaml"] },
      });
      if (!picked || picked.length === 0) return;
      const src = picked[0].fsPath;
      const bin = await findMdl(dir);
      const args =
        source.id === "ddl"
          ? ["reverse", "--ddl", src, "-o", ".", "--no-review"]
          : ["reverse", "--project", src, "-o", ".", "--no-review"];
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Modelith: reverse-engineering…" },
        async () => {
          const r = await runMdl(bin, args, dir, out);
          out.appendLine(r.stdout + r.stderr);
          if (r.code !== 0) {
            void vscode.window
              .showErrorMessage("Modelith: reverse failed.", "Show Output")
              .then((a) => a && out.show());
            return;
          }
          const summary = r.stdout.split("\n").find((l) => l.includes("reversed")) ?? "reverse complete";
          void vscode.window.showInformationMessage(`Modelith: ${summary.trim()}`);
        },
      );
      await reverse.refresh();
      void vscode.commands.executeCommand("modelithReverse.focus");
    }),
  );

  cmd("modelith.reverseRefresh", () => reverse.refresh());

  cmd("modelith.reverseAccept", (node: unknown) => {
    const d = decisionOf(node);
    if (d) void reverse.setVerdict(d.signal_key, "accept");
  });

  cmd("modelith.reverseReject", (node: unknown) => {
    const d = decisionOf(node);
    if (d) void reverse.setVerdict(d.signal_key, "reject");
  });

  cmd("modelith.lintFix", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      const r = await runMdl(bin, ["lint", "-m", ".", "--fix"], dir, out);
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
      const r = await runMdl(bin, ["new", "entity", name, "-m", "."], dir, out);
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
          const r = await runMdl(bin, ["ontology", "vendor", "fibo", "-m", "."], dir, out);
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

  // Demo hand-off: if this window was just opened ON the demo folder by
  // `modelith.initDemo` (flag set in the previous window, shared via globalState),
  // continue the walkthrough here and open the canvas beside a logical-model YAML —
  // so the demo lands with the "wow" already on screen.
  void continueDemoIfHandedOff(ctx);
}

async function continueDemoIfHandedOff(ctx: vscode.ExtensionContext): Promise<void> {
  const pending = ctx.globalState.get<string>("modelith.pendingDemoAutoOpen");
  if (!pending) return;
  const here = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  // Only fire in the window actually opened on the demo folder.
  if (!here || here !== pending) return;
  await ctx.globalState.update("modelith.pendingDemoAutoOpen", undefined); // one-shot
  // The activation "wow": the demo landed in a new window with canvas + YAML.
  track("demo_opened_in_new_window");

  const yamls = await vscode.workspace.findFiles(
    "model/logical/entities/*.yaml",
    undefined,
    1,
  );
  try {
    // Order matters so FOCUS lands on the YAML, ready to edit and watch the canvas
    // follow. Open the walkthrough and the preview first (both non-focus-stealing),
    // then show the YAML LAST with focus.
    // 1. Walkthrough tab, available but not focused. Route through our own command
    //    so the open is tracked once, in one place (walkthrough_opened).
    await vscode.commands.executeCommand("modelith.openWalkthrough");
    // 2. Open a YAML so the preview has a file to follow, then the Model Preview
    //    BESIDE it (openPreview uses ViewColumn.Beside with preserveFocus).
    if (yamls.length) {
      const doc = await vscode.workspace.openTextDocument(yamls[0]);
      await vscode.window.showTextDocument(doc, vscode.ViewColumn.One, true);
    }
    await vscode.commands.executeCommand("modelith.openPreview");
    // 3. Finally focus the YAML — the user lands here, canvas beside, ready to edit.
    if (yamls.length) {
      const doc = await vscode.workspace.openTextDocument(yamls[0]);
      await vscode.window.showTextDocument(doc, vscode.ViewColumn.One, false);
    }
  } catch {
    // best-effort — the walkthrough buttons still work manually
  }
}

export function deactivate(): Thenable<void> | undefined {
  canvas?.dispose();
  return client?.stop();
}
