# Changelog

## 0.3.5

- **Reverse Engineer writes where your model actually is.** Right-clicking a
  `dbt_project.yml` used to reverse into a `model/` folder *inside* the dbt project
  (e.g. `transform/warehouse/model`), not your real model. Now reverse defaults to the
  workspace's Modelith model dir and always shows a pre-filled, editable target picker, so
  the destination is never a surprise. Re-reversing into an existing model (to rebuild
  entities you deleted) is offered as an explicit Overwrite.
- **Honest empty result.** Reversing a warehouse that only has staging models (no marts
  generated yet) no longer looks like success and no longer leaves an empty model folder
  behind — you get a clear warning that says to run `mdl generate` first. Requires CLI
  0.4.6+.
- The Modelith output channel now logs the source, target, and result of each reverse.

## 0.3.4

- **Getting Started now walks you to a clean drift check.** The walkthrough gained two
  steps — *Generate the dbt project* and *Check drift* — in the right order, so you build
  the dbt mart models before comparing against them. (A Modelith entity maps to a dbt
  **mart**, the governed model with columns and contracts — not the raw `stg_` staging
  wrapper.) The bundled demo also ships a short README with the run order, and no longer
  carries a staging mapping that made an early drift check look alarming. Requires CLI
  0.4.5+.

## 0.3.3

- **Teach drift your warehouse's naming.** Drift matched a model entity to a dbt model by
  exact name, so a warehouse that materialises `price` as `stg_price` showed false
  "model removed" findings. Now a `model removed` item in the Drift panel has an inline
  **Map to dbt model…** action: pick the real dbt model (candidates ranked best-match
  first, e.g. `stg_price` for `price`), and the mapping is written to `reverse.model_map`
  in `mdl-project.yaml` — drift re-runs and the false finding clears. You can also declare
  whole conventions once with `reverse.layers` (e.g. a `stg_` staging prefix), or hand-edit
  the YAML; all three converge on the same git-committed config. Requires the `mdl` CLI
  0.4.4+. The bundled demo now ships this config, so it's drift-clean out of the box.

## 0.3.2

- **Right-click Reverse Engineer.** Right-click a `dbt_project.yml`, a folder of `.sql`
  DDL scripts, a single `.sql` file, or a `profiles.yml` in the Explorer and choose
  *Reverse Engineer to a Modelith Model*. It picks the right source automatically —
  a dbt project reverses from its build artifacts (and points you at `dbt docs generate`
  if none exist yet), a folder reverses all its DDL as one warehouse, and `profiles.yml`
  previews the coming live-datastore path. On a clean workspace it writes straight into
  `model/` beside the source and opens the Reverse Review panel, calling out how many
  ambiguous decisions need your accept/reject.
- **Reverse knows when a model already exists.** It inspects the target before writing,
  the way `git init` / `dbt init` do, and only prompts when there's a real decision:
  a full model there offers *reverse into a new folder* (default, name pre-filled and
  editable), *check drift against it instead*, or *overwrite*; a folder that looks like a
  damaged model, or an unrelated non-empty folder, is handled distinctly rather than
  written into blindly. If a model already lives elsewhere in the workspace, reverse
  offers to check drift against it first — usually what a returning user actually wants.
  Requires the `mdl` CLI 0.4.1+ for folder reverse, 0.4.3+ for in-place overwrite. To
  review *every* inference manually regardless of confidence, set `reverse.auto_accept:
  none` in `mdl-project.yaml` (CLI 0.4.2+).

## 0.3.1

- **Unified telemetry across the CLI and extension.** Both now share one anonymous
  install id (`~/.modelith/telemetry.json`), so editor and CLI usage form a single
  product funnel. When your VS Code telemetry setting is enabled, the extension records
  that consent in the shared file, so `mdl` runs (including in the VS Code terminal) emit
  too — under the same anonymous id. An explicit CLI opt-out is never overridden, and
  `DO_NOT_TRACK=1` turns everything off. Nothing new is collected.

## 0.3.0

- **Getting Started walkthrough.** A native VS Code walkthrough opens after install
  and guides you through the whole first-run: install the CLI, scaffold the demo
  model, open the ER canvas, and reach a green validate — each step a one-click
  button that ticks off as you go. Re-open it anytime with *Modelith: Open the
  Getting Started Walkthrough*.
- **Try the Demo Model** command (`modelith.initDemo`) — scaffolds the bundled
  7-entity example to `~/modelith-demo` (never your own repo) so the canvas has
  something real to show on first run.

## 0.2.0

- **One-click CLI install.** When the `mdl` command-line tool isn't found, the
  extension now offers an "Install the CLI for me" button that runs
  `uv tool install modelith-dbt` (or `pipx`) in an integrated terminal and
  re-detects — no more copying a command out of an error message. Also available
  from the palette as *Modelith: Install the CLI (mdl)*.
- **AI assistance.** Two Copilot Chat integrations backed by the same `mdl` engine:
  a bundled MCP server (`mdl mcp`) exposing model + ontology tools in Agent mode
  (`list_entities`, `get_entity`, `search_ontology`, `get_model_context`, `validate`,
  `create_entity`, `update_entity`), and an `@modelith` chat participant for Ask mode
  (`/list`, `/explain <entity>`). Registered automatically; no `mcp.json` to write.
- **Import.** *Modelith: Import to Model* — right-click a `.sql`/`.mmd` file (or the
  command palette) to open the Import wizard on the embedded canvas.

## 0.1.0

First release.

- Open the Modelith canvas beside your YAML (follows the active editor) or as a full
  editable tab.
- Model validation on save, surfaced in the Problems panel.
- Language server: drift and contract diagnostics on the generated dbt files, hover cards,
  and code actions.
- Commands: generate the dbt project, check drift, lint and fix naming, add an entity,
  vendor an ontology, emit the semantic layer.
- YAML schema completion for model files.
- Robust `mdl` detection across venv, conda, and standard install locations.
- Devcontainer support (`extensionKind: workspace`).
