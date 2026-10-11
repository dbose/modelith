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
 * Keep the vertical segment out of both endpoint cards' horizontal extents.
 *
 * - Forward edge (source left of target) with room between the cards: clamp to
 *   `[sourceRight + CARD_MARGIN, targetLeft - CARD_MARGIN]` so the run sits in
 *   the corridor between them.
 * - Otherwise (backward edge, or overlapping cards with no corridor): allow any
 *   x that is outside BOTH cards; if the proposed x landed inside a card, push
 *   it to that card's nearest outer edge ± CARD_MARGIN.
 *
 * `source`/`target` are the two endpoint cards. Returns a safe x.
 */
export function clampRouteX(x: number, source: CardX, target: CardX): number {
  const forward = source.right <= target.left;
  if (forward) {
    const lo = source.right + CARD_MARGIN;
    const hi = target.left - CARD_MARGIN;
    if (lo <= hi) {
      // There is a corridor between the cards — keep the run inside it.
      return Math.min(Math.max(x, lo), hi);
    }
    // Cards too close for a corridor; fall through to the outside-both rule.
  }
  // Backward edge, or no corridor: any x outside both cards is allowed.
  if (!insideCard(x, source) && !insideCard(x, target)) return x;
  // Landed inside a card — move to the nearest outer edge of whichever card
  // contains it (prefer the one it is actually inside).
  const offender = insideCard(x, source) ? source : target;
  const toLeft = x - (offender.left - CARD_MARGIN);
  const toRight = offender.right + CARD_MARGIN - x;
  const candidate =
    toRight <= toLeft ? offender.right + CARD_MARGIN : offender.left - CARD_MARGIN;
  // The nudged candidate might now sit inside the OTHER card; if so, bounce to
  // that card's far side instead.
  const other = offender === source ? target : source;
  if (insideCard(candidate, other)) {
    return candidate <= other.left ? other.left - CARD_MARGIN : other.right + CARD_MARGIN;
  }
  return candidate;
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
