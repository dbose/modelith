import { useMemo, useState } from "react";
import type { FieldChangeDoc, ModelDiffDoc, ObjectChangeDoc } from "../types";

/** The changed-object list and detail pane (plan §M1/M2).
 *
 * One list of only what changed, grouped by object, each change a plain-language
 * sentence produced server-side (mdl_core.diff builds the label and detail, so
 * the CLI, the PR body and this screen never drift). */
export function DiffView({
  diff,
  selectable,
  selected,
  onToggle,
}: {
  diff: ModelDiffDoc;
  /** per-object checkboxes for selective proposal; off when a create_* is staged */
  selectable?: boolean;
  selected?: Set<string>;
  onToggle?: (ulid: string) => void;
}) {
  const [focus, setFocus] = useState<string | null>(diff.objects[0]?.ulid ?? null);
  const obj = useMemo(
    () => diff.objects.find((o) => o.ulid === focus) ?? diff.objects[0] ?? null,
    [diff, focus],
  );

  if (!diff.objects.length) {
    return <p className="sme-placeholder">No model changes against {diff.base.label}.</p>;
  }

  return (
    <div className="rv-body">
      <div className="rv-list">
        {diff.objects.map((o) => (
          <ObjectRow
            key={o.ulid}
            obj={o}
            active={o.ulid === (obj?.ulid ?? "")}
            checked={selected?.has(o.ulid) ?? true}
            selectable={selectable}
            onSelect={() => setFocus(o.ulid)}
            onToggle={() => onToggle?.(o.ulid)}
          />
        ))}
      </div>
      <div className="rv-detail">{obj && <Detail obj={obj} />}</div>
    </div>
  );
}

function badge(o: ObjectChangeDoc): string {
  if (o.renamed) return "renamed";
  if (o.severity === "breaking") return "breaking";
  return o.change;
}

function ObjectRow({
  obj,
  active,
  checked,
  selectable,
  onSelect,
  onToggle,
}: {
  obj: ObjectChangeDoc;
  active: boolean;
  checked: boolean;
  selectable?: boolean;
  onSelect: () => void;
  onToggle: () => void;
}) {
  return (
    <div className={"rv-obj" + (active ? " active" : "")} onClick={onSelect}>
      <div className="rv-obj-head">
        {selectable && (
          <input
            type="checkbox"
            checked={checked}
            onClick={(e) => e.stopPropagation()}
            onChange={onToggle}
            aria-label={`include ${obj.name_before ?? obj.name_after}`}
          />
        )}
        <span className="rv-obj-name">{obj.name_before ?? obj.name_after}</span>
        <span className="rv-obj-kind">{obj.object_kind_label}</span>
        <span className={"sev " + (obj.renamed ? "renamed" : obj.severity)}>
          {obj.severity === "breaking" && "⚠ "}
          {badge(obj)}
        </span>
      </div>
      <ul className="rv-obj-fields">
        {obj.fields.map((f, i) => (
          <li key={f.field + i}>{f.label}</li>
        ))}
        {obj.children.flatMap((c) =>
          c.fields.map((f, i) => (
            <li key={c.ulid + i}>
              {f.label}: {c.name_before ?? c.name_after}
            </li>
          )),
        )}
      </ul>
    </div>
  );
}

function Detail({ obj }: { obj: ObjectChangeDoc }) {
  const children = obj.children.flatMap((c) =>
    c.fields.map((f) => ({ field: f, on: c.name_before ?? c.name_after ?? "" })),
  );
  return (
    <>
      <h2>
        {obj.renamed ? `${obj.name_before} → ${obj.name_after}` : (obj.name_before ?? obj.name_after)}
      </h2>
      <div className="rv-detail-kind">
        {obj.object_kind_label}
        {obj.renamed && " · renamed"}
        {obj.path && <span className="rv-src"> · {obj.path}</span>}
      </div>
      {obj.fields.map((f, i) => (
        <FieldDiff key={f.field + i} f={f} />
      ))}
      {children.map(({ field, on }, i) => (
        <FieldDiff key={"c" + i} f={field} on={on} />
      ))}
      {obj.severity === "breaking" && (
        <div className="rv-note">
          <span className="i">ⓘ</span>
          <span>
            This is a structural change (route B). It will need an architect's review, and
            CI will run <code>mdl drift --check</code>.
          </span>
        </div>
      )}
    </>
  );
}

/** Render by field SHAPE, never as a line diff: prose gets a side-by-side with
 *  word-level intra-diff, lists get +/− chips, scalars an inline before → after. */
export function FieldDiff({ f, on }: { f: FieldChangeDoc; on?: string }) {
  const heading = on ? `${f.label}: ${on}` : f.label;

  if (f.breaks?.length) {
    return (
      <div className="rv-section">
        <h3>{heading}</h3>
        <div className="rv-break">
          <div className="rv-break-head">
            <span>⚠</span>
            <span>{on ?? String(f.before ?? "")} removed</span>
            {f.detail && <span className="rv-break-meta">{f.detail}</span>}
          </div>
          <div className="rv-break-body">
            This is realised by {f.breaks.length} dbt model{f.breaks.length > 1 ? "s" : ""}.
            Removing it will break {f.breaks.length > 1 ? "them" : "it"}.
          </div>
          <ul className="rv-usage">
            {f.breaks.map((b) => (
              <li key={b.name}>
                <span className="m">→ {b.name}</span>
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
    const before = String(f.before ?? "");
    const after = String(f.after ?? "");
    return (
      <div className="rv-section">
        <h3>{heading}</h3>
        <div className="rv-ba">
          <div className="rv-ba-col">
            <div className="rv-ba-label">before</div>
            <div className="rv-ba-text">{wordDiff(before, after, "del")}</div>
          </div>
          <div className="rv-ba-col">
            <div className="rv-ba-label">after</div>
            <div className="rv-ba-text">{wordDiff(after, before, "ins")}</div>
          </div>
        </div>
      </div>
    );
  }

  if (Array.isArray(f.before) || Array.isArray(f.after)) {
    const b = new Set((f.before as unknown[] | null)?.map(String) ?? []);
    const a = new Set((f.after as unknown[] | null)?.map(String) ?? []);
    const added = [...a].filter((x) => !b.has(x));
    const removed = [...b].filter((x) => !a.has(x));
    return (
      <div className="rv-section">
        <h3>{heading}</h3>
        <div className="rv-chips">
          {added.map((x) => (
            <span key={"a" + x} className="rv-chip-add">
              + {x}
            </span>
          ))}
          {removed.map((x) => (
            <span key={"r" + x} className="rv-chip-del">
              − {x}
            </span>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="rv-section">
      <h3>{heading}</h3>
      <div className="rv-scalar">
        <span className="from">{fmt(f.before)}</span>
        <span className="arrow">→</span>
        <span className="to">{fmt(f.after)}</span>
      </div>
    </div>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Word-level LCS over whitespace tokens: mark only what genuinely differs, so an
 *  edit reads as "these three words changed" rather than lighting up the sentence. */
export function wordDiff(text: string, other: string, mark: "del" | "ins") {
  const a = text.split(/(\s+)/);
  const b = other.split(/(\s+)/);
  const norm = (t: string) => t.toLowerCase().replace(/[.,;:]/g, "");
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
