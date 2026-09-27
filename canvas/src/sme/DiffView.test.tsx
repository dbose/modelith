import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { makeDiff } from "../test/diffFixture";
import { DiffView } from "./DiffView";
import { allFieldKeys, fieldKey, includedUlids, toggleField } from "./reviewModel";

/** The list-pane row for an entity (its name also appears in the detail <h2>, so scope
 *  to the .rv-list to disambiguate). */
function listRow(name: string): HTMLElement {
  const list = document.querySelector(".rv-list") as HTMLElement;
  return within(list).getByText(name).closest(".rv-obj") as HTMLElement;
}

/** A host harness mirroring how ReviewScreen would drive per-field selection: it owns the
 *  field-keyed Set and exposes the derived included ULIDs so a test can assert them. */
function PerFieldHost() {
  const diff = makeDiff();
  const [selected, setSelected] = useState<Set<string>>(() => allFieldKeys(diff));
  const included = includedUlids(diff, selected);
  return (
    <div>
      <div data-testid="included">{included.sort().join(",")}</div>
      <DiffView
        diff={diff}
        selectable
        selected={selected}
        onToggleField={(ulid, field) => setSelected((s) => toggleField(fieldKey(ulid, field), s))}
      />
    </div>
  );
}

describe("DiffView — filters (view only)", () => {
  it("shows all changes by default and filters visibility without dropping selection", () => {
    render(<PerFieldHost />);
    const list = () => document.querySelector(".rv-list") as HTMLElement;
    // all four objects present initially
    expect(within(list()).getByText("counterparty")).toBeInTheDocument();
    expect(within(list()).getByText("instrument")).toBeInTheDocument();
    expect(within(list()).getByText("legacy")).toBeInTheDocument();

    // click the Additions filter -> only the added entity is visible in the list
    fireEvent.click(screen.getByRole("tab", { name: /Additions/ }));
    expect(within(list()).getByText("instrument")).toBeInTheDocument();
    expect(within(list()).queryByText("counterparty")).not.toBeInTheDocument();
    expect(within(list()).queryByText("legacy")).not.toBeInTheDocument();

    // filtering did NOT change selection: everything is still included
    expect(screen.getByTestId("included")).toHaveTextContent("E_ADD,E_DEL,E_MOD,E_REN");

    // back to All -> everything visible again
    fireEvent.click(screen.getByRole("tab", { name: /^All/ }));
    expect(within(list()).getByText("counterparty")).toBeInTheDocument();
  });

  it("only offers filters that have matches", () => {
    render(<PerFieldHost />);
    // the fixture has one of each, so every filter with count>0 shows
    for (const label of ["All", "Additions", "Modifications", "Renames", "Deletions", "Conflicts"]) {
      expect(screen.getByRole("tab", { name: new RegExp(label) })).toBeInTheDocument();
    }
  });
});

describe("DiffView — per-field selection", () => {
  it("unchecking every field of an object excludes it from the action", () => {
    render(<PerFieldHost />);
    // focus the modified entity (it has two field changes)
    fireEvent.click(listRow("counterparty"));
    // its two per-field checkboxes live in the detail pane
    const defBox = screen.getByLabelText("include change: definition");
    const domBox = screen.getByLabelText("include change: domain");
    expect(defBox).toBeChecked();
    expect(domBox).toBeChecked();

    // uncheck both -> E_MOD drops out, others stay
    fireEvent.click(defBox);
    fireEvent.click(domBox);
    expect(screen.getByTestId("included")).toHaveTextContent("E_ADD,E_DEL,E_REN");
    expect(screen.getByTestId("included")).not.toHaveTextContent("E_MOD");
  });

  it("the object header checkbox reflects a partial (indeterminate) selection", () => {
    render(<PerFieldHost />);
    fireEvent.click(listRow("counterparty"));
    // uncheck ONE field -> the object stays included, header is indeterminate
    fireEvent.click(screen.getByLabelText("include change: definition"));
    expect(screen.getByTestId("included")).toHaveTextContent("E_MOD");
    const row = listRow("counterparty");
    const header = within(row as HTMLElement).getByRole("checkbox") as HTMLInputElement;
    expect(header.indeterminate).toBe(true);
  });

  it("the header checkbox toggles all of an object's fields at once", () => {
    render(<PerFieldHost />);
    fireEvent.click(listRow("counterparty"));
    const row = listRow("counterparty");
    const header = within(row as HTMLElement).getByRole("checkbox");
    // header on -> off excludes the whole object
    fireEvent.click(header);
    expect(screen.getByTestId("included")).not.toHaveTextContent("E_MOD");
  });
});
