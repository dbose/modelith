import type { ModelDiffDoc, ObjectChangeDoc } from "../types";

// A realistic ModelDiffDoc with one of each change kind, for the review-view tests.
// Shapes mirror mdl_core.diff_render.render_json exactly.

function obj(o: Partial<ObjectChangeDoc> & Pick<ObjectChangeDoc, "ulid" | "change">): ObjectChangeDoc {
  return {
    object_kind: "logical_entity",
    object_kind_label: "Entity",
    name_before: null,
    name_after: null,
    renamed: false,
    severity: "additive",
    path: null,
    fields: [],
    children: [],
    ...o,
  };
}

export function makeDiff(): ModelDiffDoc {
  return {
    ok: true,
    base: { ref: "HEAD", sha: "abc", label: "on disk" },
    head: { ref: null, label: "your staged changes" },
    counts: { entities: 4 },
    max_severity: "breaking",
    has_breaking: true,
    config: [],
    objects: [
      // a modified entity with TWO field changes (per-field selection target)
      obj({
        ulid: "E_MOD",
        change: "modified",
        name_before: "counterparty",
        name_after: "counterparty",
        severity: "additive",
        fields: [
          { field: "definition", kind: "definition_changed", severity: "cosmetic", label: "Definition changed", detail: "", before: "a party", after: "a trading party" },
          { field: "domain", kind: "attribute_domain_changed", severity: "additive", label: "Domain changed", detail: "", before: "string", after: "text" },
        ],
      }),
      // an added entity (additions filter)
      obj({ ulid: "E_ADD", change: "added", name_after: "instrument", severity: "additive" }),
      // a removed entity that BREAKS dbt models (deletions + conflicts filters)
      obj({
        ulid: "E_DEL",
        change: "removed",
        name_before: "legacy",
        severity: "breaking",
        fields: [
          {
            field: "entity",
            kind: "entity_removed",
            severity: "breaking",
            label: "Entity removed",
            detail: "",
            before: "legacy",
            after: null,
            breaks: [{ name: "dim_legacy", target: "snowflake", materialization: "table" }],
          },
        ],
      }),
      // a renamed entity (moves filter)
      obj({ ulid: "E_REN", change: "modified", renamed: true, name_before: "old_name", name_after: "new_name", severity: "cosmetic" }),
    ],
  };
}
