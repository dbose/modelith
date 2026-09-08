import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeTypes,
  type Node,
  type NodeTypes,
} from "reactflow";
// This component owns React Flow, so it owns React Flow's stylesheet — relying on
// whichever shell mounts it to import this is how the modeler app ended up with an
// unpositioned minimap sprawled across the canvas.
import "reactflow/dist/style.css";
import { EntityNode, type EntityNodeData } from "./EntityNode";
import type { Capabilities, Exec } from "./exec";
import { Inspector } from "./Inspector";
import { layoutGraph } from "./layout";
import { AlignModal, NewEntityModal, RelEditModal, RelModal, TermMapModal } from "./modals";
import { RelationshipEdge, type RelationshipEdgeData } from "./RelationshipEdge";
import type { Entity, ModelDoc } from "./types";

const nodeTypes: NodeTypes = { entity: EntityNode };
const edgeTypes: EdgeTypes = { relationship: RelationshipEdge };

// Categorical palette for subject-area grouping. Leads with the amber accent, then
// spans warm-to-cool hues so groups stay distinguishable on the warm-charcoal ground.
export const PALETTE = ["#e0a63a", "#e8825a", "#d98ba8", "#c4a3e0", "#7fb0d8", "#7fc9a0", "#e6c14a"];
export const NO_SA_COLOR = "#8a7d6a";

/** Imperative handle for the things a shell needs to drive from its own chrome
 * (the toolbar's re-layout and fit buttons, and focus-by-id from search). */
export interface ModelCanvasHandle {
  relayout: () => void;
  fitView: () => void;
  focusEntity: (id: string) => void;
  /** Open the New Entity modal — a shell gesture (toolbar button, `n` shortcut). */
  newEntity: () => void;
}

export interface ModelCanvasProps {
  /** The model to draw. For a staging surface this is the PREVIEWED model, not
   *  what is on disk — the canvas neither knows nor cares which. */
  doc: ModelDoc;
  caps: Capabilities;
  /** Direct-write or staging; see exec.ts. */
  exec: Exec;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  query?: string;
  showTypes?: boolean;
  /** Surfaced by the shell however it likes (a toast, a banner). */
  onError?: (message: string) => void;
  handleRef?: React.Ref<ModelCanvasHandle>;
}

/** The ER canvas plus its editing surface: the graph, the inspector, and the five
 * modals. Deliberately free of page-level concerns — no URL parsing, no global
 * keydown listener, no polling, no ReactFlowProvider (the caller supplies one, or
 * two mounted canvases would nest providers), and no splash screens, because the
 * two shells word those differently.
 *
 * Both apps mount this: the architect canvas passes a direct-write `exec`, the
 * modeler app passes a staging one. */
export function ModelCanvas({
  doc,
  caps,
  exec,
  selectedId,
  onSelect,
  query = "",
  showTypes = true,
  onError,
  handleRef,
}: ModelCanvasProps) {
  const { fitView } = useReactFlow();
  const [nodes, setNodes] = useState<Node<EntityNodeData>[]>([]);
  const [edges, setEdges] = useState<Edge<RelationshipEdgeData>[]>([]);
  const layoutedRef = useRef(false);

  const [alignFor, setAlignFor] = useState<Entity | null>(null);
  const [mapFor, setMapFor] = useState<Entity | null>(null);
  const [newEntityOpen, setNewEntityOpen] = useState(false);
  const [relDraft, setRelDraft] = useState<{ from: string; to: string } | null>(null);
  const [relEdit, setRelEdit] = useState<string | null>(null);

  const readOnly = !caps.canEdit;

  const saColors = useMemo(() => {
    const m = new Map<string, string>();
    doc.subject_areas.forEach((sa, i) => m.set(sa.id, PALETTE[i % PALETTE.length]));
    return m;
  }, [doc]);

  const entityIndex = useMemo(() => {
    const m = new Map<string, Entity>();
    doc.entities.forEach((e) => m.set(e.id, e));
    return m;
  }, [doc]);

  useEffect(() => {
    const q = query.trim().toLowerCase();
    const matches = new Set<string>();
    if (q) {
      for (const e of doc.entities) {
        const hay =
          e.name.toLowerCase() +
          " " +
          (e.conceptual?.name.toLowerCase() ?? "") +
          " " +
          e.attributes.map((a) => a.name.toLowerCase()).join(" ");
        if (hay.includes(q)) matches.add(e.id);
      }
    }
    const neighbours = new Set<string>();
    if (selectedId) {
      neighbours.add(selectedId);
      for (const r of doc.relationships) {
        if (r.from.entity === selectedId) neighbours.add(r.to.entity);
        if (r.to.entity === selectedId) neighbours.add(r.from.entity);
      }
    }

    const newNodes: Node<EntityNodeData>[] = doc.entities.map((e) => ({
      id: e.id,
      type: "entity",
      position: { x: 0, y: 0 },
      data: {
        entity: e,
        color: e.conceptual?.subject_area
          ? saColors.get(e.conceptual.subject_area.id) ?? NO_SA_COLOR
          : NO_SA_COLOR,
        dimmed: (q !== "" && !matches.has(e.id)) || (selectedId !== null && !neighbours.has(e.id)),
        highlighted: q !== "" && matches.has(e.id),
        showTypes,
      },
    }));

    const newEdges: Edge<RelationshipEdgeData>[] = doc.relationships
      .filter((r) => entityIndex.has(r.from.entity) && entityIndex.has(r.to.entity))
      .map((r) => ({
        id: r.id,
        source: r.from.entity,
        target: r.to.entity,
        type: "relationship",
        data: {
          relationship: r,
          dimmed:
            selectedId !== null && r.from.entity !== selectedId && r.to.entity !== selectedId,
        },
      }));

    setNodes((prev) => {
      const posById = new Map(prev.map((n) => [n.id, n.position]));
      const known = newNodes.every((n) => posById.has(n.id));
      if (layoutedRef.current && known && prev.length >= newNodes.length) {
        return newNodes.map((n) => ({ ...n, position: posById.get(n.id) ?? n.position }));
      }
      // new entity (or first load): layout, keeping existing positions where known
      const laid = layoutGraph(newNodes, newEdges, entityIndex);
      const merged = laid.map((n) =>
        posById.has(n.id) && layoutedRef.current ? { ...n, position: posById.get(n.id)! } : n,
      );
      layoutedRef.current = true;
      requestAnimationFrame(() => fitView({ padding: 0.15, duration: 300 }));
      return merged;
    });
    setEdges(newEdges);
  }, [doc, query, selectedId, showTypes, saColors, entityIndex, fitView]);

  const relayout = useCallback(() => {
    setNodes((prev) => layoutGraph(prev, edges, entityIndex));
    requestAnimationFrame(() => fitView({ padding: 0.15, duration: 300 }));
  }, [edges, entityIndex, fitView]);

  const focusEntity = useCallback(
    (id: string) => {
      onSelect(id);
      setNodes((prev) => {
        const n = prev.find((x) => x.id === id);
        if (n) requestAnimationFrame(() => fitView({ nodes: [{ id }], padding: 0.4, duration: 400 }));
        return prev;
      });
    },
    [fitView, onSelect],
  );

  useImperativeHandle(
    handleRef,
    () => ({
      relayout,
      fitView: () => fitView({ padding: 0.15, duration: 300 }),
      focusEntity,
      newEntity: () => setNewEntityOpen(true),
    }),
    [relayout, fitView, focusEntity],
  );

  const onConnect = useCallback(
    (conn: Connection) => {
      if (readOnly || !conn.source || !conn.target || conn.source === conn.target) return;
      setRelDraft({ from: conn.source, to: conn.target });
    },
    [readOnly],
  );

  /** Wrap exec so a failure reaches the shell's own error surface. The canvas has
   *  no toast of its own — the two shells present errors differently. */
  const guardedExec: Exec = useCallback(
    async (op, payload) => {
      try {
        return await exec(op, payload);
      } catch (e) {
        onError?.(e instanceof Error ? e.message : String(e));
        throw e;
      }
    },
    [exec, onError],
  );

  const selected = selectedId ? entityIndex.get(selectedId) ?? null : null;
  const editing = doc.relationships.find((r) => r.id === relEdit) ?? null;

  return (
    <>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={(_, n) => onSelect(n.id)}
        onEdgeClick={(_, e) => !readOnly && setRelEdit(e.id)}
        onPaneClick={() => {
          onSelect(null);
          setRelEdit(null);
        }}
        onNodeDragStop={(_, n) =>
          setNodes((prev) => prev.map((p) => (p.id === n.id ? { ...p, position: n.position } : p)))
        }
        onConnect={onConnect}
        nodesConnectable={!readOnly}
        minZoom={0.05}
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
        fitView
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.5} color="#33291f" />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) => (n.data as EntityNodeData)?.color ?? "#5a4f42"}
          maskColor="rgba(20, 17, 15, 0.75)"
        />
        <Controls showInteractive={false} />
      </ReactFlow>

      {selected && (
        <Inspector
          entity={selected}
          doc={doc}
          caps={caps}
          exec={guardedExec}
          onClose={() => onSelect(null)}
          onFocusEntity={focusEntity}
          onAlign={setAlignFor}
          onEditMapping={setMapFor}
        />
      )}

      {alignFor && (
        <AlignModal entity={alignFor} exec={guardedExec} onClose={() => setAlignFor(null)} />
      )}
      {mapFor && (
        <TermMapModal
          entity={mapFor}
          doc={doc}
          exec={guardedExec}
          onClose={() => setMapFor(null)}
        />
      )}
      {newEntityOpen && (
        <NewEntityModal
          doc={doc}
          exec={guardedExec}
          onClose={() => setNewEntityOpen(false)}
          onCreated={focusEntity}
        />
      )}
      {relDraft && (
        <RelModal
          doc={doc}
          fromId={relDraft.from}
          toId={relDraft.to}
          exec={guardedExec}
          onClose={() => setRelDraft(null)}
        />
      )}
      {editing && (
        <RelEditModal
          rel={editing}
          doc={doc}
          exec={guardedExec}
          onClose={() => setRelEdit(null)}
        />
      )}
    </>
  );
}
