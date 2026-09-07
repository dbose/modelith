import { PROPOSALS, type ProposalDoc } from "./fixtures";

/** M5 — "My proposals", the screen that ends the blindness.
 *
 * Today an SME submits and never sees the proposal again. Everything here EXCEPT
 * the PR row is derivable from plain git (for-each-ref, log -1, branch --merged,
 * merge-base + the semantic merge driver), so with `gh` absent or offline the
 * cards still render fully — only the review state is unknown. */
export function Proposals({ withGh = true }: { withGh?: boolean }) {
  return (
    <div className="sme" style={{ height: "100%" }}>
      <header className="sme-top">
        <div className="sme-brand">
          <span className="sme-logo">◮</span>
          <span>Glossary</span>
          <span className="sme-project">pension_ibor</span>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 16, fontSize: 13 }}>
          <span style={{ color: "var(--text-dim)" }}>Terms</span>
          <span style={{ color: "var(--text-dim)" }}>Subject areas</span>
          <span style={{ color: "var(--accent)", fontWeight: 600 }}>Proposals</span>
        </div>
      </header>

      <div className="pr-wrap">
        <div className="pr-head">
          <h2>My proposals</h2>
          <span className="meta">
            {PROPOSALS.filter((p) => !p.merged).length} open · base: main
          </span>
        </div>

        {PROPOSALS.map((p) => (
          <Card key={p.branch} p={p} withGh={withGh} />
        ))}

        {!withGh && (
          <div className="pr-degrade">
            <strong>`gh` is not installed here.</strong> Branch, title, date, contents, route,
            merge state and conflicts all still come from plain git — only the PR review state
            is unavailable, so each card links out to the compare view instead.
          </div>
        )}
      </div>
    </div>
  );
}

type ChipKind = "accepted" | "changes" | "attention" | "awaiting" | "local";

function chipFor(p: ProposalDoc, withGh: boolean): { kind: ChipKind; text: string } {
  if (p.merged) return { kind: "accepted", text: "✅ Accepted" };
  if (!p.pushed) return { kind: "local", text: "📥 Saved locally" };
  if (p.conflicts) return { kind: "attention", text: "⚠ Needs your attention" };
  if (withGh && p.pr?.reviews === "CHANGES_REQUESTED")
    return { kind: "changes", text: "💬 Changes requested" };
  return { kind: "awaiting", text: "⏳ Awaiting review" };
}

function Card({ p, withGh }: { p: ProposalDoc; withGh: boolean }) {
  const chip = chipFor(p, withGh);
  return (
    <div className={"pr-card" + (chip.kind === "attention" ? " attention" : "")}>
      <div className="pr-top">
        <span className="pr-title">{p.title}</span>
        <span className={"pr-chip " + chip.kind}>{chip.text}</span>
      </div>
      <div className="pr-branch">
        {p.branch} · {p.created} · Route {p.route} · {p.route_name}
      </div>
      <div className="pr-summary">{p.summary}</div>
      {p.conflicts && <div className="pr-detail">{p.conflict_detail}</div>}
      <div className="pr-foot">
        <span className="pr-link">
          {p.merged ? (
            <>
              merged into main on {p.created}
              {withGh && p.pr && <> · PR #{p.pr.number}</>}
            </>
          ) : !p.pushed ? (
            "not sent yet — no connection to the shared repo"
          ) : withGh && p.pr ? (
            <>
              PR #{p.pr.number}
              {p.pr.reviews === "CHANGES_REQUESTED" && " · 1 reviewer asked for changes"}
            </>
          ) : (
            <a href="#">open the compare view →</a>
          )}
        </span>
        <span className="pr-btns">
          <button className="sme-secondary">View</button>
          {!p.merged && (
            <button className="sme-secondary">
              {p.conflicts ? "Resolve" : "Add more"}
            </button>
          )}
        </span>
      </div>
    </div>
  );
}
