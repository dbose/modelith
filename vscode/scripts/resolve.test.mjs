// Cross-platform resolution test for src/resolve.ts — the pure path logic that
// locates `mdl`/`mdl.exe`. Runs BOTH the win32 and posix branches from one machine
// by passing the platform + an in-memory fs stub, so macOS CI proves the Windows
// path shapes (the enterprise case we can't boot here) and vice versa.
//
// No test framework: esbuild (already a dep) transpiles resolve.ts to a temp CJS
// module, we import it, and assert. Exits non-zero on the first failure.

import { build } from "esbuild";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

let failed = 0;
function ok(cond, msg) {
  if (cond) {
    console.log(`  ✓ ${msg}`);
  } else {
    console.error(`  ✗ ${msg}`);
    failed++;
  }
}
function eq(actual, expected, msg) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  ok(a === e, `${msg}\n      expected ${e}\n      got      ${a}`);
}

// --- transpile src/resolve.ts to a loadable module ---------------------------
const here = new URL(".", import.meta.url).pathname;
const src = join(here, "..", "src", "resolve.ts");
const out = build({
  entryPoints: [src],
  bundle: true,
  format: "esm",
  platform: "node",
  write: false,
});
const built = await out;
const dir = mkdtempSync(join(tmpdir(), "mdl-resolve-"));
const file = join(dir, "resolve.mjs");
writeFileSync(file, built.outputFiles[0].text);
const R = await import(pathToFileURL(file).href);
rmSync(dir, { recursive: true, force: true });

// --- an in-memory fs stub ----------------------------------------------------
// dirs: Set of directory paths; files: Set of file paths; children: dir -> entries[]
function makeFsx({ dirs = [], files = [], children = {} } = {}) {
  const D = new Set(dirs);
  const F = new Set(files);
  return {
    isDirectory: (p) => D.has(p),
    exists: (p) => D.has(p) || F.has(p),
    readdir: (p) => children[p] ?? [],
  };
}

console.log("basename + subdir per platform");
eq(R.mdlBin("win32"), "mdl.exe", "win32 executable is mdl.exe");
eq(R.mdlBin("posix"), "mdl", "posix executable is mdl");
eq(R.scriptsSubdir("win32"), "Scripts", "win32 scripts subdir is Scripts");
eq(R.scriptsSubdir("posix"), "bin", "posix scripts subdir is bin");

console.log("\nexplicitCandidates — WINDOWS");
{
  const scriptsDir = "C:\\Users\\u314768\\AppData\\Roaming\\Python\\Python313\\Scripts";
  const exe = scriptsDir + "\\mdl.exe";
  // (1) user points at the DIRECTORY (the exact bug from the screenshot)
  eq(
    R.explicitCandidates(scriptsDir, "win32", makeFsx({ dirs: [scriptsDir], files: [exe] })),
    [exe],
    "win: directory value → <dir>\\mdl.exe",
  );
  // (1b) directory with a trailing backslash
  eq(
    R.explicitCandidates(scriptsDir + "\\", "win32", makeFsx({ dirs: [scriptsDir] })),
    [exe],
    "win: directory value with trailing sep → <dir>\\mdl.exe",
  );
  // (2) user points at the executable itself
  eq(
    R.explicitCandidates(exe, "win32", makeFsx({ files: [exe] })),
    [exe],
    "win: file value → verbatim",
  );
  // (3) a path that doesn't exist yet, but looks like a dir → try both
  eq(
    R.explicitCandidates(scriptsDir, "win32", makeFsx({})),
    [scriptsDir, exe],
    "win: nonexistent path → verbatim + dir-join",
  );
  // (4) a bare command
  eq(R.explicitCandidates("mdl", "win32", makeFsx({})), ["mdl"], "win: bare command → verbatim");
  // (5) whitespace is trimmed
  eq(
    R.explicitCandidates("  " + exe + "  ", "win32", makeFsx({ files: [exe] })),
    [exe],
    "win: surrounding whitespace trimmed",
  );
  // (6) empty / blank → nothing
  eq(R.explicitCandidates("   ", "win32", makeFsx({})), [], "win: blank → no candidates");
}

console.log("\nexplicitCandidates — MAC/POSIX");
{
  const binDir = "/Users/dev/.venv/bin";
  const bin = binDir + "/mdl";
  eq(
    R.explicitCandidates(binDir, "posix", makeFsx({ dirs: [binDir], files: [bin] })),
    [bin],
    "posix: directory value → <dir>/mdl",
  );
  eq(
    R.explicitCandidates(bin, "posix", makeFsx({ files: [bin] })),
    [bin],
    "posix: file value → verbatim",
  );
  eq(
    R.explicitCandidates("/opt/tools/mdl", "posix", makeFsx({})),
    ["/opt/tools/mdl", "/opt/tools/mdl/mdl"],
    "posix: nonexistent path → verbatim + dir-join",
  );
  eq(R.explicitCandidates("mdl", "posix", makeFsx({})), ["mdl"], "posix: bare command → verbatim");
}

console.log("\nwellKnownBins — WINDOWS (pip --user, uv, pipx)");
{
  const appData = "C:\\Users\\u314768\\AppData\\Roaming";
  const localApp = "C:\\Users\\u314768\\AppData\\Local";
  const home = "C:\\Users\\u314768";
  const pyParent = appData + "\\Python";
  const fsx = makeFsx({
    children: { [pyParent]: ["Python313", "Python311", "NotPython", "Launcher"] },
  });
  const bins = R.wellKnownBins("win32", { home, appData, localAppData: localApp }, fsx);
  ok(
    bins.includes(pyParent + "\\Python313\\Scripts\\mdl.exe"),
    "win: enumerates Python313 Scripts\\mdl.exe (the screenshot's box)",
  );
  ok(bins.includes(pyParent + "\\Python311\\Scripts\\mdl.exe"), "win: enumerates Python311 too");
  ok(
    !bins.some((b) => b.includes("NotPython") || b.includes("Launcher")),
    "win: ignores non-PythonXXX dirs",
  );
  ok(
    bins.includes(localApp + "\\uv\\tools\\modelith-dbt\\Scripts\\mdl.exe"),
    "win: includes uv tool location",
  );
  ok(
    bins.includes(localApp + "\\pipx\\venvs\\modelith-dbt\\Scripts\\mdl.exe"),
    "win: includes pipx location",
  );
  ok(
    bins.every((b) => b.includes("\\") && !b.includes("/")),
    "win: every path uses backslash separators",
  );
  ok(
    bins.every((b) => b.endsWith("mdl.exe")),
    "win: every candidate ends in mdl.exe",
  );
}

console.log("\nwellKnownBins — MAC/POSIX (uv, pipx, homebrew)");
{
  const home = "/Users/dev";
  const bins = R.wellKnownBins("posix", { home }, makeFsx({}));
  ok(bins.includes("/Users/dev/.local/bin/mdl"), "posix: includes ~/.local/bin/mdl (uv/pipx)");
  ok(bins.includes("/opt/homebrew/bin/mdl"), "posix: includes homebrew bin");
  ok(bins.includes("/usr/local/bin/mdl"), "posix: includes /usr/local/bin");
  ok(
    bins.every((b) => b.startsWith("/") && !b.includes("\\")),
    "posix: every path uses forward slashes",
  );
  ok(
    bins.every((b) => b.endsWith("/mdl")),
    "posix: every candidate ends in /mdl (no .exe)",
  );
}

console.log("\nwellKnownBins — active venv/conda lead (both platforms)");
{
  const winBins = R.wellKnownBins(
    "win32",
    { home: "C:\\Users\\d", virtualEnv: "C:\\proj\\.venv" },
    makeFsx({}),
  );
  eq(winBins[0], "C:\\proj\\.venv\\Scripts\\mdl.exe", "win: VIRTUAL_ENV bin leads");
  const posixBins = R.wellKnownBins(
    "posix",
    { home: "/Users/d", condaPrefix: "/opt/conda/envs/x" },
    makeFsx({}),
  );
  eq(posixBins[0], "/opt/conda/envs/x/bin/mdl", "posix: CONDA_PREFIX bin leads");
}

console.log("\nscriptsBinIn — sysconfig dir join");
eq(
  R.scriptsBinIn("C:\\Py\\Python313\\Scripts", "win32"),
  "C:\\Py\\Python313\\Scripts\\mdl.exe",
  "win: sysconfig dir + mdl.exe",
);
eq(R.scriptsBinIn("/usr/local/bin", "posix"), "/usr/local/bin/mdl", "posix: sysconfig dir + mdl");

console.log();
if (failed) {
  console.error(`✗ ${failed} assertion(s) failed`);
  process.exit(1);
}
console.log("✓ all resolution assertions passed (win32 + posix)");
