import { memo, useCallback, useRef, useState } from "react";
import { getSmoothStepPath, Position, useReactFlow, useStore, type EdgeProps } from "reactflow";
import type { Relationship } from "./types";

export interface RelationshipEdgeData {
  relationship: Relationship;
  dimmed: boolean;
  /** number of paired columns; >1 means a composite key drawn as one line (issue #5) */
  memberCount?: number;
  /** hover the line -> the canvas highlights both endpoint attribute rows */
  onHoverEdge?: (relId: string | null) => void;
  /** true while the mouse is over this edge — shows the drag handle so it is grabbable
   *  without a click (the editor now opens on double-click) */
  hovered?: boolean;
  /** the x of the vertical segment, in flow coords. Undefined = automatic routing. */
  routeX?: number;
  /** commit a new route (or null to reset to automatic). Absent = not draggable. */
  onRouteChange?: (relId: string, routeX: number | null) => void;
  /** endpoint card rectangles (flow coords), for clamping the route out of cards */
  sourceCard?: { left: number; right: number };
  targetCard?: { left: number; right: number };
}

// Keep in sync with edgeRouting.ts; imported there, inlined here to avoid a cycle
// of concerns (this file owns the pointer handling, edgeRouting owns the math).
import { adjustRouteX } from "./edgeRouting";

/** a press that moves less than this many px counts as a click, not a drag */
const CLICK_SLOP = 3;

/**
 * Crow's-foot notation (erwin-style), drawn as small glyph groups at each end of
 * a smooth-step path. Layout is left-to-right with ports on node sides, so edge
 * endpoints always meet a node horizontally; glyphs are mirrored per side.
 *
 * many side -> crow's foot (3 prongs); one side -> single bar.
 * optional  -> ring; mandatory -> extra bar.
 *
 * identifying (IDEF1X): the parent's key is part of the child's identity, drawn
 * as a SOLID line; non-identifying is DASHED. erwin draws the same distinction.
 *
 * The vertical segment is draggable: its x (`routeX`) is the only free degree of
 * freedom (ports are on the left/right of a card only). A handle appears on hover
 * or select; dragging slides the segment while both ends stay glued to their
 * attribute handles. See edgeRouting.ts for the snap/magnet/clamp geometry.
 */
export const RelationshipEdge = memo(function RelationshipEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}: EdgeProps<RelationshipEdgeData>) {
  const { screenToFlowPosition } = useReactFlow();
  // The canvas zoom. The handle is drawn in flow coordinates inside a scaled <g>,
  // so a fixed SVG size shrinks to nothing when zoomed out (a 3px grip became ~1px
  // on screen at the default 0.33x zoom). Divide sizes by the zoom so the grip keeps
  // a constant on-screen size and stays grabbable at any zoom.
  const zoom = useStore((s) => s.transform[2]);
  // while dragging, the live route is kept here so we do NOT rebuild all edges
  // per move; the committed value rides in data.routeX.
  const [dragX, setDragX] = useState<number | null>(null);
  const draggingRef = useRef(false);
  const movedRef = useRef(0);

  const onRouteChange = data?.onRouteChange;
  const draggable = !!onRouteChange;

  // effective vertical-segment x: live drag wins, else the committed route.
  const routeX = dragX ?? data?.routeX;

  const [path, autoLabelX, autoLabelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 10,
    ...(routeX != null ? { centerX: routeX } : {}),
  });

  const rel = data?.relationship;
  const dimmed = data?.dimmed ?? false;
  // from = many side (source), to = one side (target) per spec §2.3
  const cardinality = rel?.cardinality ?? "many_to_one";
  const optional = rel?.optionality === "optional";
  const identifying = rel?.identifying ?? false;

  const srcMany = cardinality === "many_to_one" || cardinality === "many_to_many";
  const tgtMany = cardinality === "one_to_many" || cardinality === "many_to_many";

  const cls = "rel-edge" + (selected ? " selected" : "") + (dimmed ? " dimmed" : "");
  const composite = (data?.memberCount ?? 0) > 1;
  const onHover = data?.onHoverEdge;

  // The handle sits on the MIDDLE of the actual vertical segment. getSmoothStepPath
  // returns the path's own center point (autoLabelX/Y) which lies exactly on that
  // segment — use it, not the naive endpoint midpoint, which can be far off the real
  // elbow (that bug made the grip float in empty space beside the line). Once a route
  // is set, the segment is at `routeX`, so the handle rides there.
  const handleX = routeX != null ? routeX : autoLabelX;
  const handleY = autoLabelY;
  const labelX = handleX;
  const labelY = handleY;

  const commit = useCallback(
    (x: number | null) => onRouteChange && rel && onRouteChange(rel.id, x),
    [onRouteChange, rel],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!draggable || e.button !== 0) return;
      e.stopPropagation();
      (e.target as Element).setPointerCapture?.(e.pointerId);
      draggingRef.current = true;
      movedRef.current = 0;
      const start = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const startX = start.x;
      let raf = 0;
      let latest = routeX ?? handleX;

      const onMove = (ev: PointerEvent) => {
        const p = screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
        movedRef.current = Math.max(movedRef.current, Math.abs(p.x - startX));
        latest = adjustRouteX(p.x, {
          shift: ev.shiftKey,
          source: data?.sourceCard ?? { left: sourceX, right: sourceX },
          target: data?.targetCard ?? { left: targetX, right: targetX },
        });
        if (!raf) {
          raf = requestAnimationFrame(() => {
            raf = 0;
            setDragX(latest);
          });
        }
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        if (raf) cancelAnimationFrame(raf);
        draggingRef.current = false;
        setDragX(null);
        if (movedRef.current >= CLICK_SLOP) commit(latest);
        // a sub-slop press is a click; swallow the click that follows a real drag
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [draggable, screenToFlowPosition, routeX, handleX, data, sourceX, targetX, commit],
  );

  // Swallow the click that fires right after a real drag, so it does not open the
  // edit modal (a true click — sub-slop press — is allowed through).
  const onClickCapture = useCallback(
    (e: React.MouseEvent) => {
      if (movedRef.current >= CLICK_SLOP) {
        e.stopPropagation();
        movedRef.current = 0;
      }
    },
    [],
  );

  const onDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!draggable) return;
      e.stopPropagation();
      commit(null); // reset to automatic routing
    },
    [draggable, commit],
  );

  return (
    // Hover handlers on the whole group so moving from the line onto the grip keeps the
    // edge "hovered" (and the handle mounted) instead of flickering off between children.
    <g
      className={cls}
      onMouseEnter={onHover ? () => onHover(rel!.id) : undefined}
      onMouseLeave={onHover ? () => onHover(null) : undefined}
    >
      <path
        id={id}
        className="rel-path"
        d={path}
        fill="none"
        strokeDasharray={identifying ? undefined : "6 4"}
      />
      {/* wider invisible path for easier hover/click — kept ~16px on screen at any zoom */}
      <path d={path} fill="none" strokeWidth={16 / zoom} stroke="transparent" />
      <EndGlyph x={sourceX} y={sourceY} side={sourcePosition} many={srcMany} optional={optional} />
      <EndGlyph x={targetX} y={targetY} side={targetPosition} many={tgtMany} optional={false} />
      {/* drag handle on the vertical segment — shown while hovered, selected, or mid-drag.
          Sizes are divided by the zoom so the grip keeps a constant ON-SCREEN size and
          stays visible + grabbable however far the canvas is zoomed out. */}
      {draggable && (data?.hovered || selected || dragX != null) && (
        <g
          className="rel-handle"
          style={{ cursor: "ew-resize" }}
          onPointerDown={onPointerDown}
          onClickCapture={onClickCapture}
          onDoubleClick={onDoubleClick}
        >
          {/* Grab target spanning the WHOLE vertical segment (not just a window at the
              midpoint), ~40px wide on screen, so you can grab the line anywhere along its
              run — you no longer have to aim at the grip itself. React Flow edge-interaction
              guidance: a generous (20-75px) hit area. */}
          <rect
            x={handleX - 20 / zoom}
            y={Math.min(sourceY, targetY)}
            width={40 / zoom}
            height={Math.max(Math.abs(targetY - sourceY), 24 / zoom)}
            fill="transparent"
          />
          {/* the visible grip: a rounded pill (~11x22px on screen) */}
          <rect
            className="rel-grip"
            x={handleX - 5.5 / zoom}
            y={handleY - 11 / zoom}
            width={11 / zoom}
            height={22 / zoom}
            rx={5 / zoom}
            strokeWidth={1.5 / zoom}
          />
          {/* three dots to read as a drag affordance */}
          <circle className="rel-grip-dot" cx={handleX} cy={handleY - 4.5 / zoom} r={1.1 / zoom} />
          <circle className="rel-grip-dot" cx={handleX} cy={handleY} r={1.1 / zoom} />
          <circle className="rel-grip-dot" cx={handleX} cy={handleY + 4.5 / zoom} r={1.1 / zoom} />
        </g>
      )}
      {(selected || composite) && rel && (
        <text className="rel-label" x={labelX} y={labelY - 8}>
          {/* erwin verb phrase reads "<parent> <verb> <child>"; fall back to the name */}
          {selected ? rel.verb_phrase || rel.name : ""}
          {composite ? `${selected ? " " : ""}(${data!.memberCount})` : ""}
        </text>
      )}
    </g>
  );
});

function EndGlyph({
  x,
  y,
  side,
  many,
  optional,
}: {
  x: number;
  y: number;
  side: Position;
  many: boolean;
  optional: boolean;
}) {
  // dir = +1 when the glyph extends to the right of the anchor (edge leaves a
  // node's right side or enters from the left); -1 mirrored.
  const dir = side === Position.Left ? -1 : 1;
  const s = 7; // glyph scale
  const foot = 12; // crow's foot depth

  const parts: JSX.Element[] = [];
  if (many) {
    // three prongs fanning from a point `foot` px away from the node edge
    parts.push(
      <path
        key="foot"
        className="rel-glyph"
        d={`M ${x + dir * foot} ${y} L ${x} ${y - s} M ${x + dir * foot} ${y} L ${x} ${y} M ${x + dir * foot} ${y} L ${x} ${y + s}`}
        fill="none"
      />,
    );
  } else {
    parts.push(
      <line
        key="one"
        className="rel-glyph"
        x1={x + dir * (foot - 3)}
        y1={y - s}
        x2={x + dir * (foot - 3)}
        y2={y + s}
      />,
    );
  }
  if (optional) {
    parts.push(
      <circle key="opt" className="rel-glyph ring" cx={x + dir * (foot + 6)} cy={y} r={4} />,
    );
  } else {
    parts.push(
      <line
        key="mand"
        className="rel-glyph"
        x1={x + dir * (foot + 5)}
        y1={y - s}
        x2={x + dir * (foot + 5)}
        y2={y + s}
      />,
    );
  }
  return <>{parts}</>;
}
