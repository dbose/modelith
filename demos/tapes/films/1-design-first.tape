# FILM 1 — "Design-first": a new domain, from a blank repo to running dbt + a data contract.
# One continuous session — the project is set up once (hidden) and evolves on screen; the
# `clear`s are scene breaks, not resets. Every command and every line of output is real.
Source demos/tapes/_settings.tape
Output demos/out/1-design-first.mp4

Hide
Type "PROMPT='$ '" Enter
Type "setopt interactivecomments" Enter
Type "export PATH=/Users/dbose/projects/modelith/.venv/bin:$PATH" Enter
Type "rm -rf /tmp/mdl-film1 && cp -R /Users/dbose/projects/modelith/demos/fixtures/shop /tmp/mdl-film1 && cd /tmp/mdl-film1" Enter
Type "mdl --version >/dev/null 2>&1" Enter
Type "clear" Enter
Show
Sleep 1.2s

# ── Scene 1: the model you've designed, in git as plain YAML ──
Type "# a data model, designed in git: entities, keys, relationships" Sleep 500ms Enter
Sleep 2.5s
Type "mdl model render | head -20" Sleep 700ms Enter
Sleep 7s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 2: evolve it — add an entity (ULIDs minted for you) ──
Type "# the model evolves: add an entity, Modelith mints its ULIDs" Sleep 500ms Enter
Sleep 2.5s
Type "mdl new entity payment --definition 'A settlement against an order'" Sleep 700ms Enter
Sleep 4.5s

# every change is validated — schema, references, naming
Type "mdl validate" Sleep 700ms Enter
Sleep 5s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 3: compile the model to real, contract-enforced dbt ──
Type "# one model compiles to real dbt..." Sleep 500ms Enter
Sleep 2s
Type "mdl generate" Sleep 700ms Enter
Sleep 5s

# the schema carries ENFORCED contracts, keyed back to the model by ULID
Type "awk '/- name: order$/{f=1} f{print} /primary_key/{if(f)exit}' target/dbt/models/schema.yml" Sleep 700ms Enter
Sleep 7s
Type "clear" Sleep 300ms Enter
Sleep 800ms

# ── Scene 4: ...and to more than dbt — an ODCS data contract, from the same model ──
Type "# ...and to a data contract, from the same model" Sleep 500ms Enter
Sleep 2s
Type "mdl export contract" Sleep 700ms Enter
Sleep 4s
Type "head -16 datacontract.yaml" Sleep 700ms Enter
Sleep 7s
