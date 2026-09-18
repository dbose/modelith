#!/usr/bin/env bash
# Phase 1 onboarding acceptance harness (spec §4).
#
# Runs INSIDE the clean-machine container. Times and asserts the exact loop a new
# engineer walks on a fresh machine, following only the blessed path:
#
#   install uv  ->  uv tool install modelith-dbt  ->  mdl --help
#   mdl init --demo  ->  mdl serve (non-empty ERD)  ->  mdl validate
#   mdl generate  ->  dbt build (DuckDB, offline)
#
# Each acceptance bar prints PASS / FAIL / GAP with the measured time. The script
# exits non-zero if any hard acceptance bar fails, so it doubles as a CI check.
#
# It measures, it does not paper over: the dbt-duckdb install (not bundled with
# modelith-dbt) is timed and reported as a FINDING, because a real engineer pays
# that cost too.
set -uo pipefail

# --- config (overridable via docker run -e) --------------------------------
INSTALL_SOURCE="${INSTALL_SOURCE:-pypi}"   # pypi | wheel
MODELITH_VERSION="${MODELITH_VERSION:-0.3.1}"
BAR_HELP_SECONDS="${BAR_HELP_SECONDS:-120}"   # spec: fresh machine -> mdl --help < 2 min
SERVE_PORT=4800

# --- pretty helpers --------------------------------------------------------
GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
fails=0
pass()    { echo "${GREEN}PASS${RESET}  $1  ${CYAN}(${2:-})${RESET}"; }
fail()    { echo "${RED}FAIL${RESET}  $1  ${CYAN}(${2:-})${RESET}"; fails=$((fails+1)); }
finding() { echo "${YELLOW}NOTE${RESET}  $1"; }
hr()      { echo "------------------------------------------------------------"; }
now()     { date +%s; }

echo "Modelith Phase 1 onboarding harness"
echo "install source: ${INSTALL_SOURCE}   pinned version: ${MODELITH_VERSION}"
hr

# --- 0. establish PATH for a uv tool install -------------------------------
export PATH="$HOME/.local/bin:$PATH"

# ===========================================================================
# BAR 1: fresh machine -> `mdl --help` working, under 2 minutes
# ===========================================================================
t_start=$(now)

echo "installing uv (the engineer does not have it yet)..."
# Bootstrap uv with the system pip (no curl needed on the slim image). This is one
# of uv's documented install paths and installs the same tool the shell installer
# would. `pip install --user` drops the `uv` shim into ~/.local/bin.
python3 -m pip install --user --quiet uv >/tmp/uv-install.log 2>&1
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || { fail "uv installed" "uv not on PATH; see /tmp/uv-install.log"; }

echo "uv tool install modelith-dbt ..."
if [ "$INSTALL_SOURCE" = "wheel" ]; then
  # test an unreleased branch: a wheel was COPYed to /tmp/wheels at build time
  WHL=$(ls /tmp/wheels/modelith_dbt-*.whl 2>/dev/null | head -1)
  if [ -z "$WHL" ]; then fail "wheel present for wheel-mode" "no wheel in /tmp/wheels"; fi
  uv tool install "$WHL" >/tmp/mdl-install.log 2>&1
else
  uv tool install "modelith-dbt==${MODELITH_VERSION}" >/tmp/mdl-install.log 2>&1
fi

if mdl --help >/tmp/mdl-help.log 2>&1; then
  t_help=$(( $(now) - t_start ))
  if [ "$t_help" -le "$BAR_HELP_SECONDS" ]; then
    pass "BAR 1  fresh machine -> mdl --help" "${t_help}s, bar ${BAR_HELP_SECONDS}s"
  else
    fail "BAR 1  fresh machine -> mdl --help TOO SLOW" "${t_help}s > ${BAR_HELP_SECONDS}s"
  fi
  echo "       installed: $(mdl --version 2>/dev/null || echo '(no --version)')"
else
  fail "BAR 1  mdl --help does not run" "see /tmp/mdl-install.log"
fi
hr

# ===========================================================================
# BAR 2: `mdl init --demo` scaffolds a populated model
# ===========================================================================
WORK="$HOME/demo"
rm -rf "$WORK"
t0=$(now)
if mdl init --demo "$WORK" >/tmp/init.log 2>&1; then
  n_ent=$(find "$WORK/model/logical/entities" -name '*.yaml' 2>/dev/null | wc -l | tr -d ' ')
  if [ "$n_ent" -ge 6 ]; then
    pass "BAR 2  init --demo populated model" "${n_ent} entities, $(( $(now) - t0 ))s"
  else
    fail "BAR 2  init --demo produced too few entities" "${n_ent} < 6"
  fi
else
  fail "BAR 2  mdl init --demo failed" "see /tmp/init.log; feature needs >= 0.3.1"
fi
hr

# ===========================================================================
# BAR 3: `mdl validate` passes (the activation metric)  [offline]
# ===========================================================================
t0=$(now)
if mdl validate -m "$WORK/model" >/tmp/validate.log 2>&1; then
  pass "BAR 3  mdl validate passes (activation)" "$(( $(now) - t0 ))s, offline"
else
  fail "BAR 3  mdl validate failed" "see /tmp/validate.log"
fi
hr

# ===========================================================================
# BAR 4: `mdl serve` renders a NON-EMPTY ERD  [offline]
# ===========================================================================
t0=$(now)
( cd "$WORK" && mdl serve -m model >/tmp/serve.log 2>&1 ) &
serve_pid=$!
# Probe with Python urllib, NOT curl: python:3.12-slim (a faithful clean machine)
# ships no curl. Poll up to 25s, and stop early if the server process dies.
n_served=$(python3 - "$SERVE_PORT" "$serve_pid" <<'PY'
import json, sys, time, urllib.request, os, signal
port, pid = sys.argv[1], int(sys.argv[2])
def alive(p):
    try: os.kill(p, 0); return True
    except OSError: return False
for _ in range(25):
    time.sleep(1)
    if not alive(pid):
        print(0); sys.exit()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/model", timeout=2) as r:
            d = json.load(r)
        v = d.get("logical_entities") or d.get("entities") or []
        print(len(v) if hasattr(v, "__len__") else 0); sys.exit()
    except Exception:
        continue
print(0)
PY
)
kill "$serve_pid" >/dev/null 2>&1; wait "$serve_pid" 2>/dev/null
if [ "${n_served:-0}" -ge 6 ]; then
  pass "BAR 4  serve renders non-empty ERD" "${n_served} entities via API, $(( $(now) - t0 ))s"
else
  fail "BAR 4  serve rendered empty/failed" "entities=${n_served:-0}; see /tmp/serve.log"
fi
hr

# ===========================================================================
# BAR 5: full offline loop -> `dbt build` on DuckDB, no external account
# ===========================================================================
# generate the core models from the model (conflict-free on a fresh demo)
t0=$(now)
if mdl generate -m "$WORK/model" -o "$WORK/transform/warehouse" >/tmp/generate.log 2>&1; then
  pass "BAR 5a generate (conflict-free)" "$(( $(now) - t0 ))s"
else
  fail "BAR 5a generate failed / conflicted" "see /tmp/generate.log"
fi

# --- BAR 5b, variant 1: STRICT blessed path -------------------------------
# gap #3 made visible. Following ONLY the two blessed commands (uv tool install
# modelith-dbt), `dbt` is not present, so `dbt build` cannot run. This is the real
# experience, and its failure IS the finding, not a harness bug. Not counted as a
# hard fail: we EXPECT it, and assert the gap is real.
if command -v dbt >/dev/null 2>&1; then
  finding "BAR 5b (strict): dbt already on PATH unexpectedly — gap #3 may have closed"
else
  finding "BAR 5b (strict blessed path): dbt build is BLOCKED — 'dbt' not installed by modelith-dbt (gap #3, as expected)"
fi

# --- BAR 5b, variant 2: after installing dbt-duckdb (measured friction) ----
finding "installing dbt separately (pulls dbt-core + duckdb) — the cost a new engineer pays"
t_dbt0=$(now)
# dbt-duckdb is only the ADAPTER and ships no executable; the `dbt` command comes
# from dbt-core. Install dbt-core as the tool and add the duckdb adapter with --with,
# so the `dbt` shim exists with DuckDB support.
uv tool install "dbt-core" --with "dbt-duckdb" >/tmp/dbt-install.log 2>&1
export PATH="$HOME/.local/bin:$PATH"   # uv tool shims land in ~/.local/bin
t_dbt=$(( $(now) - t_dbt0 ))
if command -v dbt >/dev/null 2>&1; then
  finding "dbt-duckdb install took ${t_dbt}s  <-- friction between the blessed path and a working dbt build"
else
  fail "dbt-duckdb install failed" "see /tmp/dbt-install.log"
fi

t0=$(now)
if ( cd "$WORK/transform/warehouse" && dbt build --profiles-dir . >/tmp/dbt-build.log 2>&1 ); then
  if grep -qE "Completed successfully|PASS=[0-9]+ WARN=[0-9]+ ERROR=0" /tmp/dbt-build.log; then
    pass "BAR 5b dbt build on DuckDB (after dbt-duckdb, offline)" "$(( $(now) - t0 ))s; $(grep -oE 'PASS=[0-9]+ WARN=[0-9]+ ERROR=[0-9]+ SKIP=[0-9]+' /tmp/dbt-build.log | tail -1)"
  else
    fail "BAR 5b dbt build exited 0 but no success line" "see /tmp/dbt-build.log"
  fi
else
  fail "BAR 5b dbt build failed" "see /tmp/dbt-build.log"
fi
hr

# --- verdict ---------------------------------------------------------------
t_total=$(( $(now) - t_start ))
echo "total wall time (install + full loop): ${t_total}s"
if [ "$fails" -eq 0 ]; then
  echo "${GREEN}ALL ACCEPTANCE BARS PASSED${RESET}"
  exit 0
else
  echo "${RED}${fails} acceptance bar(s) FAILED${RESET}  (logs under /tmp/*.log inside the container)"
  exit 1
fi
