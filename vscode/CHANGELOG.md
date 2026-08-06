# Changelog

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
