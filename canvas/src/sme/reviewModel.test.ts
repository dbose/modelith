import { describe, expect, it } from "vitest";
import { makeDiff } from "../test/diffFixture";
import type { ModelDiffDoc, ObjectChangeDoc } from "../types";
import {
  allFieldKeys,
  fieldKey,
  filterCounts,
  filterImportChanges,
  filterObjects,
  includedEntityNames,
  includedUlids,
  objectFieldKeys,
  objectIncluded,
  objectSelectionState,
  toggleField,
  toggleObject,
} from "./reviewModel";

describe("filters (view-only)", () => {
  const diff = makeDiff();

  it("counts each change category", () => {
    const c = filterCounts(diff);
    expect(c.all).toBe(4);
    expect(c.additions).toBe(1); // E_ADD
    expect(c.deletions).toBe(1); // E_DEL
    expect(c.moves).toBe(1); // E_REN
    expect(c.modifications).toBe(1); // E_MOD (renamed E_REN excluded)
    expect(c.conflicts).toBe(1); // E_DEL is breaking
  });

  it("filters the visible list without touching anything else", () => {
    expect(filterObjects(diff, "additions").map((o) => o.ulid)).toEqual(["E_ADD"]);
    expect(filterObjects(diff, "conflicts").map((o) => o.ulid)).toEqual(["E_DEL"]);
    expect(filterObjects(diff, "all")).toHaveLength(4);
  });
});

describe("per-field selection", () => {
  const diff = makeDiff();

  it("gives a modified object one key per field", () => {
    const mod = diff.objects.find((o) => o.ulid === "E_MOD")!;
    expect(objectFieldKeys(mod)).toEqual([
      fieldKey("E_MOD", "definition"),
      fieldKey("E_MOD", "domain"),
    ]);
  });

  it("gives an added object with no field rows a single '*' key", () => {
    const add = diff.objects.find((o) => o.ulid === "E_ADD")!;
    expect(objectFieldKeys(add)).toEqual([fieldKey("E_ADD", "*")]);
  });

  it("derives object state from its selected fields", () => {
    const mod = diff.objects.find((o) => o.ulid === "E_MOD")!;
    const [k1, k2] = objectFieldKeys(mod);
    expect(objectSelectionState(mod, new Set([k1, k2]))).toBe("all");
    expect(objectSelectionState(mod, new Set([k1]))).toBe("some");
    expect(objectSelectionState(mod, new Set())).toBe("none");
    // included when ≥1 field selected
    expect(objectIncluded(mod, new Set([k1]))).toBe(true);
    expect(objectIncluded(mod, new Set())).toBe(false);
  });

  it("toggleField flips one field immutably", () => {
    const k = fieldKey("E_MOD", "domain");
    const start = new Set<string>();
    const on = toggleField(k, start);
    expect(on.has(k)).toBe(true);
    expect(start.has(k)).toBe(false); // input untouched
    expect(toggleField(k, on).has(k)).toBe(false);
  });

  it("toggleObject turns all fields off when all on, else all on", () => {
    const mod = diff.objects.find((o) => o.ulid === "E_MOD")!;
    const keys = objectFieldKeys(mod);
    const allOn = new Set(keys);
    expect([...toggleObject(mod, allOn)]).toHaveLength(0); // all -> none
    const someOn = new Set([keys[0]]);
    expect(objectSelectionState(mod, toggleObject(mod, someOn))).toBe("all"); // some -> all
  });

  it("includedUlids reflects field-level exclusion", () => {
    const all = allFieldKeys(diff);
    expect(includedUlids(diff, all).sort()).toEqual(["E_ADD", "E_DEL", "E_MOD", "E_REN"]);
    // deselect both fields of E_MOD -> it drops out, others stay
    let sel = all;
    for (const k of objectFieldKeys(diff.objects.find((o) => o.ulid === "E_MOD")!)) {
      sel = toggleField(k, sel);
    }
    expect(includedUlids(diff, sel).sort()).toEqual(["E_ADD", "E_DEL", "E_REN"]);
  });
});

describe("import write-filter (apply only the kept entities, by name)", () => {
  // a minimal import command list like model_to_commands emits
  const changes = [
    { op: "create_subject_area", payload: { id: "SA1", name: "Core" } },
    { op: "create_entity", payload: { id: "e_cp", name: "counterparty" } },
    { op: "add_attribute", payload: { entity_id: "e_cp", id: "a1", name: "cp_id" } },
    { op: "create_entity", payload: { id: "e_tr", name: "trade" } },
    { op: "add_attribute", payload: { entity_id: "e_tr", id: "a2", name: "trade_id" } },
    { op: "create_relationship", payload: { id: "r1", from_entity: "e_tr", to_entity: "e_cp" } },
  ];

  it("keeps scaffolding + the kept entity + its attributes; drops the rest", () => {
    const out = filterImportChanges(changes, new Set(["counterparty"]));
    const ids = out.map((c) => `${c.op}:${c.payload.id}`);
    expect(ids).toContain("create_subject_area:SA1");
    expect(ids).toContain("create_entity:e_cp");
    expect(ids).toContain("add_attribute:a1"); // counterparty's attr (id a1)
    // trade and its attribute are dropped
    expect(ids).not.toContain("create_entity:e_tr");
    expect(ids).not.toContain("add_attribute:a2");
    // the relationship references trade (dropped) -> dropped
    expect(out.some((c) => c.op === "create_relationship")).toBe(false);
  });

  it("keeps a relationship only when BOTH endpoints are kept", () => {
    const out = filterImportChanges(changes, new Set(["counterparty", "trade"]));
    expect(out.some((c) => c.op === "create_relationship")).toBe(true);
  });

  it("nothing selected writes nothing", () => {
    expect(filterImportChanges(changes, new Set())).toHaveLength(0);
  });

  it("includedEntityNames reads kept logical-entity names from the diff", () => {
    const entity = (ulid: string, name: string): ObjectChangeDoc => ({
      ulid, object_kind: "logical_entity", object_kind_label: "entity",
      change: "added", name_before: null, name_after: name, renamed: false,
      severity: "additive", path: null,
      fields: [{ field: "*", kind: "added", severity: "additive", label: "", detail: "", before: null, after: null }],
      children: [],
    });
    const diff = {
      ok: true, base: { ref: "", sha: "", label: "" }, head: { ref: null, label: "" },
      counts: {}, max_severity: "additive", has_breaking: false,
      objects: [entity("x", "counterparty"), entity("y", "trade")], config: [],
    } as unknown as ModelDiffDoc;
    const all = allFieldKeys(diff);
    expect([...includedEntityNames(diff, all)].sort()).toEqual(["counterparty", "trade"]);
  });

  it("includedEntityNames excludes a MODIFIED (already-existing) entity", () => {
    const obj = (ulid: string, name: string, change: "added" | "modified"): ObjectChangeDoc => ({
      ulid, object_kind: "logical_entity", object_kind_label: "entity",
      change, name_before: change === "added" ? null : name, name_after: name, renamed: false,
      severity: "additive", path: null,
      fields: [{ field: "*", kind: change, severity: "additive", label: "", detail: "", before: null, after: null }],
      children: [],
    });
    const diff = {
      ok: true, base: { ref: "", sha: "", label: "" }, head: { ref: null, label: "" },
      counts: {}, max_severity: "additive", has_breaking: false,
      objects: [obj("x", "counterparty", "modified"), obj("y", "trade", "added")], config: [],
    } as unknown as ModelDiffDoc;
    const all = allFieldKeys(diff);
    // counterparty already exists (modified) -> not created; only the new entity is kept
    expect([...includedEntityNames(diff, all)]).toEqual(["trade"]);
  });
});
