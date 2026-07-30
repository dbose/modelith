# Modelith

Ontology-anchored, git-native data modeling for dbt-core teams. CLI: `mdl`.

See [`modelith-spec.md`](modelith-spec.md) for the full build specification.

## Status

Built to the spec's milestone gates (§12). **M0 and M1 are complete and tested** —
the load-bearing foundation the spec requires before anything else proceeds.

| Milestone | Scope | State |
|---|---|---|
| **M0** | IR + ULID identity + comment-preserving YAML round-trip + validator + `mdl init/validate/lint` | ✅ done, tested |
| **M1** | Protected regions + generation state + three-way merge + dbt/duckdb emitter + property tests 1,3,4 | ✅ done, tested |
| **M2** | `mdl drift --check/--reconcile` + severity classification + PR-comment/Mermaid render + CI templates | ✅ done, tested |
| **M3** | Reverse engineering + decision ledger + interactive lifting + Snowflake/Redshift/Iceberg/Trino adapters + property 2 | ✅ done, tested |
| **M4** | Generic ontology registry (FIBO = one example) + four-layer validation + RDF/SHACL + OSI 0.1.1 emit/import + MetricFlow + erwin XML import | ✅ done, tested |
| **M5** | Neutral GovernanceGraph + Jinja mapping DSL + adapter SPI + conformance kit + Collibra adapter + three reference profiles + OpenLineage | ✅ done, tested |
| **M6** | Read API (`mdl serve`) + web canvas: erwin-style ER cards, crow's-foot edges, auto-layout, search-jump, detail panel, 1000+ nodes | ✅ done, verified in-browser |
| **E1–E3** | Canvas is a full **editor**: ontology browser + four-layer stack view, typed mutation commands (comment-preserving, ULID-safe, optimistic concurrency), align-to-ontology flow, drag-to-connect relationships, editable inspector, decisions panel, git diff+commit panel, `--read-only` mode | ✅ done, verified in-browser |
| **VS Code** | Extension in `vscode/` (own npm build → .vsix): canvas as webview tab or forwarded-port browser, generate/drift/lint/new-entity commands, YAML schemas, devcontainer-ready (`extensionKind: workspace`) | ✅ built + packaged |
| **Collab** | Collaboration model (docs/collaboration-model.md): `mdl classify` routes A–E, semantic **merge driver** (ULID-keyed attribute union, verified on real git merges), sharded generation state, the **debt valve** (`mdl unmanage --reason --expires` + drift escalation), `alignment_status` proposed→accepted + `mdl ontology promote`, `mdl init --workspace` (topology + CODEOWNERS + .code-workspace); global `mdl` via `uv tool install` + terminal PATH injection in the extension | ✅ done, tested |
| **LSP** | `mdl lsp` (pygls, stdio) — one server for VS Code/Cursor/Windsurf/JetBrains/CI: drift + contract + edited-generated-block diagnostics *on the dbt files*, SME hover cards (glossary/IRI/owner), CodeLens ownership + lift, code actions (adopt column, lift model, unmanage, declare relationship), selection-scoped lifting; model preview pane follows the active editor | ✅ done, protocol-tested |

## Layout

```
packages/
  core/           # IR, ULID, YAML round-trip, validator, round-trip/merge engine (no in-repo deps)
  emit-dbt/       # dbt-core emitter, platform adapters (duckdb/snowflake/redshift/iceberg/trino), SCD2 macros
  reverse/        # manifest reader, drift + reconcile, reverse engineering, decision ledger, erwin import
  ontology/       # generic vocabulary registry, four-layer validation, RDF/OWL + SHACL export, lock
  emit-semantic/  # MetricFlow + OSI (version-isolated v0_1_1), OSI import, joinability/fan-out validation
  governance/     # neutral GovernanceGraph, Jinja mapping DSL, adapter SPI, conformance kit, OpenLineage
  adapters/
    collibra/     # Collibra governance adapter (depends only on governance)
  server/         # read API (FastAPI) + hosts the canvas build; state stays in git
  lsp/            # mdl lsp — language server (pygls): the engineer surface for any LSP editor
  cli/            # `mdl`
canvas/           # web canvas source (Vite + React + React Flow); `npm run build` -> server static
vscode/           # VS Code extension (TypeScript + esbuild); `npm run build` + `npm run package` -> .vsix
profiles/
  ci/             # shippable CI workflow templates (mdl-validate, mdl-drift, mdl-gov-sync)
  governance/     # three reference governance-profile.yaml (collibra-oob, dbt-analytics, minimal)
```

The layering rule (spec §1.3) is honored: `core` depends on nothing else in-repo;
`emit-dbt` depends only on `core`.

## Quickstart

```bash
uv sync
uv run mdl init my-model --name my_model
uv run mdl validate -m my-model
uv run mdl generate -m my-model -o my-model/target/dbt
```

Run the suite (the M1 property-test gate, spec §5.5):

```bash
uv run pytest
uv run ruff check packages/
```

## What the M1 gate proves

- **Idempotence** (property 1): `generate` twice on an unchanged model is byte-identical.
- **User-region survival** (property 3): arbitrary edits inside `-- mdl:user-*`
  blocks survive N regenerations (property-based via `hypothesis`).
- **ULID-rename propagation** (property 4): renaming by ULID leaves zero orphans.
- **Content-addressed regeneration**: every generated block carries a fingerprint;
  hand-edits to generated code raise `MDL-E201`; a model+hand change raises a
  conflict (`MDL-C301`) with git-style markers — never silent data loss (spec §5).
- **Reverse round-trip** (property 2, M3): `reverse(generate(M))` is semantically
  equal to `M` modulo an explicitly-asserted lossy set (ontology IRIs → M4,
  stewardship → M5, nullability → interactive), and `generate(reverse(generate(M)))`
  is a semantically-empty diff. ULID identity survives via `meta.mdl_ulid`.

Exit codes follow spec §10: `0` ok, `1` validation error, `2` breaking drift,
`3` merge conflict.

## Drift detection (M2)

```bash
# compile your dbt project (no warehouse needed), then:
mdl drift --manifest target/manifest.json -m model --check            # CI gate, exit 2 on breaking
mdl drift --manifest target/manifest.json -m model --format mermaid   # PR comment w/ subgraph diff
mdl drift --manifest target/manifest.json -m model --reconcile        # fold additive/cosmetic into model
```

Differences are classified `breaking` / `additive` / `cosmetic` / `unmanaged`
(spec §5.4). The 400-model acceptance test (§12) — inject a column drop, classify
breaking, fail in <30s — runs in ~2s. CI templates are in `profiles/ci/`.

## Reverse engineering (M3)

```bash
# from a compiled manifest, or (warehouse-free) an emitted schema.yml:
mdl reverse --project target/manifest.json --out model --interactive
```

Lifts a dbt project into the Modelith model (spec §6): excludes staging/intermediate,
strips surrogate keys, collapses SCD2 column triples into `pattern: scd2`, detects
Data Vault hub/link/sat, and infers relationships by ranked signal
(dbt `relationships` tests → high, accepted by default; `*_id` name/type heuristic →
medium, proposed only). Every inference is recorded in `.mdl/decisions.yaml`; a
rejected proposal is never re-asked unless its signal changes (§6.2).

Platform adapters (spec §7.2) ship for `duckdb`, `snowflake` (clustering/transient),
`redshift` (dist/sort), `iceberg` (partition/sort/format), `trino`.

## Ontology + semantic layer (M4)

```bash
mdl ontology search "counterparty"           # rank loaded vocabulary classes (§3.2)
mdl ontology check -m model                   # four-layer rules + CDO coverage report (§3.1)
mdl emit semantic --format metricflow -m model
mdl emit semantic --format osi -m model       # OSI 0.1.1, multi-dialect, ai_context (§4)
mdl export rdf --layer all --format turtle    # RDF/OWL with SKOS alignments (§3.3)
mdl export shacl                              # SHACL shapes from the logical model (§3.3)
mdl import osi model.osi.yaml --out model     # lift an existing OSI semantic layer (§4.3)
mdl import erwin model.xml --out model        # migrate from erwin (§6.4)
```

**Ontology-agnostic by design.** FIBO is only one example of an `industry`-layer
vocabulary — ACORD, FHIR, ISO 20022, GS1, or a customer's own RDF/OWL/Turtle
vocabulary plug in by declaration in `mdl-project.yaml` (`ontology_stack`), no code
changes. IRIs are resolved against the loaded graph; unresolvable IRIs are errors.

**OSI is version-isolated.** All OSI knowledge lives behind
`emit-semantic/.../osi/v0_1_1/`, pinned to the tagged `osi-0.1.1-rc1` release
(the repo's `main` is `0.2.0.dev0` DRAFT — intentionally not targeted, per §4.1).
`.mdl/lock.yaml` pins the OSI tag, dbt version, and each vocabulary version.
Joinability + fan-out risk (many-to-many, unaggregated measures across one-to-many)
is validated before any semantic emission (§8).

## Governance (M5)

```bash
mdl gov conformance --profile governance-profile.yaml -m model   # validate a bespoke mapping (§9.5)
mdl gov plan --profile governance-profile.yaml -m model -o plan.json   # never writes (§9.3)
mdl gov apply plan.json --base-url $URL --token $TOKEN           # refuses a foreign/edited plan
mdl gov pull --profile governance-profile.yaml                   # writeback into the model (§9.4)
mdl gov lineage -m model                                         # OpenLineage payload (§9.6)
```

Core emits a **neutral GovernanceGraph**; adapters consume it (the Collibra adapter
imports only `governance`, never `core`). The keystone (§9.1) is the deterministic
`external_id = mdl:<ULID>` derived from the immutable ULID — sync is idempotent, so
re-running updates and never duplicates. The mapping is a **customer-owned
`governance-profile.yaml`** with Jinja over the asset (no Python to customise a
tenant); three reference profiles ship in `profiles/governance/`. The **conformance
kit** lets a customer validate a bespoke mapping in CI without contacting us (§9.5) —
this is what keeps the mapping layer a product, not a services line. `plan` never
writes; `apply` refuses a plan it did not produce (signature check).

## Canvas (M6)

```bash
mdl serve -m model          # http://127.0.0.1:4800
```

An erwin-grade ER diagram with modern UX, served read-only from the model repo
(state stays in git — an external edit or `git pull` shows on refresh):

- **Entity cards** — header with subject-area accent + pattern badge (SCD2/hub/…)
  and ontology marker; gold-keyed primary-key section; attribute rows with type
  chips and not-null dots.
- **Crow's-foot relationships** — many/one prongs + mandatory/optional glyphs per
  end, from the model's declared cardinality and optionality.
- **Auto-layout** (dagre, left-to-right) with manual drag that persists, re-layout
  and fit-view controls, minimap, dot-grid.
- **Search** across entity and attribute names — matches highlight, everything
  else dims; `Enter` jumps to the first hit; `/` focuses the box.
- **Detail panel** — definition, ontology alignment + layer chip, stewardship,
  attribute table, clickable relationship navigation, physical realisations, and
  the copyable immutable ULID.
- **Live diagnostics chip** — `mdl validate` results surfaced in the toolbar.
- **1000+ nodes** — viewport virtualisation (only visible cards render) plus an
  mtime-fingerprint model cache on the server (cold 1000-entity load ~4s, warm
  requests ~30ms).

Rebuild the canvas after changes: `cd canvas && npm install && npm run build`
(the production bundle is committed under `packages/server/.../static`, so
`mdl serve` works from a plain Python install with no Node toolchain).

## Canvas editor (E1–E3)

`mdl serve` is a **full editor** by default (`--read-only` for shared viewers).
The canvas never writes files directly: every UI action is a typed semantic
command (`POST /api/command`) that mutates the raw YAML nodes comment-preservingly,
mints ULIDs server-side at creation, re-validates, and returns fresh diagnostics.
Commands carry the directory fingerprint they were based on — an external
`git pull` or IDE edit makes the command fail with "model changed on disk" instead
of clobbering. Git remains the only state: edits accumulate in the working tree
and the **Changes panel** shows the real diff with Commit/Discard.

- **Ontology browser** (⬡): search the loaded vocabularies (any RDF/Turtle —
  FIBO subset ships in-repo; `mdl ontology vendor fibo` pins the full QuickFIBO
  production release), term cards with definitions, broader/narrower navigation.
- **Layers view** (≣): the four-layer stack (industry/core/domain/specialised)
  with each model term's upward alignment and the live CDO coverage banner.
- **Align flow**: Inspector → "Align…" → search picker → predicate + layer →
  the entity's YAML gains its `ontology:` block.
- **Editing**: rename (ULID fixed, file follows), definitions, stewardship,
  subject areas, patterns, attribute CRUD, drag handle-to-handle to draw a
  relationship (cardinality modal), delete with cascade confirmation.
- **Decisions panel** (⚖): accept/reject reverse-engineering proposals.
- CLI twins: `mdl new entity|subject-area`, `mdl decisions list|accept|reject`.

## VS Code (standalone + devcontainer)

The extension lives in [`vscode/`](vscode/) with its own build:

```bash
cd vscode && npm install && npm run build && npm run package   # -> modelith-vscode-0.1.0.vsix
```

Open a git repo containing a Modelith model (and typically a dbt project next to
it, e.g. `model/` + `transform/`) — the extension activates on
`mdl-project.yaml`, auto-discovers both directories, and detects `mdl`
(`.venv/bin/mdl` → PATH → `uv run mdl`).

- **Canvas**: `Modelith: Open Canvas` spawns a managed `mdl serve` on a free
  port. `modelith.canvas.display` chooses **`tab`** (webview editor tab) or
  **`external`** (your browser via a forwarded port). Both resolve through
  `asExternalUri`, so devcontainers and SSH remotes tunnel automatically.
- **Validation**: on save, `mdl validate --format json` maps `MDL-*` diagnostics
  to the offending YAML lines in the Problems panel; a status-bar chip shows
  ✓ / error count.
- **Commands**: Generate dbt Project (into the discovered dbt dir, honouring
  protected regions/merge exit codes), Check Drift vs dbt Manifest (breaking
  drift → error notification), Lint `--fix`, New Entity, Vendor FIBO, Emit
  Semantic, Stop Canvas Server.
- **YAML completion**: `mdl export json-schema` output is registered with the
  Red Hat YAML extension per model glob.
- **Devcontainer**: `extensionKind: ["workspace"]` runs the extension host
  *inside* the container, next to `mdl`/dbt/credentials (spec §1.2). A template
  ships in [`profiles/devcontainer/`](profiles/devcontainer/devcontainer.json).
