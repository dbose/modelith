import { useState } from "react";
import { ApiError, proposeChanges } from "../api";
import type { ClassificationDoc, ProposeResult } from "../types";
import type { PendingChange } from "./SmeApp";

/** The entire git/PR complexity, behind one button. Shows a plain-language
 * before/after of each staged change, collects a title + who's proposing, and on
 * submit opens a `sme/<user>/<slug>` PR with a Co-authored-by trailer. The SME
 * never sees git. */
export function ProposeDialog({
  changes,
  routeAdvice,
  identity,
  user: initialUser,
  onUser,
  onDrop,
  onClose,
  onProposed,
}: {
  changes: PendingChange[];
  /** route/reviewers/gates for the staged change, for the split advice (§M6) */
  routeAdvice?: ClassificationDoc | null;
  /** server-established identity (spec §17); when trusted, we greet instead of ask */
  identity?: { name: string; email: string; source: "proxy" | "git" | "anonymous" } | null;
  user?: string;
  onUser?: (u: string) => void;
  onDrop: (idx: number) => void;
  onClose: () => void;
  onProposed: (result?: ProposeResult) => void;
}) {
  // A proxy- or git-established identity is authoritative: the server attributes the
  // commit to it and IGNORES whatever name we send, so there is nothing to ask for.
  const authenticated = Boolean(identity && identity.source !== "anonymous");
  const [user, setUser] = useState(initialUser || (localStorage.getItem("mdl.sme.user") ?? ""));
  const [title, setTitle] = useState(
    changes.length === 1 ? `Update ${changes[0].label.toLowerCase()}` : "Glossary updates",
  );
  // The branch slug defaults to the title but is separately editable, so a modeler
  // can prepend a ticket id (e.g. "PROJ-123-clarify-counterparty"). It follows the
  // title until the user touches it, then stops so their edit is not overwritten.
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const effectiveSlug = slugify(slugTouched ? slug : title);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProposeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    // An authenticated user needs no name; otherwise it is required for attribution.
    if (!authenticated && !user.trim()) return;
    if (!authenticated) {
      localStorage.setItem("mdl.sme.user", user.trim());
      onUser?.(user.trim());
    }
    setBusy(true);
    setError(null);
    // The PR body is generated server-side from the real model diff, so the
    // reviewer sees a grouped, severity-tagged summary rather than this list.
    // When authenticated the server overrides `user` with the trusted identity, so
    // what we send here is only the fallback for an anonymous deployment.
    proposeChanges({
      user: authenticated ? "" : user.trim(),
      // send the edited slug when the user set one, else let the title drive it
      slug: effectiveSlug || title,
      title,
      body: body.trim(),
      changes: changes.map((c) => ({ op: c.op, payload: c.payload })),
    })
      .then(setResult)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  if (result?.ok) {
    return (
      <div className="sme-modal-backdrop" onClick={() => onProposed(result)}>
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
            <button className="sme-primary" onClick={() => onProposed(result)}>
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

        {authenticated ? (
          <p className="sme-signed-in">
            Signed in as <strong>{identity!.name}</strong>
            {identity!.email ? ` (${identity!.email})` : ""} — this proposal is
            attributed to you.
          </p>
        ) : (
          <label className="sme-field">
            <span>Your name (for attribution)</span>
            <input
              value={user}
              onChange={(e) => setUser(e.target.value)}
              placeholder="e.g. a.hough"
            />
          </label>
        )}
        <label className="sme-field">
          <span>Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="sme-field">
          <span>Branch name (edit to add a ticket id, e.g. PROJ-123)</span>
          <input
            value={slugTouched ? slug : effectiveSlug}
            onChange={(e) => {
              setSlugTouched(true);
              setSlug(e.target.value);
            }}
            placeholder="clarify-counterparty"
          />
        </label>
        <label className="sme-field">
          <span>Note for the reviewer (optional)</span>
          <textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>

        <RouteAdvice
          cl={routeAdvice}
          user={authenticated ? identity!.name : user}
          changeSlug={effectiveSlug}
        />

        {error && <p className="sme-error-line">{error}</p>}

        <div className="sme-modal-foot">
          <button className="sme-secondary" onClick={onClose}>
            Keep editing
          </button>
          <button
            className="sme-primary"
            disabled={busy || (!authenticated && !user.trim()) || changes.length === 0}
            onClick={submit}
          >
            {busy ? "Submitting…" : "Submit for review"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** §M6 — a proposal spanning routes inherits the STRICTEST gate (_PRECEDENCE is
 *  B > E > C > A), so a definition fix bundled with a structural change waits on
 *  architects instead of a steward. Saying so, and naming where it goes, is advice
 *  erwin has no route model to give. */
function RouteAdvice({
  cl,
  user,
  changeSlug,
}: {
  cl?: ClassificationDoc | null;
  user: string;
  /** the (possibly ticket-prefixed) change slug, so the preview shows the real
   *  branch rather than an ellipsis */
  changeSlug?: string;
}) {
  if (!cl?.primary) return null;
  const reviewers = cl.reviewers_actual ?? cl.reviewers;
  const slug = slugify(user || "you");
  const mixed = cl.routes.length > 1;
  return (
    <>
      {mixed && (
        <div className="sub-advice">
          <span className="i">ⓘ</span> These changes span routes{" "}
          <strong>{cl.routes.join(" and ")}</strong>. The whole proposal will be reviewed as
          route {cl.primary} ({cl.primary_name}) — the strictest gate wins, so it needs{" "}
          {reviewers.join(", ")} plus <code>{cl.gates[cl.gates.length - 1]}</code>. Proposing
          the meaning-only changes separately would get them reviewed faster.
        </div>
      )}
      <div className="sub-dest">
        <div className="row">
          <span className="k">Goes to</span>
          <code>sme/{slug}/{changeSlug || "…"}</code>
        </div>
        <div className="row">
          <span className="k">Reviewers</span>
          <span>{reviewers.join(", ")}</span>
        </div>
      </div>
    </>
  );
}

/** Slugify a branch fragment the same way the server does (mdl_server.git_api
 *  ._slugify): lowercase, non-alphanumerics to single hyphens, trimmed. Keeping the
 *  rule identical means the branch preview matches the branch the server creates. */
function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "change";
}
