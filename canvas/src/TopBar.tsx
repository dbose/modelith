import { useEffect, useRef, useState } from "react";
import type { Exec } from "./exec";
import {
  IconCaret,
  IconChanges,
  IconCheck,
  IconCollapse,
  IconDecisions,
  IconEntity,
  IconError,
  IconFit,
  IconLayers,
  IconLayout,
  IconOntology,
  IconPlus,
  IconRefresh,
  IconRelationship,
  IconTypes,
  IconWarn,
} from "./icons";
import type { LayoutMode } from "./layout";
import type { PanelTab } from "./SidePanel";
import { ImportExportMenu } from "./sme/ImportExportMenu";
import type { DiagnosticsDoc, ModelDoc } from "./types";

export function TopBar({
  doc,
  diagnostics,
  query,
  onQuery,
  onSubmitQuery,
  showTypes,
  onToggleTypes,
  collapseDetail,
  onToggleCollapse,
  onFitView,
  onRelayout,
  onRefresh,
  saColors,
  readOnly,
  dirty,
  panelTab,
  onPanelTab,
  onNewEntity,
  exec,
  onImported,
  onImportBatch,
  openImport,
}: {
  doc: ModelDoc;
  diagnostics: DiagnosticsDoc | null;
  query: string;
  onQuery: (q: string) => void;
  onSubmitQuery: () => void;
  showTypes: boolean;
  onToggleTypes: () => void;
  collapseDetail: boolean;
  onToggleCollapse: () => void;
  onFitView: () => void;
  onRelayout: (mode?: LayoutMode) => void;
  onRefresh: () => void;
  saColors: Map<string, string>;
  readOnly: boolean;
  dirty: boolean;
  panelTab: PanelTab | null;
  onPanelTab: (t: PanelTab) => void;
  onNewEntity: () => void;
  /** the mutation seam, for the import/export menu (direct-write in this canvas) */
  exec: Exec;
  /** called after an import applies, so the shell can refresh from disk */
  onImported?: (tables: number) => void;
  /** batch applier for an import (avoids the per-op fingerprint race) */
  onImportBatch?: (changes: { op: string; payload: Record<string, unknown> }[]) => Promise<unknown>;
  /** open the Import wizard immediately (VS Code "Import to Model" via `?import=1`) */
  openImport?: boolean;
}) {
  const errors = diagnostics?.items.filter((d) => d.severity === "error").length ?? 0;
  const warnings = diagnostics?.items.filter((d) => d.severity === "warning").length ?? 0;

  // A panel toggle carries an icon + label (the four least-guessable, most-used tools).
  const panelBtn = (
    tab: PanelTab,
    icon: React.ReactNode,
    label: string,
    title: string,
    badge?: boolean,
  ) => (
    <button
      className={"grp-btn" + (panelTab === tab ? " active" : "")}
      onClick={() => onPanelTab(tab)}
      title={title}
    >
      {icon}
      <span>{label}</span>
      {badge && <span className="grp-badge" aria-label="uncommitted changes" />}
    </button>
  );

  return (
    <header className="topbar">
      {/* Row 1 — identity, search, live status */}
      <div className="topbar-row context">
        <div className="brand">
          <span className="logo">{"◮"}</span>
          <span className="brand-name">Modelith</span>
          <span className="project-name">{doc.project.name}</span>
          {readOnly && <span className="chip">read-only</span>}
        </div>

        <input
          className="search"
          type="search"
          placeholder="Search entities & attributes…  ( / , Enter jumps )"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSubmitQuery();
          }}
          id="mdl-search"
        />

        <div className="stats">
          <span className="stat-item" title="entities">
            <IconEntity size={14} />
            {doc.counts.entities}
          </span>
          <span className="stat-item" title="relationships">
            <IconRelationship size={14} />
            {doc.counts.relationships}
          </span>
          {diagnostics && (
            <span
              className={"diag-chip" + (errors ? " err" : warnings ? " warn" : " ok")}
              title={
                diagnostics.items.map((d) => `${d.code} ${d.message}`).join("\n") || "model valid"
              }
            >
              {errors ? (
                <>
                  <IconError size={14} />
                  {errors}
                </>
              ) : warnings ? (
                <>
                  <IconWarn size={14} />
                  {warnings}
                </>
              ) : (
                <>
                  <IconCheck size={14} />
                  valid
                </>
              )}
            </span>
          )}
        </div>
      </div>

      {/* Row 2 — actions, grouped by what they do */}
      <div className="topbar-row actions">
        {!readOnly && (
          <button className="tool-btn primary" onClick={onNewEntity} title="New entity (n)">
            <IconPlus size={15} />
            <span>Entity</span>
          </button>
        )}

        <div className="btn-group" role="group" aria-label="Panels">
          <span className="grp-tag">Panels</span>
          {panelBtn("ontology", <IconOntology size={15} />, "Ontology", "Ontology browser")}
          {panelBtn("layers", <IconLayers size={15} />, "Layers", "Four-layer stack & coverage")}
          {panelBtn(
            "changes",
            <IconChanges size={15} />,
            "Changes",
            dirty ? "Uncommitted changes" : "Changes",
            dirty,
          )}
          {panelBtn("decisions", <IconDecisions size={15} />, "Decisions", "Decision ledger")}
        </div>

        <div className="btn-group" role="group" aria-label="View">
          <span className="grp-tag">View</span>
          <button
            className={"grp-btn icon-only" + (showTypes ? " active" : "")}
            onClick={onToggleTypes}
            title="Toggle data types"
            aria-label="Toggle data types"
          >
            <IconTypes size={15} />
          </button>
          <button
            className={"grp-btn icon-only" + (collapseDetail ? " active" : "")}
            onClick={onToggleCollapse}
            title="Collapse to keys (PK + FK only) — readable for large models"
            aria-label="Collapse to keys"
          >
            <IconCollapse size={15} />
          </button>
          <LayoutPicker onRelayout={onRelayout} />
          <button
            className="grp-btn icon-only"
            onClick={onFitView}
            title="Fit view"
            aria-label="Fit view"
          >
            <IconFit size={15} />
          </button>
        </div>

        <div className="btn-group" role="group" aria-label="Data">
          <span className="grp-tag">Data</span>
          <button
            className="grp-btn icon-only"
            onClick={onRefresh}
            title="Refresh from disk (picks up terminal / external edits)"
            aria-label="Refresh from disk"
          >
            <IconRefresh size={15} />
          </button>
          {/* Interchange: export the model / import SQL DDL, Mermaid, JSON Schema.
              Direct-write here (the engineer canvas has no review screen), so the
              import applies to the working tree and the shell refreshes. The menu
              renders its own Export / Import buttons in the toolbar's grp-btn style. */}
          <ImportExportMenu
            exec={exec}
            canEdit={!readOnly}
            onImported={onImported}
            submitLabel="Import"
            buttonClass="grp-btn"
            applyBatch={onImportBatch}
            openImport={openImport}
          />
        </div>
      </div>

      {/* Reference strip — subject-area colour key (data, not a control) */}
      {doc.subject_areas.length > 0 && (
        <div className="sa-legend">
          <span className="sa-legend-label">Subject areas</span>
          {doc.subject_areas.map((sa) => (
            <span key={sa.id} className="legend-item">
              <span className="swatch" style={{ background: saColors.get(sa.id) }} />
              {sa.name}
            </span>
          ))}
        </div>
      )}
    </header>
  );
}

/** Auto-layout as a small picker. The icon button re-lays out in "auto" (dagre for
 *  small models, grid past the entity threshold — no regression for existing models).
 *  The caret opens a menu to force Hierarchical or Grid, so a large model can be
 *  arranged either way. */
function LayoutPicker({ onRelayout }: { onRelayout: (mode?: LayoutMode) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const pick = (mode: LayoutMode) => {
    onRelayout(mode);
    setOpen(false);
  };

  return (
    <div className="layout-picker" ref={ref}>
      <button
        className="grp-btn icon-only"
        onClick={() => onRelayout("auto")}
        title="Auto-layout"
        aria-label="Auto-layout"
      >
        <IconLayout size={15} />
      </button>
      <button
        className={"grp-btn icon-only caret" + (open ? " active" : "")}
        onClick={() => setOpen((v) => !v)}
        title="Layout style"
        aria-label="Layout style"
      >
        <IconCaret size={13} />
      </button>
      {open && (
        <div className="layout-menu" role="menu">
          <button onClick={() => pick("auto")}>Auto (by size)</button>
          <button onClick={() => pick("hierarchical")}>Hierarchical</button>
          <button onClick={() => pick("grid")}>Grid</button>
        </div>
      )}
    </div>
  );
}
