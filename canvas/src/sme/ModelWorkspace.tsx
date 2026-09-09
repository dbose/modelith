import { useCallback, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "reactflow";
import type { Capabilities, Exec } from "../exec";
import { ModelCanvas, type ModelCanvasHandle } from "../ModelCanvas";
import { LayersView, OntologyBrowser, type ReadOnlyPanelTab } from "../SidePanel";
import type { ModelDoc } from "../types";
import { newUlid } from "../ulid";
import "../styles.css";

/** Ops the modeler app may emit.
 *
 * Mirrors the server's _PROPOSABLE_OPS minus the architect-only verdicts — the
 * server is the real boundary, this just avoids offering a control whose change
 * would be refused at submit. */
export const MODELER_OPS: ReadonlySet<string> = new Set([
  "set_definition",
  "set_stewardship",
  "set_subject_area",
  "set_pattern",
  "rename_entity",
  "add_attribute",
  "update_attribute",
  "delete_attribute",
  "set_alignment",
  "clear_alignment",
  "set_term_map",
  "clear_term_map",
  "update_synonyms",
  "set_object_definition",
  "set_subject_area_members",
  // structure
  "create_entity",
  "delete_entity",
  "create_relationship",
  "rename_relationship",
  "update_relationship",
  "delete_relationship",
]);

/** The editable model view: the same canvas the architect uses, driven by a staging
 * exec instead of a direct-write one. Nothing here touches disk — edits collect in
 * the tray and land as one pull request. */
export function ModelWorkspace({
  doc,
  subjectArea,
  onSubjectArea,
  exec,
  canEdit,
  /** engineer mode: edits write the working tree instead of staging a proposal */
  direct = false,
  onSelectEntity,
  busy,
}: {
  /** the PREVIEWED model when anything is staged, otherwise the model on disk */
  doc: ModelDoc;
  subjectArea: string;
  onSubjectArea: (id: string) => void;
  exec: Exec;
  canEdit: boolean;
  direct?: boolean;
  onSelectEntity?: (id: string) => void;
  busy?: boolean;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [panel, setPanel] = useState<ReadOnlyPanelTab | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query] = useState("");
  const canvasRef = useRef<ModelCanvasHandle | null>(null);

  const caps: Capabilities = useMemo(
    () => ({
      canEdit,
      mode: direct ? "direct" : "staged",
      // In direct mode the engineer is the architect: no narrowed op list, and the
      // server's own read_only flag is the only gate.
      allow: direct ? undefined : MODELER_OPS,
      // promoting an alignment is an architect verdict: a proposer must not be able
      // to accept their own proposal in the same gesture
      canArbitrate: direct,
      // committing and proposing are mutually exclusive — propose refuses a dirty
      // tree, so a surface that can commit can strand itself
      canCommit: direct,
    }),
    [canEdit, direct],
  );

  const select = useCallback(
    (id: string | null) => {
      setSelectedId(id);
      if (id) onSelectEntity?.(id);
    },
    [onSelectEntity],
  );

  return (
    <div className="sme-model-view">
      <nav className="sme-nav">
        <button
          className={"sme-sa" + (subjectArea === "" ? " active" : "")}
          onClick={() => onSubjectArea("")}
        >
          Whole model
        </button>
        {doc.subject_areas.map((sa) => (
          <button
            key={sa.id}
            className={"sme-sa" + (subjectArea === sa.id ? " active" : "")}
            onClick={() => onSubjectArea(sa.id)}
            title={sa.definition ?? undefined}
          >
            {sa.name}
            {typeof sa.member_count === "number" && sa.member_count > 0 && (
              <span className="sme-sa-count">{sa.member_count}</span>
            )}
          </button>
        ))}
      </nav>

      <div className="erd-wrap">
        <div className="erd-bar">
          <span className="erd-count">
            {doc.entities.length} entities · {doc.relationships.length} relationships
            {doc.scope && " · scoped"}
          </span>
          {canEdit && (
            <button
              className="erd-action"
              onClick={() => canvasRef.current?.newEntity()}
              title="Add an entity (n)"
            >
              + Entity
            </button>
          )}
          <span className="erd-hint">drag between entities to relate them</span>
          <span className="erd-panels">
            <button
              className={"erd-action ghost" + (panel === "ontology" ? " on" : "")}
              onClick={() => setPanel((p) => (p === "ontology" ? null : "ontology"))}
            >
              Ontology
            </button>
            <button
              className={"erd-action ghost" + (panel === "layers" ? " on" : "")}
              onClick={() => setPanel((p) => (p === "layers" ? null : "layers"))}
            >
              Layers
            </button>
          </span>
          {busy && <span className="erd-busy">updating…</span>}
          {!canEdit && <span className="sme-chip">read-only</span>}
          {error && <span className="erd-error">{error}</span>}
        </div>
        {/* `canvas-wrap` is what styles.css positions the inspector and React Flow's
            own controls against — the same container the architect canvas uses. */}
        <div className="erd-canvas canvas-wrap">
          <ReactFlowProvider>
            <ModelCanvas
              doc={doc}
              caps={caps}
              exec={exec}
              selectedId={selectedId}
              onSelect={select}
              query={query}
              onError={setError}
              handleRef={canvasRef}
            />
          </ReactFlowProvider>
          {panel && (
            <aside className="side-panel">
              <div className="detail-header">
                <h2>{panel === "ontology" ? "Ontology" : "Ontology layers"}</h2>
                <button className="icon-btn" onClick={() => setPanel(null)}>
                  {"✕"}
                </button>
              </div>
              {panel === "ontology" ? (
                <OntologyBrowser />
              ) : (
                <LayersView
                  refreshKey={0}
                  onFocusEntity={(id) => {
                    setPanel(null);
                    canvasRef.current?.focusEntity(id);
                  }}
                />
              )}
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}

/** Mint an id for a creation so the object keeps its identity from preview through
 * to the pull request. */
export function mintId(): string {
  return newUlid();
}
