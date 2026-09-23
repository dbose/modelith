/**
 * The "Reverse from a live database" wizard — a native MultiStepInput flow that gathers a
 * dbt connection (or scaffolds a profiles.yml), tests it, picks a schema, and runs
 * `mdl reverse --connect`, ending in the same Reverse Review surface a dbt/DDL reverse does.
 *
 * Design: the CLI does the heavy lifting (`mdl reverse --connect` runs `dbt debug`, the
 * introspection macro, and the reverse). The wizard's job is to collect inputs, scaffold a
 * profiles.yml for a warehouse-only team, and hand off to `runReverseInto`. Credentials are
 * never typed here — secrets are env_var() placeholders the user sets themselves; dbt
 * resolves them. See packages/reverse/src/mdl_reverse/connect.py.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { MultiStepInput, type QuickPickParameters } from "./multiStepInput";
import { type MdlBin, offerCliInstall, type RunResult } from "./mdl";

/** A QuickPick item carrying an id (VS Code's QuickPickItem has no id field). */
interface IdItem extends vscode.QuickPickItem {
  id: string;
}

/** A wizard step returns the next step, or nothing to finish. */
type Step = (input: MultiStepInput) => Promise<Step | void>;

/** The helpers the wizard borrows from activate()'s closures. */
export interface LiveWizardContext {
  out: vscode.OutputChannel;
  findMdl: (root: string) => Promise<MdlBin>;
  runMdl: (
    bin: MdlBin,
    args: string[],
    cwd: string,
    out?: vscode.OutputChannel,
    timeoutMs?: number,
  ) => Promise<RunResult>;
  bestDefaultTarget: (sourceDir: string) => Promise<string>;
  resolveReverseTarget: (
    defaultTarget: string,
    manifest: string | undefined,
  ) => Promise<{ target: string; force: boolean } | undefined>;
  runReverseInto: (cwd: string, args: string[], sourceLabel?: string) => Promise<void>;
  /** When the wizard is launched by right-clicking a specific profiles.yml, its folder —
   * skips detection/scaffold and jumps straight to the schema step. */
  presetProfilesDir?: string;
}

const ADAPTERS: { label: string; id: string; detail: string }[] = [
  { label: "DuckDB", id: "duckdb", detail: "file-based, zero-setup (great for a first try)" },
  { label: "Postgres", id: "postgres", detail: "PostgreSQL — enforced PK/FK" },
  { label: "Snowflake", id: "snowflake", detail: "declared keys via SHOW … KEYS" },
  { label: "BigQuery", id: "bigquery", detail: "declared keys via INFORMATION_SCHEMA" },
  { label: "Redshift", id: "redshift", detail: "declared keys via SHOW CONSTRAINTS" },
  { label: "Databricks", id: "databricks", detail: "Unity Catalog information_schema" },
];

interface WizardState {
  profilesDir: string;
  schema: string;
  database?: string;
  select?: string;
  target?: string;
}

/** Find a profiles.yml in the workspace (a dbt team already has one). */
function findProfilesYml(): string | undefined {
  const folders = vscode.workspace.workspaceFolders ?? [];
  for (const f of folders) {
    const direct = path.join(f.uri.fsPath, "profiles.yml");
    if (fs.existsSync(direct)) return f.uri.fsPath;
    // a common layout: <root>/transform/<project>/profiles.yml — shallow scan
    for (const sub of ["transform", "dbt", "warehouse"]) {
      const nested = path.join(f.uri.fsPath, sub);
      if (fs.existsSync(nested)) {
        const found = shallowFind(nested, "profiles.yml", 2);
        if (found) return path.dirname(found);
      }
    }
  }
  return undefined;
}

function shallowFind(dir: string, name: string, depth: number): string | undefined {
  if (depth < 0) return undefined;
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return undefined;
  }
  for (const e of entries) {
    if (e.isFile() && e.name === name) return path.join(dir, e.name);
  }
  for (const e of entries) {
    if (e.isDirectory() && !e.name.startsWith(".") && e.name !== "target") {
      const found = shallowFind(path.join(dir, e.name), name, depth - 1);
      if (found) return found;
    }
  }
  return undefined;
}

export async function runReverseLiveWizard(ctx: LiveWizardContext): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    void vscode.window.showErrorMessage("Modelith: open a folder first.");
    return;
  }
  const root = folder.uri.fsPath;
  let bin: MdlBin;
  try {
    bin = await ctx.findMdl(root);
  } catch {
    await offerCliInstall();
    return;
  }

  const state: Partial<WizardState> = {};
  // A right-click on a specific profiles.yml seeds its folder and skips detection.
  const existing = ctx.presetProfilesDir ?? findProfilesYml();
  if (ctx.presetProfilesDir) state.profilesDir = ctx.presetProfilesDir;

  const pickAdapterToScaffold: Step = async (input) => {
    const items: IdItem[] = [
      {
        label: "$(folder-opened) I already have a profiles.yml…",
        id: "__existing__",
        detail: "point the wizard at a folder containing profiles.yml",
      },
      ...ADAPTERS.map((a) => ({
        label: `$(database) ${a.label}`,
        id: a.id,
        detail: `scaffold a ${a.label} connection — ${a.detail}`,
      })),
    ];
    const pick = await input.showQuickPick<IdItem, QuickPickParameters<IdItem>>({
      title: "Reverse from a live database",
      step: 1,
      totalSteps: 3,
      items,
      placeholder:
        "No profiles.yml found — pick your warehouse to scaffold one (or point at an existing)",
    });
    if (pick.id === "__existing__") {
      const dir = await pickExistingProfilesDir();
      if (!dir) return;
      state.profilesDir = dir;
      return pickSchema;
    }
    // scaffold via `mdl reverse --init-profile <adapter>` into the workspace root
    const r = await ctx.runMdl(
      bin,
      ["reverse", "--init-profile", pick.id, "--profiles-dir", root],
      root,
      ctx.out,
    );
    if (r.code !== 0) {
      void vscode.window
        .showErrorMessage("Modelith: could not scaffold profiles.yml.", "Show Output")
        .then((a) => a && ctx.out.show());
      return;
    }
    state.profilesDir = root;
    // Open the scaffolded profiles.yml so the user fills fields + sets env vars, then
    // continues — the CLI's `dbt debug` (run at reverse time) validates it.
    const pfile = path.join(root, "profiles.yml");
    void vscode.window.showTextDocument(vscode.Uri.file(pfile), { preview: false });
    void vscode.window.showInformationMessage(
      "Modelith: filled a profiles.yml template. Complete the connection fields, set the " +
        "printed env vars for any secrets, then continue — Modelith runs `dbt debug` to test it.",
    );
    return pickSchema;
  };

  async function pickExistingProfilesDir(): Promise<string | undefined> {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      openLabel: "Use this profiles.yml folder",
      defaultUri: existing ? vscode.Uri.file(existing) : folder!.uri,
    });
    if (!picked || picked.length === 0) return undefined;
    const dir = picked[0].fsPath;
    if (!fs.existsSync(path.join(dir, "profiles.yml"))) {
      void vscode.window.showErrorMessage("Modelith: no profiles.yml in that folder.");
      return undefined;
    }
    return dir;
  }

  const pickSchema: Step = async (input) => {
    if (!state.profilesDir) state.profilesDir = existing;
    const schema = await input.showInputBox({
      title: "Reverse from a live database",
      step: 2,
      totalSteps: 3,
      value: "",
      prompt: "Which schema to introspect? (the warehouse schema/dataset holding your tables)",
      placeholder: "e.g. analytics, public, main",
      validate: async (v) => (v.trim() ? undefined : "Enter a schema name."),
    });
    state.schema = schema.trim();
    return pickFilters;
  };

  const pickFilters: Step = async (input) => {
    const db = await input.showInputBox({
      title: "Reverse from a live database",
      step: 3,
      totalSteps: 3,
      value: "",
      prompt: "Database / catalog (needed for BigQuery & Snowflake; leave blank otherwise).",
      placeholder: "database / catalog (optional)",
      validate: async () => undefined,
    });
    if (db.trim()) state.database = db.trim();
    const sel = await input.showInputBox({
      title: "Reverse from a live database",
      step: 3,
      totalSteps: 3,
      value: "",
      prompt: "Limit to specific tables? Comma-separated names, or blank to reverse the whole schema.",
      placeholder: "e.g. customer, orders (optional)",
      validate: async () => undefined,
    });
    if (sel.trim()) state.select = sel.trim();
    // finished gathering — hand off to the reverse.
    await launchReverse(state as WizardState);
  };

  async function launchReverse(s: WizardState): Promise<void> {
    const defaultTarget = await ctx.bestDefaultTarget(root);
    // a live reverse has no manifest, so no drift arm (undefined).
    const res = await ctx.resolveReverseTarget(defaultTarget, undefined);
    if (!res) return; // cancelled
    const args = ["reverse", "--connect", "--schema", s.schema, "--profiles-dir", s.profilesDir];
    if (s.database) args.push("--database", s.database);
    if (s.select) args.push("--select", s.select);
    args.push("-o", res.target, "--no-review");
    if (res.force) args.push("--force");
    await ctx.runReverseInto(root, args, `live:${s.schema}`);
  }

  // Step flow: (scaffold?) -> profiles.yml -> schema -> [database] -> reverse. A detected
  // or preset profiles.yml skips straight to the schema step.
  const pickStart: Step = existing ? pickSchema : pickAdapterToScaffold;
  await MultiStepInput.run(pickStart);
}
