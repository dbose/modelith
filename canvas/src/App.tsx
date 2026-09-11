import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "reactflow";
import "reactflow/dist/style.css";

import {
  ApiError,
  applyImportBatch,
  fetchDiagnostics,
  fetchModel,
  gitStatus,
  sendCommand,
} from "./api";
import { directCapabilities } from "./exec";
import { ModelCanvas, PALETTE, type ModelCanvasHandle } from "./ModelCanvas";
import { SidePanel, type PanelTab } from "./SidePanel";
import { TopBar } from "./TopBar";
import type { DiagnosticsDoc, ModelDoc } from "./types";


function Canvas() {
  const [doc, setDoc] = useState<ModelDoc | null>(null);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showTypes, setShowTypes] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [panelTab, setPanelTab] = useState<PanelTab | null>(null);
  const [dirty, setDirty] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  // The graph, the inspector and the modals now live in <ModelCanvas>; the shell
  // reaches them through this handle for its toolbar and keyboard shortcuts.
  const canvasRef = useRef<ModelCanvasHandle | null>(null);
  // The latest server fingerprint. exec() sends this and advances it from each
  // command's response, so a rapid SEQUENCE of commands (an import batch, a
  // create-then-edit) each carries a fresh fingerprint instead of the stale one from
  // the last render — which otherwise 409s every op after the first. The poll and
  // refresh keep it current, so a genuine external edit is still caught.
  const fpRef = useRef<string>("");
  // While a command (or a batch of them, e.g. an import) is applying, the 3s poll
  // must not overwrite fpRef with an intermediate on-disk fingerprint — that would
  // make the next command in the batch send a stale fingerprint and 409. exec holds
  // this for the duration of each call.
  const execInFlight = useRef(false);

  // TopBar renders the subject-area legend, so it needs the same colour map the
  // canvas uses — one palette, exported from ModelCanvas.
  const saColors = useMemo(() => {
    const m = new Map<string, string>();
    doc?.subject_areas.forEach((sa, i) => m.set(sa.id, PALETTE[i % PALETTE.length]));
    return m;
  }, [doc]);

  const readOnly = doc?.read_only ?? true;
  // The architect canvas writes straight to disk; its capabilities follow the
  // server's read-only flag exactly.
  const caps = useMemo(() => directCapabilities(readOnly), [readOnly]);
  const urlParams = useMemo(() => new URLSearchParams(window.location.search), []);
  const minimal = urlParams.get("minimal") === "1";
  const initialFocusDone = useRef(false);

  const refresh = useCallback(() => {
    fetchModel().then(setDoc).catch((e) => setError(String(e)));
    fetchDiagnostics().then(setDiagnostics).catch(() => setDiagnostics(null));
    gitStatus()
      .then((s) => setDirty(Boolean(s.git && !s.clean)))
      .catch(() => setDirty(false));
    setRefreshKey((k) => k + 1);
  }, []);

  useEffect(refresh, [refresh]);

  // Live-follow the model on disk: poll the fingerprint and refresh only when it
  // changes (a `mdl new` in the terminal, another editor, git checkout). Cheap —
  // fetches the full model only on an actual change. Runs in the embedded preview
  // pane too (minimal mode), so authoring in the terminal shows up without a reload.
  useEffect(() => {
    const id = setInterval(() => {
      fetchModel()
        .then((m) => {
          setDoc((cur) => {
            if (cur && m.fingerprint === cur.fingerprint) return cur; // unchanged
            // don't clobber the batch's own fingerprint while a command is applying
            if (!execInFlight.current) fpRef.current = m.fingerprint;
            if (!minimal) {
              fetchDiagnostics().then(setDiagnostics).catch(() => setDiagnostics(null));
              gitStatus()
                .then((s) => setDirty(Boolean(s.git && !s.clean)))
                .catch(() => setDirty(false));
            }
            setRefreshKey((k) => k + 1);
            return m;
          });
        })
        .catch(() => undefined);
    }, 3000);
    return () => clearInterval(id);
  }, [minimal]);

  // Keep the fingerprint ref in step with whatever the model currently reflects.
  useEffect(() => {
    if (doc?.fingerprint) fpRef.current = doc.fingerprint;
  }, [doc]);

  /** The single mutation path: send a command with the latest known fingerprint,
   * advance the ref from the response so a following command in the same batch is
   * valid, refresh on success, and surface stale-model and validation errors. */
  const exec = useCallback(
    async (op: string, payload: Record<string, unknown>) => {
      if (!doc) return;
      execInFlight.current = true;
      try {
        const r = await sendCommand(op, payload, fpRef.current || doc.fingerprint);
        if (r?.fingerprint) fpRef.current = r.fingerprint; // next op sees the new state
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
      } finally {
        execInFlight.current = false;
      }
    },
    [doc, refresh],
  );

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // `?focus=<entity name>` — the VS Code preview pane follows the active editor
  // this way. Runs once, after the first model load.
  useEffect(() => {
    if (!doc || initialFocusDone.current) return;
    const want = urlParams.get("focus");
    if (!want) return;
    const hit = doc.entities.find(
      (e) => e.name === want || e.conceptual?.name === want,
    );
    if (hit) {
      initialFocusDone.current = true;
      requestAnimationFrame(() => canvasRef.current?.focusEntity(hit.id));
    }
  }, [doc, urlParams]);

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
    if (hit) canvasRef.current?.focusEntity(hit.id);
  }, [doc, query]);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (ev.key === "/") {
        ev.preventDefault();
        document.getElementById("mdl-search")?.focus();
      }
      if (ev.key === "n" && !readOnly) canvasRef.current?.newEntity();
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

  return (
    <div className="app">
      {!minimal && (
      <TopBar
        doc={doc}
        diagnostics={diagnostics}
        query={query}
        onQuery={setQuery}
        onSubmitQuery={submitQuery}
        showTypes={showTypes}
        onToggleTypes={() => setShowTypes((v) => !v)}
        onFitView={() => canvasRef.current?.fitView()}
        onRelayout={() => canvasRef.current?.relayout()}
        onRefresh={refresh}
        saColors={saColors}
        readOnly={readOnly}
        dirty={dirty}
        panelTab={panelTab}
        onPanelTab={(t) => setPanelTab((cur) => (cur === t ? null : t))}
        onNewEntity={() => canvasRef.current?.newEntity()}
        exec={exec}
        onImported={refresh}
        onImportBatch={applyImportBatch}
      />
      )}
      <div className="canvas-wrap">
        <ModelCanvas
          doc={doc}
          caps={caps}
          exec={exec}
          selectedId={panelTab ? null : selectedId}
          onSelect={setSelectedId}
          query={query}
          showTypes={showTypes}
          onError={setToast}
          handleRef={canvasRef}
        />
        {panelTab && (
          <SidePanel
            tab={panelTab}
            onClose={() => setPanelTab(null)}
            readOnly={readOnly}
            refreshKey={refreshKey}
            onModelChanged={refresh}
            onFocusEntity={(id) => {
              setPanelTab(null);
              canvasRef.current?.focusEntity(id);
            }}
          />
        )}
        {toast && <div className="toast">{toast}</div>}
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
