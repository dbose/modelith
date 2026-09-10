import { memo } from "react";
import { getSmoothStepPath, Position, type EdgeProps } from "reactflow";
import type { Relationship } from "./types";

export interface RelationshipEdgeData {
  relationship: Relationship;
  dimmed: boolean;
  /** number of paired columns; >1 means a composite key drawn as one line (issue #5) */
  memberCount?: number;
  /** hover the line -> the canvas highlights both endpoint attribute rows */
  onHoverEdge?: (relId: string | null) => void;
}

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
  const [path] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 10,
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

  return (
    <g className={cls}>
      <path
        id={id}
        className="rel-path"
        d={path}
        fill="none"
        strokeDasharray={identifying ? undefined : "6 4"}
      />
      {/* wider invisible path for easier hover/click */}
      <path
        d={path}
        fill="none"
        strokeWidth={14}
        stroke="transparent"
        onMouseEnter={onHover ? () => onHover(rel!.id) : undefined}
        onMouseLeave={onHover ? () => onHover(null) : undefined}
      />
      <EndGlyph x={sourceX} y={sourceY} side={sourcePosition} many={srcMany} optional={optional} />
      <EndGlyph x={targetX} y={targetY} side={targetPosition} many={tgtMany} optional={false} />
      {(selected || composite) && rel && (
        <text className="rel-label" x={(sourceX + targetX) / 2} y={(sourceY + targetY) / 2 - 8}>
          {selected ? rel.name : ""}
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
