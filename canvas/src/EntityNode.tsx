import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { Entity } from "./types";

export interface EntityNodeData {
  entity: Entity;
  color: string; // subject-area accent
  dimmed: boolean; // search-filtered out
  highlighted: boolean; // search hit / selected neighbourhood
  showTypes: boolean;
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

export const EntityNode = memo(function EntityNode({ data, selected }: NodeProps<EntityNodeData>) {
  const { entity, color, dimmed, highlighted, showTypes } = data;
  const keys = entity.attributes.filter((a) => a.role === "business_key");
  const rest = entity.attributes.filter((a) => a.role !== "business_key");

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
      <Handle type="target" position={Position.Left} className="port" />
      <Handle type="source" position={Position.Right} className="port" />

      <div className="entity-header">
        <span className="entity-name" title={entity.conceptual?.definition ?? undefined}>
          {entity.name}
        </span>
        <span className="entity-badges">
          {entity.pattern && <span className="badge pattern">{entity.pattern}</span>}
          {entity.conceptual?.ontology?.aligns_to && (
            <span className="badge ontology" title={entity.conceptual.ontology.aligns_to}>
              {"⚙"}
            </span>
          )}
        </span>
      </div>

      {keys.length > 0 && (
        <div className="attr-section keys">
          {keys.map((a) => (
            <div key={a.id} className="attr-row">
              <span className="attr-icon">{ROLE_ICON[a.role]}</span>
              <span className="attr-name pk">{a.name}</span>
              {showTypes && <span className="attr-type">{typeLabel(a.domain)}</span>}
              {!a.nullable && <span className="attr-notnull" title="not null">{"●"}</span>}
            </div>
          ))}
        </div>
      )}

      <div className="attr-section">
        {rest.map((a) => (
          <div key={a.id} className="attr-row">
            <span className="attr-icon">{ROLE_ICON[a.role]}</span>
            <span className="attr-name">{a.name}</span>
            {showTypes && <span className="attr-type">{typeLabel(a.domain)}</span>}
            {!a.nullable && <span className="attr-notnull" title="not null">{"●"}</span>}
          </div>
        ))}
        {entity.attributes.length === 0 && <div className="attr-row empty">no attributes</div>}
      </div>
    </div>
  );
});
