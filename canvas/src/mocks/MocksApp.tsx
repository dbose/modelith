import { useState } from "react";
import { Proposals } from "./Proposals";
import { RenameCompare } from "./RenameCompare";
import { ReviewScreen } from "./ReviewScreen";
import { SubmitDialog } from "./SubmitDialog";

/** Static UI mocks for the Model Git-Ops screens (plan §M1–M6).
 *
 * Not wired to the API — fixture-driven, so the screens can be reviewed and
 * argued about before any of Part II is built. `mdl mocks` / the `mocks` Vite
 * entry serves this; it ships nothing into the product surfaces. */
const MOCKS = [
  {
    id: "m1",
    label: "M1 · Review screen",
    caption:
      "The diff, grouped by object, each change a plain-language sentence. Left: what changed. Right: before/after for the selected object. Footer: route, reviewers (from the repo's real CODEOWNERS), CI gates, and whether it still merges cleanly.",
    render: () => <ReviewScreen />,
  },
  {
    id: "m2",
    label: "M2 · Breaking change",
    caption:
      "Select the Trade row. where_used already walks conceptual→logical→physical, so the warning names the dbt models that will break — at the moment the SME is about to do it. Erwin's compare has no lineage to the transformation layer and cannot say this.",
    render: () => <ReviewScreen />,
  },
  {
    id: "m3",
    label: "M3 · Diagram tab",
    caption:
      "The same diff drawn ON the model: changed entities tinted by severity, unchanged neighbours dimmed for context, removed objects as dashed ghosts. Reuses EntityNode's existing color/dimmed props — erwin's Complete Compare is a tree and structurally cannot do this.",
    render: () => <ReviewScreen initialTab="diagram" />,
  },
  {
    id: "m4",
    label: "M4 · Rename comparison",
    caption:
      "The ULID-identity argument, made literal. One cosmetic field change vs a whole-file delete + add — and erwin, which matches by name, reports it as an entity removed plus an entity added.",
    render: () => <RenameCompare />,
  },
  {
    id: "m5",
    label: "M5 · My proposals",
    caption:
      "The lifecycle after submit. Six status chips; everything except the PR row is derivable from plain git.",
    render: () => <Proposals />,
  },
  {
    id: "m5b",
    label: "M5b · …without `gh`",
    caption:
      "The same screen with `gh` absent or offline. The list is never empty — only the review state is unknown, and each card degrades to a compare link.",
    render: () => <Proposals withGh={false} />,
  },
  {
    id: "m6",
    label: "M6 · Submit + route split",
    caption:
      "_PRECEDENCE is B > E > C > A, so a mixed proposal inherits the strictest gate. Saying so — and offering to split the meaning changes out for a faster steward review — is advice erwin has no route model to give.",
    render: () => <SubmitDialog />,
  },
];

export function MocksApp() {
  const [id, setId] = useState(MOCKS[0].id);
  const cur = MOCKS.find((m) => m.id === id) ?? MOCKS[0];
  return (
    <div className="mock-shell">
      <div className="mock-picker">
        <span className="mock-picker-label">Model Git-Ops mocks</span>
        {MOCKS.map((m) => (
          <button
            key={m.id}
            className={"mock-tab" + (m.id === id ? " active" : "")}
            onClick={() => setId(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="mock-caption">{cur.caption}</div>
      {/* key on the mock id so switching tabs remounts (each mock has its own
          initial state, e.g. M3 opens on the Diagram tab). */}
      <div className="mock-frame" key={cur.id}>
        {cur.render()}
      </div>
    </div>
  );
}
