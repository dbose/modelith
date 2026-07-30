import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeTypes,
  type Node,
  type NodeTypes,
} from "reactflow";
import "reactflow/dist/style.css";

import { ApiError, fetchDiagnostics, fetchModel, gitStatus, sendCommand } from "./api";
import { EntityNode, type EntityNodeData } from "./EntityNode";
import { Inspector } from "./Inspector";
import { layoutGraph } from "./layout";
import { AlignModal, NewEntityModal, RelModal } from "./modals";
import { RelationshipEdge, type RelationshipEdgeData } from "./RelationshipEdge";
import { SidePanel, type PanelTab } from "./SidePanel";
import { TopBar } from "./TopBar";
import type { DiagnosticsDoc, Entity, ModelDoc } from "./types";

const nodeTypes: NodeTypes = { entity: EntityNode };
const edgeTypes: EdgeTypes = { relationship: RelationshipEdge };

const PALETTE = ["#5eead4", "#93c5fd", "#f0abfc", "#fcd34d", "#86efac", "#fda4af", "#c4b5fd"];
const NO_SA_COLOR = "#64748b";

function Canvas() {
  const [doc, setDoc] = useState<ModelDoc | null>(null);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showTypes, setShowTypes] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [panelTab, setPanelTab] = useState<PanelTab | null>(null);
  const [alignFor, setAlignFor] = useState<Entity | null>(null);
  const [newEntityOpen, setNewEntityOpen] = useState(false);
  const [relDraft, setRelDraft] = useState<{ from: string; to: string } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [nodes, setNodes] = useState<Node<EntityNodeData>[]>([]);
  const [edges, setEdges] = useState<Edge<RelationshipEdgeData>[]>([]);
  const layoutedRef = useRef(false);
  const { fitView, setCenter, getNode } = useReactFlow();

  const readOnly = doc?.read_only ?? true;

  const refresh = useCallback(() => {
    fetchModel().then(setDoc).catch((e) => setError(String(e)));
    fetchDiagnostics().then(setDiagnostics).catch(() => setDiagnostics(null));
    gitStatus()
      .then((s) => setDirty(Boolean(s.git && !s.clean)))
      .catch(() => setDirty(false));
    setRefreshKey((k) => k + 1);
  }, []);

  useEffect(refresh, [refresh]);

  /** The single mutation path: send a command with the fingerprint we based our
   * view on; refresh on success; surface stale-model and validation errors. */
  const exec = useCallback(
    async (op: string, payload: Record<string, unknown>) => {
      if (!doc) return;
      try {
        const r = await sendCommand(op, payload, doc.fingerprint);
        refresh();
        return r;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          setToast("Model changed on disk — view refreshed, please retry.");
          refresh();
        } else {
          setToast(e instanceof Error ? e.message : String(e));
        }
        throw e;
      }
    },
    [doc, refresh],
  );

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

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
      const posById = new Map(prev.map((n) => [n.id, n.position]));
      const known = newNodes.every((n) => posById.has(n.id));
      if (layoutedRef.current && known && prev.length >= newNodes.length) {
        return newNodes.map((n) => ({ ...n, position: posById.get(n.id) ?? n.position }));
      }
      // new entity (or first load): layout, keeping existing positions where known
      const laid = layoutGraph(newNodes, newEdges, entityIndex);
      const merged = laid.map((n) =>
        posById.has(n.id) && layoutedRef.current
          ? { ...n, position: posById.get(n.id)! }
          : n,
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
      setSelectedId(id);
      const n = getNode(id);
      if (n) setCenter(n.position.x + 132, n.position.y + 80, { zoom: 1.1, duration: 400 });
    },
    [getNode, setCenter],
  );

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

  const onConnect = useCallback(
    (conn: Connection) => {
      if (readOnly || !conn.source || !conn.target || conn.source === conn.target) return;
      setRelDraft({ from: conn.source, to: conn.target });
    },
    [readOnly],
  );

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (ev.key === "/") {
        ev.preventDefault();
        document.getElementById("mdl-search")?.focus();
      }
      if (ev.key === "n" && !readOnly) setNewEntityOpen(true);
      if (ev.key === "Escape") {
        setSelectedId(null);
        setQuery("");
        setPanelTab(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [readOnly]);

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
        readOnly={readOnly}
        dirty={dirty}
        panelTab={panelTab}
        onPanelTab={(t) => setPanelTab((cur) => (cur === t ? null : t))}
        onNewEntity={() => setNewEntityOpen(true)}
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
          onConnect={onConnect}
          nodesConnectable={!readOnly}
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

        {selected && !panelTab && (
          <Inspector
            entity={selected}
            doc={doc}
            readOnly={readOnly}
            exec={exec}
            onClose={() => setSelectedId(null)}
            onFocusEntity={focusEntity}
            onAlign={setAlignFor}
          />
        )}
        {panelTab && (
          <SidePanel
            tab={panelTab}
            onClose={() => setPanelTab(null)}
            readOnly={readOnly}
            refreshKey={refreshKey}
            onModelChanged={refresh}
            onFocusEntity={(id) => {
              setPanelTab(null);
              focusEntity(id);
            }}
          />
        )}
        {toast && <div className="toast">{toast}</div>}
      </div>

      {alignFor && <AlignModal entity={alignFor} exec={exec} onClose={() => setAlignFor(null)} />}
      {newEntityOpen && (
        <NewEntityModal
          doc={doc}
          exec={exec}
          onClose={() => setNewEntityOpen(false)}
          onCreated={(id) => requestAnimationFrame(() => focusEntity(id))}
        />
      )}
      {relDraft && (
        <RelModal
          doc={doc}
          fromId={relDraft.from}
          toId={relDraft.to}
          exec={exec}
          onClose={() => setRelDraft(null)}
        />
      )}
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
