import { useEffect, useMemo, useState } from "react";
import { ontologySearch } from "./api";
import type { Entity, ModelDoc, TermCard } from "./types";

type Exec = (op: string, payload: Record<string, unknown>) => Promise<unknown>;

function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={"modal" + (wide ? " wide" : "")} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose}>
            {"✕"}
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

const PREDICATES = [
  "skos:exactMatch",
  "skos:closeMatch",
  "skos:broadMatch",
  "skos:narrowMatch",
  "skos:relatedMatch",
];
const LAYERS = ["industry", "core", "domain", "specialised"];

/** The headline flow: search loaded vocabularies, pick a term, choose predicate
 * + layer → `set_alignment` command → YAML updated, validation refreshed. */
export function AlignModal({
  entity,
  exec,
  onClose,
}: {
  entity: Entity;
  exec: Exec;
  onClose: () => void;
}) {
  const [q, setQ] = useState(entity.conceptual?.name ?? entity.name);
  const [results, setResults] = useState<TermCard[]>([]);
  const [loaded, setLoaded] = useState<number | null>(null);
  const [picked, setPicked] = useState<TermCard | null>(null);
  const [predicate, setPredicate] = useState("skos:exactMatch");
  const [layer, setLayer] = useState(entity.conceptual?.ontology?.layer ?? "core");

  useEffect(() => {
    const t = setTimeout(() => {
      if (!q.trim()) return setResults([]);
      ontologySearch(q)
        .then((d) => {
          setResults(d.results);
          setLoaded(d.loaded_terms);
        })
        .catch(() => setResults([]));
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  const apply = () => {
    if (!picked) return;
    exec("set_alignment", {
      id: entity.id,
      aligns_to: picked.prefixed,
      alignment: predicate,
      layer,
    }).then(onClose);
  };

  return (
    <Modal title={`Align '${entity.name}' to ontology`} onClose={onClose} wide>
      <input
        autoFocus
        className="edit-input modal-search"
        placeholder="Search vocabulary terms…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {loaded === 0 && (
        <p className="empty-hint">
          No vocabulary loaded. Declare one in mdl-project.yaml's ontology_stack (or run
          `mdl ontology vendor fibo`).
        </p>
      )}
      <div className="term-results">
        {results.map((t) => (
          <button
            key={t.iri}
            className={"term-card" + (picked?.iri === t.iri ? " picked" : "")}
            onClick={() => setPicked(t)}
          >
            <div className="term-head">
              <span className="term-label">{t.label}</span>
              <span className="term-source">{t.source}</span>
            </div>
            <code className="term-iri">{t.prefixed}</code>
            {t.definition && <p className="term-def">{t.definition}</p>}
          </button>
        ))}
        {q && results.length === 0 && loaded !== 0 && (
          <p className="empty-hint">no matches for “{q}”</p>
        )}
      </div>
      <div className="modal-footer">
        <label>
          predicate
          <select value={predicate} onChange={(e) => setPredicate(e.target.value)}>
            {PREDICATES.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label>
          layer
          <select value={layer} onChange={(e) => setLayer(e.target.value)}>
            {LAYERS.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </label>
        <button className="primary-btn" disabled={!picked} onClick={apply}>
          Align{picked ? ` to ${picked.label}` : ""}
        </button>
      </div>
    </Modal>
  );
}

export function NewEntityModal({
  doc,
  exec,
  onClose,
  onCreated,
}: {
  doc: ModelDoc;
  exec: Exec;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState("");
  const [subjectArea, setSubjectArea] = useState("");
  const [layer, setLayer] = useState("");

  const create = () => {
    if (!name.trim()) return;
    exec("create_entity", {
      name,
      definition: definition || null,
      subject_area: subjectArea || null,
      layer: layer || null,
    }).then((r) => {
      const id = (r as { created_id?: string })?.created_id;
      onClose();
      if (id) onCreated(id);
    });
  };

  return (
    <Modal title="New entity" onClose={onClose}>
      <label className="form-row">
        name
        <input
          autoFocus
          className="edit-input"
          placeholder="e.g. custody_account"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
      </label>
      <label className="form-row">
        definition
        <textarea
          className="edit-input"
          rows={2}
          placeholder="business definition…"
          value={definition}
          onChange={(e) => setDefinition(e.target.value)}
        />
      </label>
      <label className="form-row">
        subject area
        <select value={subjectArea} onChange={(e) => setSubjectArea(e.target.value)}>
          <option value="">none</option>
          {doc.subject_areas.map((sa) => (
            <option key={sa.id} value={sa.id}>
              {sa.name}
            </option>
          ))}
        </select>
      </label>
      <label className="form-row">
        ontology layer
        <select value={layer} onChange={(e) => setLayer(e.target.value)}>
          <option value="">none</option>
          {LAYERS.map((l) => (
            <option key={l}>{l}</option>
          ))}
        </select>
      </label>
      <div className="modal-footer">
        <button className="primary-btn" disabled={!name.trim()} onClick={create}>
          Create
        </button>
      </div>
    </Modal>
  );
}

const CARDS = ["many_to_one", "one_to_many", "one_to_one", "many_to_many"];

/** Opened by drag-connecting two entity nodes on the canvas. */
export function RelModal({
  doc,
  fromId,
  toId,
  exec,
  onClose,
}: {
  doc: ModelDoc;
  fromId: string;
  toId: string;
  exec: Exec;
  onClose: () => void;
}) {
  const from = doc.entities.find((e) => e.id === fromId);
  const to = doc.entities.find((e) => e.id === toId);
  const toBk = useMemo(
    () => to?.attributes.find((a) => a.role === "business_key"),
    [to],
  );
  const [fromAttr, setFromAttr] = useState("");
  const [toAttr, setToAttr] = useState(toBk?.id ?? "");
  const [cardinality, setCardinality] = useState("many_to_one");
  const [optionality, setOptionality] = useState("mandatory");
  if (!from || !to) return null;

  // "__new__" = mint the FK column on the source in the same gesture
  const createFk = fromAttr === "__new__";
  const create = () =>
    exec("create_relationship", {
      from_entity: from.id,
      to_entity: to.id,
      from_attribute: createFk ? null : fromAttr || null,
      to_attribute: toAttr || null,
      create_from_fk: createFk,
      cardinality,
      optionality,
    }).then(onClose);

  return (
    <Modal title={`${from.name} → ${to.name}`} onClose={onClose}>
      <label className="form-row">
        cardinality ({from.name} : {to.name})
        <select value={cardinality} onChange={(e) => setCardinality(e.target.value)}>
          {CARDS.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </label>
      <label className="form-row">
        foreign key on {from.name}
        <select value={fromAttr} onChange={(e) => setFromAttr(e.target.value)}>
          <option value="">(none)</option>
          <option value="__new__">
            ➕ create column{toBk ? ` "${toBk.name}"` : ""}
          </option>
          {from.attributes.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <label className="form-row">
        references on {to.name}
        <select value={toAttr} onChange={(e) => setToAttr(e.target.value)}>
          <option value="">(none)</option>
          {to.attributes.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
              {a.role === "business_key" ? " 🔑" : ""}
            </option>
          ))}
        </select>
      </label>
      <label className="form-row">
        optionality
        <select value={optionality} onChange={(e) => setOptionality(e.target.value)}>
          <option>mandatory</option>
          <option>optional</option>
        </select>
      </label>
      <div className="modal-footer">
        <button className="primary-btn" onClick={create}>
          Create relationship
        </button>
      </div>
    </Modal>
  );
}
