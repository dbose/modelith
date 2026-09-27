import { useMemo, useState } from "react";
import type { FieldChangeDoc, ModelDiffDoc, ObjectChangeDoc } from "../types";
import {
  type ObjectSelectionState,
  type ReviewFilter,
  REVIEW_FILTERS,
  fieldKey,
  filterCounts,
  filterObjects,
  objectSelectionState,
} from "./reviewModel";

/** The changed-object list and detail pane (plan §M1/M2), Phase 1: display FILTERS +
 * per-FIELD selection.
 *
 * One list of only what changed, grouped by object, each change a plain-language
 * sentence produced server-side (mdl_core.diff builds the label and detail, so the CLI,
 * the PR body and this screen never drift). Filters are a VIEW concern — they change what
 * is visible, never what is selected (a hidden change is never silently dropped).
 *
 * Selection has two modes, chosen by which callback the host passes:
 *  - per-FIELD (`onToggleField` / field-keyed `selected`): the new Phase 1 model — each
 *    change is independently checkable and the object header shows a tri-state.
 *  - per-OBJECT (`onToggle` / ulid-keyed `selected`): the original propose flow, kept so
 *    existing callers work unchanged. */
export function DiffView({
  diff,
  selectable,
  selected,
  onToggle,
  onToggleField,
}: {
  diff: ModelDiffDoc;
  /** show selection checkboxes; off when a create_* is staged */
  selectable?: boolean;
  /** the selected set — object ULIDs (per-object mode) or field keys (per-field mode) */
  selected?: Set<string>;
  /** per-OBJECT toggle (original mode). When set, checkboxes are whole-object. */
  onToggle?: (ulid: string) => void;
  /** per-FIELD toggle (Phase 1). When set, checkboxes are per-field with an object
   *  tri-state header; takes precedence over onToggle. */
  onToggleField?: (ulid: string, field: string) => void;
}) {
  const [filter, setFilter] = useState<ReviewFilter>("all");
  const visible = useMemo(() => filterObjects(diff, filter), [diff, filter]);
  const counts = useMemo(() => filterCounts(diff), [diff]);
  const [focus, setFocus] = useState<string | null>(diff.objects[0]?.ulid ?? null);
  const obj = useMemo(
    () => visible.find((o) => o.ulid === focus) ?? visible[0] ?? diff.objects[0] ?? null,
    [visible, diff, focus],
  );
  const perField = !!onToggleField;

  if (!diff.objects.length) {
    return <p className="sme-placeholder">No model changes against {diff.base.label}.</p>;
  }

  return (
    <div className="rv-wrap">
      {selectable !== undefined && (
        <div className="rv-filters" role="tablist" aria-label="Filter changes">
          {REVIEW_FILTERS.filter((f) => f.id === "all" || counts[f.id] > 0).map((f) => (
            <button
              key={f.id}
              role="tab"
              aria-selected={filter === f.id}
              className={"rv-filter" + (filter === f.id ? " active" : "")}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
              <span className="rv-filter-count">{counts[f.id]}</span>
            </button>
          ))}
        </div>
      )}
      <div className="rv-body">
        <div className="rv-list">
          {visible.length === 0 ? (
            <p className="sme-placeholder">No changes match this filter.</p>
          ) : (
            visible.map((o) => (
              <ObjectRow
                key={o.ulid}
                obj={o}
                active={o.ulid === (obj?.ulid ?? "")}
                selectable={selectable}
                perField={perField}
                objState={perField ? objectSelectionState(o, selected ?? new Set()) : undefined}
                checkedObject={selected?.has(o.ulid) ?? true}
                onSelect={() => setFocus(o.ulid)}
                onToggleObject={() => onToggle?.(o.ulid)}
                onToggleField={onToggleField}
              />
            ))
          )}
        </div>
        <div className="rv-detail">
          {obj && (
            <Detail
              obj={obj}
              selectable={selectable}
              perField={perField}
              selected={selected}
              onToggleField={onToggleField}
            />
          )}
        </div>
      </div>
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
  selectable,
  perField,
  objState,
  checkedObject,
  onSelect,
  onToggleObject,
  onToggleField,
}: {
  obj: ObjectChangeDoc;
  active: boolean;
  selectable?: boolean;
  perField: boolean;
  /** the object's tri-state in per-field mode */
  objState?: ObjectSelectionState;
  /** whole-object checked, in per-object mode */
  checkedObject: boolean;
  onSelect: () => void;
  onToggleObject: () => void;
  onToggleField?: (ulid: string, field: string) => void;
}) {
  // In per-field mode the header checkbox toggles ALL of the object's fields at once, and
  // shows indeterminate when only some are selected.
  const setIndeterminate = (el: HTMLInputElement | null) => {
    if (el && perField) el.indeterminate = objState === "some";
  };
  const headerChecked = perField ? objState !== "none" : checkedObject;
  const toggleHeader = () => {
    if (!perField) return onToggleObject();
    // toggle every field key of this object via the field callback
    for (const f of obj.fields) onToggleField?.(obj.ulid, f.field);
    for (const c of obj.children) for (const f of c.fields) onToggleField?.(obj.ulid, `${c.ulid}.${f.field}`);
    if (!obj.fields.length && !obj.children.length) onToggleField?.(obj.ulid, "*");
  };

  return (
    <div className={"rv-obj" + (active ? " active" : "")} onClick={onSelect}>
      <div className="rv-obj-head">
        {selectable && (
          <input
            type="checkbox"
            ref={setIndeterminate}
            checked={headerChecked}
            onClick={(e) => e.stopPropagation()}
            onChange={toggleHeader}
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

function Detail({
  obj,
  selectable,
  perField,
  selected,
  onToggleField,
}: {
  obj: ObjectChangeDoc;
  selectable?: boolean;
  perField?: boolean;
  selected?: Set<string>;
  onToggleField?: (ulid: string, field: string) => void;
}) {
  const children = obj.children.flatMap((c) =>
    c.fields.map((f) => ({ field: f, on: c.name_before ?? c.name_after ?? "", key: `${c.ulid}.${f.field}` })),
  );
  // one selectable row per field, only when the host wired per-field selection
  const pick = (field: string, node: React.ReactNode) => {
    if (!selectable || !perField) return node;
    const key = fieldKey(obj.ulid, field);
    return (
      <div className="rv-field-pick">
        <input
          type="checkbox"
          checked={selected?.has(key) ?? true}
          onChange={() => onToggleField?.(obj.ulid, field)}
          aria-label={`include change: ${field}`}
        />
        <div className="rv-field-body">{node}</div>
      </div>
    );
  };
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
        <div key={f.field + i}>{pick(f.field, <FieldDiff f={f} />)}</div>
      ))}
      {children.map(({ field, on, key }, i) => (
        <div key={"c" + i}>{pick(key, <FieldDiff f={field} on={on} />)}</div>
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
