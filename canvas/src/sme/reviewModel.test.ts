import { describe, expect, it } from "vitest";
import { makeDiff } from "../test/diffFixture";
import {
  allFieldKeys,
  fieldKey,
  filterCounts,
  filterObjects,
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
