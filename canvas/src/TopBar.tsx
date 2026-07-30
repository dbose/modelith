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
}) {
  const errors = diagnostics?.items.filter((d) => d.severity === "error").length ?? 0;
  const warnings = diagnostics?.items.filter((d) => d.severity === "warning").length ?? 0;

  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo">{"◮"}</span>
        <span className="brand-name">Modelith</span>
        <span className="project-name">{doc.project.name}</span>
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
        <button className={"tool-btn" + (showTypes ? " active" : "")} onClick={onToggleTypes} title="Toggle data types">
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
