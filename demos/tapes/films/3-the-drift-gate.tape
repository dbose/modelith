# FILM 3 — "The drift gate": the model and the warehouse stay in lockstep. A column lands in
# the warehouse; drift classifies it; reconcile folds the safe change into the model and the
# model is clean again. Every command, every edit, and every line of output is real — the
# drift and reconcile run against a genuine dbt manifest from `dbt parse`.
Source demos/tapes/_settings.tape
Output demos/out/3-the-drift-gate.mp4

Hide
Type "PROMPT='$ '" Enter
Type "setopt interactivecomments" Enter
Type "export PATH=/Users/dbose/projects/modelith/.venv/bin:$PATH" Enter
Type "rm -rf /tmp/mdl-film3 && cp -R /Users/dbose/projects/modelith/demo/ibor /tmp/mdl-film3 && cd /tmp/mdl-film3" Enter
Type "cp /Users/dbose/projects/modelith/demos/fixtures/add-broker-code.sh ." Enter
Type "MANIFEST=transform/warehouse/target/manifest.json" Enter
Type "mdl --version >/dev/null 2>&1" Enter
Type "clear" Enter
Show
Sleep 1.2s

# ── Scene 1: the model and the warehouse agree ──
Type "# the committed model and the compiled dbt warehouse are in lockstep" Sleep 500ms Enter
Sleep 2.5s
Type "mdl drift -m model --manifest $MANIFEST" Sleep 700ms Enter
Sleep 5s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 2: a column lands in the warehouse (a real edit + a real dbt parse) ──
Type "# a teammate adds a 'broker_code' column to the transaction model + its contract" Sleep 500ms Enter
Sleep 3s
Type "bash add-broker-code.sh" Sleep 700ms Enter
Sleep 4s
Type "cd transform/warehouse && dbt parse -q && cd ../.." Sleep 700ms Enter
Sleep 5s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 3: drift catches it and classifies it — additive, not breaking ──
Type "# drift sees it, and classifies it: additive, safe" Sleep 500ms Enter
Sleep 2.5s
Type "mdl drift -m model --manifest $MANIFEST --check" Sleep 700ms Enter
Sleep 6s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 4: reconcile folds the safe change into the model — nothing lost ──
Type "# reconcile folds the safe change into the model" Sleep 500ms Enter
Sleep 2.5s
Type "mdl drift -m model --manifest $MANIFEST --reconcile" Sleep 700ms Enter
Sleep 6s

# the model is in lockstep again, and still valid
Type "mdl drift -m model --manifest $MANIFEST && mdl validate -m model" Sleep 700ms Enter
Sleep 6s
