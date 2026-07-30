import type { Entity, ModelDoc, PhysicalTable, Relationship } from "./types";

export function DetailPanel({
  entity,
  doc,
  onClose,
  onFocusEntity,
}: {
  entity: Entity;
  doc: ModelDoc;
  onClose: () => void;
  onFocusEntity: (id: string) => void;
}) {
  const c = entity.conceptual;
  const rels = doc.relationships.filter(
    (r) => r.from.entity === entity.id || r.to.entity === entity.id,
  );
  const phys = doc.physical.filter((p) => p.realises === entity.id);
  const entityName = (id: string) => doc.entities.find((e) => e.id === id)?.name ?? id;

  return (
    <aside className="detail-panel">
      <div className="detail-header">
        <div>
          <h2>{entity.name}</h2>
          {c?.subject_area && <span className="chip">{c.subject_area.name}</span>}
          {entity.pattern && <span className="chip pattern">{entity.pattern}</span>}
        </div>
        <button className="icon-btn" onClick={onClose} title="Close">
          {"✕"}
        </button>
      </div>

      {c?.definition && <p className="definition">{c.definition}</p>}

      {c?.ontology?.aligns_to && (
        <section>
          <h3>Ontology</h3>
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
        </section>
      )}

      {c?.stewardship && (c.stewardship.owner || c.stewardship.steward) && (
        <section>
          <h3>Stewardship</h3>
          {c.stewardship.owner && (
            <div className="kv">
              <span className="k">owner</span>
              <span className="v">{c.stewardship.owner}</span>
            </div>
          )}
          {c.stewardship.steward && (
            <div className="kv">
              <span className="k">steward</span>
              <span className="v">{c.stewardship.steward}</span>
            </div>
          )}
        </section>
      )}

      <section>
        <h3>Attributes ({entity.attributes.length})</h3>
        <table className="attr-table">
          <tbody>
            {entity.attributes.map((a) => (
              <tr key={a.id}>
                <td className={a.role === "business_key" ? "pk" : ""}>{a.name}</td>
                <td className="type">{a.domain ?? ""}</td>
                <td className="null">{a.nullable ? "" : "NN"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {rels.length > 0 && (
        <section>
          <h3>Relationships ({rels.length})</h3>
          {rels.map((r: Relationship) => {
            const other = r.from.entity === entity.id ? r.to.entity : r.from.entity;
            return (
              <button key={r.id} className="rel-link" onClick={() => onFocusEntity(other)}>
                <span className="rel-card">{cardinalityGlyph(r, entity.id)}</span>
                {entityName(other)}
              </button>
            );
          })}
        </section>
      )}

      {phys.length > 0 && (
        <section>
          <h3>Physical</h3>
          {phys.map((p: PhysicalTable) => (
            <div className="kv" key={p.id}>
              <span className="k">{p.target}</span>
              <code className="v">
                {p.name} · {p.materialization}
              </code>
            </div>
          ))}
        </section>
      )}

      {c?.synonyms && c.synonyms.length > 0 && (
        <section>
          <h3>Synonyms</h3>
          <div>
            {c.synonyms.map((s) => (
              <span key={s} className="chip">
                {s}
              </span>
            ))}
          </div>
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
      </section>
    </aside>
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
