import { CLASSIFICATION, CLASSIFICATION_A } from "./fixtures";

/** M6 — submit, with the route-splitting advice.
 *
 * `_PRECEDENCE` is B > E > C > A: a proposal spanning routes inherits the
 * STRICTEST gate. So a definition fix bundled with a structural change waits on
 * architects and `mdl drift --check` instead of a steward's quick approval.
 * Saying so — and offering the split — is advice erwin has no route model to give. */
export function SubmitDialog() {
  const cl = CLASSIFICATION;
  const a = CLASSIFICATION_A;
  return (
    <div className="sme" style={{ height: "100%", position: "relative" }}>
      <header className="sme-top">
        <div className="sme-brand">
          <span className="sme-logo">◮</span>
          <span>Glossary</span>
          <span className="sme-project">pension_ibor</span>
        </div>
        <button className="sme-tray">5 changes · Review →</button>
      </header>

      <div className="sme-modal-backdrop" style={{ position: "absolute" }}>
        <div className="sme-modal">
          <h2>Submit for review</h2>
          <p className="sme-hint">
            Your changes become a branch and a pull request. Nothing is written to{" "}
            <code style={{ fontFamily: "var(--mono)" }}>main</code> directly.
          </p>

          <label className="sme-field">
            <span>Title</span>
            <input defaultValue="Clarify Counterparty definition" />
          </label>
          <label className="sme-field">
            <span>Your name</span>
            <input defaultValue="a.hough" />
          </label>
          <label className="sme-field">
            <span>Note for the reviewer (optional)</span>
            <textarea rows={2} defaultValue="" />
          </label>

          <div className="sub-advice">
            <span className="i">ⓘ</span> These changes span routes{" "}
            <strong>
              {cl.routes.join(" (Meaning) and ")} ({cl.primary_name})
            </strong>
            . The whole proposal will be reviewed as route {cl.primary} —{" "}
            {(cl.reviewers_actual ?? cl.reviewers).join(", ")}, plus{" "}
            <code style={{ fontFamily: "var(--mono)", fontSize: 12 }}>mdl drift --check</code>.
            <button className="sub-split">
              Propose the 3 meaning changes separately → route {a.primary}, {" "}
              {(a.reviewers_actual ?? a.reviewers).join(", ")}
            </button>
          </div>

          <div className="sub-dest">
            <div className="row">
              <span className="k">Goes to</span>
              <code>sme/a.hough/clarify-counterparty</code>
            </div>
            <div className="row">
              <span className="k">Reviewers</span>
              <span>{(cl.reviewers_actual ?? cl.reviewers).join(", ")}</span>
            </div>
          </div>

          <div className="sme-modal-foot">
            <button className="sme-secondary">Cancel</button>
            <button className="sme-primary">Submit for review</button>
          </div>
        </div>
      </div>
    </div>
  );
}
