import { useEffect, useRef, useState } from "react";
import type { Exec } from "./exec";
import {
  IconArrow,
  IconChanges,
  IconCheck,
  IconCollapse,
  IconDecisions,
  IconEntity,
  IconError,
  IconFit,
  IconLayers,
  IconLayout,
  IconMenu,
  IconOntology,
  IconPlus,
  IconRefresh,
  IconRelationship,
  IconTypes,
  IconWarn,
} from "./icons";
import type { LayoutDir, LayoutMode } from "./layout";
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
  readOnly,
  dirty,
  panelTab,
  onPanelTab,
  onNewEntity,
  exec,
  onImported,
  onImportBatch,
  openImport,
  onToggleDrawer,
  drawerOpen,
  filterCount,
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
  onRelayout: (mode?: LayoutMode, dir?: LayoutDir) => void;
  onRefresh: () => void;
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
  /** toggle the left subject-area / layout drawer */
  onToggleDrawer: () => void;
  drawerOpen: boolean;
  /** number of subject areas currently filtered to (0 = showing all) — badges the hamburger */
  filterCount: number;
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
        <button
          className={"hamburger" + (drawerOpen ? " active" : "")}
          onClick={onToggleDrawer}
          title={drawerOpen ? "Hide subject areas & layout" : "Subject areas & layout"}
          aria-label="Toggle subject-area & layout drawer"
          aria-expanded={drawerOpen}
        >
          <IconMenu size={18} />
          {filterCount > 0 && <span className="hamburger-badge">{filterCount}</span>}
        </button>

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
    </header>
  );
}

// The flow-direction cycle for the dagre-based layouts. Grouped with the layout picker so the
// arrow toggle reads as part of one layout control. Grid ignores direction (a no-op). The arrow
// icon rotates to point the flow way.
const DIR_CYCLE: LayoutDir[] = ["LR", "TB", "RL", "BT"];
const DIR_ROT: Record<LayoutDir, number> = { LR: 0, TB: 90, RL: 180, BT: 270 };
const DIR_LABEL: Record<LayoutDir, string> = {
  LR: "left → right",
  TB: "top → bottom",
  RL: "right → left",
  BT: "bottom → top",
};

/** Auto-layout as a small picker plus a flow-direction toggle. The icon button re-lays out in
 *  "auto" (dagre for small models, grid past the entity threshold — no regression). The caret
 *  opens a menu to force Hierarchical / Clustered / Grid. The arrow button cycles the dagre flow
 *  direction (→ ↓ ← ↑) and re-applies the current layout, so a large model can be arranged either
 *  way and flowed in any direction. Direction persists across relayouts. */
function LayoutPicker({ onRelayout }: { onRelayout: (mode?: LayoutMode, dir?: LayoutDir) => void }) {
  const [open, setOpen] = useState(false);
  const [dir, setDir] = useState<LayoutDir>("LR");
  // Remember the last chosen layout so cycling the direction re-applies THAT layout, not a reset
  // to auto. Starts at auto (the on-load default).
  const [mode, setMode] = useState<LayoutMode>("auto");
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const apply = (m: LayoutMode) => {
    setMode(m);
    onRelayout(m, dir);
  };
  const pick = (m: LayoutMode) => {
    apply(m);
    setOpen(false);
  };
  const cycleDir = () => {
    const next = DIR_CYCLE[(DIR_CYCLE.indexOf(dir) + 1) % DIR_CYCLE.length];
    setDir(next);
    onRelayout(mode, next);
  };

  return (
    <div className="layout-picker" ref={ref}>
      <button
        className="grp-btn icon-only"
        onClick={() => apply("auto")}
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
        {"▾"}
      </button>
      <button
        className="grp-btn icon-only"
        onClick={cycleDir}
        title={`Flow direction: ${DIR_LABEL[dir]} (click to rotate)`}
        aria-label={`Flow direction: ${DIR_LABEL[dir]}`}
      >
        <span className="dir-arrow" style={{ transform: `rotate(${DIR_ROT[dir]}deg)` }}>
          <IconArrow size={15} />
        </span>
      </button>
      {open && (
        <div className="layout-menu" role="menu">
          <button onClick={() => pick("auto")}>Auto (by size)</button>
          <button onClick={() => pick("hierarchical")}>Hierarchical</button>
          <button onClick={() => pick("clustered")}>Clustered (by area)</button>
          <button onClick={() => pick("grid")}>Grid</button>
        </div>
      )}
    </div>
  );
}
