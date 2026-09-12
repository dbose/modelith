# Modelith — Drift, surfaced in VS Code and chat

## Context

Drift is Modelith's sharpest advantage: because the **model and the dbt implementation live
close** (one git repo, one `mdl` engine), a divergence between the committed model and the
compiled warehouse is *detectable, classified, and explainable* — not discovered in
production. The engine already exists and is strong:

- `compute_drift()` → `DriftReport` of `DriftItem{severity, kind, model, column, detail, payload}`
  (`packages/reverse/src/mdl_reverse/drift.py`).
- Severity is deterministic — breaking / additive / cosmetic (`packages/core/src/mdl_core/severity.py`);
  the tests pin direction, so **the LLM must never re-derive it**.
- `payload` on each item is a machine fix-recipe; `reconcile()` folds only additive+cosmetic,
  breaking is human-gated (`packages/reverse/src/mdl_reverse/reconcile.py`).
- **`mdl drift --format json` already emits the whole report** (`render.py:44`) with per-item
  severity/kind/model/column/detail/payload + `max_severity`/`has_breaking`.

**What exists in VS Code today is thin:** `modelith.driftCheck` (`vscode/src/extension.ts:270`)
runs `drift --check`, dumps text to an output channel, and shows a modal on breaking. No
per-model surfacing, no navigation to the affected file, no fix path, no explanation. The
JSON is there; the UX isn't.

## Research findings that shape the design

- **Drift is a diagnostics story, not a chat toy.** VS Code's own guidance: chat participants
  "should not be purely question-answering bots" and should hand off to commands/code actions
  for anything that edits, asking consent before destructive/costly operations. Findings that
  map to specific files with concrete fixes belong in the **Problems panel + code actions**,
  which is the idiom linters use (`DiagnosticCollection`, `CodeActionProvider`).
- **A dedicated view is the established idiom** for structured findings about a dbt project
  (dbt Power User uses a lineage panel + command-palette entries + click-to-navigate). Drift is
  a list of findings across many models — a **TreeView** grouped by severity fits it.
- **Chat CAN be actionable**, not just prose: `ChatResponseStream` gives `stream.button()`
  (invoke a command), `stream.markdown()` with `command:` links, `stream.reference()` /
  `stream.anchor()` (link a file+range), `stream.filetree()`. So the `@modelith` drift answer
  can explain *and* offer "Reconcile safe changes" / "Show in Problems" buttons.

Sources:
- VS Code Chat Participant API — https://code.visualstudio.com/api/extension-guides/ai/chat
- Programmatic Language Features (diagnostics, code actions) — https://code.visualstudio.com/api/language-extensions/programmatic-language-features
- code-actions-sample (diagnostics from a tool → quick fixes) — https://github.com/microsoft/vscode-extension-samples/blob/main/code-actions-sample/src/diagnostics.ts
- dbt Power User (panel + palette idiom for dbt findings) — https://github.com/AltimateAI/vscode-dbt-power-user
- Recce / Datafold (drift as a review artifact reviewers see) — https://blog.reccehq.com/column-level-lineage-options-for-dbt

## The design — three cooperating surfaces, one engine

The deterministic `mdl drift --format json` is the single source of truth. Every surface
consumes that JSON; none re-derives severity. The LLM only *explains and sequences a fix*.

### 1. Problems panel — drift as diagnostics (primary, non-AI)
A `DiagnosticCollection` (`modelith-drift`), separate from the LSP's validation diagnostics.
Each `DriftItem` becomes a `Diagnostic` on the model YAML file the item's `model` maps to
(resolve model name → `logical/entities/<name>.yaml`; range = the file, or the attribute line
when `column` is set and locatable). Severity maps: breaking → Error, additive → Warning,
cosmetic → Information. This gives squiggles, the Problems list, and the status-bar count for
free — exactly how an engineer already reads validation findings. Findings that don't resolve
to a file (e.g. `model_removed`) attach to `mdl-project.yaml` with the model named in the message.

### 2. Drift view — a TreeView grouped by severity (navigation)
A `modelithDrift` view (activity-bar or the existing Modelith container) listing findings
grouped **Breaking / Additive / Cosmetic**, each node showing `kind` + `model` + `column`,
click-to-open the file at the range. Inline node actions: "Reconcile" (additive/cosmetic only),
"Explain" (opens `@modelith /drift <model>`). A root action reconciles all safe changes. This
is the dbt-Power-User-style panel adapted to drift.

### 3. Code actions — the fix at the point of the problem
A `CodeActionProvider` on model YAML: for an additive/cosmetic drift diagnostic, offer a
`QuickFix` "Reconcile: fold this change into the model" that runs the reconcile for that one
item via `apply_command` using the item's `payload`. For a **breaking** diagnostic, offer
"Explain this breaking change (@modelith)" — never an auto-apply. This respects the engine's
own reconcile boundary at the exact spot the user is looking.

### Commands (palette: `Ctrl/Cmd+Shift+P` → "Modelith: …")
- **`Modelith: Check Drift`** (`modelith.driftCheck`, upgraded) — run `drift --format json`,
  populate the DiagnosticCollection + the tree, set the status bar, and reveal the view. Replaces
  today's text-dump-to-output behaviour. Wire it to also run on demand and (optionally, setting)
  when `target/manifest.json` changes.
- **`Modelith: Reconcile Safe Drift`** (`modelith.driftReconcile`) — apply additive+cosmetic
  via the CLI `drift --reconcile`; breaking stays untouched. Confirm first (VS Code consent
  guidance), then re-run the check to refresh.
- **`Modelith: Explain Drift`** (`modelith.driftExplain`) — hand off to `@modelith /drift`.
- A **status-bar item** reflecting drift state (✓ no drift / 🟡 N safe / 🔴 N breaking),
  clicking it reveals the Drift view — mirrors the existing validation status item.

### 4. `@modelith /drift` — the AI explainer (chat)
Ask-mode slash command. Grounds the user's Copilot model on the **drift JSON** (never
re-deriving severity — the prompt states severity is authoritative) and streams:
- a narrative grouped by severity ("3 additive, 1 breaking"),
- for each **breaking** item, a plain-English impact + the recommended remediation composed
  from its `payload` (which the engine's own `reconcile` uses),
- actionable trailer via `ChatResponseStream`: `stream.button()` "Reconcile safe changes"
  (→ `modelith.driftReconcile`), `stream.reference()` links to each affected model file, and a
  note that breaking changes need a human decision.
`/drift <model>` scopes to one model. Fails closed: if no `request.model`, fall back to the
deterministic markdown from `render_markdown` (already in `render.py`).

### 5. MCP tool `explain_drift()` (agent mode / other clients)
Returns the drift JSON plus a compact per-item structure sized for an agent's context. The
agent's own model narrates; any reconcile the agent performs goes through `apply_command`
(safe ops only — the tool refuses to auto-apply breaking, matching `reconcile()`).

### 6. `mdl drift --explain` (CLI, deterministic)
No LLM in the CLI. `--explain` emits the JSON already produced *plus* the `render_markdown`
narrative and, per item, the concrete reconcile command its `payload` implies — the structured
facts the chat/MCP layers consume, and a useful human rendering on its own. This is the source
of truth the other two surfaces wrap.

## The safety boundary (identical everywhere)

Mirror `reconcile()` exactly: **additive + cosmetic** can be one-click reconciled (with
confirmation); **breaking** is only ever explained + recommended, never auto-applied, and a
human decides. The LLM annotates and sequences; it never sets severity and never folds a
breaking change. Every write lands through `apply_command`/`drift --reconcile` after validation.

## Build order

| Step | Contents |
|---|---|
| **A. Engine** | `mdl drift --explain` (JSON + markdown + per-item reconcile command). Small — the JSON exists; add the narrative + command mapping. |
| **B. Diagnostics** | `DiagnosticCollection` + model-name→file resolver; upgrade `modelith.driftCheck` to populate it from `drift --format json` instead of dumping text; status-bar item. |
| **C. View + code actions** | `modelithDrift` TreeView (grouped by severity, click-to-open, inline reconcile/explain); `CodeActionProvider` (quick-fix reconcile for safe items, explain for breaking); `modelith.driftReconcile` command. |
| **D. Chat** | `@modelith /drift [model]` — grounded explainer with `stream.button`/`reference` actions and deterministic fallback. |
| **E. MCP** | `explain_drift()` tool. |

Each step leaves the suite green and is independently useful (A alone improves the CLI; B alone
makes drift a first-class Problems citizen).

## Critical files

- `packages/reverse/src/mdl_reverse/drift.py`, `render.py` — engine + JSON/markdown (wrap, don't change severity)
- `packages/reverse/src/mdl_reverse/reconcile.py` — the safe/breaking boundary to mirror
- `packages/cli/src/mdl_cli/main.py` — `mdl drift --explain` (the `drift` command is at ~`main.py:551`)
- `vscode/src/extension.ts` — upgrade `modelith.driftCheck` (`:270`); add `driftReconcile`, `driftExplain`, status item, view registration
- `vscode/src/driftView.ts` (NEW) — TreeDataProvider; `vscode/src/driftDiagnostics.ts` (NEW) — DiagnosticCollection + model→file resolver + CodeActionProvider
- `vscode/src/chatParticipant.ts` — `/drift` command (reuse `readModel`/`answerWithModel`, add `stream.button`/`reference`)
- `vscode/package.json` — contribute the commands, the view + viewsContainer, the `modelith.driftExplain` chat command, menus (editor/context on model YAML → Check Drift)
- `packages/mcp/src/mdl_mcp/server.py` — `explain_drift()` tool
- `packages/core/src/mdl_core/query.py` — reuse the model-name→file mapping if one exists (`repo.path_for_ulid`) rather than re-implementing

## Verification

- **Python (`uv run pytest && uv run ruff check packages/`):** `mdl drift --explain` against the
  IBoR demo emits JSON with per-item payload + a reconcile command per additive/cosmetic item;
  breaking items carry no auto-apply command. Break a generated column's contract, `dbt parse`,
  and assert the report classifies it.
- **Extension (`cd vscode && npx tsc --noEmit && node esbuild.mjs`, Node 20 via nvm):** typecheck
  + bundle; commands, view, and code-action provider register.
- **Manual, `demo/ibor`:** break a column → `dbt parse` → `Modelith: Check Drift` populates the
  Problems panel (Error on breaking, Warning on additive) and the Drift view; a quick-fix
  reconciles a safe item and it disappears on re-check; a breaking item offers only Explain;
  `@modelith /drift` narrates and its "Reconcile safe changes" button runs the command. Dev build
  uses the workspace `.venv` mdl (no global reinstall).
