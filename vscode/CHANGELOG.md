# Changelog

## Unreleased

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
