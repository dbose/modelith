import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeTypes,
  type EdgeTypes,
} from "reactflow";
import "reactflow/dist/style.css";

import { fetchDiagnostics, fetchModel } from "./api";
import { DetailPanel } from "./DetailPanel";
import { EntityNode, type EntityNodeData } from "./EntityNode";
import { layoutGraph } from "./layout";
import { RelationshipEdge, type RelationshipEdgeData } from "./RelationshipEdge";
import { TopBar } from "./TopBar";
import type { DiagnosticsDoc, Entity, ModelDoc } from "./types";

const nodeTypes: NodeTypes = { entity: EntityNode };
const edgeTypes: EdgeTypes = { relationship: RelationshipEdge };

// Subject-area accent palette (cycled).
const PALETTE = ["#5eead4", "#93c5fd", "#f0abfc", "#fcd34d", "#86efac", "#fda4af", "#c4b5fd"];
const NO_SA_COLOR = "#64748b";

function Canvas() {
  const [doc, setDoc] = useState<ModelDoc | null>(null);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showTypes, setShowTypes] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nodes, setNodes] = useState<Node<EntityNodeData>[]>([]);
  const [edges, setEdges] = useState<Edge<RelationshipEdgeData>[]>([]);
  const layoutedRef = useRef(false);
  const { fitView, setCenter, getNode } = useReactFlow();

  const saColors = useMemo(() => {
    const m = new Map<string, string>();
    doc?.subject_areas.forEach((sa, i) => m.set(sa.id, PALETTE[i % PALETTE.length]));
    return m;
  }, [doc]);

  const entityIndex = useMemo(() => {
    const m = new Map<string, Entity>();
    doc?.entities.forEach((e) => m.set(e.id, e));
    return m;
  }, [doc]);

  // Load model + diagnostics.
  useEffect(() => {
    fetchModel().then(setDoc).catch((e) => setError(String(e)));
    fetchDiagnostics().then(setDiagnostics).catch(() => setDiagnostics(null));
  }, []);

  // Build + layout graph whenever the doc, query, or selection changes.
  useEffect(() => {
    if (!doc) return;
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
      // Preserve user-dragged positions after the first layout.
      if (layoutedRef.current && prev.length === newNodes.length) {
        const posById = new Map(prev.map((n) => [n.id, n.position]));
        return newNodes.map((n) => ({ ...n, position: posById.get(n.id) ?? n.position }));
      }
      const laid = layoutGraph(newNodes, newEdges, entityIndex);
      layoutedRef.current = true;
      requestAnimationFrame(() => fitView({ padding: 0.15, duration: 300 }));
      return laid;
    });
    setEdges(newEdges);
  }, [doc, query, selectedId, showTypes, saColors, entityIndex, fitView]);

  const relayout = useCallback(() => {
    setNodes((prev) => layoutGraph(prev, edges, entityIndex));
    requestAnimationFrame(() => fitView({ padding: 0.15, duration: 300 }));
  }, [edges, entityIndex, fitView]);

  const focusEntity = useCallback(
    (id: string) => {
      setSelectedId(id);
      const n = getNode(id);
      if (n) setCenter(n.position.x + 132, n.position.y + 80, { zoom: 1.1, duration: 400 });
    },
    [getNode, setCenter],
  );

  // Enter in the search box jumps to the first match (essential at 1000+ nodes).
  const submitQuery = useCallback(() => {
    if (!doc) return;
    const q = query.trim().toLowerCase();
    if (!q) return;
    const hit = doc.entities.find((e) => {
      const hay =
        e.name.toLowerCase() +
        " " +
        (e.conceptual?.name.toLowerCase() ?? "") +
        " " +
        e.attributes.map((a) => a.name.toLowerCase()).join(" ");
      return hay.includes(q);
    });
    if (hit) focusEntity(hit.id);
  }, [doc, query, focusEntity]);

  // "/" focuses search.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "/" && document.activeElement?.tagName !== "INPUT") {
        ev.preventDefault();
        document.getElementById("mdl-search")?.focus();
      }
      if (ev.key === "Escape") {
        setSelectedId(null);
        setQuery("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (error) {
    return (
      <div className="splash error">
        <h1>{"◮"} Modelith</h1>
        <p>Could not load the model:</p>
        <pre>{error}</pre>
        <p>
          Is <code>mdl serve</code> pointed at a model directory?
        </p>
      </div>
    );
  }
  if (!doc) return <div className="splash">{"◮"} loading model…</div>;

  const selected = selectedId ? entityIndex.get(selectedId) ?? null : null;

  return (
    <div className="app">
      <TopBar
        doc={doc}
        diagnostics={diagnostics}
        query={query}
        onQuery={setQuery}
        onSubmitQuery={submitQuery}
        showTypes={showTypes}
        onToggleTypes={() => setShowTypes((v) => !v)}
        onFitView={() => fitView({ padding: 0.15, duration: 300 })}
        onRelayout={relayout}
        saColors={saColors}
      />
      <div className="canvas-wrap">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={(_, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          onNodeDragStop={(_, n) =>
            setNodes((prev) => prev.map((p) => (p.id === n.id ? { ...p, position: n.position } : p)))
          }
          minZoom={0.05}
          onlyRenderVisibleElements
          proOptions={{ hideAttribution: true }}
          fitView
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.5} color="#263042" />
          <MiniMap
            pannable
            zoomable
            nodeColor={(n) => (n.data as EntityNodeData)?.color ?? "#475569"}
            maskColor="rgba(10, 14, 20, 0.75)"
          />
          <Controls showInteractive={false} />
        </ReactFlow>
        {selected && (
          <DetailPanel
            entity={selected}
            doc={doc}
            onClose={() => setSelectedId(null)}
            onFocusEntity={focusEntity}
          />
        )}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  );
}
