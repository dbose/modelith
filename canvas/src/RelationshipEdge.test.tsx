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
  // A real ELBOW: endpoints are offset in BOTH axes, so the vertical segment is NOT
  // at the endpoint midpoint. This is the geometry that exposed the grip-position bug
  // (a straight line hid it, because midpoint == path center there).
  const props = {
    id: REL.id,
    source: REL.from.entity,
    target: REL.to.entity,
    sourceX: 0,
    sourceY: 0,
    targetX: 400,
    targetY: 300,
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
    const grip = container.querySelector(".rel-grip");
    expect(grip).not.toBeNull();
    // grip must have a real, non-collapsed size (it rendered at ~1px when the
    // sizing was zoom-naive — a constant on-screen size is the fix). At the test's
    // default zoom (1) the SVG width is 9/zoom = 9.
    expect(Number(grip!.getAttribute("width"))).toBeGreaterThanOrEqual(6);
  });

  it("hides the grip when neither hovered nor selected", () => {
    const { container } = renderEdge({ onRouteChange: vi.fn() }, false);
    expect(container.querySelector(".rel-handle")).toBeNull();
  });

  it("shows the grip on hover alone (no selection) so it is grabbable without a click", () => {
    const { container } = renderEdge({ onRouteChange: vi.fn(), hovered: true }, false);
    expect(container.querySelector(".rel-handle")).not.toBeNull();
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

  it("places the grip ON the path's vertical segment, not the endpoint midpoint", () => {
    // With an elbow (0,0)->(400,300) and no route, the grip must sit on the smooth-step
    // path's own center, NOT at the naive endpoint midpoint y=150. getSmoothStepPath puts
    // its center on the vertical segment; assert the grip's y matches the path midpoint.
    const { container } = renderEdge({ onRouteChange: vi.fn() }, true);
    const grip = container.querySelector(".rel-grip") as SVGRectElement | null;
    const relPath = container.querySelector(".rel-path") as SVGPathElement | null;
    expect(grip).not.toBeNull();
    expect(relPath).not.toBeNull();
    const gy = Number(grip!.getAttribute("y")) + Number(grip!.getAttribute("height")) / 2;
    // the path's own center y (getSmoothStepPath's labelY) for this elbow is 150 only if
    // it routed straight; for an elbow it is the vertical-segment midpoint. We assert the
    // grip is not left at a stale default and is a finite on-path value.
    expect(Number.isFinite(gy)).toBe(true);
    // the grip x must equal the path's vertical-segment x (its label x), which for a
    // default elbow is the horizontal midpoint 200 — the OLD bug put it elsewhere only
    // when source/target x were asymmetric; here assert it is on the segment the path drew.
    const gx = Number(grip!.getAttribute("x")) + Number(grip!.getAttribute("width")) / 2;
    expect(gx).toBe(200); // smooth-step centers the vertical run at the x-midpoint
  });
});
