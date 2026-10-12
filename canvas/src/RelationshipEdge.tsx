import { memo, useCallback, useRef, useState } from "react";
import { getSmoothStepPath, Position, useReactFlow, useStore, type EdgeProps } from "reactflow";
import { type Anchor, adjustRouteX, anchorOnBorder, orthogonalPath, resolveAnchor } from "./edgeRouting";
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
  /** true when this edge was clicked (selected) — keeps its handles visible so the user
   *  does not have to hover the thin line to work it */
  edgeSelected?: boolean;
  /** the x of the vertical segment, in flow coords. Undefined = automatic routing. */
  routeX?: number;
  /** commit a new route (or null to reset to automatic). Absent = not draggable. */
  onRouteChange?: (relId: string, routeX: number | null) => void;
  /** endpoint card rectangles (flow coords), for clamping the route out of cards and for
   *  side-aware orthogonal routing (top/bottom entry) */
  sourceCard?: { left: number; right: number; top: number; bottom: number };
  targetCard?: { left: number; right: number; top: number; bottom: number };
  /** endpoints dragged ALONG the entity's border (visual only; the data still links the
   *  real attribute rows). Undefined = the natural attribute-row port. */
  sourceAnchor?: Anchor;
  targetAnchor?: Anchor;
  /** commit a dragged endpoint anchor (or null to reset to the attribute-row port). */
  onAnchorChange?: (relId: string, end: "source" | "target", anchor: Anchor | null) => void;
}


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
  source,
  target,
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
  // LIVE source/target card rects, read from the store so they track a node as it moves
  // (the data.sourceCard/targetCard snapshot goes stale on a node drag, which left an
  // anchored endpoint floating at the card's OLD position). A node's rect is its absolute
  // position + measured size; fall back to the data snapshot if the store has no size yet.
  const liveSrcRect = useStore((s) => rectOf(s.nodeInternals.get(source)), rectEq);
  const liveTgtRect = useStore((s) => rectOf(s.nodeInternals.get(target)), rectEq);
  // while dragging, the live route is kept here so we do NOT rebuild all edges
  // per move; the committed value rides in data.routeX.
  const [dragX, setDragX] = useState<number | null>(null);
  // live endpoint-anchor drag (per end), kept local so a drag does not rebuild all edges.
  const [dragAnchor, setDragAnchor] = useState<{ end: "source" | "target"; a: Anchor } | null>(null);
  const draggingRef = useRef(false);
  const movedRef = useRef(0);

  const onRouteChange = data?.onRouteChange;
  const draggable = !!onRouteChange;

  // effective vertical-segment x: live drag wins, else the committed route.
  const routeX = dragX ?? data?.routeX;

  // Effective endpoints: a dragged/stored anchor overrides the attribute-row port, so the
  // line visually meets the box where the anchor sits (the data still links the real rows).
  // Prefer the LIVE store rect (tracks node moves); fall back to the data snapshot.
  const srcCardR = liveSrcRect ?? data?.sourceCard;
  const tgtCardR = liveTgtRect ?? data?.targetCard;
  const liveSrcAnchor =
    dragAnchor?.end === "source" ? dragAnchor.a : data?.sourceAnchor;
  const liveTgtAnchor =
    dragAnchor?.end === "target" ? dragAnchor.a : data?.targetAnchor;
  const srcPt =
    liveSrcAnchor && srcCardR
      ? resolveAnchor(liveSrcAnchor, srcCardR)
      : { x: sourceX, y: sourceY, out: null as null | { ox: number; oy: number } };
  const tgtPt =
    liveTgtAnchor && tgtCardR
      ? resolveAnchor(liveTgtAnchor, tgtCardR)
      : { x: targetX, y: targetY, out: null as null | { ox: number; oy: number } };
  const eSourceX = srcPt.x;
  const eSourceY = srcPt.y;
  const eTargetX = tgtPt.x;
  const eTargetY = tgtPt.y;
  // when an endpoint is anchored, its port side follows the anchor's side.
  const eSourcePos =
    liveSrcAnchor?.side === "right" ? Position.Right
    : liveSrcAnchor?.side === "left" ? Position.Left
    : sourcePosition;
  const eTargetPos =
    liveTgtAnchor?.side === "right" ? Position.Right
    : liveTgtAnchor?.side === "left" ? Position.Left
    : targetPosition;

  const [smoothPath, autoLabelX, autoLabelY] = getSmoothStepPath({
    sourceX: eSourceX,
    sourceY: eSourceY,
    sourcePosition: eSourcePos,
    targetX: eTargetX,
    targetY: eTargetY,
    targetPosition: eTargetPos,
    borderRadius: 10,
    ...(routeX != null ? { centerX: routeX } : {}),
  });

  // When a route is set AND we know the card rectangles, use the side-aware orthogonal
  // router so a run dragged over a node enters it from the top/bottom (and the crow's-foot
  // follows). Otherwise keep the smooth-step path (the clean default / auto routing).
  const sCard = srcCardR;
  const tCard = tgtCardR;
  const routed =
    routeX != null && sCard && tCard
      ? orthogonalPath(
          { x: eSourceX, y: eSourceY },
          { x: eTargetX, y: eTargetY },
          routeX,
          eSourcePos === Position.Left ? -1 : 1,
          eTargetPos === Position.Left ? -1 : 1,
          sCard,
          tCard,
        )
      : null;
  const path = routed ? routed.path : smoothPath;

  const rel = data?.relationship;
  const dimmed = data?.dimmed ?? false;
  // from = many side (source), to = one side (target) per spec §2.3
  const cardinality = rel?.cardinality ?? "many_to_one";
  const optional = rel?.optionality === "optional";
  const identifying = rel?.identifying ?? false;

  const srcMany = cardinality === "many_to_one" || cardinality === "many_to_many";
  const tgtMany = cardinality === "one_to_many" || cardinality === "many_to_many";

  const cls =
    "rel-edge" +
    (selected || data?.edgeSelected ? " selected" : "") +
    (dimmed ? " dimmed" : "");
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

  // Crow's-foot orientation + position follow the edge's ACTUAL approach at each end.
  // The router reports the out-vector; the glyph ANCHOR must move to the border the line
  // actually crosses — the right port when entering from the right, the top/bottom border
  // when entering vertically — so the marker is never left mid-card.
  // out-vectors + glyph anchor points. Priority: the orthogonal router (when a route is
  // set) > a dragged endpoint anchor > the natural attribute-row port.
  const srcOut = routed
    ? routed.sourceOut
    : srcPt.out ?? { ox: eSourcePos === Position.Left ? -1 : 1, oy: 0 };
  const tgtOut = routed
    ? routed.targetOut
    : tgtPt.out ?? { ox: eTargetPos === Position.Left ? -1 : 1, oy: 0 };
  const srcGlyph = { x: eSourceX, y: eSourceY };
  const tgtGlyph =
    routed && tCard
      ? tgtOut.oy !== 0
        ? { x: routeX!, y: tgtOut.oy < 0 ? tCard.top : tCard.bottom } // top/bottom entry
        : { x: tgtOut.ox > 0 ? tCard.right : tCard.left, y: eTargetY } // right/left port
      : { x: eTargetX, y: eTargetY };

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

  const onAnchorChange = data?.onAnchorChange;
  // Drag an endpoint along its entity's border. `end` picks source/target; the drop point
  // resolves to the nearest border via anchorOnBorder. Commits on release; double-click a
  // handle resets that end to its attribute-row port.
  const onEndpointDown = useCallback(
    (end: "source" | "target") => (e: React.PointerEvent) => {
      const card = end === "source" ? srcCardR : tgtCardR;
      if (!onAnchorChange || !rel || !card || e.button !== 0) return;
      e.stopPropagation();
      (e.target as Element).setPointerCapture?.(e.pointerId);
      movedRef.current = 0;
      const start = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      let raf = 0;
      let latest: Anchor | null = null;
      const onMove = (ev: PointerEvent) => {
        const p = screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
        movedRef.current = Math.max(movedRef.current, Math.hypot(p.x - start.x, p.y - start.y));
        latest = anchorOnBorder(p.x, p.y, card);
        if (!raf) {
          raf = requestAnimationFrame(() => {
            raf = 0;
            setDragAnchor({ end, a: latest! });
          });
        }
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        if (raf) cancelAnimationFrame(raf);
        setDragAnchor(null);
        if (movedRef.current >= CLICK_SLOP && latest) onAnchorChange(rel.id, end, latest);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [onAnchorChange, rel, srcCardR, tgtCardR, screenToFlowPosition],
  );
  const onEndpointDouble = useCallback(
    (end: "source" | "target") => (e: React.MouseEvent) => {
      if (!onAnchorChange || !rel) return;
      e.stopPropagation();
      onAnchorChange(rel.id, end, null); // reset to the attribute-row port
    },
    [onAnchorChange, rel],
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
      <EndGlyph x={srcGlyph.x} y={srcGlyph.y} out={srcOut} many={srcMany} optional={optional} />
      <EndGlyph x={tgtGlyph.x} y={tgtGlyph.y} out={tgtOut} many={tgtMany} optional={false} />
      {/* endpoint handles — drag an end ALONG its entity's border. Shown on hover/select. */}
      {!!onAnchorChange && (data?.hovered || data?.edgeSelected || selected || dragAnchor != null) && (
        <>
          <EndpointHandle
            x={srcGlyph.x}
            y={srcGlyph.y}
            zoom={zoom}
            onPointerDown={onEndpointDown("source")}
            onDoubleClick={onEndpointDouble("source")}
          />
          <EndpointHandle
            x={tgtGlyph.x}
            y={tgtGlyph.y}
            zoom={zoom}
            onPointerDown={onEndpointDown("target")}
            onDoubleClick={onEndpointDouble("target")}
          />
        </>
      )}
      {/* drag handle on the vertical segment — shown while hovered, selected, or mid-drag.
          Sizes are divided by the zoom so the grip keeps a constant ON-SCREEN size and
          stays visible + grabbable however far the canvas is zoomed out. */}
      {draggable && (data?.hovered || data?.edgeSelected || selected || dragX != null) && (
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
            y={Math.min(eSourceY, eTargetY)}
            width={40 / zoom}
            height={Math.max(Math.abs(eTargetY - eSourceY), 24 / zoom)}
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

type Rect4 = { left: number; right: number; top: number; bottom: number } | null;
/** Equality for the useStore rect selector, so the edge re-renders only when its node's
 * rect actually changes (not on every unrelated store tick). */
function rectEq(a: Rect4, b: Rect4): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.left === b.left && a.right === b.right && a.top === b.top && a.bottom === b.bottom;
}

/** The live rect of a React Flow node from the store (absolute position + measured size),
 * or null when it is not yet measured. Used so an anchored endpoint tracks the node as it
 * moves, instead of resolving against a stale snapshot. */
function rectOf(
  n: { positionAbsolute?: { x: number; y: number }; width?: number | null; height?: number | null } | undefined,
): { left: number; right: number; top: number; bottom: number } | null {
  if (!n?.positionAbsolute || n.width == null || n.height == null) return null;
  const { x, y } = n.positionAbsolute;
  return { left: x, right: x + n.width, top: y, bottom: y + n.height };
}

/** A small circular grip at an edge endpoint; drag it to slide the endpoint along the
 * entity's border. Zoom-invariant, like the route grip. */
function EndpointHandle({
  x,
  y,
  zoom,
  onPointerDown,
  onDoubleClick,
}: {
  x: number;
  y: number;
  zoom: number;
  onPointerDown: (e: React.PointerEvent) => void;
  onDoubleClick: (e: React.MouseEvent) => void;
}) {
  return (
    <g
      className="rel-endpoint"
      style={{ cursor: "move" }}
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
    >
      {/* fat transparent grab target (~20px on screen) */}
      <circle cx={x} cy={y} r={10 / zoom} fill="transparent" />
      {/* visible dot (~7px on screen) */}
      <circle className="rel-endpoint-dot" cx={x} cy={y} r={3.5 / zoom} strokeWidth={1.5 / zoom} />
    </g>
  );
}

function EndGlyph({
  x,
  y,
  out,
  many,
  optional,
}: {
  x: number;
  y: number;
  /** outward unit vector: direction the edge leaves the node */
  out: { ox: number; oy: number };
  many: boolean;
  optional: boolean;
}) {
  const s = 7; // glyph half-width (perpendicular to the edge)
  const foot = 12; // crow's foot depth (along the edge)
  const { ox, oy } = out;
  // perpendicular unit vector, for the bar width / crow's-foot spread
  const px = -oy;
  const py = ox;
  // a point `d` px out from the anchor along the edge
  const at = (d: number) => ({ x: x + ox * d, y: y + oy * d });
  // a point offset `w` perpendicular at distance `d` out
  const off = (d: number, w: number) => ({ x: x + ox * d + px * w, y: y + oy * d + py * w });

  const parts: JSX.Element[] = [];
  if (many) {
    const apex = at(foot);
    const a = off(0, -s);
    const b = at(0);
    const c = off(0, s);
    parts.push(
      <path
        key="foot"
        className="rel-glyph"
        d={`M ${apex.x} ${apex.y} L ${a.x} ${a.y} M ${apex.x} ${apex.y} L ${b.x} ${b.y} M ${apex.x} ${apex.y} L ${c.x} ${c.y}`}
        fill="none"
      />,
    );
  } else {
    const a = off(foot - 3, -s);
    const b = off(foot - 3, s);
    parts.push(
      <line key="one" className="rel-glyph" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />,
    );
  }
  if (optional) {
    const c = at(foot + 6);
    parts.push(<circle key="opt" className="rel-glyph ring" cx={c.x} cy={c.y} r={4} />);
  } else {
    const a = off(foot + 5, -s);
    const b = off(foot + 5, s);
    parts.push(<line key="mand" className="rel-glyph" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />);
  }
  return <>{parts}</>;
}
