import { describe, expect, it } from "vitest";
import {
  CARD_MARGIN,
  GRID,
  MAGNET,
  adjustRouteX,
  clampRouteX,
  magnetise,
  orthogonalPath,
  snapRouteX,
  type CardX,
} from "./edgeRouting";

describe("snapRouteX", () => {
  it("snaps to the grid", () => {
    expect(snapRouteX(483, false)).toBe(480);
    expect(snapRouteX(486, false)).toBe(490);
    expect(snapRouteX(485, false)).toBe(490); // round half up
  });
  it("is a no-op when shift is held", () => {
    expect(snapRouteX(483, true)).toBe(483);
    expect(snapRouteX(486.4, true)).toBe(486.4);
  });
  it("grid is 10", () => {
    expect(GRID).toBe(10);
  });
});

describe("magnetise", () => {
  it("snaps to another segment within MAGNET px", () => {
    expect(magnetise(484, [480], [])).toBe(480); // 4px away -> snap
  });
  it("does not snap when outside MAGNET px", () => {
    expect(magnetise(487, [480], [])).toBe(487); // 7px away -> keep
  });
  it("snaps to a card edge plus margin", () => {
    const card: CardX = { left: 100, right: 364 };
    // right edge + margin = 388; x=390 is 2px away
    expect(magnetise(390, [], [card])).toBe(364 + CARD_MARGIN);
    // left edge - margin = 76; x=74 is 2px away
    expect(magnetise(74, [], [card])).toBe(100 - CARD_MARGIN);
  });
  it("the closest candidate wins", () => {
    expect(magnetise(483, [480, 485], [])).toBe(485); // 2px vs 3px
  });
  it("MAGNET is 6", () => {
    expect(MAGNET).toBe(6);
  });
});

describe("clampRouteX", () => {
  // forward: source entirely left of target with a wide corridor
  const sFwd: CardX = { left: 0, right: 264 };
  const tFwd: CardX = { left: 600, right: 864 };

  it("leaves a run that is in the corridor or outside both cards", () => {
    expect(clampRouteX(400, sFwd, tFwd)).toBe(400); // corridor -> free
    expect(clampRouteX(-50, sFwd, tFwd)).toBe(-50); // left of both -> free
    expect(clampRouteX(950, sFwd, tFwd)).toBe(950); // right of both -> free
  });

  it("ALLOWS a run aligned over the TARGET card (vertical entry)", () => {
    // 700 is inside target(600..864) -> allowed, so the orthogonal router can enter
    // the target from top/bottom.
    expect(clampRouteX(700, sFwd, tFwd)).toBe(700);
  });

  it("pushes a run out of the SOURCE card (no top/bottom port to exit)", () => {
    // 100 is inside source(0..264); nearer left edge (100 vs 188) -> 0-24
    expect(clampRouteX(100, sFwd, tFwd)).toBe(0 - CARD_MARGIN);
    // 200 is inside source; nearer right edge (88 vs 224) -> 264+24
    expect(clampRouteX(200, sFwd, tFwd)).toBe(264 + CARD_MARGIN);
  });
});

describe("adjustRouteX", () => {
  const source: CardX = { left: 0, right: 264 };
  const target: CardX = { left: 600, right: 864 };

  it("snaps, magnetises, then clamps", () => {
    // 403 -> snap 400 -> no magnet -> inside corridor -> 400
    expect(adjustRouteX(403, { shift: false, source, target })).toBe(400);
  });

  it("clamp still applies even with shift (free placement, but legal)", () => {
    // shift bypasses snap+magnet but a drop inside the source card is still illegal;
    // 100 is inside source(0..264), nearer its left edge -> pushed to 0 - margin
    expect(adjustRouteX(100, { shift: true, source, target })).toBe(0 - CARD_MARGIN);
  });

  it("magnetises to a sibling segment when close", () => {
    // 483 -> snap 480 -> sibling at 490 is 10px away, no magnet; sibling at 482 snaps
    expect(adjustRouteX(483, { shift: false, source, target, otherSegmentXs: [478] })).toBe(
      478,
    );
  });
});

describe("orthogonalPath", () => {
  const src = { left: 0, right: 264, top: 0, bottom: 100 };
  const tgt = { left: 600, right: 864, top: 500, bottom: 620 };
  const s = { x: 264, y: 50 }; // source right port
  const t = { x: 600, y: 560 }; // target left port

  it("routes horizontally at both ends when routeX is in the corridor", () => {
    const r = orthogonalPath(s, t, 430, 1, -1, src, tgt);
    expect(r.sourceOut).toEqual({ ox: 1, oy: 0 });
    expect(r.targetOut).toEqual({ ox: -1, oy: 0 }); // enters target horizontally
    expect(r.path).toContain("M 264 50");
  });

  it("enters the TARGET from the TOP when routeX is over it and source is above", () => {
    // routeX = 700 is within target span (600..864); source(y=50) is above target(top=500)
    const r = orthogonalPath(s, t, 700, 1, -1, src, tgt);
    expect(r.targetOut).toEqual({ ox: 0, oy: -1 }); // vertical, pointing up (top entry)
    // the path's last point is on the target's TOP border at routeX
    expect(r.path.trimEnd().endsWith("L 700 500")).toBe(true);
  });

  it("enters the TARGET from the BOTTOM when the source sits below it", () => {
    const sBelow = { x: 264, y: 900 };
    const r = orthogonalPath(sBelow, t, 700, 1, -1, src, tgt);
    expect(r.targetOut).toEqual({ ox: 0, oy: 1 }); // pointing down (bottom entry)
    expect(r.path.trimEnd().endsWith("L 700 620")).toBe(true);
  });

  it("enters the TARGET from the RIGHT port when routeX is to its right", () => {
    // routeX = 950 is to the right of the target(right=864) -> enter the right side, not
    // loop back across the card to the left port.
    const r = orthogonalPath(s, t, 950, 1, -1, src, tgt);
    expect(r.targetOut).toEqual({ ox: 1, oy: 0 }); // points right
    expect(r.path.trimEnd().endsWith("L 864 560")).toBe(true); // ends at the RIGHT border
  });

  it("enters the TARGET from the LEFT port when routeX is to its left", () => {
    const r = orthogonalPath(s, t, 430, 1, -1, src, tgt);
    expect(r.targetOut).toEqual({ ox: -1, oy: 0 });
    expect(r.path.trimEnd().endsWith("L 600 560")).toBe(true); // ends at the LEFT border
  });
})
