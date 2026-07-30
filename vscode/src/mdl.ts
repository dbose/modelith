import * as cp from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

/** Resolved invocation for the mdl CLI: command + prefix args (uv needs "run mdl"). */
export interface MdlBin {
  cmd: string;
  args: string[];
  label: string;
}

let cached: MdlBin | null = null;

/** Detection order: explicit setting → workspace .venv → PATH → `uv run mdl`.
 * Runs in the extension host, which in a devcontainer is *inside* the container
 * (extensionKind: workspace), so detection sees the container's toolchain. */
export async function findMdl(root: string): Promise<MdlBin> {
  if (cached) return cached;
  const cfg = vscode.workspace.getConfiguration("modelith");
  const explicit = cfg.get<string>("mdlPath");
  const candidates: MdlBin[] = [];
  if (explicit) candidates.push({ cmd: explicit, args: [], label: explicit });
  const venv = path.join(root, ".venv", "bin", "mdl");
  if (fs.existsSync(venv)) candidates.push({ cmd: venv, args: [], label: ".venv/bin/mdl" });
  candidates.push({ cmd: "mdl", args: [], label: "mdl (PATH)" });
  candidates.push({ cmd: "uv", args: ["run", "mdl"], label: "uv run mdl" });

  for (const c of candidates) {
    if (await probe(c, root)) {
      cached = c;
      return c;
    }
  }
  throw new Error(
    "mdl CLI not found. Install Modelith (`uv tool install modelith`) or set modelith.mdlPath.",
  );
}

export function resetMdlCache(): void {
  cached = null;
}

function probe(bin: MdlBin, cwd: string): Promise<boolean> {
  return new Promise((res) => {
    const p = cp.spawn(bin.cmd, [...bin.args, "--help"], { cwd, timeout: 15000 });
    p.on("error", () => res(false));
    p.on("exit", (code) => res(code === 0));
  });
}

export interface RunResult {
  code: number;
  stdout: string;
  stderr: string;
}

export function runMdl(bin: MdlBin, args: string[], cwd: string): Promise<RunResult> {
  return new Promise((res) => {
    const p = cp.spawn(bin.cmd, [...bin.args, ...args], { cwd, timeout: 120000 });
    let stdout = "";
    let stderr = "";
    p.stdout.on("data", (d) => (stdout += d));
    p.stderr.on("data", (d) => (stderr += d));
    p.on("error", (e) => res({ code: -1, stdout, stderr: String(e) }));
    p.on("exit", (code) => res({ code: code ?? -1, stdout, stderr }));
  });
}

// --- workspace discovery -------------------------------------------------------

/** The model repo dir (contains mdl-project.yaml). Setting wins; else first hit. */
export async function findModelDir(): Promise<string | undefined> {
  const cfg = vscode.workspace.getConfiguration("modelith");
  const configured = cfg.get<string>("modelDir");
  const ws = vscode.workspace.workspaceFolders?.[0];
  if (!ws) return undefined;
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(ws.uri.fsPath, configured);
  }
  const hits = await vscode.workspace.findFiles("**/mdl-project.yaml", "**/node_modules/**", 5);
  if (hits.length === 0) return undefined;
  // prefer the shallowest match (a model repo root, not a fixture)
  hits.sort((a, b) => a.fsPath.split(path.sep).length - b.fsPath.split(path.sep).length);
  return path.dirname(hits[0].fsPath);
}

/** The dbt project dir (contains dbt_project.yml). */
export async function findDbtProjectDir(): Promise<string | undefined> {
  const cfg = vscode.workspace.getConfiguration("modelith");
  const configured = cfg.get<string>("dbtProjectDir");
  const ws = vscode.workspace.workspaceFolders?.[0];
  if (!ws) return undefined;
  if (configured) {
    return path.isAbsolute(configured) ? configured : path.join(ws.uri.fsPath, configured);
  }
  const hits = await vscode.workspace.findFiles(
    "**/dbt_project.yml",
    "**/{node_modules,dbt_packages,target}/**",
    5,
  );
  if (hits.length === 0) return undefined;
  hits.sort((a, b) => a.fsPath.split(path.sep).length - b.fsPath.split(path.sep).length);
  return path.dirname(hits[0].fsPath);
}

export async function findManifestPath(): Promise<string | undefined> {
  const cfg = vscode.workspace.getConfiguration("modelith");
  const configured = cfg.get<string>("manifestPath");
  const ws = vscode.workspace.workspaceFolders?.[0];
  if (configured && ws) {
    return path.isAbsolute(configured) ? configured : path.join(ws.uri.fsPath, configured);
  }
  const dbt = await findDbtProjectDir();
  if (!dbt) return undefined;
  const p = path.join(dbt, "target", "manifest.json");
  return fs.existsSync(p) ? p : undefined;
}
