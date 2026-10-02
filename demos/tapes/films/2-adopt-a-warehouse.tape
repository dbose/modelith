# FILM 2 — "Adopt a warehouse": take over a legacy dbt project with no data model.
# One continuous session; every command and every line of output is real. The reverse
# detection (SCD2, Data Vault, surrogate stripping) is the warehouse's real structure.
Source demos/tapes/_settings.tape
Output demos/out/2-adopt-a-warehouse.mp4

Hide
Type "PROMPT='$ '" Enter
Type "setopt interactivecomments" Enter
Type "export PATH=/Users/dbose/projects/modelith/.venv/bin:$PATH" Enter
Type "rm -rf /tmp/mdl-film2 && cp -R /Users/dbose/projects/modelith/demo/legacy-warehouse /tmp/mdl-film2 && cd /tmp/mdl-film2 && rm -rf owned" Enter
Type "mdl --version >/dev/null 2>&1" Enter
Type "clear" Enter
Show
Sleep 1.2s

# ── Scene 1: where most teams actually are — a dbt warehouse, no model ──
Type "# an existing dbt warehouse: staging, marts, a Data Vault: but no data model" Sleep 500ms Enter
Sleep 2.5s
Type "ls models/staging models/marts models/vault" Sleep 700ms Enter
Sleep 6s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 2: one command lifts it into a model, patterns recognized ──
Type "# one command lifts it into a logical model" Sleep 500ms Enter
Sleep 2s
Type "mdl reverse --project target/manifest.json --out owned" Sleep 700ms Enter
Sleep 10s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 3: nothing is a black box — every inference is reviewable ──
Type "# every inference is a reviewable decision, not a guess" Sleep 500ms Enter
Sleep 2s
Type "mdl decisions list -m owned | head -12" Sleep 700ms Enter
Sleep 9s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 4: now you own it — it validates and reads as clean business entities ──
Type "mdl validate -m owned" Sleep 700ms Enter
Sleep 4.5s
Type "mdl model render -m owned | head -16" Sleep 700ms Enter
Sleep 8s
