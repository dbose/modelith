import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchGlossary, fetchGlossaryConfig, fetchModel } from "../api";
import { useStaging } from "../staging/useStaging";
import type { ClassificationDoc, GlossaryConfig, GlossaryDoc, ModelDoc } from "../types";
import { GitBanner } from "./GitBanner";
import { ModelWorkspace } from "./ModelWorkspace";
import { ProposalsList } from "./ProposalsList";
import { ProposeDialog } from "./ProposeDialog";
import { ReviewScreen } from "./ReviewScreen";
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
  // browse | review | proposals. Written to location.hash so a view is linkable
  // (app.py serves sme.html for `sme` and any `sme/...`, so no server change).
  const [view, setView] = useState<"browse" | "model" | "review" | "proposals">(
    window.location.hash === "#proposals" ? "proposals" : "browse",
  );
  // objects the SME has unticked in the review screen (selective proposal)
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [user, setUser] = useState(() => localStorage.getItem("mdl.sme.user") ?? "");
  const [modelDoc, setModelDoc] = useState<ModelDoc | null>(null);
  const [routeAdvice, setRouteAdvice] = useState<ClassificationDoc | null>(null);

  // Selective proposal is only safe when no staged op CREATES something: a
  // create_term followed by a set_definition on it are dependent, and unticking
  // the first would apply a broken subset. Cheap and honest to disable it.
  // Turn a staged op into the sentence the review list shows. The staging hook is
  // deliberately model-agnostic, so this lives here where `modelDoc` is in scope.
  const describe = useCallback(
    (op: string, payload: Record<string, unknown>) => {
      const named = (id: unknown) => {
        const e = modelDoc?.entities.find(
          (x) => x.id === id || x.conceptual?.id === id,
        );
        return e?.conceptual?.name ?? e?.name ?? "object";
      };
      const target = named(payload.id ?? payload.entity_id);
      switch (op) {
        case "set_definition":
          return { label: `Definition: ${target}`, before: "", after: String(payload.definition ?? "") };
        case "add_attribute":
          return { label: `Attribute added to ${target}`, before: "—", after: String(payload.name ?? "") };
        case "delete_attribute":
          return { label: `Attribute removed from ${target}`, before: "", after: "—" };
        case "update_attribute":
          return { label: `Attribute changed on ${target}`, before: "", after: JSON.stringify(payload) };
        case "rename_entity":
          return { label: `Renamed ${target}`, before: target, after: String(payload.name ?? "") };
        case "set_subject_area":
          return { label: `Subject area: ${target}`, before: "", after: String(payload.subject_area ?? "none") };
        case "set_stewardship":
          return { label: `Stewardship: ${target}`, before: "", after: String(payload.steward ?? "") };
        case "set_alignment":
          return { label: `Alignment: ${target}`, before: "", after: String(payload.aligns_to ?? "") };
        default:
          return { label: `${op.replace(/_/g, " ")}: ${target}`, before: "", after: "" };
      }
    },
    [modelDoc],
  );

  const staging = useStaging({ subjectArea, describe });

  // The glossary tray and the model tray are one proposal: a definition edited on
  // the Terms tab and an attribute added on the Model tab belong in the same PR.
  const allPending = useMemo(
    () => [...pending, ...staging.pending],
    [pending, staging.pending],
  );

  // Selective proposal is only safe when no staged op CREATES something: a
  // create followed by an edit of the created object are dependent, and unticking
  // the first would apply a broken subset.
  const selectable = !allPending.some((c) => c.op.startsWith("create_"));

  const goto = useCallback((v: "browse" | "model" | "review" | "proposals") => {
    setView(v);
    window.location.hash = v === "browse" ? "" : `#${v}`;
  }, []);

  // keep the view in step with the hash, so a deep link and the back button work
  useEffect(() => {
    const onHash = () => {
      const h = window.location.hash;
      setView(
        h === "#proposals"
          ? "proposals"
          : h === "#review"
            ? "review"
            : h === "#model"
              ? "model"
              : "browse",
      );
    };
    onHash();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const load = useCallback(() => {
    fetchGlossary({ subject_area: subjectArea, q: query })
      .then(setDoc)
      .catch((e) => setError(String(e)));
  }, [subjectArea, query]);

  useEffect(load, [load]);
  useEffect(() => {
    // read_only + project name come from /api/model; the source-of-truth switch
    // (which meaning-fields the catalog masters) comes from /api/glossary/config.
    fetchModel(subjectArea || undefined)
      .then((m) => {
        setReadOnly(m.read_only);
        setProjectName(m.project.name);
        setModelDoc(m);
      })
      .catch(() => undefined);
    fetchGlossaryConfig()
      .then(setGcfg)
      .catch(() => undefined);
  }, [subjectArea]);

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
        <nav className="sme-tabs">
          <button
            className={"sme-tab" + (view === "browse" ? " active" : "")}
            onClick={() => goto("browse")}
          >
            Terms
          </button>
          <button
            className={"sme-tab" + (view === "model" ? " active" : "")}
            onClick={() => goto("model")}
          >
            Model
          </button>
          <button
            className={"sme-tab" + (view === "proposals" ? " active" : "")}
            onClick={() => goto("proposals")}
          >
            My proposals
          </button>
        </nav>
        {allPending.length > 0 && (
          <button className="sme-tray" onClick={() => goto("review")}>
            {allPending.length} change{allPending.length > 1 ? "s" : ""} · Review →
          </button>
        )}
      </header>
      {view === "browse" && <GitBanner user={user} />}

      {view === "review" ? (
        <ReviewScreen
          stagedDiff={staging.previewDiff}
          user={user}
          selectable={selectable}
          excluded={excluded}
          onToggleObject={(u) =>
            setExcluded((prev) => {
              const next = new Set(prev);
              if (next.has(u)) next.delete(u);
              else next.add(u);
              return next;
            })
          }
          onBack={() => goto("browse")}
          onSubmit={(cl) => {
            setRouteAdvice(cl);
            setProposeOpen(true);
          }}
        />
      ) : view === "model" ? (
        // The previewed model when anything is staged, so an edit is visible
        // immediately; the model on disk otherwise.
        (staging.previewDoc ?? modelDoc) ? (
          <ModelWorkspace
            doc={(staging.previewDoc ?? modelDoc)!}
            subjectArea={subjectArea}
            onSubjectArea={setSubjectArea}
            exec={staging.exec}
            canEdit={canEdit}
            busy={staging.busy}
          />
        ) : (
          <div className="sme-splash">◮ loading the model…</div>
        )
      ) : view === "proposals" ? (
        <ProposalsList user={user} onView={() => goto("review")} />
      ) : (
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
      )}

      {proposeOpen && (
        <ProposeDialog
          changes={allPending}
          routeAdvice={routeAdvice}
          user={user}
          onUser={(u) => {
            setUser(u);
            localStorage.setItem("mdl.sme.user", u);
          }}
          onDrop={dropChange}
          onClose={() => setProposeOpen(false)}
          onProposed={() => {
            onProposed();
            staging.clear();
            setExcluded(new Set());
            goto("proposals");
          }}
        />
      )}
    </div>
  );
}
