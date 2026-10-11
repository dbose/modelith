import { describe, expect, it } from "vitest";
import {
  CARD_MARGIN,
  GRID,
  MAGNET,
  adjustRouteX,
  clampRouteX,
  magnetise,
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

  it("forward edge keeps the run in the corridor", () => {
    // corridor is [264+24, 600-24] = [288, 576]
    expect(clampRouteX(400, sFwd, tFwd)).toBe(400); // already inside
    expect(clampRouteX(100, sFwd, tFwd)).toBe(288); // inside source -> clamp lo
    expect(clampRouteX(700, sFwd, tFwd)).toBe(576); // inside target -> clamp hi
    expect(clampRouteX(280, sFwd, tFwd)).toBe(288); // in source margin -> lo
  });

  it("backward edge allows any x outside both cards", () => {
    // target left of source
    const s: CardX = { left: 600, right: 864 };
    const t: CardX = { left: 0, right: 264 };
    expect(clampRouteX(950, s, t)).toBe(950); // right of both -> allowed
    expect(clampRouteX(-50, s, t)).toBe(-50); // left of both -> allowed
    expect(clampRouteX(400, s, t)).toBe(400); // between the cards -> allowed
  });

  it("backward edge pushes an x that landed inside a card to its outer edge", () => {
    const s: CardX = { left: 600, right: 864 };
    const t: CardX = { left: 0, right: 264 };
    // x=800 is inside source(600..864); nearer right edge (64 vs 200) -> 864+24
    expect(clampRouteX(800, s, t)).toBe(864 + CARD_MARGIN);
    // x=620 is inside source; nearer left edge (20 vs 244) -> 600-24
    expect(clampRouteX(620, s, t)).toBe(600 - CARD_MARGIN);
    // x=100 is inside target(0..264); nearer right edge (164 vs 100... left) -> 0-24
    expect(clampRouteX(100, s, t)).toBe(0 - CARD_MARGIN);
  });

  it("no corridor (cards too close) falls back to outside-both", () => {
    const s: CardX = { left: 0, right: 264 };
    const t: CardX = { left: 280, right: 544 }; // gap 16 < 2*margin
    // x inside the tiny gap but inside neither card is allowed
    expect(clampRouteX(272, s, t)).toBe(272);
    // x inside source -> nearest outer edge
    expect(clampRouteX(10, s, t)).toBe(0 - CARD_MARGIN);
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
    // shift bypasses snap+magnet but a drop inside the source card is still illegal
    expect(adjustRouteX(100, { shift: true, source, target })).toBe(288);
  });

  it("magnetises to a sibling segment when close", () => {
    // 483 -> snap 480 -> sibling at 490 is 10px away, no magnet; sibling at 482 snaps
    expect(adjustRouteX(483, { shift: false, source, target, otherSegmentXs: [478] })).toBe(
      478,
    );
  });
});
