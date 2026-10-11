import { fireEvent, render } from "@testing-library/react";
import { Position, ReactFlowProvider, type EdgeProps } from "reactflow";
import { describe, expect, it, vi } from "vitest";
import { RelationshipEdge, type RelationshipEdgeData } from "./RelationshipEdge";
import type { Relationship } from "./types";

const REL: Relationship = {
  id: "01REL00000000000000000000",
  name: "ae_for_subject",
  from: { entity: "01FROM0000000000000000000", attributes: [] },
  to: { entity: "01TO000000000000000000000", attributes: [] },
  cardinality: "many_to_one",
  identifying: false,
  optionality: "mandatory",
  verb_phrase: "experiences",
};

/** Render the edge component directly with EdgeProps, inside an svg + provider (so
 *  useReactFlow resolves). This exercises the component's own handle/pointer logic
 *  without the full graph machinery jsdom can't drive. */
function renderEdge(data: Partial<RelationshipEdgeData>, selected = false) {
  const props = {
    id: REL.id,
    source: REL.from.entity,
    target: REL.to.entity,
    sourceX: 0,
    sourceY: 0,
    targetX: 400,
    targetY: 0,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    selected,
    data: { relationship: REL, dimmed: false, ...data },
  } as unknown as EdgeProps<RelationshipEdgeData>;
  return render(
    <ReactFlowProvider>
      <svg>
        <RelationshipEdge {...props} />
      </svg>
    </ReactFlowProvider>,
  );
}

describe("RelationshipEdge drag handle", () => {
  it("shows the grip when selected and draggable", () => {
    const { container } = renderEdge({ onRouteChange: vi.fn() }, true);
    expect(container.querySelector(".rel-handle")).not.toBeNull();
    expect(container.querySelector(".rel-grip")).not.toBeNull();
  });

  it("hides the grip when not selected", () => {
    const { container } = renderEdge({ onRouteChange: vi.fn() }, false);
    expect(container.querySelector(".rel-handle")).toBeNull();
  });

  it("is not draggable (no handle) when onRouteChange is absent", () => {
    const { container } = renderEdge({}, true);
    expect(container.querySelector(".rel-handle")).toBeNull();
  });

  it("double-clicking the handle resets routing (onRouteChange with null)", () => {
    const onRouteChange = vi.fn();
    const { container } = renderEdge({ onRouteChange, routeX: 200 }, true);
    fireEvent.doubleClick(container.querySelector(".rel-handle")!);
    expect(onRouteChange).toHaveBeenCalledWith(REL.id, null);
  });

  it("a plain pointerdown without movement does not commit a route", () => {
    const onRouteChange = vi.fn();
    const { container } = renderEdge({ onRouteChange }, true);
    const handle = container.querySelector(".rel-handle")!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 100, clientY: 100, pointerId: 1 });
    expect(onRouteChange).not.toHaveBeenCalled();
  });

  it("positions the label at routeX when a route is set", () => {
    const { container } = renderEdge({ onRouteChange: vi.fn(), routeX: 250 }, true);
    const label = container.querySelector(".rel-label");
    expect(label?.getAttribute("x")).toBe("250");
  });
});
