# Modelith for VS Code

Ontology-anchored, git-native data modeling for dbt teams, inside VS Code, standalone
or in a devcontainer. Model your warehouse as an entity-relationship diagram beside your
YAML, generate contract-enforced dbt, and catch drift, without leaving the editor.

## Prerequisite

This extension drives the `mdl` command-line tool. Install it first:

```bash
uv tool install modelith-dbt
# or: pipx install modelith-dbt
```

The extension does not bundle its own copy of the canvas. It launches `mdl serve` and
embeds the live canvas, so whatever the CLI understands, the editor shows.

## What you get

- **Canvas beside your YAML.** Right-click a model file and choose *Modelith: Open Model
  Preview to the Side*. The canvas opens in a split beside your editor and follows the
  active file: open an entity's YAML and the diagram centers on it, switch to a generated
  `.sql` file and the preview tracks it, save the YAML and it re-renders. You read and edit
  the model as text on one side and watch the diagram update on the other.
- **The full editable canvas.** *Modelith: Open Canvas* opens the complete editor as a tab:
  drag between entities to draw relationships, edit attributes and named keys inline, browse
  the ontology and the four-layer stack, and commit from a git panel. `modelith.canvas.display`
  picks a webview tab or an external browser tab via a forwarded port, so devcontainers and
  remote work out of the box.
- **Diagnostics on save.** `mdl validate` runs when you save a model YAML and surfaces
  `MDL-*` findings in the Problems panel, mapped to the file that declares the issue. The
  status bar shows a valid or error-count chip.
- **Language server.** Drift and contract diagnostics land on the generated dbt files, with
  hover cards (glossary term, ontology IRI, owner) and code actions (adopt a column, lift a
  model, unmanage, declare a relationship).
- **Commands.** Generate the dbt project, check drift, lint and fix naming, add an entity,
  vendor an ontology, emit the semantic layer, all from the command palette.
- **YAML completion.** JSON Schemas exported from the model are registered with the Red Hat
  YAML extension, so authoring the YAML by hand is schema-checked and autocompleted.
- **mdl detection.** Resolves in order: an explicit `modelith.mdlPath` setting, a project
  `.venv`, `mdl` on PATH, the active conda or virtualenv bin, then common per-user install
  locations. A standard `uv tool install modelith-dbt` needs no configuration. If nothing
  matches, run `which mdl` in the integrated terminal and set that as `modelith.mdlPath`.

## AI assistance (Copilot Chat)

Modelith registers two AI integrations, one per Copilot Chat mode. Both use the same
`mdl` engine already detected for everything else — no `mcp.json` to write, no separate
install.

- **Agent mode — Modelith MCP tools.** In Copilot Chat's *Agent* mode the model is available
  as tools: `list_entities`, `get_entity`, `search_ontology`, `get_model_context`, `validate`,
  and the write tools `create_entity` / `update_entity`. The agent can ground itself in what
  already exists, search the ontology for an alignment, and write validated entities straight
  into your checkout (direct-write — you review the git diff, the same trust boundary as the
  CLI). The tools **only run in Agent mode**: tool invocation needs the plan/act/observe loop
  that Ask mode does not have. The server is the `mdl mcp` subcommand, registered automatically
  on activation and scoped to your workspace model. Requires VS Code 1.99+ (older hosts keep
  every other feature; only the tools are absent).
- **Ask mode — `@modelith`.** In *Ask* mode, where MCP tools cannot run, type `@modelith` and
  ask about the model: name an entity for its attributes / ontology alignment / relationships,
  `@modelith /list` for the entity list, `@modelith /explain <entity>` for one in full, or a
  general question for a model summary. It reads the model through the same query layer the MCP
  tools use, so the two modes answer consistently.

## Devcontainers

The extension declares `"extensionKind": ["workspace"]`, so in a devcontainer it runs inside
the container, next to `mdl`, dbt, and your warehouse credentials. The canvas server binds in
the container and VS Code forwards the port automatically. A ready-made devcontainer template
ships in the Modelith repository under `profiles/devcontainer/`.

## Learn more

Modelith is a full data-modeling toolchain (a CLI, a web canvas, a language server, reverse
engineering, drift detection, and governance sync). See the
[project repository](https://github.com/dbose/modelith) for the complete picture.
