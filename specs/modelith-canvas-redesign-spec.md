# Studio Canvas: toolbar redesign + subject-area filter drawer

## Context

The Studio Canvas toolbar (branch `canvas-ux`) is a single non-wrapping flex row cramming
brand + search + a subject-area color **legend** + stats + ~11 cryptic Unicode glyph buttons
(`⬡ ≣ ± ⚖ {T} ⇕ ⟳ ⌗ ▾ ← ⛶`). The legend (`TopBar.tsx` `.legend`, `styles.css:47`) has
`flex-wrap: wrap` on its items but lives in a `nowrap` row, so with several subject areas it
squeezes, stacks into multiple lines, and shoves the whole bar taller — the "renders horribly"
the user saw. Worse, subject areas are a **static color key with no interaction**: there is no
way to focus/visualise a single domain, which is the entire point of subject areas on a large
(46-entity) model.

Two things already exist and must be reused, not reinvented:
1. **A better toolbar already lives on `feat-semantic-compare-merge`**: a two-row layout
   (row 1 = brand/search/status, row 2 = grouped, *labelled* action buttons), an inline-SVG
   icon library `canvas/src/icons.tsx` (17 icons, no font dependency, crisp on every OS), and
   grouped-button CSS (`.topbar-row`, `.btn-group`, `.grp-btn`, `.grp-tag`, `.sa-legend`). That
   branch still leaves subject areas as a static `.sa-legend` strip with no filtering.
2. **`areaByEntity`** (`ModelCanvas.tsx:154`, `Map<entityId,{id,name}>`) already resolves every
   entity → its subject area from both the direct pointer and the inverted `members` list. It is
   the ready-made filter key.

**Intended outcome:** adopt the `feat` branch's grouped two-row toolbar as the base (reconciling
the two divergent toolbars), keep the layout-picker + the new flow-direction toggle already on
`canvas-ux`, and add a **left slide-out drawer** (hamburger, top-left) that lists subject areas
as an *interactive filter* — click a domain to show ONLY it (hide the rest), multi-select via
checkboxes, with color chips + entity counts — plus the layout/direction controls. Filtering
hides non-selected entities (React Flow `hidden`) and auto-fits to the visible set.

User decisions (locked): **adopt `feat` toolbar + extend**; **hide** non-selected on filter;
**left slide-out drawer** holding both the domain filter and the layout controls.

## Deliverables

### A. Adopt the grouped two-row toolbar (port from `feat-semantic-compare-merge`)
- Bring `canvas/src/icons.tsx` onto `canvas-ux` verbatim (17 inline-SVG icons), plus a few new
  icons this feature needs: **IconMenu** (hamburger), **IconFilter/IconDomains**, and direction
  arrows (or reuse `IconLayout` + a rotating glyph). Keep the `Svg` wrapper + `currentColor`/size
  convention.
- Restructure `TopBar.tsx` into the two rows and grouped `.btn-group`s exactly as the `feat`
  branch does (`context` row: brand, search, `.stats` with `IconEntity`/`IconRelationship`/diag
  chip; `actions` row: `+ Entity`, **Panels** group, **View** group, **Data** group). Replace
  every Unicode glyph with its SVG icon + (where the `feat` branch does) a text label.
- Port the toolbar CSS blocks (`.topbar-row`, `.btn-group`, `.grp-btn`, `.grp-tag`,
  `.grp-badge`, `.stat-item`) into `canvas/src/styles.css`, replacing the old single-row
  `.topbar`/`.legend`/`.actions` rules.
- **Merge, don't overwrite, the LayoutPicker**: the `feat` picker has NO direction toggle; the
  `canvas-ux` picker (current `TopBar.tsx:173`) has the `LayoutDir` cycle (→ ↓ ← ↑) I just added.
  Keep the direction toggle and render it inside the ported **View** group next to the picker.
- **Remove the `.sa-legend` strip entirely** — subject areas move into the drawer (§C). This is
  what fixes the wrapping.

### B. Subject-area filter state + canvas hide (the engine)
- Add filter state in `App.tsx` (the owner of `doc`, `selectedId`, panel state): a
  `Set<string>` of **active subject-area ids** (`activeAreas`), empty = show all. Pass it +
  `areaByEntity`-derived membership down to `ModelCanvas`.
  - `areaByEntity` currently lives inside `ModelCanvas`; either lift a small
    `entityArea: Map<entityId, areaId>` derivation to `App` (so both the drawer counts and the
    canvas filter share one source) or pass `activeAreas` into `ModelCanvas` and filter there
    using the existing internal `areaByEntity`. Prefer passing `activeAreas` down and filtering
    inside `ModelCanvas` — keeps `areaByEntity` where it is.
- **Hide, not dim** (per decision). In `ModelCanvas.tsx`:
  - Heavy derivation effect (`:212-239`): set `hidden: activeAreas.size > 0 &&
    !activeAreas.has(areaByEntity.get(e.id)?.id ?? "")` on each node. React Flow auto-hides edges
    whose endpoint node is hidden, so no edge bookkeeping needed (confirmed: no manual edge-hide
    required beyond the existing endpoint-existence filter at `:241`).
  - **Mirror the same `hidden` rule in the lightweight hover effect** (`:355-374`) or the hover
    re-derivation will un-hide filtered nodes on mouseover — this is the same reason that effect
    already re-derives `searchDim`/`neighbourDim`. This is the one required consistency point.
  - After a filter change, **auto-fit to the visible set and relayout**: call `fitView`
    (existing) and re-run `layoutGraph` over the visible nodes so the focused domain uses the
    whole canvas. Reuse the `relayout` path (`:423`) which already fits.
- `onlyRenderVisibleElements` is already on (`:510`) and the search-fit reads node rects
  (`:399-421`); `hidden: true` keeps nodes in the array (unlike array-filtering), so fit/layout
  logic keeps working. Confirmed safe by exploration.

### C. Left slide-out drawer — `canvas/src/CanvasDrawer.tsx` (new)
- A new component mirroring the `.side-panel` convention (absolute card inside `.canvas-wrap`)
  but anchored **left** and **animated** (this introduces the first slide transition in the
  codebase — `transform: translateX(-100%)` → `0`, `transition: transform .18s ease`).
- **Hamburger toggle** (`IconMenu`) at the top-left of the toolbar's row-1 (or floating top-left
  of `.canvas-wrap`), using the same single-state toggle pattern as `panelTab`
  (`App.tsx:243`) — a new `drawerOpen` boolean in `App`.
- Drawer contents, top to bottom:
  1. **Subject areas** — one row per `doc.subject_areas`: a color chip (`saColors.get(sa.id)`),
     the name, an entity **count** (from `areaByEntity` / `sa.member_count`), and a checkbox.
     Clicking a row = focus that domain (set `activeAreas` to just it); checkboxes = multi-select
     add/remove. An "All / Clear" affordance resets `activeAreas` to empty (show all). The active
     rows get an `.active` accent. This replaces the dead legend with the filter the user asked
     for.
  2. **Layout** — move the layout picker + direction toggle here too (per decision: drawer holds
     both filter and layout controls), OR keep them in the toolbar View group and only surface a
     compact mirror. Recommend: keep the primary controls in the toolbar (discoverable) and put
     the domain filter as the drawer's main job; add layout controls to the drawer only if it
     reads cleanly. (Confirm during build; low risk either way.)
- Gate the drawer + hamburger behind `!minimal` (the VS Code preview pane hides the toolbar;
  `App.tsx:225`), since it's a toolbar-adjacent control.

### D. CSS
- New `.canvas-drawer` (left, absolute, `top/bottom:12px; left:12px; width:300px`,
  `var(--bg-raised)`, radius 14px, `z-index:9`, slide transition) + `.drawer-open` modifier.
- `.sa-row` (chip + name + count + checkbox, `.active` state), reusing tokens
  (`--accent`, `--accent-soft`, `--text-dim`, swatch style at `styles.css:49`).
- Port the `feat` toolbar CSS (§A). No new color tokens — palette is complete (`:root`).

### E. Verify + test
- `cd canvas && npm run build` (Node 20 via nvm) — typecheck + bundle. Rebuilt static bundle
  lands under `packages/server/src/mdl_server/static` (committed as today).
- Live-verify on the 46-entity / 5-domain fund fixture (served on :4811 via a temporary `fund`
  launch config, reverted before commit): (1) toolbar is two-row, grouped, no wrapping legend;
  (2) hamburger opens the left drawer with 5 domains, chips + counts; (3) clicking a domain hides
  the other 4 and fits to it; (4) multi-select shows the union; (5) Clear restores all; (6) hover
  over a PK/FK does NOT un-hide filtered nodes (the consistency point); (7) minimap reflects the
  hidden set; (8) coloring + clustering still hold.
- Add a canvas Vitest for the filter predicate if a clean unit boundary exists (e.g. a pure
  `visibleUnder(activeAreas, areaByEntity)` helper) — mirrors the Vitest already added on
  `canvas-ux`. Not required if the logic stays inline.

## Critical files
- `canvas/src/icons.tsx` — **new on canvas-ux** (port from `feat-semantic-compare-merge`), + a
  few new icons (menu, filter, direction).
- `canvas/src/TopBar.tsx` — restructure to two-row grouped layout; merge the ported toolbar with
  the existing direction-toggle LayoutPicker; add the hamburger; drop `.sa-legend`.
- `canvas/src/CanvasDrawer.tsx` — **new**: left slide-out with the subject-area filter (+ layout
  controls).
- `canvas/src/App.tsx` — `drawerOpen` + `activeAreas` state; wire drawer + pass filter to canvas;
  `!minimal` gating.
- `canvas/src/ModelCanvas.tsx` — apply the `hidden` filter in BOTH the heavy effect (`:212`) and
  the hover effect (`:355`); fit + relayout on filter change.
- `canvas/src/styles.css` — port toolbar CSS from `feat`; add `.canvas-drawer` / `.sa-row`;
  remove old `.legend`/single-row `.topbar`.

## Reuse (do not reinvent — from exploration)
- `icons.tsx` (17 SVG icons) + toolbar CSS (`.topbar-row/.btn-group/.grp-btn/.grp-tag`) —
  `git show feat-semantic-compare-merge:canvas/src/{icons.tsx,TopBar.tsx,styles.css}`.
- `areaByEntity` (`ModelCanvas.tsx:154`) — the entity→area filter key, already dual-sourced.
- `saColors` (`ModelCanvas.tsx:136`) — area id → color, for the drawer chips + minimap.
- `.side-panel` card pattern (`SidePanel.tsx` + `styles.css:373`) — mirror for the left drawer.
- LayoutPicker outside-click-to-close pattern (`TopBar.tsx:176-183`) — for any drawer popovers.
- `panelTab` single-state toggle (`App.tsx:243`) — the pattern for `drawerOpen`.
- `relayout` / `fitView` (`ModelCanvas.tsx:423`) — fit-to-visible after a filter change.

## Risk posture
- **Toolbar port is the biggest surface** but it's a known-good design already shipped on another
  branch — a port + merge with the direction toggle, not a fresh invention. Reconciles the two
  divergent toolbars (net simplification for the eventual `feat-semantic-compare-merge` merge:
  both branches converge on the same toolbar).
- **The one correctness trap** is the hover effect re-un-hiding filtered nodes; the plan calls it
  out explicitly and it has a clear precedent (search dimming is already mirrored there).
- **Hide via `hidden: true`** (not array-filter) keeps fit/layout/minimap working with no extra
  edge bookkeeping — the lowest-risk hide mechanism per exploration.
- The slide animation is the first in the codebase but is cosmetic and isolated to one new CSS
  block; if it misbehaves, the drawer still works as an instant show/hide like `.side-panel`.
- All additive/branch-local on `canvas-ux`; nothing touches the server/API.

## Verification
- `cd canvas && npm run build` (Node 20) — clean typecheck + bundle.
- Manual on the fund fixture (:4811), the 8 checks in §E — especially the hover-vs-hide
  consistency check and fit-to-domain.
- Revert the temporary `fund` launch config before committing; commit on `canvas-ux` (no push /
  PR without explicit go-ahead, per standing instruction).