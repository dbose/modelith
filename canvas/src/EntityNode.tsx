import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { AttributeRow, Entity } from "./types";

export interface EntityNodeData {
  entity: Entity;
  color: string; // subject-area accent
  dimmed: boolean; // search-filtered out
  highlighted: boolean; // search hit / selected neighbourhood
  showTypes: boolean;
  /** attribute ULIDs that are an endpoint of some relationship (issue #5): pinned
   *  near the top so the anchored edges stay meaningful on tall cards */
  endpointAttrs?: Set<string>;
  /** rows to emphasise / mute while hovering an attribute or an edge (issue #5) */
  highlightAttrs?: Set<string>;
  dimAttrs?: Set<string>;
  /** hover an attribute row -> the canvas highlights the relationships it joins */
  onHoverAttr?: (attrId: string | null) => void;
}

const ROLE_ICON: Record<string, string> = {
  business_key: "\u{1F511}", // key
  surrogate_key: "#",
  measure: "\u{03A3}", // sigma
  attribute: "",
};

function typeLabel(domain: string | null): string {
  return domain ?? "";
}

/** Stable per-attribute handle ids. Both a source and a target handle share the
 *  attribute's ULID — React Flow requires uniqueness only PER TYPE, and an edge
 *  disambiguates via sourceHandle vs targetHandle. The entity-level fallback
 *  handles use reserved ids that can never collide with a ULID. */
export const ENTITY_SOURCE_HANDLE = "__entity_source";
export const ENTITY_TARGET_HANDLE = "__entity_target";

export const EntityNode = memo(function EntityNode({ data, selected }: NodeProps<EntityNodeData>) {
  const {
    entity,
    color,
    dimmed,
    highlighted,
    showTypes,
    endpointAttrs,
    highlightAttrs,
    dimAttrs,
    onHoverAttr,
  } = data;

  // business keys first (unchanged), then FK/relationship endpoints, then the rest —
  // so the attributes edges anchor to stay visible near the header (issue #5 pinning).
  const rank = (a: AttributeRow): number => {
    if (a.role === "business_key") return 0;
    if (endpointAttrs?.has(a.id)) return 1;
    return 2;
  };
  const ordered = [...entity.attributes]
    .map((a, i) => ({ a, i }))
    .sort((x, y) => rank(x.a) - rank(y.a) || x.i - y.i) // stable within a rank
    .map((x) => x.a);
  const keys = ordered.filter((a) => a.role === "business_key");
  const rest = ordered.filter((a) => a.role !== "business_key");

  const row = (a: AttributeRow, pk: boolean) => {
    const cls =
      "attr-row" +
      (highlightAttrs?.has(a.id) ? " rel-hit" : "") +
      (dimAttrs?.has(a.id) ? " rel-dim" : "") +
      (endpointAttrs?.has(a.id) ? " endpoint" : "");
    return (
      <div
        key={a.id}
        className={cls}
        onMouseEnter={onHoverAttr ? () => onHoverAttr(a.id) : undefined}
        onMouseLeave={onHoverAttr ? () => onHoverAttr(null) : undefined}
      >
        {/* per-row connection points — anchor edges to the concrete column */}
        <Handle id={a.id} type="target" position={Position.Left} className="port attr-port" />
        <Handle id={a.id} type="source" position={Position.Right} className="port attr-port" />
        <span className="attr-icon">{ROLE_ICON[a.role]}</span>
        <span className={"attr-name" + (pk ? " pk" : "")}>{a.name}</span>
        {showTypes && <span className="attr-type">{typeLabel(a.domain)}</span>}
        {!a.nullable && (
          <span className="attr-notnull" title="not null">
            {"●"}
          </span>
        )}
      </div>
    );
  };

  return (
    <div
      className={
        "entity-node" +
        (selected ? " selected" : "") +
        (dimmed ? " dimmed" : "") +
        (highlighted ? " highlighted" : "")
      }
      style={{ borderTopColor: color }}
    >
      {/* Entity-level fallback handles for relationships with no attribute mapping.
          Explicit ids so an edge can target them and they never clash with a row. */}
      <Handle id={ENTITY_TARGET_HANDLE} type="target" position={Position.Left} className="port" />
      <Handle id={ENTITY_SOURCE_HANDLE} type="source" position={Position.Right} className="port" />

      <div className="entity-header">
        <span className="entity-name" title={entity.conceptual?.definition ?? undefined}>
          {entity.name}
        </span>
        <span className="entity-badges">
          {entity.pattern && <span className="badge pattern">{entity.pattern}</span>}
          {(() => {
            const refs = (entity.conceptual?.ontology_refs ?? []).filter((r) => r.uri);
            if (refs.length === 0) return null;
            const title = refs.map((r) => r.uri).join("\n");
            return (
              <span className="badge ontology" title={title}>
                {"⚙"}
                {refs.length > 1 ? ` ${refs.length}` : ""}
              </span>
            );
          })()}
        </span>
      </div>

      {keys.length > 0 && <div className="attr-section keys">{keys.map((a) => row(a, true))}</div>}

      <div className="attr-section">
        {rest.map((a) => row(a, false))}
        {entity.attributes.length === 0 && <div className="attr-row empty">no attributes</div>}
      </div>
    </div>
  );
});
