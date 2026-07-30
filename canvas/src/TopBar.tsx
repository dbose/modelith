import type { PanelTab } from "./SidePanel";
import type { DiagnosticsDoc, ModelDoc } from "./types";

export function TopBar({
  doc,
  diagnostics,
  query,
  onQuery,
  onSubmitQuery,
  showTypes,
  onToggleTypes,
  onFitView,
  onRelayout,
  saColors,
  readOnly,
  dirty,
  panelTab,
  onPanelTab,
  onNewEntity,
}: {
  doc: ModelDoc;
  diagnostics: DiagnosticsDoc | null;
  query: string;
  onQuery: (q: string) => void;
  onSubmitQuery: () => void;
  showTypes: boolean;
  onToggleTypes: () => void;
  onFitView: () => void;
  onRelayout: () => void;
  saColors: Map<string, string>;
  readOnly: boolean;
  dirty: boolean;
  panelTab: PanelTab | null;
  onPanelTab: (t: PanelTab) => void;
  onNewEntity: () => void;
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
        <button className="tool-btn" onClick={onRelayout} title="Auto-layout">
          {"⌗"}
        </button>
        <button className="tool-btn" onClick={onFitView} title="Fit view">
          {"⛶"}
        </button>
      </div>
    </header>
  );
}
