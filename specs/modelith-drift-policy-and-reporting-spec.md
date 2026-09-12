# Modelith — Custom policy rules & stakeholder drift reporting

Companion to [`modelith-drift-spec.md`](modelith-drift-spec.md). That spec covers *how drift
surfaces* (Problems panel, tree view, code actions, chat, MCP). This one answers two questions
that extend it:

1. **How do I encode custom rules as part of the LDM** — e.g. "we don't use surrogate keys" —
   and check both the model *and* drift against them?
2. **How do I surface drift richly in VS Code and generate a report to share with the team /
   stakeholders?**

---

## Part 1 — Custom policy rules ("modeling standards as code")

### The problem, precisely

A rule like *"we don't use surrogate keys"* is a **policy about the model**, and it wants to be
checked in **two places that share one definition**:

- **Against the model** (validation): did *we* model a surrogate key? → a lint finding on the
  offending attribute.
- **Against drift** (reconciliation): did the *warehouse* introduce something the policy forbids
  (a new `*_sk` column dbt added)? → a drift finding that is elevated because it violates a
  declared standard, not just because the schema changed.

So this is **not a drift-only feature.** It is a policy layer evaluated by both `validate` and
`drift`. Designing it drift-only would duplicate the rule.

### What exists to build on

- **No policy mechanism today.** `validate()` (`packages/core/src/mdl_core/validate.py:25`) is a
  fixed sequence of hardcoded `_check_*` functions with literal MDL-* codes — no registry, no
  config-driven rules.
- **But two proven precedents** for "user declares a convention → tool enforces it":
  - the **`naming` block** in `mdl-project.yaml`, read by `naming.py` (`lint()` reads
    `model.config.naming`) — the exact template for config-driven checking.
  - the customer-owned **`governance-profile.yaml`**, validated by `run_conformance` in CI
    (`packages/governance/src/mdl_governance/conformance.py`) — proves the "customer YAML +
    a `mdl … conformance` CI gate" pattern.
- **The IR already carries the data a rule needs:** `Attribute.role` is a `Literal["business_key",
  "surrogate_key", "attribute", "measure"]` (`ir.py:302`), `Attribute.nullable` (`ir.py:303`),
  key groups, relationships. The tool can already *see* a surrogate key — nothing asserts a
  policy about it.
- `ProjectConfig` is `extra="allow"` (`ir.py:436`) — a `policy:` block would survive parsing
  today; it just needs an engine to read it. And the reverse engine's `is_surrogate_key()`
  (`packages/reverse/src/mdl_reverse/lifting.py:102`) already encodes *how to recognise* a
  surrogate key — the policy check reuses it, it does not reinvent detection.

### Design — a declarative `policy` block, checked by one engine, surfaced everywhere

**1. Declare rules in `mdl-project.yaml`** (a new `policy:` block on `ProjectConfig`):

```yaml
policy:
  rules:
    - id: no-surrogate-keys
      check: no_attribute_role          # built-in predicate
      role: surrogate_key               # its parameter
      severity: error                   # error | warning | info
      message: "This project models natural keys only; surrogate keys are forbidden."
    - id: entity-needs-business-key
      check: every_entity_has_key
      key_type: business_key
      severity: warning
    - id: no-nullable-fk
      check: no_nullable_foreign_key
      severity: error
```

Each rule is `{id, check, <params>, severity, message?}`. `check` names a **built-in predicate**
from a small catalogue — this is data-driven config over a fixed set of safe predicates, *not*
arbitrary code execution (no eval, no plugin loading in v1). The predicate catalogue is the
extensible surface; it grows in the engine, not in user YAML.

**2. A predicate catalogue** — `packages/core/src/mdl_core/policy.py` (NEW). Each predicate is a
pure function `(model, rule) -> list[PolicyViolation]`, where a `PolicyViolation` carries the
rule id, the offending object's ULID (for path mapping), and a message. Seed catalogue, all
reading fields the IR already has:

| `check` | Checks |
|---|---|
| `no_attribute_role` | no attribute has `role == <role>` (→ "no surrogate keys") |
| `every_entity_has_key` | every logical entity has a key group of `<key_type>` (or the inferred business-key pk) |
| `no_nullable_foreign_key` | no FK attribute (a relationship's `from` columns) is `nullable` |
| `require_definition` | every object of `<kind>` has a non-null `.definition` (ties into the definition-gap idea) |
| `naming_regex` | object names of `<kind>` match `<pattern>` (a general-purpose escape hatch) |

**3. Evaluate in both engines, from the one catalogue:**
- **`validate`** gains one line — `_check_policy(model, diags)` in the fixed sequence
  (`validate.py:25`), emitting each violation as a `Diagnostic` with a synthetic code
  `MDL-P<nnn>` (P for policy) or the rule id, at the object's path. This is exactly how `naming`
  folds in. Now `mdl validate` fails on a policy breach like any other rule.
- **`drift`** gains a **policy pass over the projected warehouse**: after `compute_drift`, run the
  same predicates against what the *manifest* implies (a new `*_sk` column dbt added is a
  surrogate key by `is_surrogate_key`), and emit a new `DriftKind.policy_violation` item with
  `severity` from the rule. This makes "the warehouse introduced a forbidden pattern" a
  first-class drift finding, carried through the existing `DriftReport` → every surface in the
  drift spec (Problems panel, tree, chat, report) for free.

**4. Surfaces (reuse the drift spec's plumbing):**
- CLI: `mdl policy check` (model-only) and the existing `mdl validate` now includes policy; drift
  policy violations appear in `mdl drift` output.
- VS Code: policy violations are just `Diagnostic`s and `DriftItem`s — they ride the Problems
  panel, the Drift tree (a "Policy" group), and `@modelith` explanations already specified. A
  code action can offer the fix where one is deterministic (e.g. remove the surrogate-key
  attribute — but only staged/explained, never auto-applied for a destructive change).
- `@modelith /policy` (chat) and an `explain_policy()` MCP tool follow the same grounded-LLM
  pattern: the engine decides pass/fail; the LLM explains *why the rule exists and how to
  comply*, and drafts the remediation. Never lets the LLM invent or waive a rule.

**5. Safety & scope.** v1 predicates are a **closed catalogue** (no arbitrary code from YAML —
that is a security and support boundary). The LLM never decides whether a rule passed — the
predicate does. A policy violation is explained and, where safe, a fix is *proposed* (staged via
the DecisionLedger for destructive fixes); it is never silently auto-applied. Rules are additive
and each has a `severity`, so a team can adopt them as warnings first, then promote to errors in
CI.

### Why this is the right shape

It reuses the naming-block precedent (config-driven convention checking), reuses the governance
CI-gate precedent (a `mdl policy check` gate), reuses `is_surrogate_key` (detection, already
written), and rides the entire drift surface stack for presentation. The only genuinely new code
is `policy.py` (the predicate catalogue + evaluator) and two one-line wires into `validate` and
`drift`. The `policy` block is the erwin "naming standards / model conventions" idea, made
git-native and CI-enforced.

---

## Part 2 — Stakeholder drift reporting

### The problem, precisely

The drift spec's surfaces (Problems panel, tree, chat) serve **the engineer at their desk**. A
**stakeholder / team report** is a different artifact for a different audience: a self-contained
document a data lead, an analytics-engineering manager, or a governance reviewer reads *without
VS Code open* — in a PR, in Slack, in a wiki, or as an attachment. Different audiences want
different artifacts:

| Audience | Artifact | Where |
|---|---|---|
| Reviewer on a dbt PR | Sticky markdown comment (table + mermaid subgraph) | GitHub PR |
| Team / manager | A standalone, self-contained **HTML report** | Slack, wiki, email, hosted |
| Governance / audit | The structured **JSON** + the report, archived | CI artifact store |
| Someone in the editor | The Drift view + `@modelith` (already specified) | VS Code |

### What exists to build on

- Three stdout renderers over `DriftReport` (`packages/reverse/src/mdl_reverse/render.py`):
  `render_text`, `render_json`, and **`render_markdown`** — a `## Modelith drift — <verdict>`
  heading, a `| Severity | Model | Column | Change |` table, and a **mermaid subgraph**
  (`_mermaid_subgraph`, `render.py:99`) colouring each affected model by its worst severity.
  This is the shareable seed.
- A **CI PR-comment template already exists**: `profiles/ci/mdl-drift.yml` runs
  `mdl drift --format mermaid > drift-comment.md` and posts it via the
  `marocchino/sticky-pull-request-comment` Action, plus a `--check` gate.
- **No tool-side report file / HTML export exists** — CI does it with shell redirection; Modelith
  has no comment-posting code and no HTML renderer.

### Design — one renderer family, three delivery gestures

**1. `mdl drift --report <file>` (the core new capability).** Write a **self-contained report
file** instead of only echoing to stdout. Format inferred from the extension:
- `.md` → the existing `render_markdown` (table + mermaid), written to a file.
- `.html` → a **new `render_html`** (`render.py`): a single self-contained HTML page — inline CSS,
  the severity summary as coloured cards (🔴 N breaking / 🟡 N additive / ⚪ N cosmetic / policy),
  the findings table grouped by severity, the mermaid subgraph rendered via an inlined mermaid
  script, and a footer (target, timestamp, git sha, `mdl` version). Self-contained so it can be
  emailed / dropped in a wiki / hosted with no assets. This is the stakeholder artifact.
- `.json` → `render_json` to a file (for archival / dashboards).

Same for `mdl diff --report` (there is a parallel `diff_render.py` trio) so model-diff reports
share the mechanism. `render_html` is the one genuinely new renderer; everything else is "write
the existing render to a file."

**2. VS Code: "Generate Drift Report".** A command `modelith.driftReport` that runs
`mdl drift --report`, writes the HTML to the workspace (or a temp file), and **opens it in a
webview / the Simple Browser** so the user sees the shareable artifact immediately, with a "Reveal
in Finder / Copy path" affordance. This complements the Drift view (which is for *acting* on
drift) with a *sharing* gesture. The report can also be surfaced from the Drift view's title-bar
menu ("Export report…").

**3. CI: keep the PR comment, add an uploaded HTML artifact.** Extend `profiles/ci/mdl-drift.yml`:
the existing sticky markdown comment stays (best for inline PR review); additionally
`mdl drift --report drift-report.html` and `actions/upload-artifact` so a richer report is
downloadable from the run, and a policy-aware `--check` gate fails on breaking *or* error-severity
policy violations. (Comment *posting* stays delegated to the GitHub Action — Modelith deliberately
ships no GitHub API client; it produces the artifact, CI delivers it.)

**4. The report includes policy.** Because Part 1 makes policy violations `DriftItem`s, the report
renders them alongside schema drift automatically — a stakeholder sees "the warehouse added a
surrogate key, which violates the *no-surrogate-keys* standard" in the same document, which is
exactly the cross-cutting value: *drift that matters because of your rules.*

### Safety & scope

The report is a read-only artifact — generating it writes nothing to the model. HTML is
self-contained and static (no network calls, no script beyond the inlined mermaid renderer), so it
is safe to host or attach. The `--check` CI gate is the only thing that *fails a build*, and its
policy behaviour is opt-in via each rule's `severity`.

---

## Build order (layered on the drift spec's A–E)

| Step | Contents | Depends on |
|---|---|---|
| **P1. Policy engine** | `packages/core/src/mdl_core/policy.py` — predicate catalogue + evaluator + `PolicyViolation`; `policy` block on `ProjectConfig`; `_check_policy` wired into `validate()`; `mdl policy check` CLI. | — |
| **P2. Policy drift** | Policy pass in `drift` → `DriftKind.policy_violation` items; reuse `is_surrogate_key` etc. Rides the drift surfaces automatically. | P1, drift spec B/C |
| **R1. Report renderer** | `render_html` in `render.py` (+ diff_render); `mdl drift --report <file>` / `mdl diff --report`. | — (independent) |
| **R2. VS Code report** | `modelith.driftReport` command → generate + open the HTML in a webview; Drift-view "Export report…". | R1, drift spec C |
| **R3. CI report** | Extend `profiles/ci/mdl-drift.yml` — upload the HTML artifact; policy-aware `--check`. | R1, P2 |

Each step is independently useful and leaves the suite green: R1 improves the CLI on its own; P1
makes `mdl validate` policy-aware before any drift or UI work.

## Critical files

- `packages/core/src/mdl_core/ir.py` — add `policy` to `ProjectConfig` (`ir.py:435`); the fields a
  rule reads (`Attribute.role`/`nullable`) already exist.
- `packages/core/src/mdl_core/policy.py` (NEW) — predicate catalogue + evaluator.
- `packages/core/src/mdl_core/validate.py` — one line: `_check_policy` in `validate()` (`:25`).
- `packages/reverse/src/mdl_reverse/drift.py` — the policy pass + `DriftKind.policy_violation`.
- `packages/reverse/src/mdl_reverse/lifting.py` — reuse `is_surrogate_key` (`:102`) for detection.
- `packages/reverse/src/mdl_reverse/render.py` — `render_html`; `render_markdown` already has the table + mermaid.
- `packages/cli/src/mdl_cli/main.py` — `mdl policy check`; `--report <file>` on `drift`/`diff` (drift at `:551`).
- `vscode/src/extension.ts` / `driftView.ts` — `modelith.driftReport` + "Export report…".
- `packages/mcp/src/mdl_mcp/server.py` — `explain_policy()` tool (optional, follows `explain_drift`).
- `profiles/ci/mdl-drift.yml` — upload the HTML artifact; policy-aware gate.

## Verification

- **Python (`uv run pytest && uv run ruff check packages/`):**
  - Policy: a fixture model with a `role: surrogate_key` attribute + a `no-surrogate-keys` rule →
    `validate` emits the policy diagnostic at that attribute; removing the attribute clears it.
    `every_entity_has_key` fails on a keyless entity. Predicate catalogue is a closed set (no
    eval).
  - Policy drift: a manifest that adds a `*_sk` column against a `no-surrogate-keys` project →
    `compute_drift` emits a `policy_violation` item at the rule's severity; a clean manifest emits
    none.
  - Report: `mdl drift --report out.html` writes a self-contained HTML file (no external asset
    refs) containing the summary, table, and a mermaid block; `--report out.md`/`out.json` write
    the existing renders. Byte-check the HTML has no `http` asset link.
- **Extension (`cd vscode && npx tsc --noEmit && node esbuild.mjs`, Node 20 via nvm):**
  `modelith.driftReport` registers; generates and opens the HTML.
- **Manual, `demo/ibor`:** add `policy: {rules: [no-surrogate-keys …]}`; introduce a surrogate key
  → `mdl validate` flags it and it appears in the Problems panel; break a column so the warehouse
  drifts → `Modelith: Generate Drift Report` opens an HTML page a non-VS-Code stakeholder could
  read, showing both the schema drift and any policy violation.
