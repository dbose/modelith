import { useEffect, useState } from "react";
import type { AttributeRow, Entity, ModelDoc, OntologyRef, Relationship } from "./types";

type Exec = (op: string, payload: Record<string, unknown>) => Promise<unknown>;

// Standard primitive base types the emitter understands (mdl_emit_dbt platforms),
// always offered in the attribute-type dropdown so a fresh model isn't limited to
// whatever domains happen to be seeded. Named model domains are merged on top.
const BASE_TYPES = [
  "string",
  "integer",
  "bigint",
  "decimal",
  "boolean",
  "date",
  "timestamp",
];

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
  onEditMapping,
}: {
  entity: Entity;
  doc: ModelDoc;
  readOnly: boolean;
  exec: Exec;
  onClose: () => void;
  onFocusEntity: (id: string) => void;
  onAlign: (entity: Entity) => void;
  onEditMapping: (entity: Entity) => void;
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
            {entity.category && (
              <span
                className={"cat-badge " + entity.category.role}
                title={
                  entity.category.role === "supertype"
                    ? `Supertype of category "${entity.category.category}"`
                    : `Subtype in "${entity.category.category}" (${entity.category.materialization})`
                }
              >
                {entity.category.role === "supertype" ? "◆ supertype" : "◇ subtype"}
              </span>
            )}
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
                {(c?.ontology_refs?.length ?? 0) > 0 ? "Add / re-align…" : "Align…"}
              </button>
              {(c?.ontology_refs?.length ?? 0) > 0 && (
                <button
                  className="mini-btn danger"
                  onClick={() => exec("clear_alignment", { id: entity.id })}
                  title="Clear every alignment on this object"
                >
                  Clear all
                </button>
              )}
            </span>
          )}
        </h3>
        {c?.ontology_layer && (
          <div className="kv">
            <span className="k">layer</span>
            <span className={`chip layer-${c.ontology_layer}`}>{c.ontology_layer}</span>
            {c.no_industry_equivalent && (
              <span className="chip" title="reviewed: no industry equivalent">
                no industry equivalent
              </span>
            )}
          </div>
        )}
        <OntologyRefs
          entityId={entity.id}
          refs={c?.ontology_refs ?? []}
          readOnly={readOnly}
          exec={exec}
        />
      </section>

      <section>
        <h3>
          Knowledge Graph mapping
          {!readOnly && (
            <span className="h3-actions">
              <button className="mini-btn" onClick={() => onEditMapping(entity)}>
                {entity.term_map?.subject_template || entity.term_map?.class_iri
                  ? "Edit mapping…"
                  : "Map…"}
              </button>
              {(entity.term_map?.subject_template || entity.term_map?.class_iri) && (
                <button
                  className="mini-btn danger"
                  onClick={() => exec("clear_term_map", { id: entity.id })}
                >
                  Clear
                </button>
              )}
            </span>
          )}
        </h3>
        {entity.term_map?.subject_template || entity.term_map?.class_iri ? (
          <>
            {entity.term_map?.class_iri && (
              <div className="kv">
                <span className="k">class IRI</span>
                <code className="v">{entity.term_map.class_iri}</code>
              </div>
            )}
            {entity.term_map?.subject_template && (
              <div className="kv">
                <span className="k">subject</span>
                <code className="v">{entity.term_map.subject_template}</code>
              </div>
            )}
          </>
        ) : (
          <p className="empty-hint">
            uses the default R2RML term-map (base IRI
            {doc.project.kg_base_iri ? ` ${doc.project.kg_base_iri}` : ""} + primary key)
          </p>
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
        {!readOnly && <AddAttribute entity={entity} exec={exec} />}
      </section>

      {entity.key_groups && entity.key_groups.length > 0 && (
        <section>
          <h3>Keys ({entity.key_groups.length})</h3>
          {entity.key_groups.map((kg) => {
            const cols = kg.members
              .map((mid) => entity.attributes.find((a) => a.id === mid)?.name ?? "?")
              .join(", ");
            return (
              <div key={kg.id} className="key-row">
                <span className={"key-badge " + kg.type}>{kg.type.toUpperCase()}</span>
                <span className="key-name">{kg.name}</span>
                <span className="key-cols">{cols}</span>
              </div>
            );
          })}
        </section>
      )}

      {entity.udp && Object.keys(entity.udp).length > 0 && (
        <section>
          <h3>Properties (UDP)</h3>
          <table className="attr-table">
            <tbody>
              {Object.entries(entity.udp).map(([k, v]) => (
                <tr key={k} className="udp-row">
                  <td className="udp-key">{k}</td>
                  <td className="udp-val">{String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

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

/** The full ontology_refs list (spec §1): each binding shown with its predicate,
 * term IRI, resolved-in layer, source and a proposed/accepted state. A proposed ref
 * can be promoted (architect action, §5.1) or cleared individually. */
function OntologyRefs({
  entityId,
  refs,
  readOnly,
  exec,
}: {
  entityId: string;
  refs: OntologyRef[];
  readOnly: boolean;
  exec: Exec;
}) {
  const withUri = refs.filter((r) => r.uri);
  if (withUri.length === 0) {
    return <p className="empty-hint">not aligned to any ontology term</p>;
  }
  return (
    <div className="ref-list">
      {withUri.map((r, i) => {
        const proposed = r.status === "proposed";
        return (
          <div key={`${r.uri}-${i}`} className={"ref-card" + (proposed ? " proposed" : "")}>
            <div className="ref-head">
              <code className="ref-uri" title={r.uri ?? undefined}>
                {r.uri}
              </code>
              <span className={"ref-status " + (proposed ? "proposed" : "accepted")}>
                {proposed ? "proposed" : "accepted"}
              </span>
            </div>
            <div className="ref-meta">
              {r.predicate && <span className="ref-pill">{r.predicate}</span>}
              {r.layer && <span className={`chip layer-${r.layer}`}>{r.layer}</span>}
              {r.resolved_via && (
                <span className="ref-via" title="resolver that found this term">
                  via {r.resolved_via}
                </span>
              )}
              {typeof r.confidence === "number" && (
                <span className="ref-via">conf {Math.round(r.confidence * 100)}%</span>
              )}
            </div>
            {!readOnly && (
              <div className="ref-actions">
                {proposed && (
                  <button
                    className="mini-btn"
                    onClick={() => exec("promote_alignment", { id: entityId, uri: r.uri })}
                    title="Accept this proposed alignment (architect action)"
                  >
                    Promote
                  </button>
                )}
                <button
                  className="mini-btn danger"
                  onClick={() => exec("clear_alignment", { id: entityId, uri: r.uri })}
                  title="Remove this alignment"
                >
                  Remove
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

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
        <td className={a.role === "business_key" ? "pk" : ""}>
          {a.name}
          {a.enum_values && a.enum_values.length > 0 && (
            <span className="enum-badge" title={`Allowed: ${a.enum_values.join(", ")}`}>
              enum
            </span>
          )}
        </td>
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
        {a.enum_values && a.enum_values.length > 0 && (
          <span className="enum-badge" title={`Allowed: ${a.enum_values.join(", ")}`}>
            enum
          </span>
        )}
      </td>
      <td className="type">
        <select
          className="cell-select"
          value={a.domain ?? "string"}
          onChange={(e) => upd({ domain: e.target.value })}
        >
          {[...new Set([...BASE_TYPES, ...(doc.domains ?? []), a.domain ?? "string"])]
            .sort()
            .map((d) => (
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

function AddAttribute({ entity, exec }: { entity: Entity; exec: Exec }) {
  const [name, setName] = useState("");
  const add = () => {
    if (!name.trim()) return;
    exec("add_attribute", {
      entity_id: entity.id,
      name,
      domain: "string",
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
