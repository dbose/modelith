import type { CommandResponse } from "./types";

/** The single mutation seam.
 *
 * Both apps route every model edit through a function of this shape, and neither
 * `Inspector` nor the modals know or care which one they were handed:
 *   - the architect canvas passes a DIRECT exec (POST /api/command, writes to disk);
 *   - the modeler app passes a STAGING exec (collects a PendingChange, previews).
 *
 * That is what makes ~1,200 lines of editing UI reusable across the two surfaces.
 */
export type Exec = (
  op: string,
  payload: Record<string, unknown>,
) => Promise<CommandResponse | undefined>;

/** What a surface is allowed to do.
 *
 * This replaces a single `readOnly` boolean, which conflated three separate
 * questions: whether the server will write, whether editing UI shows, and whether
 * a git-commit panel is offered. In the modeler app those come apart — the server
 * IS read-only for /api/command, yet the app can stage edits and propose them.
 */
export interface Capabilities {
  /** the edit gesture exists at all (false = pure viewer) */
  canEdit: boolean;
  /** where an edit goes: straight to disk, or into the staging tray */
  mode: "direct" | "staged";
  /** ops this surface may emit; undefined = all of them */
  allow?: ReadonlySet<string>;
  /** architect verdicts: promoting an alignment, ruling on a decision. An SME must
   *  not be able to propose an alignment AND accept it in one gesture. */
  canArbitrate: boolean;
  /** the git commit/discard panel. Mutually exclusive with proposing: propose
   *  refuses a dirty tree, so a surface that can commit can strand itself. */
  canCommit: boolean;
}

/** The architect canvas: writes straight to disk, subject to the server's flag. */
export function directCapabilities(readOnly: boolean): Capabilities {
  return {
    canEdit: !readOnly,
    mode: "direct",
    canArbitrate: !readOnly,
    canCommit: !readOnly,
  };
}

/** True when `op` is both permitted by the surface and allowed to be edited now. */
export function can(caps: Capabilities, op: string): boolean {
  if (!caps.canEdit) return false;
  return caps.allow ? caps.allow.has(op) : true;
}
