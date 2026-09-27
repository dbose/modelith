# Semantic model compare & merge — one review view across every change flow

## Context

Modelith presents model changes in many flows — staged edits (preview), SME proposals, git-ref diffs,
drift, reverse-inference acceptance, imports, merge conflicts — but the review experience is fragmented
and the richest view is trapped in one corner of the Studio Canvas (the SME propose flow). A mature
commercial modeling tool (Hackolade Studio) ships a 3-pane semantic compare & merge (left / merged /
right) with per-change accept/reject for additions, modifications, moves and deletions, matching-method
and display filters, and conflict markers. That is the bar for a data-modeling tool.

This is also the paid wedge made visible. The GTM thesis is that the commercial product is
drift-enforcement-in-CI, whose hard SLO is "classify a change as breaking/additive/cosmetic and name the
exact dbt models a breaking change will take down, at PR time." A rich, shared diff/merge view is how
that classification lands in the product. It ships FREE for local review (the bottom-up funnel); the
*blocking* CI gate, shared policy, and report stay the paid server boundary. The incumbent gates all of
this behind a desktop license key; Modelith gives compare/merge away and charges for the team gate.

**The foundation already exists** (validated by codebase exploration):
- One canonical semantic-diff payload — `ModelDiffDoc`: ULID-keyed, nested object→fields→children,
  severity-tagged (breaking/additive/cosmetic/unmanaged), per-field before/after. Built by
  `ModelDiff.to_doc()` (`packages/core/src/mdl_core/diff.py:283`), serialized by `render_json`
  (`packages/core/src/mdl_core/diff_render.py:29`), mirrored at `canvas/src/types.ts:351`. Server
  enriches it with `path` (source file per object) and `breaks` (dbt models a breaking change would
  break) via `_attach_paths`/`_attach_breaks` (`packages/server/src/mdl_server/git_api.py:417,430`).
- One shape-aware renderer — `canvas/src/sme/DiffView.tsx` (word-diff for prose, +/− chips for lists,
  scalar before→after, break-impact panel), already prop-driven and API-free.
- An ER diff overlay — `canvas/src/sme/ModelDiagram.tsx` tints `EntityNode`/`RelationshipEdge` by a
  `severityByUlid` map.
- A review shell — `canvas/src/sme/ReviewScreen.tsx` (changes/diagram tabs, route/reviewer panel,
  conflict banner).
- VS Code already embeds the Canvas via an iframe webview parameterized by `?query` (`?import=1`),
  `vscode/src/canvasPanel.ts:59,169` — so a review route renders in VS Code with no new rendering code.
- Flows already producing `ModelDiffDoc`: preview (`/api/preview` → `PreviewDoc.diff`), propose
  (`/api/git/propose`), git-diff (`/api/git/diff/model|refs`). A name-keyed variant
  `diff_models_by_name` (`diff.py:550`) exists for fresh-ULID comparisons (import/compare).

**The gaps this spec closes:**
1. Selection is per-OBJECT, not per-FIELD, and there is no 3-pane merge / conflict resolution.
2. Drift (`DriftReport`), reverse-decisions (ledger), and conflicts (`ConflictDoc`) don't speak
   `ModelDiffDoc` — they need adapters into it (severity vocabulary is already shared).
3. Import writes-then-git-diffs instead of a semantic "review before it lands."
4. VS Code has no semantic diff (only native text diff + a drift tree); `compareReversed` is a
   text-diff outlier.
5. Accept/apply semantics differ per flow (PR / ledger verdict / reconcile / write) — the view needs a
   pluggable apply action, not one hardcoded to propose.

Intended outcome: a modeler reviews any model change — staged edit, drift, inferred relationship, erwin
import, branch comparison, merge conflict — in the SAME rich, filterable, per-change-selectable view,
in both the Canvas and VS Code.

## Design

### One payload, one component, pluggable sources and sinks

```
  SOURCES (adapters → ModelDiffDoc)          SHARED VIEW                 SINKS (pluggable apply)
  preview       ─┐                         ┌───────────────┐            propose → PR
  git-diff/refs  ┼─ already ModelDiffDoc ─▶│  ReviewView   │──accept──▶ ledger  → verdict write
  propose       ─┘                         │  DiffView++   │            drift    → --reconcile fold
  drift  ─────────  DriftReport  ─adapter─▶│  filters      │            import   → write files
  decisions ──────  Decision[]   ─adapter─▶│  per-field    │            (read-only sources: no sink)
  import ─────────  diff_by_name ────────▶ │  ER overlay   │
  conflicts ──────  merge_driver ─3-way──▶ │  3-pane merge │
                                           └───────────────┘
```

The whole design is: everything converges on `ModelDiffDoc`, one React component renders it, and each
flow supplies (a) a fetch that returns the doc and (b) an apply action. `ModelDiffDoc` is not changed in
shape; where a 3-pane merge needs base/ours/theirs, that is carried as an optional wrapper, not a new
per-node schema.

### The shared component: `ReviewView`

Generalize `DiffView`/`ReviewScreen` into a reusable `ReviewView` that stays prop-driven and API-free
(so VS Code hosts it verbatim in the webview). It renders a `ModelDiffDoc` and takes:
- `diff: ModelDiffDoc` (2-way) or `merge: { base, ours, theirs, conflicts }` (3-way).
- `selection`: per-FIELD selection — keys are `(ulid, field)` not just object ULIDs, so a user can
  accept the definition change but reject the domain change on one attribute (the competitor's
  per-addition/modification/move toggles). Widen the existing staging set (`SmeApp.excluded`,
  `canvas/src/staging/useStaging.ts`) from object to field granularity; keep the dependency-locking
  (`dependentKeys`) so changes that must travel together stay linked.
- `filters`: client-side over the doc — show all / only differences / only additions / only deletions /
  only modifications / only moves / only conflicts (severity + ChangeType are already on each node).
  This is the competitor's left rail.
- `actions: { label, apply(selection) }[]`: the pluggable action bar. Same component drives Propose
  (PR), Accept (ledger verdict), Reconcile (drift fold), Apply-import (write files). Read-only sources
  pass none.
- `mode`: `"unified"` (default 2-pane master-detail, today's view) or `"three-pane"` (base | merged |
  right, for merge/conflict sources) reusing `FieldDiff` in each pane.
- ER overlay tab stays (`ModelDiagram` + `severityByUlid`), now available to every source.

Reuse, do not rewrite: `FieldDiff`/`wordDiff` (shape-aware rendering), `ModelDiagram` (ER overlay),
`ReviewScreen`'s route/reviewer/conflict panels.

### Source adapters → `ModelDiffDoc`

Keep `diff_models` pure; adapters live beside the server enrichers and reuse `_attach_paths`/
`_attach_breaks` so every source gets source-file + break-impact enrichment.
- **Drift → ModelDiffDoc**: `drift_to_diff(report, model)` maps each `DriftItem` (name-keyed,
  manifest-shaped, `packages/reverse/src/mdl_reverse/drift.py`) onto an `ObjectChange`/`FieldChange`
  (name-keyed, like the `--against` path), preserving `payload` for the reconcile action and the shared
  severity. New endpoint `GET /api/drift?target=` returns the adapted doc (drift has no server endpoint
  today — it is CLI/CI-only). Apply action = `mdl drift --reconcile` fold for additive/cosmetic.
- **Decisions → ModelDiffDoc**: `decisions_to_diff(ledger, model)` turns each proposed `Decision` (an
  inferred relationship, a stripped key, an SCD2 pattern; `packages/reverse/src/mdl_reverse/ledger.py`)
  into a proposed `ObjectChange` with its `evidence` as detail. Apply action = `set_verdict`
  (accept/reject) — reuse `POST /api/decisions/{signal_key}/verdict`.
- **Import (pre-write) → ModelDiffDoc**: wire `diff_models_by_name(current_model, imported_model)`
  (`diff.py:550`, name-keyed since imported ULIDs are fresh) so erwin/DDL imports show a semantic diff
  BEFORE writing. New endpoint `POST /api/import/preview` returns the doc; apply action writes the
  files. This is the highest-value new flow — today imports write then git-diff.
- **Conflicts → 3-way (last)**: `merge_model_files` already computes structural base/ours/theirs
  conflicts (`packages/core/src/mdl_core/merge_driver.py:31`); `conflicts_to_diff` feeds the 3-pane
  mode. Lowest priority (furthest from the current shape); design the 3-pane contract for it, implement
  drift/decisions/import first.

### Routing in the Canvas

Add a source-parameterized review route to the hash router (`canvas/src/sme/SmeApp.tsx` already
hash-routes tabs): `#review?source=drift`, `?source=diff&base=main&head=<branch>`, `?source=import`,
`?source=decisions`. A small per-source loader fetches the adapted doc and mounts `ReviewView` with the
right actions. Re-point the architect canvas's raw `<pre>` textual git diff (`ChangesView`,
`canvas/src/SidePanel.tsx:291`) and `DecisionsView` at the shared component so the direct-write app gets
the rich review too.

### Surfacing in VS Code (via the existing webview — no new rendering code)

Primary path: commands `modelith.reviewDrift`, `modelith.reviewChanges` (working tree vs base),
`modelith.reviewImport`, `modelith.reviewDecisions`, each calling `canvas.open(dir, "review&source=…")`
so `ReviewView` renders in the `modelithCanvas` iframe webview exactly as `?import=1` does today. Keep
the native Drift tree (`vscode/src/driftView.ts`) and Reverse Review tree (`vscode/src/reverseView.ts`)
as fast glance-able entry points, and add an "Open in review" action on each that launches the webview
for the deep-dive. Re-point `modelith.compareReversed` (`vscode/src/extension.ts:1472`) from the native
text diff of `mdl model render` to the webview review over `diff_models_by_name`→`ModelDiffDoc`.

## Open-core boundary (which parts are free vs paid)

- **Free** (local, complete): the entire `ReviewView` — 2-pane and 3-pane, per-field selection, filters,
  ER overlay — for reviewing staged edits, imports, local `mdl drift`, branch/ref diffs, and resolving a
  local merge conflict. This is the bottom-up funnel; it must never be crippled.
- **Paid (Team, server boundary)**: the *blocking* CI gate that runs the same classification in the
  org's CI with a shared policy file and a posted PR report; CODEOWNERS-routed proposal review with PR
  state; hosted SME Studio. The view is free; enforcing it across a team is the wedge.

## Phasing (each phase shippable)

1. **Per-field selection + filters + pluggable actions** on the existing 2-pane `ReviewView`, wired to
   flows that ALREADY produce `ModelDiffDoc` (preview, propose, git-diff/refs). Immediate UX win, no
   adapters. Modernizes the SME review on its own.
2. **Import pre-write review** (import adapter + canvas route + VS Code command) — highest-value new
   flow; erwin import becomes "review before it lands," reusing phase 1.
3. **Drift review** (drift adapter + `/api/drift` + canvas route + VS Code command + tree "Open in
   review").
4. **Decisions review** (decisions adapter + route + VS Code).
5. **3-pane merge / conflict resolution** (3-pane mode + conflicts adapter) — full compare/merge parity.
   Last, since it is furthest from the current shape; the common 2-way review ships long before it.
6. Re-point `compareReversed`; retire the architect `<pre>` git diff for the shared component.

## Critical files

- `canvas/src/sme/DiffView.tsx`, `ReviewScreen.tsx`, `ModelDiagram.tsx` — the component to generalize.
- `canvas/src/staging/useStaging.ts`, `sme/SmeApp.tsx` — selection state, widened to per-field.
- `canvas/src/types.ts` — `ModelDiffDoc` and friends (shared contract; optional 3-way wrapper).
- `canvas/src/api.ts` — new fetches (`/api/drift`, `/api/import/preview`, decisions-as-diff).
- `packages/server/src/mdl_server/git_api.py` — reuse `_attach_paths`/`_attach_breaks`; new endpoints.
- `packages/server/src/mdl_server/` — new adapter module(s): `drift_to_diff`, `decisions_to_diff`.
- `packages/core/src/mdl_core/diff.py` — `diff_models_by_name` (reuse); shared adapter helpers.
- `vscode/src/extension.ts`, `canvasPanel.ts`, `driftView.ts`, `reverseView.ts` — review commands +
  `canvas.open(dir, "review&source=…")`; re-point `compareReversed`.

## Verification

- `uv run pytest && uv run ruff check packages/` — adapters produce valid `ModelDiffDoc` the canvas
  types accept; new endpoints return the right shape; existing diff/drift tests unchanged.
- `cd canvas && npm run build` (Node 20) — generalized `ReviewView` typechecks; per-field selection +
  filters render.
- `cd vscode && npm run typecheck && npm run regression` (Node 20) — review commands registered
  (Gate 3), all gates green.
- Manual per phase: open the review from each wired flow in the Canvas AND VS Code (webview); toggle
  per-field selections + filters; exercise each source's apply action; import shows a semantic diff
  before writing.
- Clean-install Docker check (`./sandbox/clean-install/run.sh`) still green (server/import path).

## Non-goals / explicitly rejected

- A monolithic desktop-IDE workspace (Object Browser + Properties pane + central pane): fights the
  git-native, CLI-first, every-surface-a-client architecture. Modelith's equivalent is Canvas +
  Inspector + VS Code.
- An in-app Git panel (branch/commit/PR management inside the tool): anti-strategic. Modelith IS
  git-native; re-centralizing git state contradicts the core differentiator.
- Changing `ModelDiffDoc`'s per-node shape. Merge state (base/ours/theirs) is an optional wrapper, not a
  schema change, so the one payload and one renderer stay singular.

## Risk posture

- Additive and phased: phase 1 modernizes the existing review with no adapters and no new payload; later
  phases add one source at a time behind the same component. Each phase ships alone.
- One payload, one renderer: adapters converge on `ModelDiffDoc`, so there is exactly one review UI to
  maintain; the two intentional deviations (drift name-keying, compareReversed text) are reconciled by
  adopting the existing `diff_models_by_name`, not by inventing a shape.
- VS Code reuse via the existing iframe webview + `?query` means the extension gains the full semantic
  review with essentially no new rendering code — the lowest-risk cross-frontend path.
- The 3-pane merge/conflict work is isolated to the last phase, so the hard part never blocks the wins.
