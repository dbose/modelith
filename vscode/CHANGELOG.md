# Changelog

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
