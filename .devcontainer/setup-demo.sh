#!/usr/bin/env bash
# postCreate for the Modelith onboarding devcontainer.
#
# Deliberately does NOT scaffold anything into the workspace. This container is
# meant to be opened on YOUR repo, and a setup script that ran `mdl init --demo`
# into the workspace would litter your working tree with model/ and transform/.
# So this only confirms the CLI is installed; you run the demo yourself, when you
# want it, from the integrated terminal (see the banner below).
#
# The Modelith extension is installed automatically by Dev Containers from the
# customizations.vscode.extensions list in devcontainer.json (marketplace id).
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

echo "==> mdl on PATH?"
command -v mdl >/dev/null 2>&1 && mdl --help >/dev/null 2>&1 \
  && echo "    yes: $(command -v mdl)" \
  || { echo "    NO — CLI not detected; the extension's zero-config path would also fail"; exit 1; }

cat <<'EOF'

============================================================
CLI ready, extension installed. Nothing was scaffolded into your workspace.

To try the demo (writes to ~/modelith-demo, never your repo):

    mdl init --demo                     # 7-entity model + DuckDB dbt project
    mdl serve -m ~/modelith-demo/model  # populated ER canvas on :4800

Or point the extension at it: set modelith.modelDir to
~/modelith-demo/model, then run "Modelith: Open Canvas".

To model YOUR project instead, reverse an existing dbt warehouse:

    mdl reverse --project path/to/target/manifest.json -o model

Zero-config check: nothing set modelith.mdlPath — the extension found `mdl`
on PATH by itself.
============================================================
EOF
