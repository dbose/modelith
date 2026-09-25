import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import type { LanguageClient } from "vscode-languageclient/node";
import { CanvasManager } from "./canvasPanel";
import { registerChatParticipant } from "./chatParticipant";
import { ConfigTreeProvider } from "./configView";
import { DocsProvider } from "./docsView";
import { DriftCodeActionProvider, DriftManager } from "./driftDiagnostics";
import { DriftTreeProvider } from "./driftView";
import { executeLspCommand, startLsp } from "./lspClient";
import { type Decision, ReverseReviewProvider } from "./reverseView";
import { registerMcpProvider } from "./mcpProvider";
import { OntologyProvider } from "./ontologyView";
import { type LiveWizardContext, runReverseLiveWizard } from "./reverseLiveWizard";
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
import { StatusModel } from "./statusModel";
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

/** What a reverse target folder already contains. Reverse must never silently write
 * into something it doesn't recognise, so we classify the target by cheap structural
 * signals (the same way `git init` / `dbt init` probe a directory) before writing. */
type TargetState =
  | "empty" //  absent or empty -> safe to write, zero friction
  | "model" //  a Modelith model (has mdl-project.yaml) -> offer new-folder/drift/overwrite
  | "partial" // Modelith layout (logical/ or conceptual/) but no project file -> damaged
  | "foreign"; // non-empty, none of the above -> write into a model/ subfolder

function classifyTarget(dir: string): TargetState {
  try {
    if (!fs.existsSync(dir)) return "empty";
    if (fs.existsSync(path.join(dir, "mdl-project.yaml"))) return "model";
    const entries = fs.readdirSync(dir);
    if (entries.length === 0) return "empty";
    // Modelith's signature layout (see mdl_reverse.writer.write_model): a model dir
    // holds logical/ and conceptual/ trees. Their presence without a project file means
    // a model whose mdl-project.yaml was renamed/deleted — recognisable, not foreign.
    if (entries.includes("logical") || entries.includes("conceptual")) return "partial";
    return "foreign";
  } catch {
    // If we can't read it, treat it as foreign (the cautious branch) rather than empty.
    return "foreign";
  }
}

/** In-memory content for the "before" side of an import diff. Keyed by a counter so
 * successive imports don't collide. Backed by a TextDocumentContentProvider registered in
 * activate() under the `modelith-before` scheme. */
const beforeContents = new Map<string, string>();
let beforeSeq = 0;

/** In-memory content for the two sides of a "compare reversed models" diff — each is a
 * `mdl model render` canonical text (ULID-free, name-keyed), so VS Code's native diff shows
 * the semantic delta with no ULID noise. Backed by the `modelith-render` scheme. */
const renderContents = new Map<string, string>();
let renderSeq = 0;

/** Show a native diff of mdl-project.yaml: its pre-import content (left) vs the current
 * on-disk content (right), so "review the diff" is literal, not a status-bar hint. */
async function showProjectDiff(
  ctx: vscode.ExtensionContext,
  projPath: string,
  before: string,
): Promise<void> {
  const key = String(beforeSeq++);
  beforeContents.set(key, before);
  const left = vscode.Uri.parse(`modelith-before:mdl-project.yaml?${key}`);
  const right = vscode.Uri.file(projPath);
  await vscode.commands.executeCommand(
    "vscode.diff",
    left,
    right,
    "mdl-project.yaml — imported changes",
  );
}

/** Open the model's mdl-project.yaml so the user can review/tweak the reverse: block. */
async function openProjectYaml(dir: string): Promise<void> {
  const p = path.join(dir, "mdl-project.yaml");
  if (fs.existsSync(p)) {
    const doc = await vscode.workspace.openTextDocument(p);
    await vscode.window.showTextDocument(doc, { preview: false });
  }
}

/** The bundled reverse: starter packs, shipped in the vsix under media/reverse-configs/. */
function starterPacks(ctx: vscode.ExtensionContext): { label: string; detail: string; path: string }[] {
  const base = path.join(ctx.extensionPath, "media", "reverse-configs");
  const known = [
    { file: "kimball.yaml", label: "dbt / Kimball", detail: "stg_/int_ excluded · dim_/fct_ entities" },
    { file: "medallion.yaml", label: "Medallion", detail: "bronze / silver / gold layers" },
    { file: "data-vault.yaml", label: "Data Vault", detail: "hub / link / satellite" },
  ];
  return known
    .map((k) => ({ label: k.label, detail: k.detail, path: path.join(base, k.file) }))
    .filter((k) => fs.existsSync(k.path));
}

/** A tiny, dependency-free YAML serializer for the suggestion PREVIEW only (block-style,
 * enough for the reverse: block: nested maps, lists of scalars, and inline {…} for the
 * leaf match objects). The actual write is done by the CLI's ruamel round-trip. */
function toYaml(obj: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  if (Array.isArray(obj)) {
    return obj
      .map((v) => {
        if (v !== null && typeof v === "object") {
          // render the object's first key on the "- " line, the rest indented under it
          const body = toYaml(v, indent + 1);
          return `${pad}-${body.slice(pad.length + 1)}`;
        }
        return `${pad}- ${scalar(v)}`;
      })
      .join("\n");
  }
  if (obj !== null && typeof obj === "object") {
    return Object.entries(obj as Record<string, unknown>)
      .map(([k, v]) => {
        if (v !== null && typeof v === "object") return `${pad}${k}:\n${toYaml(v, indent + 1)}`;
        return `${pad}${k}: ${scalar(v)}`;
      })
      .join("\n");
  }
  return `${pad}${scalar(obj)}`;
}
function scalar(v: unknown): string {
  if (typeof v === "string") return /[:#{}[\]]/.test(v) ? `"${v}"` : v;
  return String(v);
}

/** Next free `model-reversed-v<N>` sibling name for a target dir, mirroring the CLI's
 * no-clobber guard so the extension proposes the same default the CLI would pick. */
function suggestSiblingName(targetDir: string): string {
  const parent = path.dirname(targetDir);
  const stem = `${path.basename(targetDir)}-reversed`;
  let n = 1;
  while (fs.existsSync(path.join(parent, `${stem}-v${n}`))) n += 1;
  return `${stem}-v${n}`;
}

/** The best default target for a reverse: the workspace's real Modelith model dir when
 * one exists (so reverse feeds the model the user actually has — the sibling `model/`,
 * not a stray folder inside the dbt project), else `model/` beside the source. This is
 * only the DEFAULT — the user always confirms it in the target picker. */
async function bestDefaultTarget(sourceDir: string): Promise<string> {
  const existing = await findModelDir();
  return existing ?? path.join(sourceDir, "model");
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

  // Shared workspace assessment (`mdl status`): one source of truth for "what next",
  // feeding the Reverse view's resting row, the docs-state context keys, and the panel
  // next-actions. Fail-soft: an old CLI without `mdl status` just leaves it empty.
  const statusModel = new StatusModel(out);
  ctx.subscriptions.push({ dispose: () => statusModel.dispose() });
  ctx.subscriptions.push(
    statusModel.onDidChange((s) => {
      void vscode.commands.executeCommand(
        "setContext",
        "modelith.hasModels",
        !!s && s.entity_count > 0,
      );
      void vscode.commands.executeCommand(
        "setContext",
        "modelith.hasPendingDecisions",
        !!s && s.pending_count > 0,
      );
      void vscode.commands.executeCommand(
        "setContext",
        "modelith.docsGenerated",
        !!s && s.docs_generated,
      );
    }),
  );

  // Reverse Review: the decision-ledger proposals from `mdl reverse`, in a tree with
  // Accept/Reject actions — the editor form of `mdl reverse --interactive`.
  const reverse = new ReverseReviewProvider(out, statusModel);
  ctx.subscriptions.push(vscode.window.registerTreeDataProvider("modelithReverse", reverse));
  void reverse.refresh();
  void statusModel.refresh();

  // Ontology: proposed alignments awaiting review (promote/reject) + industry coverage.
  // The review surface for ontology alignment, mirroring Reverse Review.
  const ontology = new OntologyProvider(out);
  ctx.subscriptions.push(vscode.window.registerTreeDataProvider("modelithOntology", ontology));
  void ontology.refresh();

  // Documentation: warehouse-global model docs (dbt-docs style), a concern of the WHOLE
  // model — deliberately its own frame, not hung off Reverse Review. Driven by the
  // shared StatusModel so its generated/stale state stays current.
  const docs = new DocsProvider(statusModel);
  ctx.subscriptions.push(vscode.window.registerTreeDataProvider("modelithDocs", docs));

  // Live refresh: when the decision ledger, the compiled manifest, or a model file
  // changes on disk, re-read proposals, re-assess the workspace, and re-read ontology
  // alignments so the panel's counts and next-actions stay current without a manual
  // refresh. (The only prior watcher fed the LSP; the trees were command-driven.)
  const stateWatcher = vscode.workspace.createFileSystemWatcher(
    "**/{.mdl/decisions.yaml,target/manifest.json,target/mdl-docs/index.html}",
  );
  // Derive the model dir from a changed `.mdl/decisions.yaml` (two parents up), so a
  // reverse into a FRESH dir (e.g. model_reversed/) refreshes THAT ledger — not the
  // shallowest auto-discovered model dir (the empty-frame bug). A manifest/docs change
  // carries no such dir, so those fall back to auto-discovery.
  const onStateChange = (uri: vscode.Uri) => {
    let dir: string | undefined;
    const p = uri.fsPath;
    if (p.endsWith(`${path.sep}decisions.yaml`) && p.includes(`${path.sep}.mdl${path.sep}`)) {
      // <modelDir>/.mdl/decisions.yaml -> <modelDir>
      dir = path.dirname(path.dirname(path.dirname(p)));
    }
    void reverse.refresh(dir);
    void statusModel.refresh(dir);
    void ontology.refresh(dir);
  };
  ctx.subscriptions.push(
    stateWatcher,
    stateWatcher.onDidChange(onStateChange),
    stateWatcher.onDidCreate(onStateChange),
    stateWatcher.onDidDelete(onStateChange),
  );

  // Warehouse Config: a live view of how the reverse: config classifies every dbt model,
  // grouped by role, with Suggest/Import in its title bar. The home of the config workflow.
  const configTree = new ConfigTreeProvider(out);
  ctx.subscriptions.push(vscode.window.registerTreeDataProvider("modelithConfig", configTree));
  void configTree.refresh();

  // Active-model indicator: which model dir the panels read. In a multi-model workspace
  // (e.g. an original model/ plus a fresh model_reversed/) auto-discovery picks the
  // shallowest, which may not be the one the user is reviewing. This status-bar item
  // shows the active model and lets them switch it (sets modelith.modelDir, honored by
  // findModelDir). Refreshes all state-driven panels on change.
  const activeModelStatus = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    88,
  );
  activeModelStatus.command = "modelith.setActiveModel";
  ctx.subscriptions.push(activeModelStatus);
  const refreshActiveModelStatus = async () => {
    const dir = await findModelDir();
    if (dir) {
      activeModelStatus.text = `$(database) ${path.basename(dir)}`;
      activeModelStatus.tooltip = `Modelith active model: ${dir}\nClick to switch`;
      activeModelStatus.show();
    } else {
      activeModelStatus.hide();
    }
  };
  void refreshActiveModelStatus();
  // Serves the "before" side of an import diff (the pre-import mdl-project.yaml).
  ctx.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("modelith-before", {
      provideTextDocumentContent(uri) {
        return beforeContents.get(uri.query) ?? "";
      },
    }),
    vscode.workspace.registerTextDocumentContentProvider("modelith-render", {
      provideTextDocumentContent(uri) {
        return renderContents.get(uri.query) ?? "";
      },
    }),
  );

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

  cmd("modelith.docsGenerate", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Modelith: generating docs…" },
        async () => {
          const r = await runMdl(bin, ["docs", "generate", "-m", "."], dir, out);
          out.appendLine(r.stdout + r.stderr);
          if (r.code !== 0) {
            out.show(true);
            void vscode.window.showErrorMessage("Modelith: docs generation failed — see output.");
            return;
          }
          track("docs_generated");
          await statusModel.refresh(dir);
          const open = "Open Docs";
          const choice = await vscode.window.showInformationMessage(
            "Modelith docs generated.",
            open,
          );
          if (choice === open) await canvas.openDocs(dir);
        },
      );
    }),
  );

  cmd("modelith.docsOpen", () =>
    withModelDir(async (dir) => {
      // Generate on demand if the site is missing, so "Open Docs" always shows something.
      if (!statusModel.status?.docs_generated) {
        const bin = await findMdl(dir);
        const r = await runMdl(bin, ["docs", "generate", "-m", "."], dir, out);
        out.appendLine(r.stdout + r.stderr);
        await statusModel.refresh(dir);
      }
      try {
        await canvas.openDocs(dir);
        track("docs_opened");
      } catch (e) {
        if (isMdlNotFound(e)) throw e;
        void vscode.window.showErrorMessage(`Modelith docs: ${e}`);
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
      // Always reveal the Drift view: a deliberate "Check Drift" (menu, or the reverse
      // "Check drift instead" modal) must land the user somewhere visible, even when the
      // model is clean — the view renders its own "no drift ✓" resting row. Focusing only
      // on findings made a clean check look like nothing happened.
      void vscode.commands.executeCommand("modelithDrift.focus");
      if (report.items.length === 0) {
        void vscode.window.setStatusBarMessage("Modelith: no drift ✓", 4000);
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

  // Inline "Map to dbt model…" on a `model_removed` drift item. Often the entity isn't
  // really gone from the warehouse — it just materialises under a name this project's
  // convention doesn't resolve yet (price -> stg_price). Let the user teach the mapping:
  // pick the real dbt model (candidates ranked by similarity), write it to
  // reverse.model_map via the CLI, then re-run drift so the false finding clears.
  cmd("modelith.driftMapModel", (node: unknown) => withModelDir(async (dir) => {
    const item =
      node && typeof node === "object" && "item" in node
        ? (node as { item: { model: string } }).item
        : undefined;
    if (!item?.model) return;
    const entity = item.model;
    const bin = await findMdl(dir);
    const manifest = await findManifestPath();
    if (!manifest) {
      void vscode.window.showWarningMessage(
        "Modelith: no dbt manifest found — run `dbt docs generate` (or Check Drift) first.",
      );
      return;
    }
    // Ask the CLI for the unclaimed dbt models, ranked best-match-first for this entity.
    const r = await runMdl(
      bin,
      ["mapping", "list", "-m", ".", "--manifest", manifest, "--rank-for", entity, "--format", "json"],
      dir,
      out,
    );
    let candidates: string[] = [];
    try {
      candidates = (JSON.parse(r.stdout || "{}").unclaimed_models as string[]) ?? [];
    } catch {
      candidates = [];
    }
    const ENTER_MANUALLY = "$(edit) Enter a dbt model name…";
    const items: vscode.QuickPickItem[] = candidates.length
      ? candidates.map((m) => ({ label: m }))
      : [{ label: ENTER_MANUALLY, alwaysShow: true }];
    const picked = await vscode.window.showQuickPick(items, {
      placeHolder: `Which dbt model materialises “${entity}”?`,
      matchOnDescription: true,
    });
    if (!picked) return;
    let target = picked.label;
    if (target === ENTER_MANUALLY || !candidates.includes(target)) {
      const typed = await vscode.window.showInputBox({
        title: `Map “${entity}” to a dbt model`,
        prompt: "Exact dbt model name",
        validateInput: (v) => (v.trim() ? undefined : "Enter a model name."),
      });
      if (!typed) return;
      target = typed.trim();
    }
    const w = await runMdl(bin, ["mapping", "set", entity, target, "-m", "."], dir, out);
    out.appendLine(w.stdout + w.stderr);
    if (w.code !== 0) {
      void vscode.window
        .showErrorMessage("Modelith: could not write the mapping.", "Show Output")
        .then((a) => a && out.show());
      return;
    }
    void vscode.window.setStatusBarMessage(`Modelith: mapped ${entity} → ${target} ✓`, 4000);
    await drift.check(dir); // re-run drift; the false model_removed clears
  }));

  // --- warehouse config workflow (author / discover / iterate / import) --------

  cmd("modelith.configRefresh", () => configTree.refresh());

  cmd("modelith.configFocusView", () =>
    vscode.commands.executeCommand("modelithConfig.focus"),
  );

  // Discover: run `reverse config suggest`, preview the proposed reverse: block in an
  // editor, and apply on confirmation. Preview-then-apply — never a silent write.
  cmd("modelith.configSuggest", () => withModelDir(async (dir) => {
    const manifest = await findManifestPath();
    if (!manifest) {
      void vscode.window.showWarningMessage(
        "Modelith: no dbt manifest found — run `dbt docs generate` first, then suggest config.",
      );
      return;
    }
    const bin = await findMdl(dir);
    const r = await runMdl(
      bin,
      ["reverse-config", "suggest", "--manifest", manifest, "-m", ".", "--format", "json"],
      dir,
      out,
    );
    let payload: { reverse?: unknown; rationale?: string[] } = {};
    try {
      payload = JSON.parse(r.stdout || "{}");
    } catch {
      payload = {};
    }
    if (!payload.reverse || Object.keys(payload.reverse as object).length === 0) {
      void vscode.window.showInformationMessage(
        "Modelith: no clear folder/prefix conventions found — nothing to suggest. Author the reverse: block by hand, or import a starter pack.",
      );
      return;
    }
    // Preview the suggestion as a readable YAML doc (with the rationale as comments) so
    // the user reviews exactly what will be added before it's written.
    const rationale = (payload.rationale ?? []).map((l) => `# ${l}`).join("\n");
    const yaml = toYaml({ reverse: payload.reverse });
    const doc = await vscode.workspace.openTextDocument({
      language: "yaml",
      content: `# Suggested Modelith reverse: config (review, then Apply)\n${rationale}\n${yaml}`,
    });
    await vscode.window.showTextDocument(doc, { preview: true });
    const APPLY = "Apply to mdl-project.yaml";
    const choice = await vscode.window.showInformationMessage(
      "Apply this suggested config? It merges into mdl-project.yaml — review it as a git diff before committing.",
      APPLY,
    );
    if (choice !== APPLY) return;
    const w = await runMdl(
      bin,
      ["reverse-config", "suggest", "--manifest", manifest, "-m", ".", "--apply"],
      dir,
      out,
    );
    if (w.code !== 0) {
      void vscode.window.showErrorMessage("Modelith: could not apply the config.", "Show Output")
        .then((a) => a && out.show());
      return;
    }
    await configTree.refresh();
    void openProjectYaml(dir);
    void vscode.window.setStatusBarMessage("Modelith: applied suggested config ✓", 4000);
  }));

  // Share: import a reverse: config — a bundled starter pack, a file, or a URL.
  cmd("modelith.configImport", () => withModelDir(async (dir) => {
    const packs = starterPacks(ctx);
    const FILE = "$(file) From a file…";
    const URL = "$(link) From a URL…";
    const picked = await vscode.window.showQuickPick(
      [
        ...packs.map((p) => ({ label: `$(cloud-download) ${p.label}`, detail: p.detail, id: p.path })),
        { label: FILE, detail: "A local reverse: config YAML", id: "file" },
        { label: URL, detail: "An https:// URL a team published", id: "url" },
      ],
      { placeHolder: "Import a reverse: config to merge into this model" },
    );
    if (!picked) return;
    let src: string | undefined;
    if (picked.id === "file") {
      const f = await vscode.window.showOpenDialog({
        canSelectMany: false, filters: { "reverse config": ["yaml", "yml"] },
        openLabel: "Import this config",
      });
      src = f?.[0]?.fsPath;
    } else if (picked.id === "url") {
      src = await vscode.window.showInputBox({
        title: "Import reverse: config from a URL",
        prompt: "https:// URL of a shared reverse: config",
        validateInput: (v) => (/^https?:\/\//.test(v.trim()) ? undefined : "Enter an http(s) URL."),
      });
    } else {
      src = picked.id; // a bundled starter pack path
    }
    if (!src) return;
    const bin = await findMdl(dir);
    // Preview first: --dry-run fetches + merges + validates, printing the resulting
    // reverse: block WITHOUT writing. Show it, then apply on confirm (never a silent
    // remote write). A real diff of the project file follows on apply.
    const preview = await runMdl(bin, ["reverse-config", "import", src, "-m", ".", "--dry-run"], dir, out);
    if (preview.code !== 0) {
      const msg = (preview.stderr.trim() || preview.stdout.trim()).split("\n")[0];
      void vscode.window.showErrorMessage(`Modelith: ${msg || "import failed."}`, "Show Output")
        .then((a) => a && out.show());
      return;
    }
    const doc = await vscode.workspace.openTextDocument({
      language: "yaml",
      content: preview.stdout,
    });
    await vscode.window.showTextDocument(doc, { preview: true });
    const APPLY = "Apply to mdl-project.yaml";
    const choice = await vscode.window.showInformationMessage(
      `Import this config from ${src}? It merges into mdl-project.yaml — review the diff before committing.`,
      APPLY,
    );
    if (choice !== APPLY) return;
    // capture the before-image for a real diff
    const projPath = path.join(dir, "mdl-project.yaml");
    const before = fs.existsSync(projPath) ? fs.readFileSync(projPath, "utf8") : "";
    const r = await runMdl(bin, ["reverse-config", "import", src, "-m", "."], dir, out);
    if (r.code !== 0) {
      void vscode.window.showErrorMessage("Modelith: import failed.", "Show Output")
        .then((a) => a && out.show());
      return;
    }
    await configTree.refresh();
    await showProjectDiff(ctx, projPath, before); // literal "review the diff"
    void vscode.window.setStatusBarMessage("Modelith: imported config — review the diff ✓", 5000);
  }));

  // Click a model in the tree -> open mdl-project.yaml so the user can tweak the rule.
  cmd("modelith.configOpenProject", () => withModelDir((dir) => openProjectYaml(dir)));

  // Switch which model dir the panels read (multi-model workspaces). Sets
  // modelith.modelDir (workspace scope) so findModelDir resolves it everywhere, then
  // refreshes every state-driven panel + the status bar.
  cmd("modelith.setActiveModel", async () => {
    const hits = await vscode.workspace.findFiles("**/mdl-project.yaml", "**/node_modules/**", 20);
    if (hits.length === 0) {
      void vscode.window.showWarningMessage("Modelith: no mdl-project.yaml found in this workspace.");
      return;
    }
    const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const items = hits
      .map((h) => path.dirname(h.fsPath))
      .sort()
      .map((dir) => ({
        label: `$(database) ${path.basename(dir)}`,
        description: ws ? path.relative(ws, dir) || "." : dir,
        dir,
      }));
    const pick = await vscode.window.showQuickPick(items, {
      title: "Set the active Modelith model",
      placeHolder: "Which model should the panels read?",
    });
    if (!pick) return;
    const rel = ws ? path.relative(ws, pick.dir) : pick.dir;
    await vscode.workspace
      .getConfiguration("modelith")
      .update("modelDir", rel || ".", vscode.ConfigurationTarget.Workspace);
    await Promise.all([
      reverse.refresh(pick.dir),
      statusModel.refresh(pick.dir),
      ontology.refresh(pick.dir),
      configTree.refresh(),
      refreshActiveModelStatus(),
    ]);
    void vscode.window.setStatusBarMessage(`Modelith: active model → ${path.basename(pick.dir)}`, 4000);
  });

  // Assign a role to an unclassified custom-prefix group (e.g. pres_art_* -> mart), or
  // exclude it — the discover→assign UX. `node` is the prefixGroup tree node; its id is
  // `prefix:<pfx>`. Authors a reverse.layers or reverse.exclude entry via `reverse-config
  // apply` (reading a temp block file), then refreshes the classification.
  const prefixOf = (node: unknown): string | undefined => {
    const id = (node as { id?: string })?.id;
    return id?.startsWith("prefix:") ? id.slice("prefix:".length) : undefined;
  };
  const applyReverseBlock = async (dir: string, block: unknown): Promise<boolean> => {
    const bin = await findMdl(dir);
    const tmp = path.join(os.tmpdir(), `mdl-reverse-block-${Date.now()}.json`);
    await fs.promises.writeFile(tmp, JSON.stringify(block), "utf8");
    try {
      const r = await runMdl(bin, ["reverse-config", "apply", tmp, "-m", "."], dir, out);
      if (r.code !== 0) {
        void vscode.window
          .showErrorMessage("Modelith: could not apply the config.", "Show Output")
          .then((a) => a && out.show());
        return false;
      }
      return true;
    } finally {
      void fs.promises.unlink(tmp).catch(() => undefined);
    }
  };

  cmd("modelith.configAssignPrefix", (node: unknown) =>
    withModelDir(async (dir) => {
      const prefix = prefixOf(node);
      if (!prefix) return;
      const ROLES = [
        { label: "Mart", role: "mart" },
        { label: "Dimension", role: "dimension" },
        { label: "Fact", role: "fact" },
        { label: "Staging (exclude)", role: "staging" },
        { label: "Exclude entirely", role: "__exclude__" },
      ];
      const pick = await vscode.window.showQuickPick(
        ROLES.map((r) => r.label),
        { title: `Classify ${prefix}* models`, placeHolder: "Assign a role, or exclude" },
      );
      if (!pick) return;
      const chosen = ROLES.find((r) => r.label === pick)!;
      const block =
        chosen.role === "__exclude__"
          ? { exclude: [`${prefix}*`] }
          : { layers: [{ name: prefix.replace(/_+$/, ""), role: chosen.role, match: { prefix } }] };
      if (await applyReverseBlock(dir, block)) {
        await configTree.refresh();
        void vscode.window.setStatusBarMessage(`Modelith: classified ${prefix}* ✓`, 4000);
      }
    }),
  );

  cmd("modelith.configExcludePrefix", (node: unknown) =>
    withModelDir(async (dir) => {
      const prefix = prefixOf(node);
      if (!prefix) return;
      if (await applyReverseBlock(dir, { exclude: [`${prefix}*`] })) {
        await configTree.refresh();
        void vscode.window.setStatusBarMessage(`Modelith: excluded ${prefix}* ✓`, 4000);
      }
    }),
  );

  // --- reverse engineering -----------------------------------------------------

  // Run `mdl reverse …` in `cwd`, then surface what needs human review. Shared by the
  // context-menu Reverse Engineer command. The CLI writes into `cwd/model` (and diverts
  // to model-reversed-v<N> if that already holds a model — the no-clobber guard lives in
  // the CLI). On success we refresh + reveal the Reverse Review panel; when the engine
  // left ambiguous inferences pending, we say so loudly (they are the whole point of the
  // review flow) with a button that jumps to the panel.
  const runReverseInto = async (
    cwd: string,
    args: string[],
    reverseView: ReverseReviewProvider,
    sourceLabel?: string,
  ): Promise<void> => {
    const bin = await findMdl(cwd);
    // Where the CLI wrote. Default is <cwd>/<-o value>; the no-clobber guard may divert
    // to a sibling and announce it as "reversing into <path> instead" — honor that so
    // the review panel reads the model we actually just created.
    const outFlag = args[args.indexOf("-o") + 1] ?? "model";
    let writtenDir = path.isAbsolute(outFlag) ? outFlag : path.join(cwd, outFlag);
    // Trace what's happening so a surprising result (wrong dir, 0 entities) is diagnosable
    // from the Modelith output channel without guesswork.
    out.appendLine(`[reverse] source: ${sourceLabel ?? "(cwd)"}  (cwd ${cwd})`);
    out.appendLine(`[reverse] target: ${writtenDir}  (force=${args.includes("--force")})`);
    let ok = false;
    let summary = "reverse complete";
    // True when the CLI (or its output) reports nothing was reversed — a staging-only
    // warehouse. We warn with the remedy and DON'T open an empty review panel.
    let empty = false;
    let emptyMsg = "";
    // Set when the no-clobber guard diverted to a sibling (model existed) — the trigger to
    // offer a semantic compare of the new reverse against the existing model.
    let divertedTo = "";
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Modelith: reverse-engineering…" },
      async () => {
        // A live reverse (`--connect`) runs dbt debug + introspection over a possibly-cold
        // warehouse; give it 5 minutes instead of the default 2.
        const timeoutMs = args.includes("--connect") ? 300000 : 120000;
        const r = await runMdl(bin, args, cwd, out, timeoutMs);
        out.appendLine(r.stdout + r.stderr);
        const zeroEntities = /reversed 0 entities/i.test(r.stdout + r.stderr);
        if (r.code !== 0) {
          if (zeroEntities) {
            // Expected, guided failure (CLI ≥0.4.6): nothing was written.
            empty = true;
            emptyMsg = (r.stderr.trim() || r.stdout.trim()).split("\n")[0];
            return;
          }
          void vscode.window
            .showErrorMessage("Modelith: reverse failed.", "Show Output")
            .then((a) => a && out.show());
          return;
        }
        // Belt-and-suspenders for an older CLI that exits 0 on a hollow reverse: refuse to
        // celebrate or focus an empty model.
        if (zeroEntities) {
          empty = true;
          emptyMsg = (r.stdout.split("\n").find((l) => /reversed 0 entities/i.test(l)) ?? "").trim();
          return;
        }
        ok = true;
        summary = r.stdout.split("\n").find((l) => l.includes("reversed")) ?? summary;
        const diverted = (r.stdout + r.stderr).match(/reversing into (.+?) instead/);
        if (diverted) {
          writtenDir = diverted[1].trim();
          divertedTo = writtenDir;
        }
      },
    );
    if (empty) {
      out.appendLine(`[reverse] ${emptyMsg}`);
      void vscode.window
        .showWarningMessage(
          emptyMsg ||
            "Modelith: reversed 0 entities — this warehouse has no mart/entity models (run `mdl generate` first).",
          "Show Output",
        )
        .then((a) => a === "Show Output" && out.show());
      return;
    }
    if (!ok) return;
    out.appendLine(`[reverse] ${summary.trim()}`);
    await reverseView.refresh(writtenDir);
    void vscode.commands.executeCommand("modelithReverse.focus");
    const pending = reverseView.pendingCount;
    if (pending > 0) {
      const REVIEW = "Review decisions";
      void vscode.window
        .showInformationMessage(
          `Modelith: ${summary.trim()} — ${pending} decision${pending === 1 ? "" : "s"} need your review.`,
          REVIEW,
        )
        .then((a) => a === REVIEW && vscode.commands.executeCommand("modelithReverse.focus"));
    } else {
      void vscode.window.showInformationMessage(`Modelith: ${summary.trim()}`);
    }
    // The reverse diverted to a sibling because a model already existed — offer to see
    // what the new reverse changed vs the existing model (name-keyed semantic diff, so
    // the two versions' different ULIDs don't drown the real delta).
    if (divertedTo) {
      const COMPARE = "Compare with existing model";
      void vscode.window
        .showInformationMessage(
          `Modelith: reversed into ${path.basename(divertedTo)} (a model already existed).`,
          COMPARE,
        )
        .then((a) => a === COMPARE && vscode.commands.executeCommand("modelith.compareReversed"));
    }
  };

  // Decide WHERE a right-click reverse should write, handling every "something is
  // already here" case so we never silently overwrite or mix into an existing model.
  // Returns the target dir to write into (absolute), or undefined to abort (the user
  // cancelled, or chose to check drift instead — which we launch here). `manifest` is
  // the drift source when one is resolvable (dbt reverse); undefined for DDL reverse,
  // where a drift comparison is impossible and that arm is hidden.
  const resolveReverseTarget = async (
    defaultTarget: string,
    manifest: string | undefined,
  ): Promise<{ target: string; force: boolean } | undefined> => {
    // A model living elsewhere in the workspace is the strongest signal the user may
    // want drift, not a second model — surface it first (only if we can drift it).
    const existing = await findModelDir();
    if (existing && path.resolve(existing) !== path.resolve(defaultTarget) && manifest) {
      const DRIFT = "Check drift instead";
      const GO = "Continue reversing";
      const choice = await vscode.window.showInformationMessage(
        `You already have a Modelith model at ${vscode.workspace.asRelativePath(existing)}. ` +
          "Check drift against it, or reverse this source into a new model?",
        { modal: true },
        DRIFT,
        GO,
      );
      if (!choice) return undefined; // cancelled
      if (choice === DRIFT) {
        await vscode.commands.executeCommand("modelith.driftCheck");
        return undefined;
      }
      // GO -> fall through to target classification
    }

    const state = classifyTarget(defaultTarget);
    // Happy path: an empty/absent target. Still confirm WHERE via the pre-filled picker
    // (the user always sees and can correct the destination), but no modal is stacked.
    if (state === "empty") return promptForFolder(defaultTarget);

    const rel = vscode.workspace.asRelativePath(defaultTarget);
    const NEW_FOLDER = "Reverse into a new folder";
    const OVERWRITE = "Update in place";
    const DRIFT = "Check drift instead";

    let choice: string | undefined;
    if (state === "model") {
      // "Update in place" re-reverses into the SAME folder: it applies (and preserves)
      // the folder's reverse:/naming:/glossary: config, refreshes the entities, and
      // prunes ones that no longer exist — the natural "I tweaked the config, re-run"
      // flow. "Reverse into a new folder" keeps a fresh snapshot beside it.
      const actions = manifest ? [OVERWRITE, NEW_FOLDER, DRIFT] : [OVERWRITE, NEW_FOLDER];
      choice = await vscode.window.showInformationMessage(
        `Re-reverse the model at ${rel}? "Update in place" applies and keeps that folder's ` +
          "config; \"new folder\" writes a fresh copy beside it" +
          (manifest ? ", or check drift against it." : "."),
        { modal: true },
        ...actions,
      );
    } else if (state === "partial") {
      choice = await vscode.window.showWarningMessage(
        `${rel} looks like a Modelith model with a missing or renamed project file. ` +
          "Update it in place, or reverse into a new folder?",
        { modal: true },
        OVERWRITE,
        NEW_FOLDER,
      );
    } else {
      // foreign: non-empty folder that isn't a model. Offer a self-contained subfolder.
      const SUBFOLDER = "Reverse into a subfolder";
      choice = await vscode.window.showWarningMessage(
        `${rel} isn't empty and isn't a Modelith model. Reverse into a new subfolder inside it?`,
        { modal: true },
        SUBFOLDER,
      );
      if (choice !== SUBFOLDER) return undefined;
      return promptForFolder(path.join(defaultTarget, "model"));
    }

    if (!choice) return undefined; // cancelled / dismissed
    if (choice === DRIFT) {
      await vscode.commands.executeCommand("modelith.driftCheck");
      return undefined;
    }
    if (choice === OVERWRITE) return { target: defaultTarget, force: true }; // in place
    // NEW_FOLDER -> pre-fill the CLI's own next-free sibling name, let the user edit.
    const suggested = path.join(path.dirname(defaultTarget), suggestSiblingName(defaultTarget));
    return promptForFolder(suggested);
  };

  // A pre-filled, editable, validated target-folder input box. Returns the chosen path
  // (force:false — a fresh folder), or undefined if cancelled. Rejects an existing model.
  const promptForFolder = async (
    suggestedAbs: string,
  ): Promise<{ target: string; force: boolean } | undefined> => {
    const rel = vscode.workspace.asRelativePath(suggestedAbs);
    const picked = await vscode.window.showInputBox({
      title: "Reverse Engineer — target folder",
      prompt: "Where to write the reversed model (relative to the workspace)",
      value: rel,
      valueSelection: [Math.max(0, rel.lastIndexOf("/") + 1), rel.length],
      validateInput: (v) => {
        const t = v.trim();
        if (!t) return "Enter a folder path.";
        const abs = path.isAbsolute(t)
          ? t
          : path.join(
              vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? path.dirname(suggestedAbs),
              t,
            );
        if (fs.existsSync(path.join(abs, "mdl-project.yaml"))) {
          return "A Modelith model already exists there — choose another folder.";
        }
        return undefined;
      },
    });
    if (picked === undefined) return undefined;
    const t = picked.trim();
    const abs = path.isAbsolute(t)
      ? t
      : path.join(
          vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? path.dirname(suggestedAbs),
          t,
        );
    return { target: abs, force: false };
  };

  // The live-database wizard borrows the reverse closures (they own the output channel,
  // the review-panel refresh, and the target picker). runReverseInto is curried with the
  // shared reverse view so the wizard's signature stays small.
  const liveWizardCtx: LiveWizardContext = {
    out,
    findMdl,
    runMdl,
    bestDefaultTarget,
    resolveReverseTarget,
    runReverseInto: (cwd, args, sourceLabel) => runReverseInto(cwd, args, reverse, sourceLabel),
  };
  cmd("modelith.reverseLive", () => runReverseLiveWizard(liveWizardCtx));

  // Compare two reversed models with VS Code's native diff, fed Modelith-rendered
  // canonical text (`mdl model render`) so the diff is name-keyed and ULID-free — two
  // independent reverses have different ULIDs, so a raw YAML diff would flag everything.
  const renderModel = async (dir: string): Promise<string | undefined> => {
    const bin = await findMdl(dir);
    const r = await runMdl(bin, ["model", "render", "-m", "."], dir, out);
    if (r.code !== 0) {
      void vscode.window
        .showErrorMessage(`Modelith: could not render ${path.basename(dir)}.`, "Show Output")
        .then((a) => a && out.show());
      return undefined;
    }
    return r.stdout;
  };
  const showModelCompare = async (leftDir: string, rightDir: string): Promise<void> => {
    const [leftText, rightText] = await Promise.all([renderModel(leftDir), renderModel(rightDir)]);
    if (leftText === undefined || rightText === undefined) return;
    const lk = String(renderSeq++);
    const rk = String(renderSeq++);
    renderContents.set(lk, leftText);
    renderContents.set(rk, rightText);
    const left = vscode.Uri.parse(`modelith-render:${path.basename(leftDir)}.model?${lk}`);
    const right = vscode.Uri.parse(`modelith-render:${path.basename(rightDir)}.model?${rk}`);
    await vscode.commands.executeCommand(
      "vscode.diff",
      left,
      right,
      `${path.basename(leftDir)} ↔ ${path.basename(rightDir)} — semantic model diff`,
    );
  };
  // Find `model-reversed-v*` siblings of a model dir (the no-clobber guard's output).
  const reversedSiblings = (modelDir: string): string[] => {
    const parent = path.dirname(modelDir);
    const base = path.basename(modelDir);
    try {
      return fs
        .readdirSync(parent)
        .filter((n) => n.startsWith(`${base}-reversed-v`))
        .map((n) => path.join(parent, n))
        .filter((p) => fs.existsSync(path.join(p, "mdl-project.yaml")));
    } catch {
      return [];
    }
  };
  cmd("modelith.compareReversed", async () => {
    const dir = await findModelDir();
    if (!dir) {
      void vscode.window.showInformationMessage("Modelith: no model to compare.");
      return;
    }
    const siblings = reversedSiblings(dir);
    let other: string | undefined;
    if (siblings.length === 1) {
      other = siblings[0];
    } else if (siblings.length > 1) {
      const pick = await vscode.window.showQuickPick(
        siblings.map((p) => ({ label: path.basename(p), dir: p })),
        { placeHolder: "Compare your model against which reversed version?" },
      );
      other = pick?.dir;
    } else {
      // no sibling — let the user pick any other model folder
      const picked = await vscode.window.showOpenDialog({
        canSelectFolders: true,
        canSelectFiles: false,
        openLabel: "Compare against this model",
      });
      other = picked?.[0]?.fsPath;
    }
    if (!other) return;
    await showModelCompare(dir, other);
  });

  cmd("modelith.reverse", () =>
    withModelDir(async (dir) => {
      const source = await vscode.window.showQuickPick(
        [
          { label: "$(package) dbt project", detail: "manifest.json or an emitted schema.yml", id: "dbt" },
          { label: "$(file-code) SQL DDL file", detail: "a CREATE TABLE … script", id: "ddl" },
          {
            label: "$(database) Live database connection",
            detail: "introspect a live warehouse via dbt + profiles.yml",
            id: "live",
          },
        ],
        { placeHolder: "Reverse-engineer from which source?" },
      );
      if (!source) return;
      if (source.id === "live") {
        await runReverseLiveWizard(liveWizardCtx);
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
      let empty = false;
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Modelith: reverse-engineering…" },
        async () => {
          const r = await runMdl(bin, args, dir, out);
          out.appendLine(r.stdout + r.stderr);
          const zeroEntities = /reversed 0 entities/i.test(r.stdout + r.stderr);
          if (zeroEntities) {
            // Staging-only warehouse: nothing to reverse. Warn with the remedy; don't
            // open an empty review panel.
            empty = true;
            const msg = (r.stderr.trim() || r.stdout.trim()).split("\n")[0];
            void vscode.window
              .showWarningMessage(
                msg ||
                  "Modelith: reversed 0 entities — no mart/entity models to reverse (run `mdl generate` first).",
                "Show Output",
              )
              .then((a) => a === "Show Output" && out.show());
            return;
          }
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
      if (empty) return;
      await reverse.refresh();
      void vscode.commands.executeCommand("modelithReverse.focus");
    }),
  );

  // Right-click "Reverse Engineer" on the Explorer. ONE command that dispatches on
  // what was clicked, so every reverse source shares this entry-point (and the planned
  // live-datastore path is a filled-in branch, not new UI):
  //   - dbt_project.yml  -> reverse from its build artifacts (target/manifest.json);
  //     if none, guide the user to `dbt docs generate` first.
  //   - a folder / a .sql -> reverse the DDL under it (CLI 0.4.1 accepts a directory).
  //   - profiles.yml      -> live-datastore reverse: a guided stub for now (install the
  //     dbt adapter, pick a profile), with its branch reserved for the CLI path.
  // All paths reverse into a fresh `model/` beside the source, then reveal the Reverse
  // Review panel so the ambiguous decisions the engine recorded can't be missed.
  cmd("modelith.reverseEngineer", async (arg: unknown) => {
    const uri = arg instanceof vscode.Uri ? arg : undefined;
    if (!uri) {
      // Invoked from the palette (no clicked resource) — send them to the wizard.
      await vscode.commands.executeCommand("modelith.reverse");
      return;
    }
    const fsPath = uri.fsPath;
    const name = path.basename(fsPath);
    const isDir = fs.existsSync(fsPath) && fs.statSync(fsPath).isDirectory();

    // profiles.yml -> live-datastore reverse, seeded with THIS file's folder.
    if (name === "profiles.yml") {
      await runReverseLiveWizard({ ...liveWizardCtx, presetProfilesDir: path.dirname(fsPath) });
      return;
    }

    // dbt_project.yml -> reverse from build artifacts (target/manifest.json).
    if (name === "dbt_project.yml") {
      const projectDir = path.dirname(fsPath);
      const manifest = path.join(projectDir, "target", "manifest.json");
      if (!fs.existsSync(manifest)) {
        const GEN = "Run dbt docs generate";
        const choice = await vscode.window.showWarningMessage(
          "No dbt build artifacts found (target/manifest.json). Run `dbt docs generate` " +
            "(or `dbt compile`) in this project first, then Reverse Engineer again — the " +
            "catalog is what lets reverse detect surrogate keys and SCD2 columns.",
          GEN,
        );
        if (choice === GEN) {
          const term = vscode.window.createTerminal({ name: "dbt docs generate", cwd: projectDir });
          term.show(true);
          term.sendText("dbt docs generate", true);
        }
        return;
      }
      // dbt reverse CAN drift (a manifest exists), so the drift arm is available.
      // Default the target at the workspace's real model dir, not a folder inside the
      // dbt project — then let the user confirm/redirect in the picker.
      const res = await resolveReverseTarget(await bestDefaultTarget(projectDir), manifest);
      if (!res) return; // cancelled, or handed off to drift
      const args = ["reverse", "--project", manifest, "-o", res.target];
      if (res.force) args.push("--force");
      await runReverseInto(projectDir, args, reverse, manifest);
      return;
    }

    // a folder or a .sql file -> reverse the DDL under/at it.
    const isSql = name.toLowerCase().endsWith(".sql");
    if (isDir || isSql) {
      const cwd = isDir ? fsPath : path.dirname(fsPath);
      const ddlArg = fsPath;
      // DDL reverse has no manifest -> no drift comparison possible (arm hidden).
      const res = await resolveReverseTarget(await bestDefaultTarget(cwd), undefined);
      if (!res) return;
      const args = ["reverse", "--ddl", ddlArg, "-o", res.target];
      if (res.force) args.push("--force");
      await runReverseInto(cwd, args, reverse, ddlArg);
      return;
    }

    void vscode.window.showInformationMessage(
      "Reverse Engineer works on a dbt_project.yml, a profiles.yml, a folder of .sql DDL, or a .sql file.",
    );
  });

  cmd("modelith.reverseRefresh", () => reverse.refresh());

  cmd("modelith.ontologyRefresh", () => ontology.refresh());

  cmd("modelith.ontologyPromote", (node: unknown) => {
    const a = ontology.proposalOf(node);
    return a ? ontology.act(a, "promote") : undefined;
  });

  cmd("modelith.ontologyReject", (node: unknown) => {
    const a = ontology.proposalOf(node);
    return a ? ontology.act(a, "reject") : undefined;
  });

  cmd("modelith.ontologyAlign", () =>
    withModelDir(async (dir) => {
      const bin = await findMdl(dir);
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Modelith: aligning to ontology…" },
        async () => {
          const r = await runMdl(bin, ["ontology", "align", "-m", "."], dir, out);
          out.appendLine(r.stdout + r.stderr);
          await ontology.refresh(dir);
        },
      );
    }),
  );

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
