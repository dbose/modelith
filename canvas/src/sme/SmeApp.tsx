import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchGlossary, fetchGlossaryConfig, fetchModel } from "../api";
import type { GlossaryConfig, GlossaryDoc } from "../types";
import { ProposeDialog } from "./ProposeDialog";
import { TermCard } from "./TermCard";
import { TermEditor } from "./TermEditor";

/** A pending change the SME has made in the UI but not yet proposed. Each is a
 * `set_definition` / `set_stewardship` / … command + a human-readable before/after
 * for the review dialog. Nothing is written to git until "Submit for review". */
export interface PendingChange {
  op: string;
  payload: Record<string, unknown>;
  label: string; // e.g. "Definition"
  before: string;
  after: string;
}

/** The SME glossary app: git-native, narrow surface, propose-as-PR.
 * No ERD, no cardinality, no keys — meaning only (collaboration model §5.1). */
export function SmeApp() {
  const [doc, setDoc] = useState<GlossaryDoc | null>(null);
  const [readOnly, setReadOnly] = useState(true);
  const [gcfg, setGcfg] = useState<GlossaryConfig | null>(null);
  const [projectName, setProjectName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [subjectArea, setSubjectArea] = useState<string>("");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState<PendingChange[]>([]);
  const [proposeOpen, setProposeOpen] = useState(false);

  const load = useCallback(() => {
    fetchGlossary({ subject_area: subjectArea, q: query })
      .then(setDoc)
      .catch((e) => setError(String(e)));
  }, [subjectArea, query]);

  useEffect(load, [load]);
  useEffect(() => {
    // read_only + project name come from /api/model; the source-of-truth switch
    // (which meaning-fields the catalog masters) comes from /api/glossary/config.
    fetchModel()
      .then((m) => {
        setReadOnly(m.read_only);
        setProjectName(m.project.name);
      })
      .catch(() => undefined);
    fetchGlossaryConfig()
      .then(setGcfg)
      .catch(() => undefined);
  }, []);

  const selected = useMemo(
    () => doc?.terms.find((t) => t.id === selectedId) ?? null,
    [doc, selectedId],
  );

  const stageChange = useCallback((c: PendingChange) => {
    setPending((prev) => {
      // collapse repeated edits to the same field into one
      const rest = prev.filter((p) => !(p.op === c.op && p.label === c.label));
      return [...rest, c];
    });
  }, []);

  const dropChange = (idx: number) => setPending((prev) => prev.filter((_, i) => i !== idx));

  const onProposed = () => {
    setPending([]);
    setProposeOpen(false);
    setEditing(false);
    load();
  };

  if (error) {
    return (
      <div className="sme-splash error">
        <h1>◮ Glossary</h1>
        <pre>{error}</pre>
        <p>
          Is <code>mdl glossary</code> pointed at a model?
        </p>
      </div>
    );
  }
  if (!doc) return <div className="sme-splash">◮ loading glossary…</div>;

  const canEdit = !readOnly;
  const catalogOwned = gcfg?.catalog_owned_fields ?? [];
  const catalogMasters = catalogOwned.length > 0;

  return (
    <div className="sme">
      <header className="sme-top">
        <div className="sme-brand">
          <span className="sme-logo">◮</span>
          <span>Glossary</span>
          <span className="sme-project">{projectName}</span>
          {readOnly && <span className="sme-chip">read-only</span>}
          {catalogMasters && (
            <span className="sme-chip catalog" title={`Definitions are mastered in ${gcfg?.catalog_name}`}>
              mirrors {gcfg?.catalog_name}
            </span>
          )}
        </div>
        <input
          className="sme-search"
          type="search"
          placeholder="Search terms & definitions…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {pending.length > 0 && (
          <button className="sme-tray" onClick={() => setProposeOpen(true)}>
            {pending.length} change{pending.length > 1 ? "s" : ""} · Submit for review →
          </button>
        )}
      </header>

      <div className="sme-body">
        <nav className="sme-nav">
          <button
            className={"sme-sa" + (subjectArea === "" ? " active" : "")}
            onClick={() => setSubjectArea("")}
          >
            All subject areas
          </button>
          {doc.subject_areas.map((sa) => (
            <button
              key={sa.id}
              className={"sme-sa" + (subjectArea === sa.id ? " active" : "")}
              onClick={() => setSubjectArea(sa.id)}
              title={sa.definition ?? undefined}
            >
              {sa.name}
            </button>
          ))}
        </nav>

        <ul className="sme-list">
          {doc.terms.map((t) => (
            <li
              key={t.id}
              className={"sme-list-item" + (t.id === selectedId ? " active" : "")}
              onClick={() => {
                setSelectedId(t.id);
                setEditing(false);
              }}
            >
              <span className="sme-term-name">{t.name}</span>
              {t.kind === "term" && <span className="sme-kind">term</span>}
              {t.ontology?.status === "proposed" && (
                <span className="sme-badge proposed" title="alignment awaiting architect">
                  proposed
                </span>
              )}
              <span className="sme-term-def">{t.definition ?? "no definition yet"}</span>
            </li>
          ))}
          {doc.terms.length === 0 && <li className="sme-empty">no terms match</li>}
        </ul>

        <section className="sme-detail">
          {selected ? (
            editing && canEdit ? (
              <TermEditor
                term={selected}
                pending={pending}
                onStage={stageChange}
                onDone={() => setEditing(false)}
                catalogOwned={catalogOwned}
                catalog={gcfg ? { name: gcfg.catalog_name, url: gcfg.catalog_url } : null}
              />
            ) : (
              <TermCard
                term={selected}
                canEdit={canEdit}
                pendingCount={pending.length}
                onEdit={() => setEditing(true)}
              />
            )
          ) : (
            <div className="sme-placeholder">Select a term to see its definition.</div>
          )}
        </section>
      </div>

      {proposeOpen && (
        <ProposeDialog
          changes={pending}
          onDrop={dropChange}
          onClose={() => setProposeOpen(false)}
          onProposed={onProposed}
        />
      )}
    </div>
  );
}
