import type { ViewLayout } from "./types";

/**
 * The layout-persistence seam, injected the same way `exec` is (see exec.ts).
 *
 * The canvas never calls the API directly: it is handed a `LayoutStore` and does
 * not know or care whether it is HTTP-backed (the architect app) or absent (the
 * SME / catalog shells, which never persist a staged or previewed model's layout).
 *
 * Layout is VISUAL only. A `save` that fails (e.g. 403 under read-only) must be a
 * quiet no-op from the canvas's point of view — session-only layout, never an error.
 */
export interface LayoutStore {
  /** Load one view's saved arrangement; empty maps when absent. */
  load(view: string): Promise<ViewLayout>;
  /** Persist one whole view. Fire-and-forget; resolves/rejects are the store's concern. */
  save(view: string, layout: ViewLayout): Promise<void>;
  /** Remove one view, or (no arg) all saved layout. */
  reset(view?: string): Promise<void>;
}

/** An empty arrangement — what `load` returns when nothing is saved. */
export const EMPTY_LAYOUT: ViewLayout = { nodes: {}, edges: {} };
