import { useEffect, useMemo, useState } from "react";

// Wire type mirroring packages/server/src/mdl_server/catalog_app.py::_entry_doc
interface CatalogRow {
  model: string;
  namespace_ulid: string | null;
  remote: string | null;
  commit: string | null;
  ontology_layers: string[];
  published_at: string | null;
  modelith_schema_version: string;
  source_link: string | null;
}

/** Cross-repo catalog browse view (spec §4). A read-only, searchable list of published
 * model repos — pointers plus summary fields only. Each row links out to its source
 * repo at the published commit; model content is fetched from the source on demand. */
function slugOf(model: string): string {
  return [...model.toLowerCase()]
    .map((c) => (/[a-z0-9\-_]/.test(c) ? c : "-"))
    .join("");
}

export function CatalogApp() {
  const [rows, setRows] = useState<CatalogRow[]>([]);
  const [q, setQ] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [layerFilter, setLayerFilter] = useState<string>("");
  const [opening, setOpening] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);

  /** Open an entry's LDM canvas: ask the server to materialise + mount it, then
   * navigate. Falls back to the source repo link when the backend can't materialise. */
  async function openModel(r: CatalogRow) {
    setOpenError(null);
    setOpening(r.model);
    try {
      const res = await fetch(`/api/catalog/open/${slugOf(r.model)}`, { method: "POST" });
      const d = await res.json();
      if (d.ok && d.url) {
        window.location.href = d.url;
        return;
      }
      if (d.source_link) {
        window.open(d.source_link, "_blank", "noreferrer");
      }
      setOpenError(d.reason ?? "Could not open this model's canvas.");
    } catch {
      setOpenError("Could not reach the catalog server.");
    } finally {
      setOpening(null);
    }
  }

  useEffect(() => {
    fetch("/api/catalog/list")
      .then((r) => r.json())
      .then((d) => setRows(d.entries ?? []))
      .catch(() => setRows([]))
      .finally(() => setLoaded(true));
  }, []);

  const allLayers = useMemo(() => {
    const s = new Set<string>();
    rows.forEach((r) => r.ontology_layers.forEach((l) => s.add(l)));
    return [...s].sort();
  }, [rows]);

  const shown = useMemo(() => {
    const ql = q.toLowerCase().trim();
    return rows.filter((r) => {
      if (layerFilter && !r.ontology_layers.includes(layerFilter)) return false;
      if (!ql) return true;
      const hay = [r.model, r.namespace_ulid, r.remote, ...r.ontology_layers]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(ql);
    });
  }, [rows, q, layerFilter]);

  return (
    <div className="cat-shell">
      <header className="cat-header">
        <div className="cat-brand">
          <span className="cat-mark">◮</span> Modelith <span className="cat-sub">catalog</span>
        </div>
        <input
          className="cat-search"
          placeholder="Search models, namespaces, ontology layers…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <span className="cat-count">
          {shown.length} of {rows.length} model{rows.length === 1 ? "" : "s"}
        </span>
      </header>

      {allLayers.length > 0 && (
        <div className="cat-filters">
          <button
            className={"cat-chip" + (layerFilter === "" ? " active" : "")}
            onClick={() => setLayerFilter("")}
          >
            all layers
          </button>
          {allLayers.map((l) => (
            <button
              key={l}
              className={"cat-chip" + (layerFilter === l ? " active" : "")}
              onClick={() => setLayerFilter(layerFilter === l ? "" : l)}
            >
              {l}
            </button>
          ))}
        </div>
      )}

      {loaded && rows.length === 0 && (
        <p className="cat-empty">
          The catalog is empty. Publish a model with <code>mdl catalog publish</code>.
        </p>
      )}

      {openError && <p className="cat-openerr">{openError}</p>}

      <div className="cat-list">
        {shown.map((r) => (
          <div
            className={"cat-card cat-card-open" + (opening === r.model ? " opening" : "")}
            key={r.model + (r.commit ?? "")}
            role="button"
            tabIndex={0}
            title="Open the LDM canvas for this model"
            onClick={() => opening === null && openModel(r)}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && opening === null) {
                e.preventDefault();
                openModel(r);
              }
            }}
          >
            <div className="cat-card-head">
              <span className="cat-model">{r.model}</span>
              {r.commit && <code className="cat-commit">{r.commit.slice(0, 8)}</code>}
              <span className="cat-open-hint">
                {opening === r.model ? "opening…" : "open canvas →"}
              </span>
            </div>
            <div className="cat-meta">
              {r.ontology_layers.map((l) => (
                <span className="cat-layer" key={l}>
                  {l}
                </span>
              ))}
              {r.ontology_layers.length === 0 && (
                <span className="cat-nolayer">no ontology layers</span>
              )}
            </div>
            <div className="cat-foot">
              {r.published_at && (
                <span className="cat-when">published {r.published_at.slice(0, 10)}</span>
              )}
              {r.source_link ? (
                <a
                  className="cat-link"
                  href={r.source_link}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  source repo ↗
                </a>
              ) : (
                r.remote && <span className="cat-remote">{r.remote}</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
