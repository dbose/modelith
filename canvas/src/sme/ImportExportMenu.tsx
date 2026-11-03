import { useEffect, useRef, useState } from "react";
import {
  type ExportFormat,
  type ImportPreviewDoc,
  exportErwinWithTemplate,
  exportUrl,
  fetchExportFormats,
  importModel,
  importPreview,
} from "../api";
import type { Exec } from "../exec";
import "./ImportExportMenu.css";

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
  submitLabel = "Import & review",
  buttonClass = "erd-action ghost",
  applyBatch,
  openImport = false,
  prefillImport = "",
  onPreview,
  scopeSubjectArea = null,
}: {
  exec: Exec;
  canEdit: boolean;
  /** called after a successful import so the shell can jump to the review screen */
  onImported?: (tables: number) => void;
  /** when set, an erwin import is reviewed before it lands (see ImportPanel.onPreview) */
  onPreview?: (doc: ImportPreviewDoc) => void;
  /** pre-fill the import content (a host-stashed .xml) */
  prefillImport?: string;
  /** the import button's label — "Import & review" in the staged (SME) app, plain
   *  "Import" in the direct-write architect canvas where there is no review screen */
  submitLabel?: string;
  /** the host toolbar's button class, so the trigger buttons match their toolbar
   *  (erd-action ghost in Studio, tool-btn in the architect canvas) */
  buttonClass?: string;
  /** apply the whole parsed change list at once. The direct-write canvas passes this
   *  to avoid the per-command fingerprint race; when absent, changes go one at a time
   *  through the staging exec, which is already batch-safe. */
  applyBatch?: (changes: { op: string; payload: Record<string, unknown> }[]) => Promise<unknown>;
  /** pop the Import panel open on mount — the VS Code "Import to Model" command
   *  reveals the canvas with `?import=1`, so the same wizard the toolbar button
   *  opens is already up when the panel appears. */
  openImport?: boolean;
  /** when a single subject area is filtered on the canvas, the erwin export is scoped to it
   *  (its id); null = export the whole model. Other formats ignore it. */
  scopeSubjectArea?: string | null;
}) {
  const [open, setOpen] = useState<"export" | "import" | null>(
    openImport && canEdit ? "import" : null,
  );
  const [formats, setFormats] = useState<ExportFormat[]>([]);
  const [dialect, setDialect] = useState("postgres");
  const [erwinBusy, setErwinBusy] = useState(false);
  const [erwinError, setErwinError] = useState<string | null>(null);
  // true when the server remembers the erwin file this model was imported from: the plain erwin
  // export then auto-clones it (erwin-ready) and the picker below just overrides it.
  const [erwinReady, setErwinReady] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const templateInputRef = useRef<HTMLInputElement | null>(null);

  // erwin's "erwin-ready" export clones a native erwin export (the mart) as a template, so it opens
  // in real erwin Data Modeler. The user picks that mart file; we POST it and download the result.
  const onTemplatePicked = async (file: File | null) => {
    if (!file) return;
    setErwinBusy(true);
    setErwinError(null);
    try {
      await exportErwinWithTemplate(file, scopeSubjectArea);
      setOpen(null);
    } catch (e) {
      setErwinError(e instanceof Error ? e.message : String(e));
    } finally {
      setErwinBusy(false);
      if (templateInputRef.current) templateInputRef.current.value = "";
    }
  };

  useEffect(() => {
    fetchExportFormats()
      .then((d) => {
        setFormats(d.formats);
        setErwinReady(!!d.erwinTemplateRemembered);
      })
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
        className={buttonClass + (open === "export" ? " on" : "")}
        onClick={() => setOpen(open === "export" ? null : "export")}
      >
        <span className="erd-ico" aria-hidden="true">
          ↧
        </span>
        Export <span className="erd-caret">▾</span>
      </button>
      {canEdit && (
        <button
          className={buttonClass + (open === "import" ? " on" : "")}
          onClick={() => setOpen(open === "import" ? null : "import")}
        >
          <span className="erd-ico" aria-hidden="true">
            ↥
          </span>
          Import <span className="erd-caret">▾</span>
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
          {formats.map((f) => {
            // erwin and SQL DDL export honour a single active subject-area filter (export just
            // that slice, like erwin's own subject-area export); every other format always
            // exports the whole model.
            const scoped = f.id === "erwin" || f.id === "sql" ? scopeSubjectArea : null;
            return (
              <a
                key={f.id}
                className="ie-item"
                href={exportUrl(f.id, f.id === "sql" ? dialect : undefined, scoped)}
                // download attribute lets the browser save rather than navigate; the
                // server also sets Content-Disposition: attachment as a belt-and-braces.
                download
                onClick={() => setOpen(null)}
              >
                {f.label}
                {/* when the model was imported from erwin, the plain erwin export auto-clones that
                    remembered file, so it already opens in real erwin. */}
                {f.id === "erwin" && erwinReady && (
                  <span className="ie-scope"> · erwin-ready</span>
                )}
                {scoped && <span className="ie-scope"> · filtered area only</span>}
              </a>
            );
          })}
          {/* Clone a native erwin export so the file opens in real erwin Data Modeler. When a
              template is already remembered (erwinReady) the plain "erwin XML" item above
              auto-clones it, and this picker just OVERRIDES it with a different mart. Otherwise it
              is the only way to get an erwin-ready file (the plain item is the self-built envelope,
              which round-trips through Modelith but is not guaranteed to open in erwin). */}
          {formats.some((f) => f.id === "erwin") && (
            <button
              type="button"
              className="ie-item ie-item-btn"
              disabled={erwinBusy}
              onClick={() => templateInputRef.current?.click()}
              title="Pick a native erwin export of the target mart to clone its settings from"
            >
              {erwinBusy
                ? "Building…"
                : erwinReady
                  ? "erwin XML — clone a different mart…"
                  : "erwin XML (erwin-ready) — pick mart…"}
              {scopeSubjectArea && <span className="ie-scope"> · filtered area only</span>}
            </button>
          )}
          <input
            ref={templateInputRef}
            type="file"
            accept=".xml"
            style={{ display: "none" }}
            onChange={(e) => onTemplatePicked(e.target.files?.[0] ?? null)}
          />
          {erwinError && <p className="ie-error">{erwinError}</p>}
        </div>
      )}

      {open === "import" && (
        <ImportPanel
          exec={exec}
          submitLabel={submitLabel}
          buttonClass={buttonClass}
          applyBatch={applyBatch}
          onClose={() => setOpen(null)}
          onImported={(n) => {
            setOpen(null);
            onImported?.(n);
          }}
          onPreview={onPreview}
          prefillImport={prefillImport}
        />
      )}
    </div>
  );
}

const IMPORT_FORMATS = [
  { id: "sql", label: "SQL DDL", hint: "CREATE TABLE … PRIMARY KEY / FOREIGN KEY" },
  { id: "mermaid", label: "Mermaid erDiagram", hint: "structural: entities, attributes, relationships" },
  { id: "json-schema", label: "JSON Schema", hint: "each object definition becomes an entity" },
  { id: "erwin", label: "erwin XML", hint: "entities, keys, relationships, subject areas, subtypes" },
];

function ImportPanel({
  exec,
  submitLabel,
  buttonClass,
  applyBatch,
  onClose,
  onImported,
  onPreview,
  prefillImport,
}: {
  exec: Exec;
  submitLabel: string;
  buttonClass: string;
  applyBatch?: (changes: { op: string; payload: Record<string, unknown> }[]) => Promise<unknown>;
  onClose: () => void;
  onImported: (tables: number) => void;
  /** When set, an erwin import is sent for PRE-WRITE REVIEW instead of being applied: the
   *  parsed semantic diff is handed up so the host mounts the review screen with an "Apply
   *  import" action. Absent = apply immediately (today's behaviour / direct-write canvas). */
  onPreview?: (doc: ImportPreviewDoc) => void;
  prefillImport?: string;
}) {
  // When opened for pre-write review (onPreview wired), default to erwin — the only format the
  // review path supports today — so the VS Code "Review Erwin import" flow lands ready to paste.
  const [format, setFormat] = useState(onPreview ? "erwin" : "sql");
  const [dialect, setDialect] = useState("postgres");
  const [content, setContent] = useState(prefillImport ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  // Host-stashed content (a VS Code .xml) arrives after mount; fill the panel when it does.
  useEffect(() => {
    if (prefillImport) setContent(prefillImport);
  }, [prefillImport]);

  const onFile = (f: File | null) => {
    if (!f) return;
    f.text().then(setContent).catch(() => setError("could not read that file"));
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    setWarnings([]);
    try {
      // Pre-write review: for an erwin import in the SME app, fetch the semantic diff and hand
      // it up for review BEFORE anything is written; the review's "Apply import" action does
      // the write. Other formats / the direct-write canvas keep the apply-now path below.
      if (onPreview && format === "erwin") {
        const doc = await importPreview(format, content);
        if (!doc.changes.length) {
          setError(doc.warnings[0] ?? "nothing to import from that document");
          setBusy(false);
          return;
        }
        onClose();
        onPreview(doc);
        setBusy(false);
        return;
      }
      const res = await importModel(format, content, format === "sql" ? dialect : undefined);
      if (!res.changes.length) {
        setError(res.warnings[0] ?? "nothing to import from that document");
        setBusy(false);
        return;
      }
      // Apply the parsed change list. The client mints no ids — the server already
      // did — so preview/propose (staging) or a direct batch replay them intact.
      // A dedicated batch applier avoids the per-op fingerprint race in the
      // direct-write canvas; without one, the staging exec batches safely anyway.
      if (applyBatch) {
        await applyBatch(res.changes);
      } else {
        for (const c of res.changes) {
          await exec(c.op, c.payload as Record<string, unknown>);
        }
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
        accept=".sql,.mmd,.md,.json,.xml,.txt"
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
        <button className={buttonClass} onClick={onClose}>
          Cancel
        </button>
        <button className={buttonClass} disabled={busy || !content.trim()} onClick={run}>
          {busy ? "Importing…" : submitLabel}
        </button>
      </div>
    </div>
  );
}
