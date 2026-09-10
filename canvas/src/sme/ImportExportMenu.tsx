import { useEffect, useRef, useState } from "react";
import { type ExportFormat, exportUrl, fetchExportFormats, importModel } from "../api";
import type { Exec } from "../exec";

/** Model-tab Import / Export (issue: interchange with a wide toolset).
 *
 * Export hands the browser a download of the model as SQL DDL, Mermaid, DBML, CSV,
 * an ODCS contract, or Cypher. Import parses SQL DDL / Mermaid / JSON Schema on the
 * server into a change list, which is STAGED through the same exec seam as any edit,
 * so it previews on the canvas and lands as one proposal. */
export function ImportExportMenu({
  exec,
  canEdit,
  onImported,
}: {
  exec: Exec;
  canEdit: boolean;
  /** called after a successful import so the shell can jump to the review screen */
  onImported?: (tables: number) => void;
}) {
  const [open, setOpen] = useState<"export" | "import" | null>(null);
  const [formats, setFormats] = useState<ExportFormat[]>([]);
  const [dialect, setDialect] = useState("postgres");
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    fetchExportFormats()
      .then((d) => setFormats(d.formats))
      .catch(() => setFormats([]));
  }, []);

  // close the dropdown on an outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(null);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const sqlFmt = formats.find((f) => f.id === "sql");

  return (
    <div className="ie-wrap" ref={wrapRef}>
      <button
        className={"erd-action ghost" + (open === "export" ? " on" : "")}
        onClick={() => setOpen(open === "export" ? null : "export")}
      >
        Export ▾
      </button>
      {canEdit && (
        <button
          className={"erd-action ghost" + (open === "import" ? " on" : "")}
          onClick={() => setOpen(open === "import" ? null : "import")}
        >
          Import ▾
        </button>
      )}

      {open === "export" && (
        <div className="ie-menu" role="menu">
          {sqlFmt?.dialects && (
            <label className="ie-dialect">
              <span>SQL dialect</span>
              <select value={dialect} onChange={(e) => setDialect(e.target.value)}>
                {sqlFmt.dialects.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
          )}
          {formats.map((f) => (
            <a
              key={f.id}
              className="ie-item"
              href={exportUrl(f.id, f.id === "sql" ? dialect : undefined)}
              // download attribute lets the browser save rather than navigate; the
              // server also sets Content-Disposition: attachment as a belt-and-braces.
              download
              onClick={() => setOpen(null)}
            >
              {f.label}
            </a>
          ))}
        </div>
      )}

      {open === "import" && (
        <ImportPanel
          exec={exec}
          onClose={() => setOpen(null)}
          onImported={(n) => {
            setOpen(null);
            onImported?.(n);
          }}
        />
      )}
    </div>
  );
}

const IMPORT_FORMATS = [
  { id: "sql", label: "SQL DDL", hint: "CREATE TABLE … PRIMARY KEY / FOREIGN KEY" },
  { id: "mermaid", label: "Mermaid erDiagram", hint: "structural: entities, attributes, relationships" },
  { id: "json-schema", label: "JSON Schema", hint: "each object definition becomes an entity" },
];

function ImportPanel({
  exec,
  onClose,
  onImported,
}: {
  exec: Exec;
  onClose: () => void;
  onImported: (tables: number) => void;
}) {
  const [format, setFormat] = useState("sql");
  const [dialect, setDialect] = useState("postgres");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const onFile = (f: File | null) => {
    if (!f) return;
    f.text().then(setContent).catch(() => setError("could not read that file"));
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    setWarnings([]);
    try {
      const res = await importModel(format, content, format === "sql" ? dialect : undefined);
      if (!res.changes.length) {
        setError(res.warnings[0] ?? "nothing to import from that document");
        setBusy(false);
        return;
      }
      // Stage every parsed change through the exec seam. The client mints no ids —
      // the server already did — so preview and propose replay them intact.
      for (const c of res.changes) {
        await exec(c.op, c.payload as Record<string, unknown>);
      }
      setWarnings(res.warnings);
      onImported(res.tables);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ie-menu ie-import" role="dialog">
      <div className="ie-tabs">
        {IMPORT_FORMATS.map((f) => (
          <button
            key={f.id}
            className={"ie-tab" + (format === f.id ? " active" : "")}
            onClick={() => setFormat(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>
      <p className="ie-hint">{IMPORT_FORMATS.find((f) => f.id === format)?.hint}</p>
      {format === "sql" && (
        <label className="ie-dialect">
          <span>dialect</span>
          <select value={dialect} onChange={(e) => setDialect(e.target.value)}>
            {["postgres", "snowflake", "mysql", "duckdb"].map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
      )}
      <textarea
        className="ie-textarea"
        placeholder={`Paste ${format} here, or choose a file…`}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={8}
      />
      <input
        type="file"
        className="ie-file"
        accept=".sql,.mmd,.md,.json,.txt"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
      {format === "mermaid" && (
        <p className="ie-warn">
          Mermaid is a diagram format: types and keys are approximate and relationships
          are inferred from the lines.
        </p>
      )}
      {warnings.map((w, i) => (
        <p key={i} className="ie-warn">
          {w}
        </p>
      ))}
      {error && <p className="ie-error">{error}</p>}
      <div className="ie-foot">
        <button className="erd-action ghost" onClick={onClose}>
          Cancel
        </button>
        <button className="erd-action" disabled={busy || !content.trim()} onClick={run}>
          {busy ? "Importing…" : "Import & review"}
        </button>
      </div>
    </div>
  );
}
