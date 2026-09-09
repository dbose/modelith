import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchGlossary, fetchGlossaryConfig, fetchModel, sendCommand } from "../api";
import {
  collapseKey,
  dependentKeys,
  useStaging,
  type PendingChange,
} from "../staging/useStaging";
import type { ClassificationDoc, GlossaryConfig, GlossaryDoc, ModelDoc } from "../types";
import type { Exec } from "../exec";
import { GitBanner } from "./GitBanner";
import { ModelWorkspace } from "./ModelWorkspace";
import { SubjectAreaEditor } from "./SubjectAreaEditor";
import { ProposalsList } from "./ProposalsList";
import { ProposeDialog } from "./ProposeDialog";
import { ReviewScreen } from "./ReviewScreen";
import { TermCard } from "./TermCard";
import { TermEditor } from "./TermEditor";

/** A pending change the SME has made in the UI but not yet proposed. Each is a
 * `set_definition` / `set_stewardship` / … command + a human-readable before/after
 * for the review dialog. Nothing is written to git until "Submit for review". */
// One definition, shared by the glossary tray and the model tray — they are one
// proposal, so they must be one type.
export type { PendingChange } from "../staging/useStaging";

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
  const [view, setView] = useState<"browse" | "model" | "areas" | "review" | "proposals">(
    window.location.hash === "#proposals" ? "proposals" : "browse",
  );
  // objects the SME has unticked in the review screen (selective proposal)
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [user, setUser] = useState(() => localStorage.getItem("mdl.sme.user") ?? "");
  // Server-established identity (spec §17). When the source is proxy or git, the
  // acting user is known and trustworthy — the propose dialog greets them instead
  // of asking for a name, and the server ignores any name we send anyway.
  const [identity, setIdentity] = useState<ModelDoc["identity"] | null>(null);
  const [modelDoc, setModelDoc] = useState<ModelDoc | null>(null);
  // engineer mode: the server was started with --direct, so edits write the tree
  const [direct, setDirect] = useState(false);
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
        // Creations name themselves: the object is not on disk yet, so `named`
        // cannot find it and would fall back to "object".
        case "create_entity":
          return { label: `New entity: ${payload.name}`, before: "—", after: String(payload.name ?? "") };
        case "create_relationship":
          return {
            label: `New relationship: ${named(payload.from_entity)} → ${named(payload.to_entity)}`,
            before: "—",
            after: String(payload.cardinality ?? "many_to_one"),
          };
        case "delete_entity":
          return { label: `Deleted ${target}`, before: target, after: "—" };
        case "delete_relationship":
          return { label: "Relationship removed", before: "", after: "—" };
        case "update_relationship":
          return {
            label: `Relationship changed`,
            before: "",
            after: [payload.cardinality, payload.optionality, payload.identifying ? "identifying" : ""]
              .filter(Boolean)
              .join(", "),
          };
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
        case "set_subject_area_members": {
          // the editor passes a ready-made summary; it knows the counts
          const custom = payload.__label as string | undefined;
          const n = (payload.members as string[] | undefined)?.length ?? 0;
          return {
            label: custom ?? `Subject area members`,
            before: "",
            after: `${n} object${n === 1 ? "" : "s"}`,
          };
        }
        case "set_alignment":
          return { label: `Alignment: ${target}`, before: "", after: String(payload.aligns_to ?? "") };
        default:
          return { label: `${op.replace(/_/g, " ")}: ${target}`, before: "", after: "" };
      }
    },
    [modelDoc],
  );

  const staging = useStaging({ subjectArea, describe });

  // In direct mode edits go straight to the working tree, the way the architect
  // canvas has always worked — same components, same exec shape, different target.
  const directExec = useCallback<Exec>(
    async (op, payload) => {
      const fp = modelDoc?.fingerprint ?? "";
      const r = await sendCommand(op, payload, fp);
      await fetchModel(subjectArea || undefined).then(setModelDoc);
      return r;
    },
    [modelDoc, subjectArea],
  );

  // The glossary tray and the model tray are one proposal: a definition edited on
  // the Terms tab and an attribute added on the Model tab belong in the same PR.
  // Members staged but not yet on disk, so the picker reflects the tray rather
  // than snapping back to the committed state on every click.
  const stagedMembers = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const c of staging.pending) {
      if (c.op === "set_subject_area_members") {
        out[c.payload.id as string] = (c.payload.members as string[]) ?? [];
      }
    }
    return out;
  }, [staging.pending]);

  const allPending = useMemo(
    () => [...pending, ...staging.pending],
    [pending, staging.pending],
  );

  // Only the changes that actually depend on a staged creation are locked
  // together; everything else can still be proposed selectively.
  const locked = useMemo(() => dependentKeys(allPending), [allPending]);
  const selectable = locked.size < allPending.length;

  const goto = useCallback((v: "browse" | "model" | "areas" | "review" | "proposals") => {
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
              : h === "#areas"
                ? "areas"
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
        setDirect(Boolean(m.direct));
        setIdentity(m.identity ?? null);
        // A trusted identity seeds the display name so "your proposals" and the
        // route advice slug reflect the real user without a manual entry.
        if (m.identity && m.identity.source !== "anonymous" && m.identity.name) {
          setUser(m.identity.name);
        }
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

  const stageChange = useCallback((c: Omit<PendingChange, "key"> & { key?: string }) => {
    // Collapse on the target's ULID, not on op+label. The old rule silently DROPPED
    // an edit: staging a definition change on Trade and then on Counterparty
    // collapsed into one, because both are labelled "Definition".
    const key = c.key ?? collapseKey(c.op, c.payload);
    setPending((prev) => [...prev.filter((p) => p.key !== key), { ...c, key }]);
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
            className={"sme-tab" + (view === "areas" ? " active" : "")}
            onClick={() => goto("areas")}
          >
            Subject areas
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
            exec={direct ? directExec : staging.exec}
            canEdit={canEdit}
            direct={direct}
            busy={staging.busy}
          />
        ) : (
          <div className="sme-splash">◮ loading the model…</div>
        )
      ) : view === "areas" ? (
        <SubjectAreaEditor
          exec={staging.exec}
          canEdit={canEdit}
          stagedMembers={stagedMembers}
        />
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
          identity={identity}
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
