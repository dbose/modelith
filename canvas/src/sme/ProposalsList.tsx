import { useEffect, useState } from "react";
import { fetchProposals } from "../api";
import type { ProposalDoc, ProposalsDoc } from "../types";

/** "My proposals" (plan §M5) — the screen that ends the blindness after submit.
 *
 * Everything except the PR row comes from plain git (for-each-ref, log -1,
 * branch --merged, rev-list), so the list is fully populated with `gh` absent,
 * unauthed or offline; only the review state is unknown. */
export function ProposalsList({ user, onView }: { user: string; onView: (branch: string) => void }) {
  const [doc, setDoc] = useState<ProposalsDoc | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProposals(user).then(setDoc).catch((e) => setError(String(e)));
  }, [user]);

  if (error) return <p className="sme-splash error">{error}</p>;
  if (!doc) return <div className="sme-splash">◮ looking for your proposals…</div>;
  if (!doc.git) {
    return <p className="sme-placeholder">This model isn't in a git repository.</p>;
  }

  const open = doc.proposals.filter((p) => !p.merged).length;
  return (
    <div className="pr-wrap">
      <div className="pr-head">
        <h2>My proposals</h2>
        <span className="meta">
          {open} open · base: {doc.base}
        </span>
      </div>

      {!doc.proposals.length && (
        <p className="sme-placeholder">
          Nothing proposed yet. Edits you submit for review will appear here.
        </p>
      )}

      {doc.proposals.map((p) => (
        <Card key={p.branch} p={p} gh={doc.gh} onView={() => onView(p.branch)} />
      ))}

      {!doc.gh && doc.proposals.length > 0 && (
        <div className="pr-degrade">
          Review status isn't available here (<code>gh</code> is not installed or not signed
          in). Branch, contents, merge state and conflicts all still come from git — each
          card links out to the compare view.
        </div>
      )}
    </div>
  );
}

type ChipKind = "accepted" | "changes" | "attention" | "awaiting" | "local" | "declined";

function chipFor(p: ProposalDoc, gh: boolean): { kind: ChipKind; text: string } {
  if (p.merged) return { kind: "accepted", text: "✅ Accepted" };
  if (!p.pushed) return { kind: "local", text: "📥 Saved locally" };
  if (gh && p.pr?.state === "CLOSED") return { kind: "declined", text: "✖ Declined" };
  if (gh && p.pr?.reviews === "CHANGES_REQUESTED")
    return { kind: "changes", text: "💬 Changes requested" };
  return { kind: "awaiting", text: "⏳ Awaiting review" };
}

function Card({ p, gh, onView }: { p: ProposalDoc; gh: boolean; onView: () => void }) {
  const chip = chipFor(p, gh);
  return (
    <div className={"pr-card" + (chip.kind === "attention" ? " attention" : "")}>
      <div className="pr-top">
        <span className="pr-title">{p.title}</span>
        <span className={"pr-chip " + chip.kind}>{chip.text}</span>
      </div>
      <div className="pr-branch">
        {p.branch} · {p.created}
        {p.behind > 0 && ` · ${p.behind} behind`}
      </div>
      <div className="pr-foot">
        <span className="pr-link">
          {p.merged ? (
            <>merged{gh && p.pr ? ` · PR #${p.pr.number}` : ""}</>
          ) : !p.pushed ? (
            "not sent yet — no connection to the shared repo"
          ) : gh && p.pr ? (
            <a href={p.pr.url} target="_blank" rel="noreferrer">
              PR #{p.pr.number}
            </a>
          ) : p.compare_url ? (
            <a href={p.compare_url} target="_blank" rel="noreferrer">
              open the compare view →
            </a>
          ) : (
            "pushed"
          )}
        </span>
        <span className="pr-btns">
          <button className="sme-secondary" onClick={onView}>
            View
          </button>
        </span>
      </div>
    </div>
  );
}
