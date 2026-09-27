#!/usr/bin/env bash
# Clean-install verification harness (runs INSIDE the core-only container).
#
# Installs the locally built wheel with NO extras, then runs the erwin-import logical
# flow the way a real user on a core install does. Asserts each step and exits
# non-zero on the first hard failure, so this is a CI gate as well as a manual check.
#
# What this specifically guards (the bugs that shipped to the Windows box):
#   * `mdl import erwin` into an EMPTY folder must not need the [ontology] extra
#     (scaffolding writes .mdl/lock.yaml via mdl_ontology.lock.Lock, which must be
#     reachable without pyoxigraph).
#   * an erwin name with filesystem-illegal characters (a domain named "<root>")
#     must write to a safe filename, not crash open() (a Linux fs rejects the raw
#     path differently than macOS, so this runs on a real non-macOS filesystem).
set -uo pipefail

GREEN=$'\033[32m'; RED=$'\033[31m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
fails=0
pass() { echo "${GREEN}PASS${RESET}  $1"; }
fail() { echo "${RED}FAIL${RESET}  $1  ${CYAN}($2)${RESET}"; fails=$((fails+1)); }
hr()   { echo "------------------------------------------------------------"; }

echo "Modelith clean-install (core-only) verification"
export PATH="$HOME/.local/bin:$PATH"
hr

# --- install the built wheel, CORE ONLY (no extras) ------------------------
WHL=$(ls /tmp/wheels/modelith_dbt-*.whl 2>/dev/null | head -1)
if [ -z "$WHL" ]; then
  fail "wheel present" "no modelith_dbt-*.whl in /tmp/wheels (build it and mount dist/)"
  echo "${RED}1 failure${RESET}"; exit 1
fi
echo "installing (core only, no [ontology]): $(basename "$WHL")"
python3 -m pip install --user --quiet "$WHL" >/tmp/install.log 2>&1 || {
  fail "pip install core wheel" "see /tmp/install.log"; cat /tmp/install.log; exit 1;
}

# prove the optional backend is genuinely ABSENT — otherwise this test is meaningless
if python3 -c "import pyoxigraph" 2>/dev/null; then
  fail "core install is truly core" "pyoxigraph is present — not a core-only env"
else
  pass "core install has no pyoxigraph (as intended)"
fi
hr

# --- BAR 1: the CLI loads and --version works (no crash on import) ----------
if mdl --version >/tmp/version.log 2>&1; then
  pass "mdl --version runs on a core install ($(cat /tmp/version.log))"
else
  fail "mdl --version" "see /tmp/version.log"; cat /tmp/version.log
fi
hr

# --- a minimal real-shaped erwin export, incl. a "<root>" domain -----------
# namespaced, GUID-referenced, props-as-text — the real shape. The <root> domain
# exercises the filesystem-safe-filename path; the whole thing exercises the
# scaffold path that pulled in the optional ontology backend.
cat > /home/user/model.xml <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<erwin xmlns="http://www.erwin.com/dm">
  <Model xmlns="http://www.erwin.com/dm/data">
    <Domain_Groups>
      <Domain id="D0"><DomainProps><Name>&lt;root&gt;</Name></DomainProps></Domain>
      <Domain id="D1"><DomainProps><Name>id_type</Name><Logical_Data_Type>BIGINT</Logical_Data_Type></DomainProps></Domain>
    </Domain_Groups>
    <Entity_Groups>
      <Entity id="E1"><EntityProps><Name>Party</Name><Physical_Name>PARTY</Physical_Name></EntityProps>
        <Attribute_Groups>
          <Attribute id="A1"><AttributeProps><Name>party_id</Name><Parent_Domain_Ref>D1</Parent_Domain_Ref></AttributeProps></Attribute>
        </Attribute_Groups>
        <Key_Group_Groups>
          <Key_Group id="K1"><Key_GroupProps><Name>pk_party</Name><Key_Group_Type>primary_key</Key_Group_Type></Key_GroupProps>
            <Key_Group_Member_Groups><Key_Group_Member id="M1"><Key_Group_MemberProps><Attribute_Ref>A1</Attribute_Ref></Key_Group_MemberProps></Key_Group_Member></Key_Group_Member_Groups>
          </Key_Group>
        </Key_Group_Groups>
      </Entity>
    </Entity_Groups>
  </Model>
</erwin>
XML

# --- BAR 2: import erwin into an EMPTY folder scaffolds a runnable project --
PROJ="/home/user/proj"
rm -rf "$PROJ"
if mdl import erwin /home/user/model.xml -o "$PROJ" >/tmp/import.log 2>&1; then
  pass "mdl import erwin (empty folder, core install) — no crash"
else
  fail "mdl import erwin" "see below"; sed -n '1,40p' /tmp/import.log
fi

# the skeleton the scaffold path writes (this is where pyoxigraph used to be pulled)
[ -f "$PROJ/.mdl/lock.yaml" ] && pass ".mdl/lock.yaml written (scaffold ran without ontology extra)" \
  || fail ".mdl/lock.yaml missing" "scaffold path failed"

# the erwin objects landed
[ -f "$PROJ/logical/entities/party.yaml" ] && pass "logical entity written" \
  || fail "logical entity missing" "$(ls -R "$PROJ" 2>/dev/null | head)"

# the "<root>" domain wrote to a SAFE filename, not a crash / not angle brackets on disk
if ls "$PROJ"/logical/domains/*.yaml >/tmp/doms.log 2>&1; then
  if ls "$PROJ"/logical/domains/ | grep -q '[<>]'; then
    fail "domain filename has illegal chars" "$(ls "$PROJ"/logical/domains/)"
  else
    pass "illegal-char domain (<root>) wrote to a safe filename ($(ls "$PROJ"/logical/domains/ | tr '\n' ' '))"
  fi
else
  fail "no domain files written" "see /tmp/doms.log"
fi
hr

# --- BAR 3: the imported model validates (still core-only) ------------------
if mdl validate -m "$PROJ" >/tmp/validate.log 2>&1; then
  pass "mdl validate passes on the imported model (core install)"
else
  fail "mdl validate" "see below"; sed -n '1,30p' /tmp/validate.log
fi
hr

# --- verdict ---------------------------------------------------------------
if [ "$fails" -eq 0 ]; then
  echo "${GREEN}ALL CLEAN-INSTALL CHECKS PASSED${RESET}"
  exit 0
else
  echo "${RED}${fails} check(s) FAILED${RESET}  (logs under /tmp/*.log in the container)"
  exit 1
fi
