# AI features for Modelith — Phase 1 roadmap

## Context

Modelith already ships two AI surfaces: an **MCP server** (`packages/mcp/src/mdl_mcp/server.py`,
7 tools — reads + `create_entity`/`update_entity`) and a **VS Code `@modelith` chat
participant** (`vscode/src/chatParticipant.ts`, Ask-mode, grounded on the user's Copilot
model). Both read through the shared query layer `packages/core/src/mdl_core/query.py`.

The question this plan answers: *what else can AI add that generic Copilot cannot?* The
answer is Modelith's four **structured knowledge assets** a generic assistant can't see —
the ontology alignment engine, the drift engine (with fix-recipe payloads), the
DecisionLedger governance seam, and the validation/naming engines (which emit concrete
fixes). Each feature wraps a deterministic engine as the source of structural truth and
uses the LLM only to **explain, rank, compose prose, and draft** — never to invent
structure. Every write still lands through `apply_command` after validation.

The user chose **four features across all three surfaces (MCP + `@modelith` + CLI)**:
1. **Alignment Reviewer** — explain + recommend a verdict on each ontology proposal.
2. **Diagnostic/Naming Fixer** — explain each MDL-* diagnostic; apply deterministic naming fixes.
3. **Definition Generator** — draft definitions for undocumented objects.
4. **Drift Explainer** — narrate a drift report; compose a recommended fix from the payload.

**Out of scope (owned by a separate cloud session):** functional-dependency capture / a
normalization checker. Do not build it here.

## The shared foundation (build first)

Two reusable pieces make all four features small; build them before any feature.

### A. AI grounding helper — `packages/core/src/mdl_core/ai_context.py` (NEW)
A pure function set that assembles the compact, structured "fact sheet" each feature hands
to an LLM — an extension of what `query.py` already does. No LLM call lives in core; core
only shapes facts and parses back structured results. This keeps the engine as the source
of truth and stays testable without a model.

### B. Surface pattern (repeat per feature, per surface)
- **CLI (`mdl`, Typer):** the engine + a JSON/text `--explain`-style output. The CLI has no
  LLM — it emits the structured facts an external model (or the `@modelith`/MCP layers)
  consume, plus a deterministic human-readable rendering. This is the source of truth each
  other surface wraps.
- **MCP tool (`packages/mcp`):** a new `@mcp.tool()` that returns the same structured facts
  (JSON) for the agent's own model to reason over. Reads shape facts; any write goes through
  `apply_command` exactly as `create_entity`/`update_entity` do today (`server.py:200`).
- **`@modelith` (`vscode/src/chatParticipant.ts`):** a slash command that fetches the facts
  via the `mdl` JSON command (the existing `readModel` path) and grounds `request.model` to
  compose the answer, pinned to the facts (same pattern as `answerWithModel`).

The LLM lives only in the MCP client's model and in `@modelith`'s `request.model`. The CLI
and core stay LLM-free and deterministic.

## Feature 1 — Alignment Reviewer (Phase 1a, effort S)

For each unpromoted alignment proposal, explain in plain English why the top candidate fits
(and why the runner-up doesn't) and recommend accept/reject/needs-review. Never picks
candidates — `align_model` already ranks them with confidence bands.

- **Wraps:** `align_model` + `AlignmentProposal`/`Candidate` (`packages/ontology/src/mdl_ontology/align.py`);
  the `DecisionLedger` pending set (`packages/reverse/src/mdl_reverse/ledger.py`, `Decision`
  carries `confidence`, `subject`, structured `evidence`). Human promotes via the existing
  `promote_alignment` op (`commands.py`).
- **NEW:** `mdl ontology align --explain` (renders proposals + evidence as structured JSON);
  MCP tool `explain_alignment(object_name?)`; `@modelith /align <entity>`. The
  **"explain + recommend verdict over a ledger Decision"** helper built here is reused by F4-review, F2, and later features.
- **Safety:** writes nothing. Annotates existing `proposed` ledger entries; MDL-W206 keeps
  flagging unpromoted. Safest possible surface.

## Feature 2 — Diagnostic / Naming Fixer (Phase 1a, effort S)

Explain each `Diagnostic` (MDL-* code) in context; for naming hits, offer the fix the engine
already computed.

- **Wraps:** `validate()` → `DiagnosticSet` (`packages/core/src/mdl_core/validate.py`, already
  MCP-exposed at `server.py:98`); `naming.lint()` → `(DiagnosticSet, NamingFix)`
  (`packages/core/src/mdl_core/naming.py`) where `NamingFix.entities`/`.attributes` hold the
  exact corrected names. Applies via `rename_entity` + attribute-rename ops (the CLI's
  `_apply_naming_fixes` already maps `NamingFix` → commands).
- **NEW:** MCP tools `explain_diagnostics()` / `suggest_fixes()`; `@modelith /fix`; extend
  `mdl validate`/`mdl naming` with an explain flag. Small — the LLM only explains; corrected
  names come from `NamingFix` verbatim.
- **Safety:** deterministic naming fixes are opt-in direct-apply behind confirmation (as
  `mdl naming --fix` is today). Structural diagnostics with no auto-fix are explained only.
  No new write authority.

## Feature 3 — Definition Generator (Phase 1b, effort M)

Find objects with a null `.definition` and draft one grounded in the object's own
attributes, keys, relationships, and any ontology alignment.

- **Wraps:** the query-layer shapes (definition already threaded through `get_entity` etc.);
  writes via existing `set_definition` / `set_object_definition` (`commands.py`).
- **NEW:** (a) a **definition-gap scanner** over the IR — mirror `coverage_report`'s shape in
  `packages/ontology/src/mdl_ontology/layers.py` (there is no definition-coverage report
  today, only ontology-alignment coverage); (b) LLM draft/parse. Surfaces: `mdl doc gaps`
  (report) + `mdl doc draft` (stage); MCP `draft_definitions(scope?)`; `@modelith /document <entity>`.
- **Safety:** definitions are prose — route drafts through the `DecisionLedger` as
  `kind="definition_draft"` proposals (reuse `signal_key` dedupe so re-runs don't re-propose
  accepted drafts). Human reviews a batch; a promote folds accepted drafts through
  `set_definition`. Never bulk auto-write.

## Feature 4 — Drift Explainer (Phase 1b, effort M)

Narrate a `DriftReport` grouped by severity; for breaking items (which `--reconcile` refuses)
compose a recommended remediation from the item's `payload` fix recipe.

- **Wraps:** `compute_drift` → `DriftReport`/`DriftItem` (`packages/reverse/src/mdl_reverse/drift.py`);
  each `DriftItem` carries `severity`, `kind`, `detail`, and a machine `payload`. Severity
  vocabulary in `packages/core/src/mdl_core/severity.py`.
- **NEW:** `mdl drift --explain`; MCP `explain_drift()`; `@modelith /drift`. LLM narrates +
  sequences a fix from the recipe; it must never re-derive severity/direction (the tests pin
  those).
- **Safety:** mirror the existing `reconcile` boundary exactly — additive/cosmetic can be
  staged for one-click apply; breaking is explained + recommended only, staged in the ledger
  for a human verdict, never auto-applied.

## Phasing

| Phase | Contents | Why |
|---|---|---|
| **0** | Shared foundation: `ai_context.py` grounding helper; the reusable "explain + recommend verdict over a ledger Decision" component; establish the per-surface pattern (CLI JSON → MCP tool → `@modelith` slash command). | Everything else is thin on top of this. |
| **1a** | **F1 Alignment Reviewer** + **F2 Diagnostic/Naming Fixer** — both effort S, both wrap engines that already emit structured/reproducible output, both extend the thin MCP surface. F1 builds the ledger-verdict layer F3 reuses. | Fastest to shippable value, lowest risk. |
| **1b** | **F3 Definition Generator** + **F4 Drift Explainer** — both effort M; F3 needs the new gap scanner, F4 maps payloads to staged commands. | Higher-value, moderate new code, both reuse the Phase 0 seams. |

Each phase leaves the suite green and ships all three surfaces for its features.

## The one guardrail (enforce across all four)

The LLM's output is always **annotation, prose, or a proposal of named operations** — never
raw structure. The engine (align / validate / naming / drift / `apply_command`) remains the
sole source of structural truth; every write lands through `apply_command` after validation,
and staged proposals go through the `DecisionLedger` for a human verdict. A feature must fail
closed: if the LLM is unavailable, the deterministic CLI/engine output still works (as
`@modelith` already falls back today).

## Critical files

- `packages/core/src/mdl_core/ai_context.py` (NEW — grounding helper)
- `packages/core/src/mdl_core/query.py` (existing shapes to extend)
- `packages/mcp/src/mdl_mcp/server.py` (new tools; reuse the `apply_command` write path at `server.py:200`)
- `packages/cli/src/mdl_cli/main.py` (new `--explain` flags + `mdl doc` group, following the Typer subcommand pattern)
- `vscode/src/chatParticipant.ts` (new slash commands, reusing `readModel`/`answerWithModel`)
- Engines wrapped: `packages/ontology/src/mdl_ontology/align.py` (F1),
  `packages/core/src/mdl_core/{validate,naming}.py` (F2),
  `packages/ontology/src/mdl_ontology/layers.py` as the gap-scanner pattern (F3),
  `packages/reverse/src/mdl_reverse/drift.py` (F4)
- `packages/reverse/src/mdl_reverse/ledger.py` (the propose→verdict seam F1/F3 write into)

## Verification

Each feature is testable without an LLM, because the engine + CLI JSON output is
deterministic — that is the layer the tests target.

- **Python (`uv run pytest && uv run ruff check packages/`):**
  - F1: `mdl ontology align --explain` emits structured proposals with candidates + evidence
    for the demo model; a promoted alignment disappears from the pending set.
  - F2: `mdl validate`/`mdl naming` explain output includes each MDL-* code and, for naming,
    the exact `NamingFix` correction; applying it renames on disk and re-validates clean.
  - F3: the definition-gap scanner finds the null-definition objects in a fixture; a staged
    `definition_draft` promotes through `set_definition` and the gap closes; re-run doesn't
    re-propose an accepted draft (signal_key dedupe).
  - F4: `mdl drift --explain` groups by severity and attaches the `payload` recipe; breaking
    items are never in the auto-apply set.
  - New MCP tools: drive them through the FastMCP `call_tool` path (as `packages/mcp/tests/test_server.py`
    already does) and assert the JSON shape.
- **Extension (`cd vscode && npx tsc --noEmit && node esbuild.mjs`, Node 20 via nvm):**
  typecheck + bundle; the slash commands compile and register.
- **Manual, against `demo/ibor/model` in VS Code:** `@modelith /align instrument`,
  `@modelith /fix`, `@modelith /document position`, `@modelith /drift` — each grounds on real
  facts and, for writes, shows the proposal before anything lands. Reinstall path uses the
  workspace `.venv` mdl (dev builds pick up working-tree code without a global reinstall).