# Canvas: draggable relationship edges and saved layout

Spec for Claude Code. Target repo: github.com/dbose/modelith, branch from `main` at or after `7dd170b`. Put this file at `specs/modelith-canvas-edge-drag-and-layout-spec.md`.

## Context

Author has imported a 7 MB erwin file, reversed it, and used the canvas with GitHub Copilot driving the MCP tools. Two gaps showed up against erwin's diagram editor (see the screen recording of erwin: selecting a relationship line shows a handle on its vertical segment, and dragging it slides the segment sideways while the ends stay glued to the entities).

1. A relationship edge cannot be dragged. Every edge is drawn by `getSmoothStepPath` in `canvas/src/RelationshipEdge.tsx`, so its elbow position is decided by the library and cannot be adjusted. On a dense model many edges run through the same corridor and the diagram is unreadable.
2. Layout is not saved. `ModelCanvas.tsx` keeps node positions only in React state (`setNodes`), and recomputes them with `layoutGraph` on load. Every reload throws away manual placement. Nothing in the server stores layout.

Layout is a visual concern only. It must never appear in the model, drift, validation, exports, diffs, proposals or dbt output.

## Decisions (already made, do not reopen)

- Layout lives in a sidecar file, `<model_dir>/.mdl/layout.json`, not in any model YAML and not in `mdl-project.yaml`.
- JSON, not YAML. Several code paths glob `*.yaml` or `*.y*ml` under the model dir (`commands.dir_fingerprint`, `status.py`, `app._dir_fingerprint`). A `.json` file stays out of all of them by construction.
- The file is git-ignored by default (see Part B). Sharing layout across a team is a later opt-in, not v1.
- Edge routing is orthogonal, same style as today. The only adjustable parameter per edge is the x position of its vertical segment (`routeX`). The canvas has ports on left and right sides only (`EntityNode.tsx`), so a vertical segment is the only free degree of freedom. No free-form waypoints in v1.
- The canvas component stays free of API calls. Persistence is injected through a prop, the same way `exec` is.

## Part A: draggable relationship edges

### Behaviour

1. Hovering or selecting an edge shows a drag handle at the midpoint of its vertical segment (a small grip, cursor `ew-resize`). The existing invisible 14 px hit path stays for hover and click.
2. Dragging the handle moves the vertical segment horizontally. Both endpoints stay anchored to their attribute handles. The path redraws live while dragging, at 60 fps on a 7 MB erwin model (about 60 to 100 entities, a few hundred edges).
3. Smart adjustment, applied while dragging and on release:
   - Snap to a 10 px grid. Holding Shift disables snapping.
   - Magnetise: if the segment is within 6 px of another edge's segment x or of a card's left or right edge plus margin, snap to it so lines can be aligned.
   - Never allow the segment inside a card's horizontal extent. If the drop lands inside a card, move it to the nearest outer edge plus a 24 px margin. For a forward edge (source left of target) with room, clamp to `[sourceX + 24, targetX - 24]`. For a backward edge (target left of source), allow any x outside both cards.
4. Double-clicking the handle (or the edge, when selected) resets that edge to automatic routing by removing its `routeX`.
5. A click still opens `RelEditModal` as today. A press on the handle that moves less than 3 px counts as a click. A real drag must not open the modal (call `stopPropagation` on the handle's pointer events and swallow the click that follows a drag).
6. When a node is dragged, its connected edges keep their stored `routeX`, re-clamped at render time. The stored value is not rewritten by a node move, so moving a card back restores the old routing.
7. The selected-edge label (`rel.verb_phrase || rel.name`) and the composite-key count label should sit at the vertical segment's x, not the geometric midpoint, once `routeX` is set.

### Implementation notes

- Add `routeX?: number` (flow coordinates) and `onRouteChange?: (relId: string, routeX: number | null) => void` to `RelationshipEdgeData`.
- Path: pass `centerX: routeX` to `getSmoothStepPath`. In `reactflow@11.11.4` (`@reactflow/core@11.11.4`, pinned in `canvas/package-lock.json`) `getSmoothStepPath` accepts `centerX` and `centerY`. Verify this in `node_modules/@reactflow/core` types before relying on it. If it behaves badly for backward edges, write a small `orthogonalPath(sourceX, sourceY, targetX, targetY, routeX, radius)` in `edgeRouting.ts` that draws horizontal stub, vertical run at `routeX`, horizontal stub, with the existing 10 px corner radius.
- Convert pointer position to flow coordinates with `useReactFlow().screenToFlowPosition` (available in 11.10+). Use pointer events with `setPointerCapture` and update on `requestAnimationFrame`.
- During a drag keep the in-progress `routeX` in local state inside the edge component. Do not call `setEdges` for every move; that rebuilds the whole array and will stutter on a large model (the hover overlay in `ModelCanvas.tsx` was already refactored for exactly this reason). Commit once on pointer release through `onRouteChange`.
- Put the geometry in a new pure module `canvas/src/edgeRouting.ts`: `snapRouteX`, `clampRouteX`, `magnetise`. Unit-test it without React.
- Card rectangles for clamping: node `position` from the store, width `NODE_WIDTH` (264) from `layout.ts`, height from `nodeHeight(entity)`. Expose them to the edge through edge data or `useStore`, whichever avoids re-rendering every edge on every node move.
- `onlyRenderVisibleElements` is on. An edge being dragged must stay mounted while off-screen endpoints scroll away. Check this.
- Readonly (`caps.canEdit === false`): dragging is still allowed. Routing is layout, not a model edit. It saves only if a layout store is provided (Part B).

## Part B: saved layout, visual only

### File and schema

Path: `<model_dir>/.mdl/layout.json`. `model_dir` is the directory `mdl serve -m <dir>` was started with (the same root `DecisionLedger` uses for `.mdl/decisions.yaml`).

```json
{
  "version": 1,
  "views": {
    "__all__": {
      "nodes": { "<entity ulid>": { "x": 120, "y": 340 } },
      "edges": { "<relationship ulid>": { "routeX": 480 } }
    },
    "sa:<subject area ulid>": { "nodes": {}, "edges": {} }
  }
}
```

- A view is one canvas arrangement. `__all__` is the unfiltered canvas. When the subject-area filter (`activeAreas`) is active, the view key is `sa:` plus the selected ids, sorted and joined with `+`. This matters because `ModelCanvas.tsx` already re-lays-out the visible subset when filtering, so one flat position map would be overwritten every time the filter changes.
- Numbers are rounded to integers. Keys are sorted and the file is written with 1-space indent and a trailing newline, so any diff is small and stable.
- Unknown top-level keys and a higher `version` are preserved on read-modify-write and never crash the server. An unreadable or corrupt file is treated as empty (log once), never fatal. Never raise from `GET /api/model` because of it.

### Server (`packages/server/src/mdl_server`)

New module `layout_store.py`, plus two routes in `app.py`.

- `GET /api/layout?view=<key>` returns `{ "version": 1, "view": key, "nodes": {...}, "edges": {...} }`, empty maps when absent. Always 200 for a readable model.
- `PUT /api/layout` takes `{ "view": key, "nodes": {...}, "edges": {...} }` and replaces that view. It writes atomically (temp file in the same directory, then `os.replace`). Body limit 1 MB. Reject non-finite numbers and keys longer than 64 characters with 422. On write, drop entries whose id is not an entity or relationship in the current model (`_load().model`), so the file does not accumulate dead ids.
- `DELETE /api/layout?view=<key>` removes one view. Omitting `view` removes the whole file (the "reset all layout" action).
- Under `read_only` (catalog browse, SME just looking) PUT and DELETE return 403. The canvas treats that as "session only" and does not show an error.
- Layout routes do not go through `commands.apply_command`, do not bump the model fingerprint, do not call `cache["repo"] = None`, and do not write an audit record. They are not model mutations.
- On first successful write, ensure the file is ignored by git: create `<model_dir>/.mdl/.gitignore` containing `layout.json` if that file is not already ignored. Do this only when `.mdl/` is inside a git work tree and never edit a user's existing ignore files beyond creating this one.

### Canvas (`canvas/src`)

- Add `LayoutStore` to `exec.ts` or a new `layoutStore.ts`: `{ load(view): Promise<ViewLayout>, save(view, layout): void, reset(view?): Promise<void> }`. Implement `httpLayoutStore` in `api.ts` using the existing `get` helper and the `API_BASE` prefix. Add an optional prop `layoutStore?: LayoutStore` to `ModelCanvasProps`. The architect app (`App.tsx`) passes it. The SME and catalog shells do not, so staged or previewed models never persist layout.
- Load: after the model is derived and nodes are created, call `layoutStore.load(viewKey)` and apply saved positions in place of the `layoutGraph` result for entities that have one. Entities without a saved position (new ones, such as those Copilot just created) keep their auto-layout position, then get a simple overlap nudge so they do not land on top of an existing card. Run auto-layout once for the whole view as today, then override. Fit the view only when no saved layout exists, so a restored layout is not re-zoomed away.
- Save: on `onNodeDragStop`, on edge route commit, and on the toolbar Re-layout action. Debounce 800 ms and send the whole view. Show nothing on success, a quiet toast on failure.
- Re-layout is destructive of manual work. Keep the previous positions in memory and offer Undo in a toast for the session. Add "Reset layout" to the toolbar menu, calling `reset(viewKey)`.
- A node drag today only updates React state in `onNodeDragStop`. Keep that behaviour and add the save call there.
- Viewport (pan and zoom) is not saved in v1.

### Isolation guarantees (must hold, with tests)

`layout.json` must be invisible to:

- `ModelRepo.load` and everything built on it: `mdl validate`, drift, `mdl diff`, emitters (dbt, ODCS, Pydantic, graph, RDF, OSI, MetricFlow).
- `/api/export` and `mdl export`.
- `commands.dir_fingerprint` and `app._dir_fingerprint` (they only glob `*.yaml`).
- The reverse writer prune (`writer.py` already excludes `.mdl/`; add a test so it stays that way).
- `git_api.py`: `status`, `diff`, propose, commit and the revert path that removes untracked files (around the `git status --porcelain` calls near lines 635, 793, 969, and the revert near line 901). If a user commits `layout.json` anyway, or ignores nothing, these paths must filter `.mdl/layout.json` out of "dirty" and must never delete it on revert.

## Files expected to change

- `canvas/src/RelationshipEdge.tsx`: handle, path, labels.
- `canvas/src/edgeRouting.ts`: new, pure geometry.
- `canvas/src/ModelCanvas.tsx`: layout load and save, route commit, overlap nudge, undo.
- `canvas/src/exec.ts` or `canvas/src/layoutStore.ts`, `canvas/src/api.ts`, `canvas/src/App.tsx`, `canvas/src/TopBar.tsx` (Reset layout).
- `canvas/src/types.ts`: layout payload types.
- `packages/server/src/mdl_server/layout_store.py` (new), `app.py` (routes), `git_api.py` (path filter).
- The built bundle under `packages/server/src/mdl_server/static/` is committed. Run `cd canvas && npm ci && npm run build` and commit the regenerated assets. Expect hashed filenames to change.
- `README.md` or docs: one short paragraph on saved layout and the git-ignore behaviour. Changelog line.

`vscode/` needs no change: the canvas webview loads the served page.

## Tests

Python (`packages/server/tests/test_layout_api.py`, new):

- GET with no file returns empty maps. PUT then GET round-trips. Output is key-sorted with integer values.
- PUT drops ids not present in the model. Corrupt existing file is treated as empty and overwritten on the next PUT.
- PUT and DELETE return 403 with `read_only=True`.
- Layout writes do not change `commands.dir_fingerprint`, and `GET /api/model` returns an identical document before and after a PUT.
- With `layout.json` present, `mdl validate`, drift and every emitter produce byte-identical output to the same model without it. Use the demo or the erwin test fixture.
- `/api/export` output is identical with and without the file.
- `git_api` status is not dirty because of `layout.json`, and revert does not delete it.
- The auto-created `.mdl/.gitignore` exists after the first PUT inside a git repo and is not created outside one.

Canvas (`vitest`):

- `edgeRouting.ts`: grid snap, Shift bypass, magnet within 6 px, clamp out of a card, forward and backward edge cases, reset returns auto.
- `RelationshipEdge`: renders handle on hover or select, calls `onRouteChange` once on release (not per move), a sub-3 px press does not call it, double-click calls it with `null`.
- Restore: saved positions override auto-layout, a new entity is placed without overlap, no `fitView` when a saved layout exists, a 403 from the store does not surface an error.

## Acceptance criteria

1. On the erwin-imported model, an edge can be dragged horizontally with both ends staying attached, snaps to the grid, and cannot be dropped inside a card.
2. Double-click returns the edge to automatic routing.
3. Reload the page or restart `mdl serve`: card positions and edge routes are restored exactly. Switching the subject-area filter and back restores each view's own arrangement.
4. New entities created through MCP (Copilot) appear without overlapping existing cards and do not disturb restored positions.
5. `git status` in the model repo stays clean after a layout change. `mdl validate`, drift, export and all emitters give identical results with and without `layout.json`.
6. Re-layout can be undone within the session. Reset layout removes the saved view.
7. `npm run build` (includes `tsc --noEmit`), `vitest run`, the server pytest suite, and `ruff` all pass. Dragging an edge on a 100-entity model stays smooth (no per-move rebuild of all nodes and edges).

## Out of scope for v1

- Free-form multi-bend waypoints or horizontal segment dragging for top and bottom ports.
- Saving viewport, selection, collapse state or the chosen layout mode.
- Sharing layout through git as a first-class feature (a `layout: shared` setting) and merge handling for it.
- Layout for the SME and catalog views.
- Importing erwin's own diagram coordinates. This is a good follow-up: the erwin file carries positions, which would make the first import look like the original diagram. Note it, do not build it here.

## Open questions

- Should `__all__` and per-area views be separate, as specified, or should filtered views reuse `__all__` positions and only fall back to auto-layout for entities missing from it? Separate views are simpler and match current behaviour; reuse looks more consistent. Default to separate unless it feels wrong in use.
- Team sharing: ignore by default (as specified) or commit by default? Defaulting to ignored avoids noisy diffs and any chance of layout entering review flows. Revisit after v1 if teams ask.