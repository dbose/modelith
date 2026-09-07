import { useState } from "react";
import {
  CLASSIFICATION,
  CONFLICTS_CLEAN,
  DIFF,
  ERD_EDGES,
  ERD_NODES,
  type FieldChangeDoc,
  type ObjectChangeDoc,
} from "./fixtures";

/** M1/M2/M3 — the review screen at /sme#review.
 *
 * One list of only what changed, grouped by object, each change a plain-language
 * sentence. Erwin's Complete Compare is two mirrored trees of the WHOLE model
 * with change glyphs you have to interpret; this shows three objects and says
 * what happened to them. */
export function ReviewScreen({ initialTab = "changes" }: { initialTab?: "changes" | "diagram" }) {
  const [tab, setTab] = useState<"changes" | "diagram">(initialTab);
  const [selected, setSelected] = useState<string>(DIFF.objects[0].ulid);
  const obj = DIFF.objects.find((o) => o.ulid === selected) ?? DIFF.objects[0];
  const c = DIFF.counts;

  return (
    <div className="sme" style={{ height: "100%" }}>
      <header className="sme-top">
        <div className="sme-brand">
          <span className="sme-logo">◮</span>
          <span>Glossary</span>
          <span className="sme-project">pension_ibor</span>
        </div>
        <button className="sme-tray">5 changes · Review →</button>
      </header>

      <div className="rv-head">
        <div>
          <button className="rv-back">‹ Back to terms</button>
          <h1 className="rv-title">Review your changes</h1>
          <div className="rv-sub">
            {c.modified + c.added + c.removed} objects · {c.breaking + c.additive + c.cosmetic}{" "}
            changes{" "}
            {DIFF.has_breaking && <span className="warn">· ⚠ {c.breaking} breaking</span>} ·
            comparing to <code>{DIFF.base.ref}</code>
          </div>
        </div>
        <div className="rv-tabs">
          <button
            className={"rv-tab" + (tab === "changes" ? " active" : "")}
            onClick={() => setTab("changes")}
          >
            Changes {DIFF.objects.length}
          </button>
          <button
            className={"rv-tab" + (tab === "diagram" ? " active" : "")}
            onClick={() => setTab("diagram")}
          >
            Diagram
          </button>
        </div>
      </div>

      {tab === "changes" ? (
        <div className="rv-body">
          <div className="rv-list">
            {DIFF.objects.map((o) => (
              <ObjectRow
                key={o.ulid}
                obj={o}
                active={o.ulid === selected}
                onSelect={() => setSelected(o.ulid)}
              />
            ))}
          </div>
          <div className="rv-detail">
            <Detail obj={obj} />
          </div>
        </div>
      ) : (
        <DiagramTab />
      )}

      <Footer />
    </div>
  );
}

function ObjectRow({
  obj,
  active,
  onSelect,
}: {
  obj: ObjectChangeDoc;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <div className={"rv-obj" + (active ? " active" : "")} onClick={onSelect}>
      <div className="rv-obj-head">
        <span className="rv-obj-name">{obj.name_before ?? obj.name_after}</span>
        <span className="rv-obj-kind">{obj.object_kind.replace(/_/g, " ")}</span>
        <span className={"sev " + (obj.renamed ? "renamed" : obj.severity)}>
          {obj.severity === "breaking" && "⚠ "}
          {obj.renamed ? "renamed" : obj.severity === "breaking" ? "breaking" : obj.change}
        </span>
      </div>
      <ul className="rv-obj-fields">
        {obj.fields.map((f) => (
          <li key={f.field}>{f.label}</li>
        ))}
      </ul>
    </div>
  );
}

function Detail({ obj }: { obj: ObjectChangeDoc }) {
  return (
    <>
      <h2>
        {obj.renamed ? (
          <>
            {obj.name_before} → {obj.name_after}
          </>
        ) : (
          (obj.name_before ?? obj.name_after)
        )}
      </h2>
      <div className="rv-detail-kind">
        {obj.object_kind.replace(/_/g, " ")}
        {obj.renamed && " · renamed"}
        {obj.severity === "breaking" && " · 1 attribute removed"}
      </div>
      {obj.fields.map((f) => (
        <FieldDiff key={f.field} f={f} />
      ))}
      {obj.severity === "breaking" && (
        <div className="rv-note">
          <span className="i">ⓘ</span>
          <span>
            This is a structural change (route B). It will need an architect's review, and CI
            will run <code>mdl drift --check</code>.
          </span>
        </div>
      )}
    </>
  );
}

/** Rendering is by field SHAPE, never a line diff: prose gets a side-by-side with
 *  word-level intra-diff, scalars an inline before → after, lists +/− chips. */
function FieldDiff({ f }: { f: FieldChangeDoc }) {
  if (f.breaks) {
    return (
      <div className="rv-section">
        <h3>{f.label}</h3>
        <div className="rv-break">
          <div className="rv-break-head">
            <span>⚠</span>
            <span>{f.before} removed</span>
            <span className="rv-break-meta">{f.detail}</span>
          </div>
          <div className="rv-break-body">
            This attribute is realised by {f.breaks.length} dbt models. Removing it will break
            them.
          </div>
          <ul className="rv-usage">
            {f.breaks.map((b) => (
              <li key={b.model}>
                <span className="m">→ {b.model}</span>
                <span className="p">
                  {b.target} · {b.materialization}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    );
  }

  if (f.kind === "definition_changed") {
    return (
      <div className="rv-section">
        <h3>{f.label}</h3>
        <div className="rv-ba">
          <div className="rv-ba-col">
            <div className="rv-ba-label">before</div>
            <div className="rv-ba-text">{wordDiff(f.before ?? "", f.after ?? "", "del")}</div>
          </div>
          <div className="rv-ba-col">
            <div className="rv-ba-label">after</div>
            <div className="rv-ba-text">{wordDiff(f.after ?? "", f.before ?? "", "ins")}</div>
          </div>
        </div>
        <div className="rv-hint">word-level intra-diff, not a line diff</div>
      </div>
    );
  }

  if (f.kind === "subject_area_members_changed") {
    return (
      <div className="rv-section">
        <h3>{f.label}</h3>
        <div className="rv-chips">
          {f.detail.split(", ").map((s) => (
            <span key={s} className="rv-chip-add">
              + {s}
            </span>
          ))}
        </div>
        <div className="rv-hint">
          {f.before} → {f.after}
        </div>
      </div>
    );
  }

  return (
    <div className="rv-section">
      <h3>{f.label}</h3>
      <div className="rv-scalar">
        <span className="from">{f.before}</span>
        <span className="arrow">→</span>
        <span className="to">{f.after}</span>
      </div>
    </div>
  );
}

/** Word-level LCS: mark only the tokens that genuinely differ, so an edit reads
 *  as "these three words changed" rather than lighting up half the sentence.
 *  The real implementation is the same algorithm over whitespace tokens. */
function wordDiff(text: string, other: string, mark: "del" | "ins") {
  const a = text.split(/(\s+)/);
  const b = other.split(/(\s+)/);
  const norm = (t: string) => t.toLowerCase().replace(/[.,;:]/g, "");
  // classic LCS table over the two token streams
  const n = a.length;
  const m = b.length;
  const L: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      L[i][j] = norm(a[i]) === norm(b[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    }
  }
  const keep = new Set<number>();
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (norm(a[i]) === norm(b[j])) {
      keep.add(i);
      i++;
      j++;
    } else if (L[i + 1][j] >= L[i][j + 1]) i++;
    else j++;
  }
  return a.map((tok, k) => {
    if (!tok.trim() || keep.has(k)) return <span key={k}>{tok}</span>;
    return mark === "del" ? <del key={k}>{tok}</del> : <ins key={k}>{tok}</ins>;
  });
}

/** M3 — the diff drawn ON the model. Erwin's compare is a tree; Modelith has a
 *  real ER canvas, so changed entities are tinted by severity and unchanged
 *  neighbours are dimmed for context. */
function DiagramTab() {
  const byId = new Map(ERD_NODES.map((n) => [n.id, n]));
  return (
    <div className="rv-canvas">
      <div className="erd">
        <svg className="erd-svg" width="860" height="400">
          {ERD_EDGES.map(([a, b]) => {
            const na = byId.get(a)!;
            const nb = byId.get(b)!;
            const ghost = na.ghost || nb.ghost;
            return (
              <line
                key={a + b}
                x1={na.x + 95}
                y1={na.y + 30}
                x2={nb.x + 95}
                y2={nb.y + 30}
                stroke={ghost ? "#3d4a5c" : "#4b5768"}
                strokeWidth={1.5}
                strokeDasharray={ghost ? "5 4" : undefined}
              />
            );
          })}
        </svg>
        {ERD_NODES.map((n) => (
          <div
            key={n.id}
            className={
              "erd-node " + (n.ghost ? "ghost" : n.severity ? n.severity : "context")
            }
            style={{ left: n.x, top: n.y }}
          >
            <div className="erd-name">{n.name}</div>
            {n.note && <div className="erd-note">{n.note}</div>}
          </div>
        ))}
      </div>
      <div className="erd-legend">
        <span>
          <span className="sw" style={{ background: "var(--sev-breaking)" }} />
          breaking
        </span>
        <span>
          <span className="sw" style={{ background: "var(--sev-additive)" }} />
          additive
        </span>
        <span>
          <span className="sw" style={{ background: "var(--pk)" }} />
          cosmetic
        </span>
        <span>
          <span className="sw" style={{ background: "var(--sev-context)" }} />
          unchanged (context)
        </span>
        <span>▫ dashed = removed</span>
      </div>
    </div>
  );
}

function Footer() {
  const cl = CLASSIFICATION;
  const cf = CONFLICTS_CLEAN;
  return (
    <div className="rv-foot">
      <div className="rv-route">
        <span className="rv-route-badge">
          Route {cl.primary} · {cl.primary_name}
        </span>
        <span className="k">Reviewers</span>
        <span>{(cl.reviewers_actual ?? cl.reviewers).join(", ")}</span>
        {cl.reviewers_actual && <span className="rv-src">from .github/CODEOWNERS</span>}
      </div>
      <div className="rv-gates">
        CI gates{" "}
        {cl.gates.map((g) => (
          <code key={g}>{g}</code>
        ))}
      </div>
      <div className={"rv-conflict " + (cf.clean ? "ok" : "bad")}>
        <span>{cf.clean ? "✓" : "⚠"}</span>
        <span>{cf.clean ? "Merges cleanly onto " + cf.base : "Conflicts with " + cf.base}</span>
        {cf.behind > 0 && (
          <span className="detail">
            · {cf.base} is {cf.behind} commits ahead
          </span>
        )}
      </div>
      <div className="rv-actions">
        <button className="sme-secondary">Keep editing</button>
        <button className="sme-primary">Submit for review →</button>
      </div>
    </div>
  );
}
