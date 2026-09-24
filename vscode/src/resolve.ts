// Pure, platform-parameterized path logic for locating the `mdl` executable.
//
// Split out of mdl.ts so the Windows and POSIX resolution can be unit-tested from a
// single machine: every function here takes the platform and its filesystem probes as
// arguments instead of reading `process.platform` / `fs` directly. mdl.ts wires the
// real `process.platform` and `node:fs` in; the regression suite drives both platforms
// with in-memory stubs.

import * as path from "node:path";

export type Platform = "win32" | "posix";

/** Filesystem probes the resolver needs, injectable for tests. */
export interface Fsx {
  isDirectory(p: string): boolean; // true iff p exists and is a directory
  exists(p: string): boolean; // true iff p exists (file or dir)
  readdir(p: string): string[]; // entries of p, or [] if unreadable
}

/** The console-script basename for a platform. */
export function mdlBin(platform: Platform): string {
  return platform === "win32" ? "mdl.exe" : "mdl";
}

/** The venv/conda scripts subdir for a platform. */
export function scriptsSubdir(platform: Platform): string {
  return platform === "win32" ? "Scripts" : "bin";
}

/** Join with the platform's own separator regardless of the host we run on, so a
 * Windows resolution computed on macOS still produces backslash paths (and vice
 * versa). node's `path.win32` / `path.posix` give us that deterministically. */
function joiner(platform: Platform): (...s: string[]) => string {
  return platform === "win32" ? path.win32.join : path.posix.join;
}

function sep(platform: Platform): RegExp {
  return platform === "win32" ? /[\\/]/ : /\//;
}

/** Expand an explicit `modelith.mdlPath` into the candidate command(s) to probe.
 * Handles the three shapes a user might enter:
 *   - a directory (the Scripts/bin dir) → <dir>/mdl(.exe)
 *   - a file that exists → use it verbatim
 *   - a bare command or an as-yet-nonexistent path → try it verbatim, and if it
 *     looks like a path, also try it + mdl(.exe) in case they meant the dir. */
export function explicitCandidates(explicit: string, platform: Platform, fsx: Fsx): string[] {
  const join = joiner(platform);
  const bin = mdlBin(platform);
  const trimmedInput = explicit.trim();
  if (!trimmedInput) return [];
  // Strip a trailing separator so join behaves and a directory check works.
  const trimmed = trimmedInput.replace(/[\\/]+$/, "") || trimmedInput;
  if (fsx.isDirectory(trimmed)) return [join(trimmed, bin)];
  if (fsx.exists(trimmed)) return [trimmed]; // an existing file
  // Doesn't exist yet. If it contains a path separator the user meant a path — offer
  // both the verbatim path and a dir-style join, so a Scripts dir that appears after
  // install still resolves. A bare command → verbatim only.
  const looksLikePath = sep(platform).test(trimmed);
  return looksLikePath ? [trimmed, join(trimmed, bin)] : [trimmed];
}

/** Environment inputs for well-known-bin discovery, injectable for tests. */
export interface WellKnownEnv {
  home: string;
  virtualEnv?: string;
  condaPrefix?: string;
  appData?: string; // %APPDATA% (Windows)
  localAppData?: string; // %LOCALAPPDATA% (Windows)
}

/** Well-known absolute install locations to probe when PATH is stripped, per OS.
 * Returns paths in the target platform's own separator style. Existence is NOT
 * checked here (the caller filters with fsx.exists); winScriptsUnder DOES read dirs
 * because it must enumerate PythonXXX version folders. */
export function wellKnownBins(platform: Platform, env: WellKnownEnv, fsx: Fsx): string[] {
  const join = joiner(platform);
  const bin = mdlBin(platform);
  const sub = scriptsSubdir(platform);
  const bins: string[] = [];
  // An active conda/virtualenv exports its prefix via env vars even when PATH is
  // stripped by a GUI launch — check those first.
  if (env.virtualEnv) bins.push(join(env.virtualEnv, sub, bin));
  if (env.condaPrefix) bins.push(join(env.condaPrefix, sub, bin));

  if (platform === "win32") {
    for (const parent of [
      env.appData && join(env.appData, "Python"),
      env.localAppData && join(env.localAppData, "Programs", "Python"),
    ]) {
      if (parent) bins.push(...winScriptsUnder(parent, fsx, platform));
    }
    if (env.localAppData) {
      bins.push(join(env.localAppData, "uv", "tools", "modelith-dbt", "Scripts", bin));
      bins.push(join(env.localAppData, "pipx", "venvs", "modelith-dbt", "Scripts", bin));
    }
    bins.push(join(env.home, ".local", "bin", bin));
  } else {
    bins.push(join(env.home, ".local", "bin", "mdl"));
    bins.push(join(env.home, ".local", "share", "uv", "tools", "modelith", "bin", "mdl"));
    bins.push("/opt/homebrew/bin/mdl");
    bins.push("/usr/local/bin/mdl");
  }
  return bins;
}

/** Every `<parent>\PythonXXX\Scripts\mdl(.exe)` under a Windows Python parent dir. */
export function winScriptsUnder(parent: string, fsx: Fsx, platform: Platform = "win32"): string[] {
  const join = joiner(platform);
  const bin = mdlBin(platform);
  return fsx
    .readdir(parent)
    .filter((d) => /^Python\d+/i.test(d))
    .map((d) => join(parent, d, "Scripts", bin));
}

/** The mdl(.exe) path inside a sysconfig scripts dir, for a given platform. */
export function scriptsBinIn(dir: string, platform: Platform): string {
  return joiner(platform)(dir, mdlBin(platform));
}
