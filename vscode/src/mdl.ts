import * as cp from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import {
  type Fsx,
  type Platform,
  explicitCandidates as explicitCandidatesPure,
  mdlBin,
  scriptsBinIn,
  scriptsSubdir,
  wellKnownBins as wellKnownBinsPure,
} from "./resolve";

/** Resolved invocation for the mdl CLI: command + prefix args (uv needs "run mdl"). */
export interface MdlBin {
  cmd: string;
  args: string[];
  label: string;
}

let cached: MdlBin | null = null;

const IS_WIN = process.platform === "win32";
const PLATFORM: Platform = IS_WIN ? "win32" : "posix";
/** The console-script basename: `mdl.exe` on Windows, `mdl` elsewhere. */
const MDL_BIN = mdlBin(PLATFORM);
/** venv/conda script dir: `Scripts` on Windows, `bin` elsewhere. */
const SCRIPTS_SUBDIR = scriptsSubdir(PLATFORM);

/** Real filesystem probes for the pure resolver (statSync/existsSync/readdirSync). */
const REAL_FSX: Fsx = {
  isDirectory(p) {
    try {
      return fs.statSync(p).isDirectory();
    } catch {
      return false;
    }
  },
  exists(p) {
    return fs.existsSync(p);
  },
  readdir(p) {
    try {
      return fs.readdirSync(p);
    } catch {
      return [];
    }
  },
};

/** Detection order: explicit setting → workspace venv → PATH → well-known bins →
 * the active interpreter's own Scripts/bin dir → `python -m mdl_cli` → `uv run mdl`.
 *
 * The interpreter-driven tiers are what make this work on locked-down enterprise
 * Windows/macOS boxes: a `pip install --user modelith-dbt` puts `mdl.exe` in the
 * interpreter's per-user Scripts dir (e.g.
 * `%APPDATA%\Python\Python313\Scripts\mdl.exe`) which is frequently NOT on the GUI
 * process's PATH, and neither `uv` nor `pipx` is available to install differently.
 * So we ask Python itself where its scripts live (sysconfig), probe that, and as a
 * final resort run the package as a module — the one invocation that needs no PATH
 * entry and no console script at all.
 *
 * Runs in the extension host, which in a devcontainer is *inside* the container
 * (extensionKind: workspace), so detection sees the container's toolchain. */
export async function findMdl(root: string): Promise<MdlBin> {
  if (cached) return cached;
  const cfg = vscode.workspace.getConfiguration("modelith");
  const explicit = cfg.get<string>("mdlPath")?.trim();
  const candidates: MdlBin[] = [];
  if (explicit) {
    // Tolerate the natural mistake of pointing at the Scripts/bin DIRECTORY rather
    // than the executable inside it (see explicitCandidates in ./resolve).
    for (const c of explicitCandidatesPure(explicit, PLATFORM, REAL_FSX)) {
      candidates.push({ cmd: c, args: [], label: c });
    }
  }
  const venv = path.join(root, ".venv", SCRIPTS_SUBDIR, MDL_BIN);
  if (fs.existsSync(venv)) {
    candidates.push({ cmd: venv, args: [], label: `.venv/${SCRIPTS_SUBDIR}/${MDL_BIN}` });
  }
  // A workspace-folder venv, ranked ABOVE the global PATH install. This is what lets
  // someone developing Modelith itself use their working-tree (editable) mdl instead
  // of a stale `uv tool` global — the model dir is usually a demo/ subfolder with no
  // venv, so the model-dir probe above misses the repo-root editable install. For a
  // normal user (no workspace venv) this adds nothing.
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const wsVenv = path.join(folder.uri.fsPath, ".venv", SCRIPTS_SUBDIR, MDL_BIN);
    if (wsVenv !== venv && fs.existsSync(wsVenv)) {
      candidates.push({ cmd: wsVenv, args: [], label: `${folder.name}/.venv/${MDL_BIN}` });
    }
  }
  candidates.push({ cmd: MDL_BIN, args: [], label: `${MDL_BIN} (PATH)` });
  // GUI-launched VS Code has a minimal PATH that excludes the per-user install
  // locations a shell profile would add, so `mdl (PATH)` misses a `uv tool` / `pipx`
  // / `pip install --user` install. Probe those well-known bins per platform.
  for (const abs of wellKnownBinsPure(PLATFORM, wellKnownEnv(), REAL_FSX)) {
    if (fs.existsSync(abs)) candidates.push({ cmd: abs, args: [], label: abs });
  }

  // Interpreter-driven tiers. Resolve the Python(s) most likely to own the install —
  // the interpreter the user selected in the Python extension first, then the common
  // PATH names — and for each: (a) probe <its scripts dir>/mdl(.exe) via sysconfig,
  // and (b) fall back to `<python> -m mdl_cli`, which needs no console script at all.
  const interpreters = await candidateInterpreters();
  for (const py of interpreters) {
    const scriptsBin = await scriptsDirBin(py);
    if (scriptsBin && fs.existsSync(scriptsBin)) {
      candidates.push({ cmd: scriptsBin, args: [], label: scriptsBin });
    }
  }
  for (const py of interpreters) {
    candidates.push({ cmd: py, args: ["-m", "mdl_cli"], label: `${py} -m mdl_cli` });
  }

  candidates.push({ cmd: "uv", args: ["run", "mdl"], label: "uv run mdl" });

  for (const c of candidates) {
    if (await probe(c, root)) {
      cached = c;
      return c;
    }
  }
  throw new MdlNotFoundError();
}

/** Gather the env inputs the well-known-bin probe needs from this process.
 * (explicitCandidates, wellKnownBins, winScriptsUnder live in ./resolve so they can
 * be unit-tested on both platforms from one machine.) */
function wellKnownEnv() {
  return {
    home: process.env.HOME || process.env.USERPROFILE || os.homedir(),
    virtualEnv: process.env.VIRTUAL_ENV,
    condaPrefix: process.env.CONDA_PREFIX,
    appData: process.env.APPDATA,
    localAppData: process.env.LOCALAPPDATA,
  };
}

/** Interpreters to interrogate, best-first: the user's selected Python (from the
 * ms-python.python extension if present), then the usual PATH launchers. Deduped. */
async function candidateInterpreters(): Promise<string[]> {
  const out: string[] = [];
  const selected = await selectedPythonPath();
  if (selected) out.push(selected);
  // `py` (the Windows launcher) picks the active/default install; python3/python are
  // the POSIX names. Order is best-effort — probe() ultimately validates each.
  out.push(...(IS_WIN ? ["py", "python", "python3"] : ["python3", "python"]));
  return [...new Set(out)];
}

/** The interpreter the user selected in the Python extension, if it's installed and
 * has exposed one. No hard dependency: absent extension → undefined, use PATH. */
async function selectedPythonPath(): Promise<string | undefined> {
  try {
    const ext = vscode.extensions.getExtension("ms-python.python");
    if (!ext) return undefined;
    if (!ext.isActive) await ext.activate();
    // The modern Environments API: environments.getActiveEnvironmentPath().path.
    const api = ext.exports as {
      environments?: { getActiveEnvironmentPath?: () => { path?: string } };
    };
    const p = api?.environments?.getActiveEnvironmentPath?.().path;
    return typeof p === "string" && p.length > 0 ? p : undefined;
  } catch {
    return undefined;
  }
}

/** Ask an interpreter for its own console-scripts dir (sysconfig) and return the
 * `mdl(.exe)` path inside it. Tries the per-user scheme first (nt_user / posix_user,
 * where `pip install --user` lands — the locked-down-box case), then the default
 * scheme. Returns undefined if the interpreter can't be run. */
async function scriptsDirBin(python: string): Promise<string | undefined> {
  // One tiny program prints the user-scheme scripts dir then the default one; we take
  // the first that exists. Kept to sysconfig only (no pip, no network).
  const code =
    "import sysconfig,os;" +
    "u='nt_user' if os.name=='nt' else 'posix_user';" +
    "print(sysconfig.get_path('scripts',u));" +
    "print(sysconfig.get_path('scripts'))";
  const dirs = await runInterpreter(python, ["-c", code]);
  if (!dirs) return undefined;
  for (const dir of dirs.split(/\r?\n/).map((s) => s.trim())) {
    if (!dir) continue;
    const bin = scriptsBinIn(dir, PLATFORM);
    if (fs.existsSync(bin)) return bin;
  }
  return undefined;
}

/** Run `<python> <args>` and return trimmed stdout, or undefined on any failure. */
function runInterpreter(python: string, args: string[]): Promise<string | undefined> {
  return new Promise((res) => {
    const p = cp.spawn(python, args, { timeout: 8000 });
    let stdout = "";
    p.stdout?.on("data", (d) => (stdout += d));
    p.on("error", () => res(undefined));
    p.on("exit", (code) => res(code === 0 ? stdout.trim() : undefined));
  });
}

/** Thrown by findMdl when no `mdl` resolves. Carries a stable marker so callers
 * can offer the one-click installer instead of just printing the message. */
export class MdlNotFoundError extends Error {
  readonly notFound = true as const;
  constructor() {
    super(
      "The Modelith CLI (`mdl`) was not found. Install it, then reload. It is probed " +
        "on PATH, in well-known install dirs, in your selected Python's Scripts/bin " +
        "dir, and via `python -m mdl_cli`. If it is installed but still not detected, " +
        "run `where mdl` (Windows) or `which mdl` and set that path as " +
        "`modelith.mdlPath` in Settings.",
    );
    this.name = "MdlNotFoundError";
  }
}

export function isMdlNotFound(e: unknown): e is MdlNotFoundError {
  return e instanceof MdlNotFoundError || (typeof e === "object" && e !== null && "notFound" in e);
}

/**
 * The blessed install command (matches the README and PyPI name `modelith-dbt`).
 * `--force` is deliberate: `uv tool install` is a no-op when the tool already
 * exists, so a lingering/stale `mdl` (e.g. left behind after uninstalling an older
 * extension) would NOT be upgraded and the extension could then call commands the
 * old CLI lacks. --force reinstalls to the current version whether mdl is absent,
 * stale, or current, and is a clean no-op-equivalent when already current.
 */
export const CLI_INSTALL_CMD = "uv tool install --force modelith-dbt";

/**
 * When `mdl` is missing, show an actionable error with a one-click install button
 * (doc §4.1: not a docs link — run the command for them). Runs the install in an
 * integrated terminal, then clears the detection cache so the next action re-probes.
 * Returns true if the user chose to install (the terminal was launched).
 */
export async function offerCliInstall(): Promise<boolean> {
  const PIP = "Install with pip";
  const UV = "Use uv";
  const PIPX = "Use pipx";
  // pip --user is the reliable path on a locked-down box that already has Python but
  // no uv/pipx (the common enterprise Windows case), so it leads. Prefer the Python
  // the user selected in the Python extension; else a sensible launcher.
  const python = (await selectedPythonPath()) ?? (IS_WIN ? "py" : "python3");
  const choice = await vscode.window.showErrorMessage(
    "Modelith needs the `mdl` command-line tool, which isn't installed yet.",
    { modal: false },
    PIP,
    UV,
    PIPX,
  );
  if (choice !== PIP && choice !== UV && choice !== PIPX) return false;

  // --force / --user / -U each reinstall over any existing/stale copy. After a pip
  // --user install, `python -m mdl_cli` resolves even if the Scripts dir isn't on
  // PATH, so the very next Modelith action works without touching PATH.
  const cmd =
    choice === PIP
      ? `${python} -m pip install --user -U modelith-dbt`
      : choice === PIPX
        ? "pipx install --force modelith-dbt"
        : CLI_INSTALL_CMD;
  const term = vscode.window.createTerminal({ name: "Install Modelith CLI" });
  term.show(true);
  // Send the install; on success, clear the cache so the very next Modelith action
  // finds the freshly-installed binary without a window reload.
  term.sendText(cmd, true);
  vscode.window.showInformationMessage(
    "Installing the Modelith CLI in the terminal. When it finishes, run your Modelith command again.",
  );
  resetMdlCache();
  return true;
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

export function runMdl(
  bin: MdlBin,
  args: string[],
  cwd: string,
  out?: vscode.OutputChannel,
  timeoutMs = 120000,
): Promise<RunResult> {
  // Echo the exact CLI invocation before running, so every model UI action is
  // transparent in the Modelith output (matching the `[canvas] mdl serve …` line).
  out?.appendLine(`[run] ${bin.label} ${args.join(" ")} (cwd ${cwd})`);
  return new Promise((res) => {
    // Default 120s; a live-DB reverse (`--connect` runs dbt debug + introspection over a
    // possibly-cold warehouse) passes a longer timeout.
    const p = cp.spawn(bin.cmd, [...bin.args, ...args], { cwd, timeout: timeoutMs });
    let stdout = "";
    let stderr = "";
    p.stdout.on("data", (d) => (stdout += d));
    p.stderr.on("data", (d) => (stderr += d));
    p.on("error", (e) => res({ code: -1, stdout, stderr: String(e) }));
    p.on("exit", (code) => res({ code: code ?? -1, stdout, stderr }));
  });
}

// --- CLI capability handshake (version skew) -----------------------------------

/** Suggest the upgrade command that matches HOW this `mdl` was installed. The
 * extension and the CLI are versioned independently (Marketplace .vsix vs a
 * separate `mdl` install), so a stale CLI lacks commands the extension calls; we
 * can only advise, never assume a package manager. Inferred from the resolved
 * binary path — uv tool, pipx, a project .venv, or an unknown location. */
export function upgradeHint(bin: MdlBin): string {
  const c = `${bin.cmd} ${bin.args.join(" ")}`.toLowerCase();
  if (c.includes("uv") && bin.args.includes("run")) {
    return "run `uv sync` in the model repo (this is the workspace's own mdl)";
  }
  if (bin.args.includes("-m")) {
    // Resolved via `<python> -m mdl_cli` — upgrade that interpreter's package.
    return `run \`${bin.cmd} -m pip install --user -U modelith-dbt\``;
  }
  if (c.includes("/uv/tools/") || c.includes("\\uv\\tools\\") || c.includes(".local/bin")) {
    return "run `uv tool install --force modelith-dbt` (or `pipx upgrade modelith-dbt`)";
  }
  if (c.includes(".venv")) {
    return "upgrade mdl in that virtualenv, e.g. `uv sync` or `pip install -U modelith-dbt`";
  }
  if (c.includes("scripts") || c.includes("site-packages")) {
    // A per-user / interpreter Scripts-dir install → pip is the right upgrade path.
    return "run `python -m pip install --user -U modelith-dbt` for that interpreter";
  }
  return (
    "upgrade your mdl install — `uv tool install --force modelith-dbt`, " +
    "`pipx upgrade modelith-dbt`, or `pip install -U modelith-dbt`"
  );
}

/** Probe once that the resolved `mdl` has the commands the extension's AI surfaces
 * (chat participant, MCP server) call. `mdl model` is the proxy: it shipped in the
 * same release as `mdl mcp`, so its presence means the CLI is new enough. Returns
 * true when capable; false when `mdl` is missing the command (stale) or unusable. */
export async function hasAiCommands(bin: MdlBin, cwd: string): Promise<boolean> {
  const r = await runMdl(bin, ["model", "--help"], cwd);
  return r.code === 0;
}

// --- workspace discovery -------------------------------------------------------

/** The model repo dir (contains mdl-project.yaml). Setting wins; else first hit. */
/** Resolve a relative config path against whichever workspace root actually
 * contains `marker`. A multi-root `.code-workspace` (model + transform/warehouse
 * + the `.` repo root) means folders[0] is often NOT the repo root, so joining a
 * relative setting onto folders[0] can produce a bogus path like model/model.
 * Try every root; pick the first where <root>/<configured>/<marker> exists. */
function resolveConfiguredDir(configured: string, marker: string): string | undefined {
  if (path.isAbsolute(configured)) return configured;
  const roots = vscode.workspace.workspaceFolders ?? [];
  for (const r of roots) {
    const candidate = path.join(r.uri.fsPath, configured);
    if (fs.existsSync(path.join(candidate, marker))) return candidate;
  }
  // nothing matched the marker — fall back to folders[0] join (previous behaviour)
  const ws = roots[0];
  return ws ? path.join(ws.uri.fsPath, configured) : undefined;
}

export async function findModelDir(): Promise<string | undefined> {
  const cfg = vscode.workspace.getConfiguration("modelith");
  const configured = cfg.get<string>("modelDir");
  const ws = vscode.workspace.workspaceFolders?.[0];
  if (!ws) return undefined;
  if (configured) {
    return resolveConfiguredDir(configured, "mdl-project.yaml");
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
    return resolveConfiguredDir(configured, "dbt_project.yml");
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
