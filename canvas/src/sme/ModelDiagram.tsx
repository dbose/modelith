import { useEffect, useMemo, useState } from "react";
import ReactFlow, { Background, Controls, MiniMap, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
// EntityNode and RelationshipEdge are the architect canvas's components, so they
// need its stylesheet. Importing it HERE (not in the app entry) keeps it in this
// lazy chunk: the terms and review views never download it.
import "../styles.css";
import { fetchModel } from "../api";
import { EntityNode, type EntityNodeData } from "../EntityNode";
import { layoutGraph } from "../layout";
import { RelationshipEdge, type RelationshipEdgeData } from "../RelationshipEdge";
import type { Entity, ModelDoc } from "../types";

const NODE_TYPES = { entity: EntityNode };
const EDGE_TYPES = { relationship: RelationshipEdge };

/** Severity tints, and the subject-area palette when nothing is being diffed.
 * Both ride EntityNode's existing `color` prop, so the node component is reused
 * unchanged — the same cards the architect canvas draws. */
const SEV_COLOR: Record<string, string> = {
  breaking: "#f87171",
  additive: "#34d399",
  cosmetic: "#fcd34d",
};
const PALETTE = ["#5eead4", "#fcd34d", "#a78bfa", "#fb923c", "#38bdf8", "#f472b6", "#a3e635"];
const NO_SA = "#64748b";

/** The ER diagram, inside the modeler app.
 *
 * The architect canvas and this share EntityNode, RelationshipEdge and the dagre
 * layout, so an entity looks identical in both — but this one is scoped (a subject
 * area) and can be tinted by change severity, which is what the review screen
 * wants. React Flow only loads when this component does; the rest of the app stays
 * light. */
export function ModelDiagram({
  subjectArea,
  severityByUlid,
  focusUlid,
  onSelect,
}: {
  /** scope to one subject area; omit for the whole model */
  subjectArea?: string;
  /** ulid -> severity, when showing a diff */
  severityByUlid?: Map<string, string>;
  focusUlid?: string | null;
  onSelect?: (ulid: string | null) => void;
}) {
  const [doc, setDoc] = useState<ModelDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showTypes, setShowTypes] = useState(true);

  useEffect(() => {
    setDoc(null);
    fetchModel(subjectArea)
      .then(setDoc)
      .catch((e) => setError(String(e)));
  }, [subjectArea]);

  const { nodes, edges } = useMemo(() => {
    if (!doc) return { nodes: [] as Node<EntityNodeData>[], edges: [] as Edge[] };

    const saColor = new Map<string, string>();
    doc.subject_areas.forEach((sa) => {
      // hash the ULID rather than using array position, so colours are stable
      // when an area is added or renamed
      let h = 2166136261;
      for (let i = 0; i < sa.id.length; i++) {
        h = Math.imul(h ^ sa.id.charCodeAt(i), 16777619);
      }
      saColor.set(sa.id, PALETTE[Math.abs(h) % PALETTE.length]);
    });

    const entityMap = new Map<string, Entity>(doc.entities.map((e) => [e.id, e]));
    const diffing = severityByUlid && severityByUlid.size > 0;

    const rawNodes: Node<EntityNodeData>[] = doc.entities.map((e) => {
      // The diagram draws LOGICAL entities, but a meaning change (a definition, a
      // synonym) is reported against the CONCEPTUAL one. Check both, so an edit
      // tints the card it belongs to rather than silently dimming everything.
      const sev =
        severityByUlid?.get(e.id) ??
        (e.conceptual ? severityByUlid?.get(e.conceptual.id) : undefined);
      const color = diffing
        ? (sev ? SEV_COLOR[sev] ?? NO_SA : NO_SA)
        : e.conceptual?.subject_area
          ? saColor.get(e.conceptual.subject_area.id) ?? NO_SA
          : NO_SA;
      return {
        id: e.id,
        type: "entity",
        position: { x: 0, y: 0 },
        data: {
          entity: e,
          color,
          // when diffing, everything unchanged is context
          dimmed: Boolean(diffing && !sev),
          highlighted: e.id === focusUlid,
          showTypes,
        },
      };
    });

    const rawEdges: Edge<RelationshipEdgeData>[] = doc.relationships.map((r) => ({
      id: r.id,
      source: r.from.entity,
      target: r.to.entity,
      type: "relationship",
      data: { relationship: r, dimmed: false },
    }));

    return { nodes: layoutGraph(rawNodes, rawEdges, entityMap), edges: rawEdges };
  }, [doc, severityByUlid, focusUlid, showTypes]);

  if (error) return <p className="sme-splash error">{error}</p>;
  if (!doc) return <div className="sme-splash">◮ drawing the model…</div>;
  if (!doc.entities.length) {
    return (
      <p className="sme-placeholder">
        Nothing to draw — this subject area has no entities in it yet.
      </p>
    );
  }

  return (
    <div className="erd-wrap">
      <div className="erd-bar">
        <span className="erd-count">
          {doc.entities.length} entities · {doc.relationships.length} relationships
          {doc.scope && " · scoped"}
        </span>
        <label className="erd-toggle">
          <input
            type="checkbox"
            checked={showTypes}
            onChange={(e) => setShowTypes(e.target.checked)}
          />
          show types
        </label>
        {severityByUlid && severityByUlid.size > 0 && (
          <span className="erd-legend">
            <span className="sw" style={{ background: SEV_COLOR.breaking }} /> breaking
            <span className="sw" style={{ background: SEV_COLOR.additive }} /> added
            <span className="sw" style={{ background: SEV_COLOR.cosmetic }} /> changed
            <span className="sw" style={{ background: NO_SA }} /> unchanged
          </span>
        )}
      </div>
      <div className="erd-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          fitView
          minZoom={0.1}
          nodesConnectable={false}
          onNodeClick={(_e, n) => onSelect?.(n.id)}
          onPaneClick={() => onSelect?.(null)}
          onlyRenderVisibleElements
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} color="#1f2937" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
    </div>
  );
}
