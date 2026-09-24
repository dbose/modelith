#!/usr/bin/env bash
# Extension regression gates — run before every publish.
#
# The extension has no @vscode/test-electron harness (runtime behavior is verified
# manually in a devcontainer). These are the STATIC gates that catch the regressions
# that actually break extensions: broken types, a command declared but not wired,
# a walkthrough step pointing at a missing command, a packaging break, a stale
# openWalkthrough id after a publisher rename.
#
# Usage:  cd vscode && npm run regression      (or: bash scripts/regression.sh)
# Exits non-zero on the first failing gate, so it doubles as a CI/pre-publish check.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # -> vscode/

fail() { echo "✗ FAIL: $1"; exit 1; }
gate() { echo; echo "──────────────────────────────────────────"; echo " $1"; echo "──────────────────────────────────────────"; }

# Node 20 for the toolchain (vsce/esbuild need 18+); pick up nvm if the default is old.
if ! node --version 2>/dev/null | grep -qE 'v(18|20|22|2[0-9])'; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm use 20 >/dev/null 2>&1
fi
echo "node $(node --version)"

# ── GATE 1: TypeScript type check (strict) ────────────────────────────────────
gate "GATE 1  tsc --noEmit (types)"
npx tsc --noEmit || fail "type errors"
echo "✓ types clean"

# ── GATE 2: esbuild bundle ────────────────────────────────────────────────────
gate "GATE 2  esbuild bundle"
node esbuild.mjs >/dev/null 2>&1 || fail "bundle failed"
echo "✓ bundles"

# ── GATE 2b: mdl resolution (win32 + posix from one machine) ──────────────────
gate "GATE 2b  mdl path resolution (cross-platform)"
node scripts/resolve.test.mjs || fail "mdl resolution logic broke on win32 or posix"

# ── GATE 3: every declared command is registered in code ──────────────────────
gate "GATE 3  command wiring (no silent no-ops)"
python3 - <<'PY' || fail "a declared command is not registered"
import json, re, sys
pkg = json.load(open("package.json"))
declared = [c["command"] for c in pkg["contributes"]["commands"]]
src = open("src/extension.ts").read()
registered = set(re.findall(r'(?:cmd|registerCommand)\(\s*"([^"]+)"', src))
missing = [c for c in declared if c not in registered]
if missing:
    print("declared but NOT registered:", missing); sys.exit(1)
print(f"✓ all {len(declared)} declared commands are registered")
PY

# ── GATE 4: walkthrough integrity ─────────────────────────────────────────────
gate "GATE 4  walkthrough integrity"
python3 - <<'PY' || fail "walkthrough references a missing command / media / id"
import json, re, os, sys
pkg = json.load(open("package.json"))
declared = {c["command"] for c in pkg["contributes"]["commands"]}
wt = pkg["contributes"].get("walkthroughs", [])
if not wt:
    print("no walkthrough contributed"); sys.exit(1)
w = wt[0]; ok = True
for s in w["steps"]:
    for c in re.findall(r'command:([\w.]+)', s["description"]) + \
             [e.split(":",1)[1] for e in s.get("completionEvents",[]) if e.startswith("onCommand:")]:
        if c not in declared:
            print(f"step '{s['id']}' -> unknown command {c}"); ok = False
    img = s.get("media", {}).get("image")
    if img and not os.path.exists(img):
        print(f"step '{s['id']}' -> missing media {img}"); ok = False
opener = f"{pkg['publisher']}.{pkg['name']}#{w['id']}"
if opener not in open("src/extension.ts").read():
    print(f"openWalkthrough id '{opener}' not found in extension.ts (publisher rename?)"); ok = False
if not ok: sys.exit(1)
print(f"✓ {len(w['steps'])} steps valid; openWalkthrough id = {opener}")
PY

# ── GATE 5: package + assets ship, no source leak ─────────────────────────────
gate "GATE 5  vsce package + asset integrity"
npm run package >/dev/null 2>&1 || fail "vsce package failed"
VSIX=$(ls -t modelith-vscode-*.vsix 2>/dev/null | head -1)
[ -n "$VSIX" ] || fail "no vsix produced"
python3 - "$VSIX" <<'PY' || fail "vsix missing an expected asset or leaking source"
import zipfile, sys
z = zipfile.ZipFile(sys.argv[1]); names = z.namelist()
checks = {
    "dist bundle": any("extension/dist/extension.js" in n for n in names),
    "walkthrough media": any("media/canvas.gif" in n for n in names),
    "no .ts source leak": not any(n.endswith(".ts") for n in names),
    "README ships": any(n.endswith("readme.md") for n in names),
}
for k, v in checks.items():
    print(("✓ " if v else "✗ ") + k)
sys.exit(0 if all(checks.values()) else 1)
PY

# ── GATE 6: manifest is publishable ───────────────────────────────────────────
gate "GATE 6  manifest valid (vsce ls)"
npx @vscode/vsce ls >/dev/null 2>&1 || fail "vsce rejects the manifest"
echo "✓ manifest publishable"

echo
echo "══════════════════════════════════════════"
echo " ALL GATES PASSED"
echo "══════════════════════════════════════════"
