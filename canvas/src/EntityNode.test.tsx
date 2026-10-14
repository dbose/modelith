import { describe, expect, it } from "vitest";
import { type NodeProps } from "reactflow";
import { entityNodePropsEqual, type EntityNodeData } from "./EntityNode";
import type { Entity } from "./types";

const ENTITY: Entity = {
  id: "01ENT00000000000000000000",
  name: "interventions",
  pattern: null,
  conceptual: null,
  attributes: [],
};

function data(over: Partial<EntityNodeData> = {}): EntityNodeData {
  return {
    entity: ENTITY,
    color: "#8a7d6a", // NO_SA grey
    dimmed: false,
    highlighted: false,
    showTypes: true,
    collapseDetail: false,
    expanded: false,
    ...over,
  };
}

// Only `data` and `selected` are read by the comparator; the rest of NodeProps is
// irrelevant to the memo decision, so a bare cast keeps the fixtures minimal.
function props(d: EntityNodeData, selected = false): NodeProps<EntityNodeData> {
  return { data: d, selected } as NodeProps<EntityNodeData>;
}

describe("entityNodePropsEqual (memo comparator)", () => {
  it("re-renders when the subject-area colour changes (the stale-recolor bug)", () => {
    const grey = props(data({ color: "#8a7d6a" }));
    const purple = props(data({ color: "#c4a3e0" }));
    // false === NOT equal === React re-renders -> the accent repaints in place
    expect(entityNodePropsEqual(grey, purple)).toBe(false);
  });

  it("skips re-render when nothing render-affecting changed (new data ref, same fields)", () => {
    // the derivation hands a fresh data object every refresh; identical fields must bail
    expect(entityNodePropsEqual(props(data()), props(data()))).toBe(true);
  });

  it("re-renders on dimmed / highlighted / expand / selected changes", () => {
    const base = data();
    expect(entityNodePropsEqual(props(base), props(data({ dimmed: true })))).toBe(false);
    expect(entityNodePropsEqual(props(base), props(data({ highlighted: true })))).toBe(false);
    expect(entityNodePropsEqual(props(base), props(data({ expanded: true })))).toBe(false);
    expect(entityNodePropsEqual(props(base, false), props(base, true))).toBe(false);
  });

  it("re-renders when the entity object identity changes (content edit)", () => {
    const edited: Entity = { ...ENTITY, name: "renamed" };
    expect(entityNodePropsEqual(props(data()), props(data({ entity: edited })))).toBe(false);
  });

  it("re-renders when a hover/highlight Set identity changes", () => {
    const base = data();
    const hovered = data({ highlightAttrs: new Set(["a"]) });
    expect(entityNodePropsEqual(props(base), props(hovered))).toBe(false);
  });
});
