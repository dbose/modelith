import { useEffect, useRef, useState } from "react";
import type { Exec } from "./exec";
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

  const tabBtn = (tab: PanelTab, label: string, title: string, badge?: boolean) => (
    <button
      className={"tool-btn" + (panelTab === tab ? " active" : "") + (badge ? " badged" : "")}
      onClick={() => onPanelTab(tab)}
      title={title}
    >
      {label}
    </button>
  );

  return (
    <header className="topbar">
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

      <div className="legend">
        {doc.subject_areas.map((sa) => (
          <span key={sa.id} className="legend-item">
            <span className="swatch" style={{ background: saColors.get(sa.id) }} />
            {sa.name}
          </span>
        ))}
      </div>

      <div className="stats">
        <span title="entities">{doc.counts.entities} ⬛</span>
        <span title="relationships">{doc.counts.relationships} ⤳</span>
        {diagnostics && (
          <span
            className={"diag-chip" + (errors ? " err" : warnings ? " warn" : " ok")}
            title={diagnostics.items.map((d) => `${d.code} ${d.message}`).join("\n") || "model valid"}
          >
            {errors ? `${errors} ✗` : warnings ? `${warnings} ⚠` : "✓ valid"}
          </span>
        )}
      </div>

      <div className="actions">
        {!readOnly && (
          <button className="tool-btn primary" onClick={onNewEntity} title="New entity (n)">
            {"+ Entity"}
          </button>
        )}
        {tabBtn("ontology", "⬡", "Ontology browser")}
        {tabBtn("layers", "≣", "Four-layer stack & coverage")}
        {tabBtn("changes", "±", dirty ? "Uncommitted changes!" : "Changes", dirty)}
        {tabBtn("decisions", "⚖", "Decision ledger")}
        <span className="divider" />
        <button
          className={"tool-btn" + (showTypes ? " active" : "")}
          onClick={onToggleTypes}
          title="Toggle data types"
        >
          {"{T}"}
        </button>
        <button
          className={"tool-btn" + (collapseDetail ? " active" : "")}
          onClick={onToggleCollapse}
          title="Collapse to keys (PK + FK only) — readable for large models"
        >
          {"⇕"}
        </button>
        <button
          className="tool-btn"
          onClick={onRefresh}
          title="Refresh from disk (picks up terminal / external edits)"
        >
          {"⟳"}
        </button>
        <LayoutPicker onRelayout={onRelayout} />
        <button className="tool-btn" onClick={onFitView} title="Fit view">
          {"⛶"}
        </button>
        <span className="divider" />
        {/* Interchange: export the model / import SQL DDL, Mermaid, JSON Schema.
            Direct-write here (the engineer canvas has no review screen), so the
            import applies to the working tree and the shell refreshes. */}
        <ImportExportMenu
          exec={exec}
          canEdit={!readOnly}
          onImported={onImported}
          submitLabel="Import"
          buttonClass="tool-btn"
          applyBatch={onImportBatch}
          openImport={openImport}
        />
      </div>
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
      <button className="tool-btn" onClick={() => onRelayout("auto")} title="Auto-layout">
        {"⌗"}
      </button>
      <button
        className={"tool-btn caret" + (open ? " active" : "")}
        onClick={() => setOpen((v) => !v)}
        title="Layout style"
      >
        {"▾"}
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
