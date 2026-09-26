# `mdl docs` (static + served warehouse docs) + a state-aware Modelith panel

## Context

Modelith reverses a warehouse into logical models with recovered PK/FK, ontology alignment,
confidence bands, and a decision ledger — but today the only way to *see* a model is the live,
editable canvas (`mdl serve`/`mdl studio`, embedded in VS Code). There is no portable, shareable
documentation artifact: nothing a data consumer, reviewer, or stakeholder who does not run Modelith
can open. dbt has dbt-docs; Modelith has no equivalent. This adds one.

Two connected pieces the user asked for:

1. **`mdl docs`** — a documentation site for a warehouse and its logical models, analogous to
   dbt-docs: a **static, self-contained HTML artifact** (committable, publishable to a wiki / S3 /
   Pages, viewable with no server) *and* a **served route** for local preview, surfaced inside VS
   Code as an integrated browser tab. Docs are **mostly a reference artifact** (overview, per-entity
   pages, ERD, glossary) with only a light "Next actions" summary block, not per-entity state
   callouts. It behaves like dbt-docs: a **persistent left tree-view** of logical models (grouped by
   subject area) on every page for navigation; each logical model has a **structured detail page**
   (metadata, attributes, keys, relationships, ontology alignment, in clear sections); and each
   detail page renders a **Mermaid neighbourhood diagram** of that entity plus its related entities
   within N hops (`--neighbourhood-radius`, default 2).

2. **A state-aware Modelith panel** — the left-panel views (Warehouse Config, Reverse Review, Drift)
   should stop showing blank voids and instead surface, in the **existing frames** (adding a new
   frame only where a view has no home for it), the **actions that can/should be performed based on
   the current workspace + `.mdl` state**, plus a one-click link to open the docs once generated.
   The next-action logic lives in a new **`mdl status --format json`** CLI command (single source of
   truth); the extension shells out to it (matching the existing "extension shells out to mdl"
   architecture) and renders rows.

Intended outcome: a new user reverses a warehouse, the panel tells them exactly what to do next at
each step, and one click produces a shareable docs site — the "taste that sells" free-tier surface
the GTM spec calls for (local surfacing gets no downgrade; hosted/multi-user docs is a future paid
extension point, not built here).

**Constraints (standing):** feature branch, no dev on main; no Claude attribution on commits/PRs;
no em-dashes / "load bearing" in generated prose; docs are a reproducible offline artifact (no LLM
at generate-time). Render tech is **Python + jinja2** (already a dep), reusing existing data and
emitters — no Node/vite build, no coupling to the React canvas bundle.

## Architecture principle

Both surfaces read the **same model + `.mdl` state** through existing loaders. Nothing in the
reverse/drift/ledger engines changes. `mdl docs` is a new *reader+renderer* over
`projection.project()`; `mdl status` is a new *assessor* composing existing state sources. The VS
Code changes are additive rows/commands over the existing tree providers.

## Deliverables

### A. `packages/docs` — the static docs generator (NEW package `mdl_docs`)
A new workspace member (mirror an existing `packages/emit-*` package layout; register it in
`hatch_build.py` FORCE_INCLUDE + `pyproject.toml` dev-mode-dirs — see
[[modelith-editable-install-gotcha]]). Pure `Model -> files` rendering, no server dependency.
- `render_site(model: Model, out: Path, *, base_url: str = "", neighbourhood_radius: int = 2) ->
  list[Path]` — the entry point. Renders jinja2 templates to a self-contained directory:
  `index.html` (overview), one `entities/<name>.html` per logical entity, `glossary.html`,
  `subject-areas/<name>.html`, plus a single vendored `assets/` (one CSS file, a pinned
  `mermaid.min.js` for client-side ERD render, no external CDN, offline-safe). `base_url` supports
  publishing under a sub-path (Pages).
- **Left tree-view (dbt-docs style), on EVERY page.** A persistent sidebar listing all logical models
  grouped by subject area (ungrouped bucket for entities with no subject area), each a link to its
  detail page, with the current page highlighted. Rendered once into `base.html` from the sorted
  projection so every page shares it; a small client-side filter box (plain JS in the vendored asset,
  no framework) narrows the list by name. This is the primary navigation, mirroring dbt-docs' left
  model tree.
- **Structured per-entity detail page (dbt-docs style).** Clear sections in a fixed order:
  (1) header (name, subject area, pattern, conceptual definition); (2) attributes table (name, type/
  domain, role, nullable, PK/FK/unique flags, enum values, per-attribute definition); (3) keys
  (PK + alternate/unique key groups); (4) relationships (incoming and outgoing, with cardinality and
  the joining columns); (5) ontology alignment (each `OntologyRef`: predicate, uri, layer, status,
  confidence); (6) stewardship (owner/steward) and UDPs; (7) where-used. All fields come straight
  from `projection.project()` (it already carries every one).
- **Per-entity Mermaid neighbourhood diagram.** On each detail page, an ERD scoped to the entity plus
  every entity reachable within `neighbourhood_radius` hops over the relationship graph (BFS from the
  entity's ULID over `Relationship.from_.entity`/`to.entity` adjacency; radius 2 default). Compute
  the id set in the generator, then render via a new **backward-compatible** `emit_mermaid(model, *,
  include: set[str] | None = None)` overload (`packages/emit-erd/src/mdl_emit_erd/mermaid.py:22`) that
  filters tables/edges to `include` (default None = whole model, unchanged for all existing callers).
  Overview and subject-area pages use the unscoped / subject-area-scoped diagram.
- **Data source (reuse, do not reinvent):** `projection.project(model)` (`packages/server/src/
  mdl_server/projection.py:110`) already pre-joins every doc-relevant field — definition, pattern,
  attributes (domain/role/nullable/enum_values/ontology_refs), key_groups, category role, conceptual
  context (definition, synonyms, subject_area, ontology_layer, ontology_refs, stewardship),
  relationships, subject_areas, counts. Call it once, render from the dict. For "where used" per
  entity, reuse `where_used(model, conceptual_id)` (`projection.py:71`).
- **ERD (reuse):** `emit_mermaid(model)` (`packages/emit-erd/src/mdl_emit_erd/mermaid.py`) for the
  overview diagram and a per-subject-area scoped diagram; embed the Mermaid text in a `<pre
  class="mermaid">` block rendered by the vendored mermaid.js. PK/FK/nullability per entity come from
  `build_tables(model, adapter)` (`packages/emit-erd/src/mdl_emit_erd/model_view.py:68`), the same
  neutral view SQL/DBML use.
- **Light "Next actions" block** on `index.html`: render the summary from `mdl status` (call the
  Deliverable C function directly, in-process — no subprocess). Reference-only; no per-entity state
  callouts.
- **Templates** under `packages/docs/src/mdl_docs/templates/` (jinja2, `autoescape=True` for HTML;
  note governance uses `autoescape=False` for SQL — docs must differ). A `base.html` + page
  templates. Deterministic output (sorted iteration) so regenerated docs diff cleanly.

### B. CLI: `mdl docs` sub-app (`packages/cli/src/mdl_cli/main.py`)
A new `docs_app = typer.Typer(...)` registered on the root `app` (alongside `emit_app`/`export_app`
at `main.py:41-55`). **`mdl docs` — not `mdl generate docs`** (the top-level `generate` at
`main.py:293` is the dbt build; overloading it would confuse). Two commands:
- `docs generate` — `-m/--model-dir`, `-o/--out` (default `target/mdl-docs/`), `--base-url`,
  `--neighbourhood-radius` (default 2). Loads the model (`ModelRepo.load`), calls `render_site`,
  prints the output path. The static artifact.
- `docs serve` — `-m`, `--host`, `--port`. Serve the generated (or freshly-rendered-to-temp) site
  over a tiny static file server. **Reuse the existing FastAPI app**: add a read-only `/docs` mount
  to `create_app` (`packages/server/src/mdl_server/app.py:74`) that serves the rendered site
  directory, so `mdl serve` also exposes `/docs` and the extension can embed it without a second
  process. `docs serve` is the standalone convenience wrapper.

### C. `mdl status` — the shared next-action assessor (`packages/cli/src/mdl_cli/main.py` + a core helper)
The single source of truth for "where is this workspace and what should I do next", consumed by both
the panel (via subprocess JSON) and docs (in-process).
- Put the logic in a new `packages/core/src/mdl_core/status.py`: `assess(model_dir: Path,
  manifest: Path | None) -> WorkspaceStatus` returning a typed structure: pipeline stage + a ranked
  list of `NextAction { id, title, detail, command, severity }`. It composes **existing** state
  sources only:
  - manifest presence (`findManifestPath` equivalent — a compiled dbt project exists?),
  - reversed models present (any logical entities in the model dir?),
  - pending decisions + confidence bands via `DecisionLedger.load(root).pending()`
    (`packages/reverse/src/mdl_reverse/ledger.py:111,134` — each `Decision.confidence`),
  - drift status (has a manifest to compare; do not run drift, just report "drift not checked" vs a
    cached result is session-only in the extension — CLI reports "checkable"),
  - model hygiene signals already in the IR: entities/attributes with empty `definition`, ontology
    refs with `status: proposed` (unaligned terms),
  - docs freshness: does `target/mdl-docs/index.html` exist and is it older than the newest model
    YAML?
- CLI command `mdl status` with `--format text|json`, `-m`, `--manifest`. JSON is what the extension
  parses (mirror `decisions list --format json`).
- Ranked next-actions map to real commands, e.g. `{command: "modelith.reverseEngineer"}` when no
  models, `{command: "modelith.driftCheck"}` when models exist but drift unchecked, `{command:
  "modelith.docsOpen"}` once reviewed, "review N medium-confidence proposals" when pending.

### D. VS Code: state-aware panel + docs tab (`vscode/src/`)
Surface C in the **existing frames**; add the docs tab; fix the blank voids.
- **NEW `vscode/src/statusModel.ts`** — a thin client that runs `mdl status --format json` (via
  `runMdl`) and caches `WorkspaceStatus`, with `onDidChange`. One assessor call feeds all views.
- **Reverse Review resting/next-action rows** (fix the blank void = the deferred Backlog #1): add a
  `StatusNode` type to `ReverseReviewProvider` (`vscode/src/reverseView.ts:20,101`) mirroring
  `DriftTreeProvider`'s resting-row pattern (`vscode/src/driftView.ts:98-117`). When
  `decisions.length === 0`, render a next-action row from `statusModel` (e.g. "No proposals — reverse
  a warehouse" or "All reviewed ✓ — generate docs") instead of `[]`. Rows get `TreeItem.command`
  (the clickable-row pattern from `configView.ts:139`) pointing at the ranked action's command.
- **Drift + Warehouse Config next-action rows**: Drift already has resting rows; enrich them from
  `statusModel` so the resting label reflects the ranked next action. Config already has
  `viewsWelcome` — leave it, but its rows can carry the docs link when appropriate.
- **Context keys for state** (`setContext`): add `modelith.hasModels`, `modelith.hasPendingDecisions`,
  `modelith.docsGenerated` (alongside the existing `modelith.configHasManifest` at
  `configView.ts:101`) so `viewsWelcome`/menu `when` clauses can gate the docs button and next-action
  buttons.
- **Docs commands + tab**: register via the `cmd()` helper (`extension.ts:309`):
  - `modelith.docsGenerate` — runs `mdl docs generate -m . -o target/mdl-docs`, then sets
    `modelith.docsGenerated`.
  - `modelith.docsOpen` — ensure the canvas/serve process is up (reuse `CanvasManager.ensureServer`
    /`externalUrl`, `vscode/src/canvasPanel.ts:78,103`), then open a WebviewPanel iframe at
    `<base>/docs` (clone the `CanvasManager.open` "tab" mode, `canvasPanel.ts:27-54`) — the same
    mechanism as the canvas tab in the screenshot. Honor `modelith.canvas.display` (tab vs external).
  - A **title-bar "Open Docs" button** on the appropriate view(s) via `contributes.menus["view/title"]`
    gated on `modelith.docsGenerated`, and a next-action row that offers "Generate docs" when not yet
    generated. Command declared in `contributes.commands` (Gate 3 of the regression suite enforces
    every declared command is registered).
- **Live refresh** (close the gap the survey found): add a `createFileSystemWatcher` on
  `**/.mdl/decisions.yaml` and the manifest path that triggers `statusModel.refresh()` +
  `reverse.refresh()`, so counts and next-actions update without a manual refresh. (Today the only
  watcher feeds the LSP, `vscode/src/lspClient.ts:28`.)

### E. Docs + packaging
- `packages/docs/pyproject.toml` (new member) with jinja2 dep; add to root `pyproject.toml`
  dev-mode-dirs and `hatch_build.py` FORCE_INCLUDE ([[modelith-editable-install-gotcha]] — the
  templates/assets are package DATA, ensure they ship in the wheel like `mdl_server/static`).
- README: a "Documentation" section under the canvas section (README.md:158), showing `mdl docs
  generate` + the VS Code "Open Docs" tab, and how to publish the static site.
- Version bumps: root `pyproject.toml` (CLI) + `vscode/package.json`.

## Critical files
- **NEW** `packages/docs/src/mdl_docs/{__init__.py,render.py,templates/,assets/}` — the generator.
- **NEW** `packages/core/src/mdl_core/status.py` — `assess()` next-action engine.
- `packages/cli/src/mdl_cli/main.py` — `docs_app` (`docs generate`/`docs serve`) + `status` command;
  register on root `app` at the sub-app block (`:41-55`).
- `packages/emit-erd/src/mdl_emit_erd/mermaid.py` — add the optional `include` filter to
  `emit_mermaid` (`:22`) for the neighbourhood diagram (backward-compatible).
- `packages/server/src/mdl_server/app.py` — add read-only `/docs` static mount to `create_app`
  (`:74`), served like the existing SPA statics (`:412-451`).
- **NEW** `vscode/src/statusModel.ts` — `mdl status --format json` client + `onDidChange`.
- `vscode/src/reverseView.ts` — `StatusNode` resting/next-action rows (mirror `driftView.ts:98-117`);
  clickable via `TreeItem.command` (`configView.ts:139`).
- `vscode/src/driftView.ts` / `vscode/src/configView.ts` — enrich resting rows from `statusModel`.
- `vscode/src/extension.ts` — `modelith.docsGenerate`/`docsOpen` via `cmd()` (`:309`); docs tab
  cloning `CanvasManager.open` tab mode (`canvasPanel.ts:27-54`); new `setContext` keys; the
  `.mdl/decisions.yaml`/manifest file watcher.
- `vscode/package.json` — `contributes.commands` (docsGenerate/docsOpen), `view/title` "Open Docs"
  button gated on `modelith.docsGenerated`, any new `viewsWelcome`.
- `hatch_build.py`, root `pyproject.toml`, `README.md`, `vscode/scripts/regression.sh` (the new
  commands pass Gate 3).

## Reuse (found in exploration — do not reinvent)
- `projection.project(model, subject_area=None)` (`packages/server/src/mdl_server/projection.py:110`)
  + `where_used` (`:71`) + `scoped_ulids` (`:97`) — the docs data layer, already complete.
- `emit_mermaid(model)` (`packages/emit-erd/.../mermaid.py:22`) + `build_tables(model, adapter)`
  (`.../model_view.py:68`) — ERDs and PK/FK/nullability, same neutral view SQL/DBML use. Extend
  `emit_mermaid` with an optional `include: set[str] | None` filter (default None = whole model,
  unchanged for existing callers) for the per-entity neighbourhood diagram.
- Relationship adjacency for the neighbourhood BFS: `Relationship.from_.entity` / `to.entity`
  (`packages/core/src/mdl_core/ir.py:319,334`) — a plain radius-N graph walk, no new data needed.
- `_export_sme_app(dest)` (`main.py:2912`) — the precedent for writing a self-contained static bundle
  (follow-imports + copy assets) if the CSS/JS needs chunking; docs is simpler (one CSS + mermaid.js).
- `DecisionLedger.load(root).pending()` (`packages/reverse/.../ledger.py:111,134`) — pending
  decisions + confidence bands for `assess()`.
- `CanvasManager` (`vscode/src/canvasPanel.ts`): `open` tab mode (`:27-54`), `ensureServer` (`:78`),
  `externalUrl` (`:103`), `asExternalUri` tunnelling — clone for the docs tab.
- `DriftTreeProvider.getChildren` resting rows (`vscode/src/driftView.ts:98-117`); `ti.command`
  clickable row (`vscode/src/configView.ts:139`); `cmd()` registrar (`extension.ts:309`);
  `setContext modelith.configHasManifest` (`configView.ts:101`) as the context-key template.
- jinja2 already a dependency (used in `packages/governance/.../profile.py:138`, but with
  `autoescape=False` — docs must set `autoescape=True`).

## Sequencing (feature branch `feat-mdl-docs`; each step keeps the suite green)
1. `packages/docs` generator (A) + `mdl_core/status.py assess()` (C-core) + Python tests: render a
   fixture model to HTML (assert entity pages, ERD block, glossary present, deterministic output);
   `assess()` returns correct ranked actions for the states no-manifest / no-models / pending /
   reviewed. Shippable core: `mdl docs generate` + `mdl status` from the terminal.
2. CLI wiring (B + C-cli): `docs generate`/`docs serve`, `status --format json/text`, the `/docs`
   server mount + CLI tests (mock/serve to a temp dir).
3. VS Code: `statusModel.ts` + Reverse resting rows (Backlog #1) + Drift/Config enrichment + context
   keys; regression `npm run typecheck && npm run regression`.
4. VS Code: docs tab (`docsGenerate`/`docsOpen`, title-bar button) + the `.mdl/decisions.yaml`
   watcher.
5. Packaging (hatch_build/pyproject/dev-mode-dirs) + README + version bumps.

## Verification
- **Python (`uv run pytest && uv run ruff check packages/`):** fixture-model render test (files
  exist, deterministic, ERD + ontology-confidence rendered, no CDN refs, the left tree-view lists all
  entities on an entity page, the structured sections are present); a neighbourhood test — radius 1 vs
  2 on a fixture chain A->B->C->D includes exactly the expected entity set, and `emit_mermaid(model,
  include=...)` filters edges to that set while `emit_mermaid(model)` is unchanged; `assess()`
  state-machine test across the pipeline stages; `mdl docs generate`/`mdl status --format json` CLI
  tests (temp dir, assert file tree + JSON shape); `/docs` mount returns 200 for `index.html` and an
  entity page.
- **Extension (`cd vscode && npm run typecheck && npm run regression`, Node 20):** all gates incl.
  Gate 3 (docs commands registered) + Gate 2b (cross-platform resolution) green; Reverse view no
  longer returns `[]` when empty (a StatusNode row appears).
- **Manual (acceptance), against the DuckDB demo:** reverse the demo → panel shows ranked
  next-actions in the existing frames (no blank Reverse void) → click "Generate docs" → `target/
  mdl-docs/` written → "Open Docs" opens the integrated tab at `/docs` with the left model tree-view,
  overview ERD, structured per-entity detail pages (each with its radius-2 neighbourhood diagram),
  and glossary → click through the tree to navigate → open `index.html` directly in a browser (no
  server) and confirm it renders fully offline (mermaid diagrams included, tree filter works).