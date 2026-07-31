import { useState } from "react";
import { ApiError, proposeChanges } from "../api";
import type { ProposeResult } from "../types";
import type { PendingChange } from "./SmeApp";

/** The entire git/PR complexity, behind one button. Shows a plain-language
 * before/after of each staged change, collects a title + who's proposing, and on
 * submit opens a `sme/<user>/<slug>` PR with a Co-authored-by trailer. The SME
 * never sees git. */
export function ProposeDialog({
  changes,
  onDrop,
  onClose,
  onProposed,
}: {
  changes: PendingChange[];
  onDrop: (idx: number) => void;
  onClose: () => void;
  onProposed: () => void;
}) {
  const [user, setUser] = useState(localStorage.getItem("mdl.sme.user") ?? "");
  const [title, setTitle] = useState(
    changes.length === 1 ? `Update ${changes[0].label.toLowerCase()}` : "Glossary updates",
  );
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProposeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!user.trim()) return;
    localStorage.setItem("mdl.sme.user", user.trim());
    setBusy(true);
    setError(null);
    const bodyText =
      (body.trim() ? body.trim() + "\n\n" : "") +
      changes.map((c) => `- ${c.label}: "${c.before}" → "${c.after}"`).join("\n");
    proposeChanges({
      user: user.trim(),
      slug: title,
      title,
      body: bodyText,
      changes: changes.map((c) => ({ op: c.op, payload: c.payload })),
    })
      .then(setResult)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  if (result?.ok) {
    return (
      <div className="sme-modal-backdrop" onClick={onProposed}>
        <div className="sme-modal" onClick={(e) => e.stopPropagation()}>
          <h2>✓ Sent for review</h2>
          <p>{result.message}</p>
          {result.pr_url && (
            <p>
              <a href={result.pr_url} target="_blank" rel="noreferrer">
                View pull request →
              </a>
            </p>
          )}
          {result.compare_url && (
            <p>
              <a href={result.compare_url} target="_blank" rel="noreferrer">
                Open the pull request →
              </a>
            </p>
          )}
          <p className="sme-muted">
            Your suggestion is on branch <code>{result.branch}</code>. A steward will review it.
          </p>
          <div className="sme-modal-foot">
            <button className="sme-primary" onClick={onProposed}>
              Done
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="sme-modal-backdrop" onClick={onClose}>
      <div className="sme-modal" onClick={(e) => e.stopPropagation()}>
        <div className="sme-card-head">
          <h2>Submit for review</h2>
          <button className="sme-secondary" onClick={onClose}>
            ✕
          </button>
        </div>

        <h3>Your changes</h3>
        <ul className="sme-diff">
          {changes.map((c, i) => (
            <li key={i}>
              <div className="sme-diff-head">
                <b>{c.label}</b>
                <button className="sme-link danger" onClick={() => onDrop(i)}>
                  discard
                </button>
              </div>
              <div className="sme-diff-before">{c.before}</div>
              <div className="sme-diff-after">{c.after}</div>
            </li>
          ))}
        </ul>

        <label className="sme-field">
          <span>Your name (for attribution)</span>
          <input value={user} onChange={(e) => setUser(e.target.value)} placeholder="e.g. a.hough" />
        </label>
        <label className="sme-field">
          <span>Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="sme-field">
          <span>Note for the reviewer (optional)</span>
          <textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>

        {error && <p className="sme-error-line">{error}</p>}

        <div className="sme-modal-foot">
          <button className="sme-secondary" onClick={onClose}>
            Keep editing
          </button>
          <button className="sme-primary" disabled={busy || !user.trim() || changes.length === 0} onClick={submit}>
            {busy ? "Submitting…" : "Submit for review"}
          </button>
        </div>
      </div>
    </div>
  );
}
