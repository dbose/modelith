import { useEffect, useState } from "react";
import type { AttributeRow, Entity, ModelDoc, Relationship } from "./types";

type Exec = (op: string, payload: Record<string, unknown>) => Promise<unknown>;

/** Editable entity inspector (E3). Every field edit becomes a semantic command;
 * the YAML files change, comments survive, ULIDs never move. */
export function Inspector({
  entity,
  doc,
  readOnly,
  exec,
  onClose,
  onFocusEntity,
  onAlign,
}: {
  entity: Entity;
  doc: ModelDoc;
  readOnly: boolean;
  exec: Exec;
  onClose: () => void;
  onFocusEntity: (id: string) => void;
  onAlign: (entity: Entity) => void;
}) {
  const c = entity.conceptual;
  const rels = doc.relationships.filter(
    (r) => r.from.entity === entity.id || r.to.entity === entity.id,
  );
  const entityName = (id: string) => doc.entities.find((e) => e.id === id)?.name ?? id;

  return (
    <aside className="detail-panel">
      <div className="detail-header">
        <div style={{ flex: 1 }}>
          <EditableText
            value={entity.name}
            className="entity-title-input"
            readOnly={readOnly}
            onCommit={(v) => exec("rename_entity", { id: entity.id, name: v })}
          />
          <div>
            <SubjectAreaPicker entity={entity} doc={doc} readOnly={readOnly} exec={exec} />
            <PatternPicker entity={entity} readOnly={readOnly} exec={exec} />
          </div>
        </div>
        <button className="icon-btn" onClick={onClose} title="Close">
          {"✕"}
        </button>
      </div>

      <section>
        <h3>Definition</h3>
        <EditableText
          value={c?.definition?.trim() ?? ""}
          multiline
          placeholder={readOnly ? "—" : "add a business definition…"}
          className="definition-input"
          readOnly={readOnly}
          onCommit={(v) => exec("set_definition", { id: entity.id, definition: v })}
        />
      </section>

      <section>
        <h3>
          Ontology
          {!readOnly && (
            <span className="h3-actions">
              <button className="mini-btn" onClick={() => onAlign(entity)}>
                {c?.ontology?.aligns_to ? "Re-align…" : "Align…"}
              </button>
              {c?.ontology?.aligns_to && (
                <button
                  className="mini-btn danger"
                  onClick={() => exec("clear_alignment", { id: entity.id })}
                >
                  Clear
                </button>
              )}
            </span>
          )}
        </h3>
        {c?.ontology?.aligns_to ? (
          <>
            <div className="kv">
              <span className="k">aligns to</span>
              <code className="v">{c.ontology.aligns_to}</code>
            </div>
            {c.ontology.alignment && (
              <div className="kv">
                <span className="k">predicate</span>
                <code className="v">{c.ontology.alignment}</code>
              </div>
            )}
            {c.ontology.layer && (
              <div className="kv">
                <span className="k">layer</span>
                <span className={`chip layer-${c.ontology.layer}`}>{c.ontology.layer}</span>
              </div>
            )}
          </>
        ) : (
          <p className="empty-hint">not aligned to any ontology term</p>
        )}
      </section>

      <section>
        <h3>Stewardship</h3>
        <div className="kv">
          <span className="k">owner</span>
          <EditableText
            value={c?.stewardship?.owner ?? ""}
            placeholder="—"
            readOnly={readOnly}
            onCommit={(v) =>
              exec("set_stewardship", {
                id: entity.id,
                owner: v,
                steward: c?.stewardship?.steward ?? null,
              })
            }
          />
        </div>
        <div className="kv">
          <span className="k">steward</span>
          <EditableText
            value={c?.stewardship?.steward ?? ""}
            placeholder="—"
            readOnly={readOnly}
            onCommit={(v) =>
              exec("set_stewardship", {
                id: entity.id,
                owner: c?.stewardship?.owner ?? null,
                steward: v,
              })
            }
          />
        </div>
      </section>

      <section>
        <h3>Attributes ({entity.attributes.length})</h3>
        <table className="attr-table">
          <tbody>
            {entity.attributes.map((a) => (
              <AttrRow key={a.id} a={a} entity={entity} doc={doc} readOnly={readOnly} exec={exec} />
            ))}
          </tbody>
        </table>
        {!readOnly && <AddAttribute entity={entity} doc={doc} exec={exec} />}
      </section>

      {rels.length > 0 && (
        <section>
          <h3>Relationships ({rels.length})</h3>
          {rels.map((r: Relationship) => {
            const other = r.from.entity === entity.id ? r.to.entity : r.from.entity;
            return (
              <div key={r.id} className="rel-row">
                <button className="rel-link" onClick={() => onFocusEntity(other)}>
                  <span className="rel-card">{cardinalityGlyph(r, entity.id)}</span>
                  {entityName(other)}
                </button>
                {!readOnly && (
                  <button
                    className="mini-btn danger"
                    title={`delete ${r.name}`}
                    onClick={() => exec("delete_relationship", { id: r.id })}
                  >
                    {"✕"}
                  </button>
                )}
              </div>
            );
          })}
        </section>
      )}

      <section>
        <h3>Identity</h3>
        <code
          className="ulid"
          title="Immutable ULID — click to copy"
          onClick={() => navigator.clipboard?.writeText(entity.id)}
        >
          {entity.id}
        </code>
        {!readOnly && (
          <div style={{ marginTop: 12 }}>
            <button
              className="mini-btn danger"
              onClick={() => {
                if (confirm(`Delete entity '${entity.name}' (and its relationships)?`)) {
                  exec("delete_entity", { id: entity.id, cascade: true }).then(onClose);
                }
              }}
            >
              Delete entity…
            </button>
          </div>
        )}
      </section>
    </aside>
  );
}

// --- widgets -----------------------------------------------------------------

function EditableText({
  value,
  onCommit,
  readOnly,
  multiline = false,
  placeholder = "",
  className = "",
}: {
  value: string;
  onCommit: (v: string) => void;
  readOnly: boolean;
  multiline?: boolean;
  placeholder?: string;
  className?: string;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const commit = () => {
    if (v.trim() !== value.trim()) onCommit(v.trim());
  };
  if (readOnly) {
    return <span className={className}>{value || placeholder}</span>;
  }
  if (multiline) {
    return (
      <textarea
        className={`edit-input ${className}`}
        value={v}
        placeholder={placeholder}
        rows={3}
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
      />
    );
  }
  return (
    <input
      className={`edit-input ${className}`}
      value={v}
      placeholder={placeholder}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

function SubjectAreaPicker({
  entity,
  doc,
  readOnly,
  exec,
}: {
  entity: Entity;
  doc: ModelDoc;
  readOnly: boolean;
  exec: Exec;
}) {
  const cur = entity.conceptual?.subject_area?.id ?? "";
  if (readOnly) {
    return entity.conceptual?.subject_area ? (
      <span className="chip">{entity.conceptual.subject_area.name}</span>
    ) : null;
  }
  return (
    <select
      className="chip-select"
      value={cur}
      onChange={(e) => exec("set_subject_area", { id: entity.id, subject_area: e.target.value || null })}
    >
      <option value="">no subject area</option>
      {doc.subject_areas.map((sa) => (
        <option key={sa.id} value={sa.id}>
          {sa.name}
        </option>
      ))}
    </select>
  );
}

const PATTERNS = ["", "scd2", "hub", "link", "satellite", "bridge"];

function PatternPicker({
  entity,
  readOnly,
  exec,
}: {
  entity: Entity;
  readOnly: boolean;
  exec: Exec;
}) {
  if (readOnly) {
    return entity.pattern ? <span className="chip pattern">{entity.pattern}</span> : null;
  }
  return (
    <select
      className="chip-select"
      value={entity.pattern ?? ""}
      onChange={(e) => exec("set_pattern", { id: entity.id, pattern: e.target.value || null })}
    >
      {PATTERNS.map((p) => (
        <option key={p} value={p}>
          {p || "no pattern"}
        </option>
      ))}
    </select>
  );
}

const ROLES = ["attribute", "business_key", "measure", "surrogate_key"];

function AttrRow({
  a,
  entity,
  doc,
  readOnly,
  exec,
}: {
  a: AttributeRow;
  entity: Entity;
  doc: ModelDoc;
  readOnly: boolean;
  exec: Exec;
}) {
  if (readOnly) {
    return (
      <tr>
        <td className={a.role === "business_key" ? "pk" : ""}>{a.name}</td>
        <td className="type">{a.domain ?? ""}</td>
        <td className="null">{a.nullable ? "" : "NN"}</td>
      </tr>
    );
  }
  const upd = (patch: Record<string, unknown>) =>
    exec("update_attribute", { entity_id: entity.id, attribute_id: a.id, ...patch });
  return (
    <tr>
      <td className={a.role === "business_key" ? "pk" : ""}>
        <EditableText value={a.name} readOnly={false} onCommit={(v) => upd({ name: v })} />
      </td>
      <td className="type">
        <select
          className="cell-select"
          value={a.domain ?? "string"}
          onChange={(e) => upd({ domain: e.target.value })}
        >
          {[...new Set([...(doc.domains ?? []), "string", a.domain ?? "string"])].sort().map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </td>
      <td>
        <select
          className="cell-select"
          value={a.role}
          onChange={(e) => upd({ role: e.target.value })}
          title="role"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r === "business_key" ? "🔑 key" : r === "measure" ? "Σ measure" : r}
            </option>
          ))}
        </select>
      </td>
      <td className="null">
        <input
          type="checkbox"
          checked={!a.nullable}
          title="not null"
          onChange={(e) => upd({ nullable: !e.target.checked })}
        />
      </td>
      <td>
        <button
          className="mini-btn danger"
          title="delete attribute"
          onClick={() =>
            exec("delete_attribute", { entity_id: entity.id, attribute_id: a.id })
          }
        >
          {"✕"}
        </button>
      </td>
    </tr>
  );
}

function AddAttribute({ entity, doc, exec }: { entity: Entity; doc: ModelDoc; exec: Exec }) {
  const [name, setName] = useState("");
  const add = () => {
    if (!name.trim()) return;
    exec("add_attribute", {
      entity_id: entity.id,
      name,
      domain: doc.domains?.[0] ?? "string",
    }).then(() => setName(""));
  };
  return (
    <div className="add-attr">
      <input
        className="edit-input"
        placeholder="+ add attribute…"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && add()}
      />
      {name && (
        <button className="mini-btn" onClick={add}>
          Add
        </button>
      )}
    </div>
  );
}

function cardinalityGlyph(r: Relationship, selfId: string): string {
  const fromSelf = r.from.entity === selfId;
  switch (r.cardinality) {
    case "many_to_one":
      return fromSelf ? "N:1 →" : "1:N ←";
    case "one_to_many":
      return fromSelf ? "1:N →" : "N:1 ←";
    case "one_to_one":
      return "1:1";
    default:
      return "N:M";
  }
}
