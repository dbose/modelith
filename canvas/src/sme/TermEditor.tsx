import { useState } from "react";
import { ontologySearch } from "../api";
import type { GlossaryTerm, TermCard as _TC } from "../types";
import type { PendingChange } from "./SmeApp";

// The ONLY things an SME may change (collaboration model §5.1): definition,
// synonyms, stewardship, and an alignment *proposal*. Deliberately no cardinality,
// keys, attributes, relationships, or materialisation anywhere in this component.

/** The narrow SME edit surface. Each field change is staged as a semantic
 * command with a before/after; nothing hits git until "Submit for review". */
export function TermEditor({
  term,
  pending,
  onStage,
  onDone,
}: {
  term: GlossaryTerm;
  pending: PendingChange[];
  onStage: (c: PendingChange) => void;
  onDone: () => void;
}) {
  const staged = (label: string) => pending.find((p) => p.label === label);

  const cur = <T,>(label: string, live: T): T => {
    const s = staged(label);
    return s ? (s.after as unknown as T) : live;
  };

  // local editable copies seeded from staged-or-live
  const [definition, setDefinition] = useState(cur("Definition", term.definition ?? ""));
  const [synonyms, setSynonyms] = useState<string[]>(term.synonyms);
  const [synInput, setSynInput] = useState("");
  const [owner, setOwner] = useState(term.stewardship?.owner ?? "");
  const [steward, setSteward] = useState(term.stewardship?.steward ?? "");

  const stageDefinition = () => {
    if (definition.trim() === (term.definition ?? "").trim()) return;
    onStage({
      op: "set_definition",
      payload: { id: term.id, definition: definition.trim() },
      label: "Definition",
      before: term.definition ?? "(none)",
      after: definition.trim() || "(none)",
    });
  };

  const stageStewardship = (nextOwner: string, nextSteward: string) => {
    onStage({
      op: "set_stewardship",
      payload: { id: term.id, owner: nextOwner || null, steward: nextSteward || null },
      label: "Stewardship",
      before: `owner: ${term.stewardship?.owner ?? "—"} · steward: ${term.stewardship?.steward ?? "—"}`,
      after: `owner: ${nextOwner || "—"} · steward: ${nextSteward || "—"}`,
    });
  };

  const commitSynonyms = (next: string[]) => {
    setSynonyms(next);
    onStage({
      op: "update_synonyms",
      payload: { id: term.id, synonyms: next },
      label: "Also called",
      before: term.synonyms.join(", ") || "(none)",
      after: next.join(", ") || "(none)",
    });
  };

  const isTerm = term.kind === "term";

  return (
    <div className="sme-editor">
      <div className="sme-card-head">
        <h1>Suggest an edit — {term.name}</h1>
        <button className="sme-secondary" onClick={onDone}>
          Back
        </button>
      </div>
      <p className="sme-hint">
        You can refine what this term <em>means</em>. Structure (keys,
        relationships, how it's built) stays with the data team.
      </p>

      <label className="sme-field">
        <span>Definition</span>
        <textarea
          rows={4}
          value={definition}
          onChange={(e) => setDefinition(e.target.value)}
          onBlur={stageDefinition}
          placeholder="A plain-language business definition…"
        />
      </label>

      <label className="sme-field">
        <span>Also called (synonyms)</span>
        <div className="sme-syn-row">
          {synonyms.map((s) => (
            <span key={s} className="sme-chip removable" onClick={() => commitSynonyms(synonyms.filter((x) => x !== s))}>
              {s} ✕
            </span>
          ))}
          <input
            value={synInput}
            placeholder="add + Enter"
            onChange={(e) => setSynInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && synInput.trim()) {
                commitSynonyms([...synonyms, synInput.trim()]);
                setSynInput("");
              }
            }}
          />
        </div>
      </label>

      {!isTerm && (
        <div className="sme-field-row">
          <label className="sme-field">
            <span>Owner</span>
            <input value={owner} onChange={(e) => setOwner(e.target.value)} onBlur={() => stageStewardship(owner, steward)} />
          </label>
          <label className="sme-field">
            <span>Steward</span>
            <input value={steward} onChange={(e) => setSteward(e.target.value)} onBlur={() => stageStewardship(owner, steward)} />
          </label>
        </div>
      )}

      <AlignmentProposer term={term} onStage={onStage} />

      <p className="sme-note">
        {pending.length > 0
          ? `${pending.length} change(s) staged — use “Submit for review” at the top when you're done.`
          : "Changes you make are collected until you submit them for review."}
      </p>
    </div>
  );
}

const PREDICATES = ["skos:exactMatch", "skos:closeMatch", "skos:broadMatch", "skos:narrowMatch"];

function AlignmentProposer({
  term,
  onStage,
}: {
  term: GlossaryTerm;
  onStage: (c: PendingChange) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState(term.name);
  const [results, setResults] = useState<_TC[]>([]);
  const [predicate, setPredicate] = useState("skos:closeMatch");

  const search = (v: string) => {
    setQ(v);
    ontologySearch(v)
      .then((d) => setResults(d.results))
      .catch(() => setResults([]));
  };

  if (!open) {
    return (
      <div className="sme-field">
        <span>Standard mapping</span>
        <div>
          {term.ontology?.aligns_to ? (
            <code className="sme-current-align">{term.ontology.aligns_to}</code>
          ) : (
            <span className="sme-muted">not mapped to a standard</span>
          )}
          <button className="sme-link" onClick={() => setOpen(true)}>
            Propose a mapping…
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="sme-field sme-align">
      <span>Propose a standard mapping (a suggestion; the architect confirms)</span>
      <input value={q} onChange={(e) => search(e.target.value)} placeholder="Search standards…" autoFocus />
      <div className="sme-align-results">
        {results.map((r) => (
          <button
            key={r.iri}
            className="sme-align-card"
            onClick={() => {
              onStage({
                op: "set_alignment",
                payload: {
                  id: term.id,
                  aligns_to: r.prefixed,
                  alignment: predicate,
                  status: "proposed",
                },
                label: "Standard mapping",
                before: term.ontology?.aligns_to ?? "(none)",
                after: `${r.prefixed} (${predicate}, proposed)`,
              });
              setOpen(false);
            }}
          >
            <strong>{r.label}</strong> <code>{r.prefixed}</code>
            {r.definition && <span className="sme-align-def">{r.definition}</span>}
          </button>
        ))}
      </div>
      <select value={predicate} onChange={(e) => setPredicate(e.target.value)}>
        {PREDICATES.map((p) => (
          <option key={p}>{p}</option>
        ))}
      </select>
    </div>
  );
}
