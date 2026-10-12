/**
 * Pure geometry for draggable relationship edges (no React, no reactflow).
 *
 * An edge's only adjustable degree of freedom is the x of its vertical segment
 * (`routeX`) — the canvas has ports on the left and right of a card only
 * (EntityNode.tsx), so there is nothing to drag but that one vertical run.
 *
 * All coordinates are flow coordinates. These helpers are unit-tested directly
 * (edgeRouting.test.ts); keep them free of side effects so they stay testable.
 */

/** Grid step for snapping the vertical segment. */
export const GRID = 10;
/** A segment within this many px of another segment / card edge magnetises to it. */
export const MAGNET = 6;
/** Keep the vertical segment at least this far outside a card's horizontal extent. */
export const CARD_MARGIN = 24;

/** A card's horizontal extent in flow coordinates. */
export interface CardX {
  left: number;
  right: number;
}

/** Snap `x` to the GRID, unless `shift` is held (free placement). */
export function snapRouteX(x: number, shift: boolean): number {
  if (shift) return x;
  return Math.round(x / GRID) * GRID;
}

/**
 * Magnetise `x` to the nearest candidate within MAGNET px: another edge's
 * segment x, or a card's left/right edge (± CARD_MARGIN so aligned lines sit
 * just off the card, matching the clamp). Returns the snapped x, or `x` if
 * nothing is close enough. The closest candidate wins.
 */
export function magnetise(x: number, segmentXs: number[], cards: CardX[]): number {
  const candidates: number[] = [...segmentXs];
  for (const c of cards) {
    candidates.push(c.left - CARD_MARGIN, c.right + CARD_MARGIN);
  }
  let best = x;
  let bestDist = MAGNET; // only snap when strictly within MAGNET
  for (const cand of candidates) {
    const d = Math.abs(cand - x);
    if (d < bestDist) {
      bestDist = d;
      best = cand;
    }
  }
  return best;
}

/** True when `x` falls within a card's horizontal extent (its forbidden zone). */
function insideCard(x: number, card: CardX): boolean {
  return x > card.left && x < card.right;
}

/**
 * Keep the vertical segment legal while allowing a VERTICAL entry into the target.
 *
 * Valid positions for the run:
 *  - in the corridor outside both cards (horizontal entry), OR
 *  - aligned OVER the target card's horizontal span (a top/bottom entry — the
 *    orthogonal router then drops the line onto the target's top/bottom border).
 * Never inside the SOURCE card (there is no top/bottom port to exit from there).
 *
 * So: if x is inside the source card, push it to the source's nearest outer edge.
 * If x is inside the target card, LEAVE IT (vertical entry). Otherwise x is free.
 *
 * `source`/`target` are the two endpoint cards. Returns a legal x.
 */
export function clampRouteX(x: number, source: CardX, target: CardX): number {
  // Over the target card -> allow (vertical entry into target).
  if (insideCard(x, target)) return x;
  // Inside the source card -> push to its nearest outer edge (+ margin).
  if (insideCard(x, source)) {
    const toLeft = x - (source.left - CARD_MARGIN);
    const toRight = source.right + CARD_MARGIN - x;
    const candidate =
      toRight <= toLeft ? source.right + CARD_MARGIN : source.left - CARD_MARGIN;
    // if that nudges onto the target card, that's fine (vertical entry); else return it
    return candidate;
  }
  // Outside both cards -> free.
  return x;
}

/**
 * Full adjustment applied while dragging and on release: snap to grid (unless
 * Shift), magnetise to nearby segments/cards, then clamp out of the endpoint
 * cards. Clamp runs last so the result is always legal.
 */
export function adjustRouteX(
  x: number,
  opts: {
    shift: boolean;
    source: CardX;
    target: CardX;
    otherSegmentXs?: number[];
    otherCards?: CardX[];
  },
): number {
  const snapped = snapRouteX(x, opts.shift);
  const magnetised = opts.shift
    ? snapped
    : magnetise(snapped, opts.otherSegmentXs ?? [], opts.otherCards ?? []);
  return clampRouteX(magnetised, opts.source, opts.target);
}

// --- orthogonal edge path with side-aware entry ------------------------------

/** A 2D point in flow coordinates. */
export interface Pt {
  x: number;
  y: number;
}

/** An endpoint's outward unit vector (direction the edge leaves the node). */
export interface Out {
  ox: number;
  oy: number;
}

/** The result of routing one edge: the SVG path plus each end's approach vector,
 * so the crow's-foot glyph can be drawn on the side the line actually crosses. */
export interface Routed {
  path: string;
  sourceOut: Out;
  targetOut: Out;
}

const R = 10; // corner radius

/** Round a corner from `prev`→`corner`→`next` with radius `r` (clamped to the shorter
 * leg), returning the SVG snippet from the entry tangent to the exit tangent. */
function corner(prev: Pt, c: Pt, next: Pt, r: number): string {
  const d1 = Math.hypot(c.x - prev.x, c.y - prev.y);
  const d2 = Math.hypot(next.x - c.x, next.y - c.y);
  const rr = Math.max(0, Math.min(r, d1 / 2, d2 / 2));
  const t1 = { x: c.x + ((prev.x - c.x) / (d1 || 1)) * rr, y: c.y + ((prev.y - c.y) / (d1 || 1)) * rr };
  const t2 = { x: c.x + ((next.x - c.x) / (d2 || 1)) * rr, y: c.y + ((next.y - c.y) / (d2 || 1)) * rr };
  return `L ${t1.x} ${t1.y} Q ${c.x} ${c.y} ${t2.x} ${t2.y} `;
}

/** Build an orthogonal SVG path through the given waypoints with rounded corners. */
function polyline(pts: Pt[]): string {
  if (pts.length < 2) return "";
  let d = `M ${pts[0].x} ${pts[0].y} `;
  for (let i = 1; i < pts.length - 1; i++) d += corner(pts[i - 1], pts[i], pts[i + 1], R);
  const last = pts[pts.length - 1];
  d += `L ${last.x} ${last.y}`;
  return d;
}

/**
 * Route an orthogonal edge whose vertical segment sits at `routeX`, choosing which SIDE
 * of each node the line enters so the crow's-foot lands on the border the line crosses.
 *
 * Ports are on the node's left/right (source exits right, target enters left by default).
 * Cases:
 *  - routeX in the corridor between the cards → classic 3-segment Z, both ends horizontal.
 *  - routeX aligned over/under the TARGET card → the line drops straight onto the target's
 *    top or bottom edge; target approach becomes vertical, so its glyph reorients.
 *  - likewise for the SOURCE when routeX is over/under it.
 *
 * `srcSide`/`tgtSide` are the horizontal port sides (+1 right, -1 left). `cards` give the
 * vertical extent (top/bottom) and horizontal extent so we can pick the entry border.
 */
export function orthogonalPath(
  s: Pt,
  t: Pt,
  routeX: number,
  srcSide: 1 | -1,
  tgtSide: 1 | -1,
  srcCard: { left: number; right: number; top: number; bottom: number },
  tgtCard: { left: number; right: number; top: number; bottom: number },
): Routed {
  const STUB = 12; // minimum horizontal stub out of a port before turning

  // Does routeX fall within a card's horizontal span (i.e. the vertical run is over/under it)?
  const overTarget = routeX > tgtCard.left && routeX < tgtCard.right;
  const overSource = routeX > srcCard.left && routeX < srcCard.right;

  const pts: Pt[] = [];
  let sourceOut: Out = { ox: srcSide, oy: 0 };
  let targetOut: Out = { ox: tgtSide, oy: 0 };

  // --- SOURCE exit: horizontal stub out of the side port, then vertical at routeX.
  // (When routeX is over the source, the stub direction is still the port side; the run
  // then rises/falls at routeX. A vertical source exit would need a top/bottom port, which
  // we do not have, so the source keeps a horizontal stub.) ---
  if (overSource) {
    pts.push({ x: s.x, y: s.y }, { x: s.x + srcSide * STUB, y: s.y }, { x: routeX, y: s.y });
  } else {
    pts.push({ x: s.x, y: s.y }, { x: routeX, y: s.y });
  }

  // --- TARGET entry: pick the side the route actually approaches from, so the line
  //     never crosses the card body to reach a far port. ---
  if (overTarget) {
    // Vertical: drop straight onto the nearest horizontal border at routeX.
    const fromAbove = s.y <= t.y;
    pts.push({ x: routeX, y: fromAbove ? tgtCard.top : tgtCard.bottom });
    targetOut = { ox: 0, oy: fromAbove ? -1 : 1 };
  } else if (routeX >= tgtCard.right) {
    // Route is to the RIGHT of the target -> enter the RIGHT port (glyph points right).
    pts.push({ x: routeX, y: t.y }, { x: tgtCard.right, y: t.y });
    targetOut = { ox: 1, oy: 0 };
  } else {
    // Route is to the LEFT -> enter the LEFT port (glyph points left).
    pts.push({ x: routeX, y: t.y }, { x: tgtCard.left, y: t.y });
    targetOut = { ox: -1, oy: 0 };
  }

  return { path: polyline(pts), sourceOut, targetOut };
}

// --- draggable endpoint anchors (slide the endpoint along an entity's border) ---

/** A card's full rectangle in flow coordinates. */
export interface Rect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** Which border a dragged endpoint snaps to, and where on it. The endpoint slides
 * freely along a border; `side` is the nearest border to the drop point, `x`/`y` the
 * point on it (`x` only matters on top/bottom). Purely visual — the model still links
 * the real attribute rows. Matches the stored EdgeAnchor wire type. */
export interface Anchor {
  side: "left" | "right" | "top" | "bottom";
  x?: number;
  y: number;
}

const ANCHOR_INSET = 8; // keep the anchor this far from a corner

/**
 * Resolve a free drag point `(px, py)` to an anchor ON the card's border: pick the
 * nearest side, then clamp the point to that side's span (minus a small corner inset).
 * This lets the endpoint slide smoothly along whichever edge the cursor is closest to.
 */
export function anchorOnBorder(px: number, py: number, card: Rect): Anchor {
  const dLeft = Math.abs(px - card.left);
  const dRight = Math.abs(px - card.right);
  const dTop = Math.abs(py - card.top);
  const dBottom = Math.abs(py - card.bottom);
  const min = Math.min(dLeft, dRight, dTop, dBottom);
  const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
  if (min === dLeft) {
    return { side: "left", x: card.left, y: clamp(py, card.top + ANCHOR_INSET, card.bottom - ANCHOR_INSET) };
  }
  if (min === dRight) {
    return { side: "right", x: card.right, y: clamp(py, card.top + ANCHOR_INSET, card.bottom - ANCHOR_INSET) };
  }
  if (min === dTop) {
    return { side: "top", x: clamp(px, card.left + ANCHOR_INSET, card.right - ANCHOR_INSET), y: card.top };
  }
  return { side: "bottom", x: clamp(px, card.left + ANCHOR_INSET, card.right - ANCHOR_INSET), y: card.bottom };
}

/** Re-resolve a stored anchor against the card's CURRENT rect (the card may have moved
 * or resized since the anchor was saved), keeping it on the same side at the same
 * relative position. Returns the point + outward unit vector for the glyph. */
export function resolveAnchor(a: Anchor, card: Rect): { x: number; y: number; out: Out } {
  const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
  switch (a.side) {
    case "left":
      return { x: card.left, y: clamp(a.y, card.top, card.bottom), out: { ox: -1, oy: 0 } };
    case "right":
      return { x: card.right, y: clamp(a.y, card.top, card.bottom), out: { ox: 1, oy: 0 } };
    case "top":
      return { x: clamp(a.x ?? (card.left + card.right) / 2, card.left, card.right), y: card.top, out: { ox: 0, oy: -1 } };
    default:
      return { x: clamp(a.x ?? (card.left + card.right) / 2, card.left, card.right), y: card.bottom, out: { ox: 0, oy: 1 } };
  }
}
