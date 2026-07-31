import type { GlossaryTerm } from "../types";

/** Read-only term view: definition, synonyms, steward, ontology alignment, and
 * "where used" — the dbt models that realise this term. The reassurance an SME
 * needs before touching a definition. */
export function TermCard({
  term,
  canEdit,
  pendingCount,
  onEdit,
}: {
  term: GlossaryTerm;
  canEdit: boolean;
  pendingCount: number;
  onEdit: () => void;
}) {
  return (
    <div className="sme-card">
      <div className="sme-card-head">
        <div>
          <h1>{term.name}</h1>
          {term.subject_area?.name && <span className="sme-chip">{term.subject_area.name}</span>}
          {term.kind === "term" && <span className="sme-chip">glossary term</span>}
        </div>
        {canEdit && (
          <button className="sme-primary" onClick={onEdit}>
            {pendingCount > 0 ? "Continue editing" : "Suggest an edit"}
          </button>
        )}
      </div>

      <p className="sme-definition">{term.definition ?? <em>No definition yet.</em>}</p>

      {term.synonyms.length > 0 && (
        <section>
          <h3>Also called</h3>
          <div>
            {term.synonyms.map((s) => (
              <span key={s} className="sme-chip">
                {s}
              </span>
            ))}
          </div>
        </section>
      )}

      {term.stewardship && (term.stewardship.owner || term.stewardship.steward) && (
        <section>
          <h3>Who owns this</h3>
          {term.stewardship.owner && (
            <div className="sme-kv">
              <span className="k">owner</span>
              <span>{term.stewardship.owner}</span>
            </div>
          )}
          {term.stewardship.steward && (
            <div className="sme-kv">
              <span className="k">steward</span>
              <span>{term.stewardship.steward}</span>
            </div>
          )}
        </section>
      )}

      {term.ontology?.aligns_to && (
        <section>
          <h3>Standard it maps to</h3>
          <div className="sme-kv">
            <span className="k">aligned to</span>
            <code>{term.ontology.aligns_to}</code>
            {term.ontology.status === "proposed" && (
              <span className="sme-badge proposed">awaiting architect</span>
            )}
          </div>
        </section>
      )}

      <section>
        <h3>Where it's used</h3>
        {term.where_used.length === 0 ? (
          <p className="sme-muted">
            {term.kind === "term"
              ? "A glossary term — not yet realised by a data model."
              : "Not yet realised by any data model."}
          </p>
        ) : (
          <ul className="sme-usage">
            {term.where_used.map((u) => (
              <li key={u.logical_id}>
                <span className="sme-usage-model">{u.logical_entity}</span>
                {u.unmanaged && <span className="sme-badge">engineer-owned</span>}
                {u.physical.length > 0 && (
                  <span className="sme-usage-phys">
                    {u.physical.map((p) => p.name).join(", ")}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
