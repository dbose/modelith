// Tree-provider rendering test for the panel frames. VS Code calls getChildren() then
// getTreeItem() on each provider to draw a view; this runs that exact path against a
// stubbed `vscode` module and asserts the ROWS a user would see — the "does the frame
// populate, and does it show the flow" question that a contract test alone can't answer.
//
// No @vscode/test-electron: esbuild bundles the provider with `vscode` aliased to an
// in-memory stub, we drive the provider directly, and assert. Exits non-zero on failure.

import { build } from "esbuild";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

let failed = 0;
const ok = (c, m) => (c ? console.log(`  ✓ ${m}`) : (console.error(`  ✗ ${m}`), failed++));

// --- a minimal `vscode` stub: only what the tree providers touch --------------
const VSCODE_STUB = `
export const TreeItemCollapsibleState = { None: 0, Collapsed: 1, Expanded: 2 };
export class ThemeIcon { constructor(id, color) { this.id = id; this.color = color; } }
export class ThemeColor { constructor(id) { this.id = id; } }
export class MarkdownString { constructor(v) { this.value = v; } }
export class TreeItem {
  constructor(label, collapsibleState) { this.label = label; this.collapsibleState = collapsibleState; }
}
export class EventEmitter { constructor(){ this.event = () => ({ dispose(){} }); } fire(){} dispose(){} }
export const window = { createOutputChannel: () => ({ appendLine(){}, append(){}, show(){} }) };
export const workspace = { getConfiguration: () => ({ get: () => undefined }) };
// ./mdl exports the provider imports — never called here (we drive getChildren directly).
export const findMdl = async () => ({ cmd: "mdl", args: [], label: "mdl" });
export const findModelDir = async () => undefined;
export const runMdl = async () => ({ code: 1, stdout: "", stderr: "" });
`;

const here = new URL(".", import.meta.url).pathname;
const dir = mkdtempSync(join(tmpdir(), "mdl-treeview-"));
const stubPath = join(dir, "vscode-stub.mjs");
writeFileSync(stubPath, VSCODE_STUB);

async function load(srcRel) {
  const res = await build({
    entryPoints: [join(here, "..", "src", srcRel)],
    bundle: true,
    format: "esm",
    platform: "node",
    write: false,
    // the provider imports ./mdl (findMdl/runMdl) — stub the whole thing; we drive
    // getChildren() directly with injected status, never touching the CLI here.
    plugins: [
      {
        name: "stub-imports",
        setup(b) {
          b.onResolve({ filter: /^vscode$/ }, () => ({ path: stubPath }));
          b.onResolve({ filter: /\.\/mdl$/ }, () => ({ path: stubPath })); // exports enough to import
        },
      },
    ],
  });
  const file = join(dir, srcRel.replace(/[/.]/g, "_") + ".mjs");
  writeFileSync(file, res.outputFiles[0].text);
  return import(pathToFileURL(file).href);
}

function labels(items) {
  // getTreeItem(node).label for each row, as the user would read them
  return items.map((n) => n.label);
}

const { OntologyProvider } = await load("ontologyView.ts");

// A realistic status payload: 2 to review (one high, one low confidence), 1 accepted,
// coverage with an uncovered term.
const STATUS = {
  proposed: [
    { id: "1", name: "Counterparty", kind: "conceptual_entity", uri: "https://x/Party",
      predicate: "skos:closeMatch", layer: "industry", confidence: 0.9, resolved_via: "demo" },
    { id: "2", name: "Instrument", kind: "conceptual_entity", uri: "https://x/Instrument",
      predicate: "skos:closeMatch", layer: "industry", confidence: 0.3, resolved_via: "demo" },
  ],
  accepted: [
    { id: "3", name: "Benchmark", kind: "conceptual_entity", uri: "https://x/Index",
      predicate: "skos:exactMatch", layer: "industry", confidence: null, resolved_via: "demo" },
  ],
  coverage: { coverage_pct: 66.7, total_core: 3, core_with_industry: 2, core_exempt: 0,
    core_uncovered: ["Portfolio"] },
};

console.log("Ontology frame — sections show the flow");
{
  const p = new OntologyProvider({ appendLine() {}, append() {}, show() {} });
  p.status = STATUS; // inject (bypass the CLI); getChildren renders from it

  const top = p.getChildren();
  const topItems = top.map((n) => p.getTreeItem(n));
  const topLabels = labels(topItems);
  ok(topLabels.some((l) => l.startsWith("To review (2)")), `review section counts pending: ${topLabels}`);
  ok(topLabels.some((l) => l.startsWith("Accepted (1)")), "accepted section shows the done side");
  ok(topLabels.some((l) => l.startsWith("Industry coverage")), "coverage section present");

  // review section is EXPANDED (state 2); accepted/coverage COLLAPSED (state 1)
  const review = top.find((n) => n.id === "review");
  const accepted = top.find((n) => n.id === "accepted");
  ok(p.getTreeItem(review).collapsibleState === 2, "To review is expanded (the action stage)");
  ok(p.getTreeItem(accepted).collapsibleState === 1, "Accepted is collapsed (reassurance)");

  // review children are the two proposals, gated for inline Promote/Reject
  const reviewRows = p.getChildren(review);
  const reviewItems = reviewRows.map((n) => p.getTreeItem(n));
  ok(reviewRows.length === 2, "two proposals under To review");
  ok(reviewItems.every((ti) => ti.contextValue === "ontologyProposal"),
    "proposed rows gate the inline Promote/Reject actions");
  ok(reviewItems[0].label.includes("Counterparty") && reviewItems[0].description.includes("90%"),
    "proposal row shows name → uri and a confidence badge");

  // accepted children carry the accepted contextValue (no promote/reject), green state
  const acceptedRows = p.getChildren(accepted);
  const acceptedItems = acceptedRows.map((n) => p.getTreeItem(n));
  ok(acceptedItems[0].contextValue === "ontologyAccepted", "accepted row has no review actions");

  // proposalOf resolves an alignment node (what the promote/reject commands receive)
  ok(p.proposalOf(reviewRows[0])?.name === "Counterparty", "proposalOf resolves the row's alignment");
  ok(p.proposalOf(review) === undefined, "proposalOf ignores a section header");
}

console.log("\nOntology frame — empty and resting states");
{
  const p = new OntologyProvider({ appendLine() {}, append() {}, show() {} });
  p.status = { proposed: [], accepted: [], coverage: { coverage_pct: 100, total_core: 0,
    core_with_industry: 0, core_exempt: 0, core_uncovered: [] } };
  const top = p.getChildren();
  const review = top.find((n) => n.id === "review");
  ok(p.getTreeItem(review).label === "To review (0)", "empty review still shows a counted section");
  const child = p.getChildren(review)[0];
  ok(p.getTreeItem(child).label.startsWith("Nothing to review"), "empty review shows a resting row, not blank");
  ok(!top.some((n) => n.id === "accepted"), "no Accepted section when there are none");

  const noData = new OntologyProvider({ appendLine() {}, append() {}, show() {} });
  const rows = noData.getChildren(); // status undefined
  ok(p.getTreeItem(rows[0]).label.includes("No ontology data"), "no-model state explains itself");
}

rmSync(dir, { recursive: true, force: true });
console.log();
if (failed) { console.error(`✗ ${failed} assertion(s) failed`); process.exit(1); }
console.log("✓ all tree-view rendering assertions passed");
