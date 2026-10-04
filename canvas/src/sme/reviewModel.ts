// Pure helpers behind the review view: change filtering and per-field selection.
// Kept out of the component so the semantics are unit-testable on their own.

import type { ChangeType, ModelDiffDoc, ObjectChangeDoc } from "../types";

// The display filters (competitor parity): a VIEW concern only — filtering changes
// what is visible, never what is selected for the action (so a hidden change can
// never be silently dropped from a proposal).
export type ReviewFilter = "all" | "additions" | "modifications" | "moves" | "deletions" | "conflicts";

export const REVIEW_FILTERS: { id: ReviewFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "additions", label: "Additions" },
  { id: "modifications", label: "Modifications" },
  { id: "moves", label: "Renames" },
  { id: "deletions", label: "Deletions" },
  { id: "conflicts", label: "Conflicts" },
];

/** Does an object match the active filter? `conflicts` maps to breaking severity (the
 *  only conflict signal a 2-way diff carries); `moves` maps to a rename. */
export function objectMatchesFilter(o: ObjectChangeDoc, f: ReviewFilter): boolean {
  switch (f) {
    case "all":
      return true;
    case "additions":
      return o.change === "added";
    case "deletions":
      return o.change === "removed";
    case "moves":
      return o.renamed;
    case "modifications":
      return o.change === "modified" && !o.renamed;
    case "conflicts":
      return o.severity === "breaking";
  }
}

/** The objects visible under a filter. Selection is untouched — this is view-only. */
export function filterObjects(diff: ModelDiffDoc, f: ReviewFilter): ObjectChangeDoc[] {
  return f === "all" ? diff.objects : diff.objects.filter((o) => objectMatchesFilter(o, f));
}

/** Count per filter, for filter chips that show how many changes each reveals. */
export function filterCounts(diff: ModelDiffDoc): Record<ReviewFilter, number> {
  const out = {} as Record<ReviewFilter, number>;
  for (const { id } of REVIEW_FILTERS) {
    out[id] = id === "all" ? diff.objects.length : diff.objects.filter((o) => objectMatchesFilter(o, id)).length;
  }
  return out;
}

// --- per-field selection -----------------------------------------------------------
// Selection is keyed by (object ulid, field). An object is INCLUDED when at least one
// of its fields is selected; unchecking every field excludes the object. The object
// header shows a tri-state derived from its fields.

/** The stable key for one field change of one object. A whole-object change (added /
 *  removed, which carry object-level identity rather than a field) uses the "*" field. */
export function fieldKey(ulid: string, field: string): string {
  return `${ulid}\u0000${field}`;
}

/** Every selectable field key of an object, including its children's fields. An
 *  added/removed object with no field rows still gets one "*" key so it is selectable. */
export function objectFieldKeys(o: ObjectChangeDoc): string[] {
  const keys = o.fields.map((f) => fieldKey(o.ulid, f.field));
  for (const c of o.children) {
    for (const f of c.fields) keys.push(fieldKey(o.ulid, `${c.ulid}.${f.field}`));
  }
  return keys.length ? keys : [fieldKey(o.ulid, "*")];
}

export type ObjectSelectionState = "all" | "some" | "none";

/** The tri-state of an object, derived from which of its field keys are selected. */
export function objectSelectionState(o: ObjectChangeDoc, selected: Set<string>): ObjectSelectionState {
  const keys = objectFieldKeys(o);
  const on = keys.filter((k) => selected.has(k)).length;
  if (on === 0) return "none";
  if (on === keys.length) return "all";
  return "some";
}

/** An object is included in the action when ≥1 of its fields is selected. */
export function objectIncluded(o: ObjectChangeDoc, selected: Set<string>): boolean {
  return objectSelectionState(o, selected) !== "none";
}

/** The ULIDs the action should apply to, given a field-level selection. */
export function includedUlids(diff: ModelDiffDoc, selected: Set<string>): string[] {
  return diff.objects.filter((o) => objectIncluded(o, selected)).map((o) => o.ulid);
}

/** The NEW logical-entity names the reviewer kept, for filtering an import's command list.
 *  Only `added` entities are returned: the import emits `create_entity` for each entity, which
 *  fails for one that already exists in the current model (a name-matched "modified" object), so
 *  an import applies only its genuinely-new entities — a modified existing entity is left as-is
 *  (shown in the review, not clobbered). Name is the stable join: the diff doc carries remapped
 *  ULIDs (base's ULID for a name-matched object) while the import commands carry fresh ULIDs. */
export function includedEntityNames(diff: ModelDiffDoc, selected: Set<string>): Set<string> {
  const names = new Set<string>();
  for (const o of diff.objects) {
    if (o.object_kind !== "logical_entity") continue;
    if (o.change !== "added") continue; // only new entities can be created by the import
    if (!objectIncluded(o, selected)) continue;
    const n = o.name_after ?? o.name_before;
    if (n) names.add(n);
  }
  return names;
}

type ImportCommand = { op: string; payload: Record<string, unknown> };

/** Filter an erwin import's command list to the entities the reviewer kept (by name).
 *  Scaffolding ops (subject areas, domains, categories, members) always apply; an entity and
 *  its attributes apply when the entity's name is kept; a relationship or key group applies
 *  only when EVERY entity it references is kept. `model_to_commands` emits entities before the
 *  attributes/relationships that reference them, so a single pass can track which entity ids
 *  survive. */
export function filterImportChanges(
  changes: ImportCommand[],
  keepNames: Set<string>,
): ImportCommand[] {
  if (keepNames.size === 0) return []; // nothing selected -> write nothing
  const keptEntityIds = new Set<string>(); // import-ULIDs of entities we keep
  const out: ImportCommand[] = [];
  const refEntityIds = (p: Record<string, unknown>): string[] =>
    [p.entity_id, p.from_entity, p.to_entity, p.from, p.to].filter(
      (v): v is string => typeof v === "string",
    );
  for (const c of changes) {
    const p = c.payload;
    switch (c.op) {
      case "create_entity": {
        const name = typeof p.name === "string" ? p.name : "";
        const id = typeof p.id === "string" ? p.id : "";
        if (keepNames.has(name)) {
          keptEntityIds.add(id);
          out.push(c);
        }
        break;
      }
      case "add_attribute": {
        const eid = typeof p.entity_id === "string" ? p.entity_id : "";
        if (keptEntityIds.has(eid)) out.push(c);
        break;
      }
      case "create_relationship":
      case "create_key_group": {
        const refs = refEntityIds(p);
        if (refs.length > 0 && refs.every((id) => keptEntityIds.has(id))) out.push(c);
        break;
      }
      default:
        // scaffolding (create_subject_area, create_domain, create_category,
        // set_subject_area_members, …) always applies
        out.push(c);
    }
  }
  return out;
}

/** All field keys across the whole diff — the initial "everything selected" set. */
export function allFieldKeys(diff: ModelDiffDoc): Set<string> {
  const s = new Set<string>();
  for (const o of diff.objects) for (const k of objectFieldKeys(o)) s.add(k);
  return s;
}

/** Toggle every field of an object at once (the header checkbox): if all are on, turn
 *  them off; otherwise turn them all on. Returns a NEW set (never mutates the input). */
export function toggleObject(o: ObjectChangeDoc, selected: Set<string>): Set<string> {
  const next = new Set(selected);
  const keys = objectFieldKeys(o);
  const allOn = keys.every((k) => next.has(k));
  for (const k of keys) {
    if (allOn) next.delete(k);
    else next.add(k);
  }
  return next;
}

/** Toggle one field key. Returns a NEW set. */
export function toggleField(key: string, selected: Set<string>): Set<string> {
  const next = new Set(selected);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

// A pluggable action the review view offers (Propose / Accept / Reconcile / Apply
// import). The view stays source-agnostic; the host wires the label + what apply does.
export interface ReviewAction {
  id: string;
  label: string;
  primary?: boolean;
  /** disabled reason, or undefined when enabled */
  disabledReason?: string;
  apply: (selectedUlids: string[]) => void;
}

export type { ChangeType };
