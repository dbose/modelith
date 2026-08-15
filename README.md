# Modelith

Ontology-anchored, git-native data modeling for dbt teams. Design your model as an
entity-relationship diagram, generate contract-enforced dbt from it, reverse an
existing warehouse back into a model, and catch drift before it ships. The model
lives in git as plain YAML, so every surface (the `mdl` CLI, the web canvas, the
VS Code extension, the drift bot) is a client and none of them owns state.

[![tests](https://img.shields.io/badge/tests-215%20passing-brightgreen)](#development)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![dbt](https://img.shields.io/badge/dbt--core-1.7%2B-orange)](https://www.getdbt.com/)
[![license](https://img.shields.io/badge/license-Apache--2.0-lightgrey)](#license)
[![code style: ruff](https://img.shields.io/badge/lint-ruff-black)](https://docs.astral.sh/ruff/)

![Modelith canvas: a seven-entity pension IBoR model with crow's-foot relationships](docs/assets/canvas.png)

## Why

Warehouse teams keep their meaning in three disconnected places: an ER tool that
never sees production, dbt SQL that drifts from the design, and a governance catalog
nobody edits. Modelith puts one model in git and makes every tool read and write it.
You get a real data modeler (entities, attributes, keys, domains, relationships,
subtypes) that generates dbt you can actually run, verifies the warehouse still
matches the model, and reads a legacy dbt project (or an erwin export) back into a
clean logical model.

Nothing here is a mockup. Every capability below is exercised by the test suite and,
where it touches SQL, by a real `dbt build` against DuckDB.

## Install

The PyPI package is named `modelith-dbt` (the bare `modelith` name belongs to an
unrelated project). It installs a single command, `mdl`.

```bash
uv tool install modelith-dbt    # isolated tool install, puts `mdl` on your PATH
# or: pipx install modelith-dbt
# or into an existing environment: pip install modelith-dbt
mdl --help
```

Modelith runs in the same environment as dbt-core (Python 3.11+). One install gives
you the whole toolchain: the CLI, the web canvas (`mdl serve`), the language server,
reverse engineering, drift detection, and the ontology and governance stack.

### VS Code, Cursor, Windsurf

The editor extension drives the same `mdl` CLI, so install the CLI first (above),
then add the extension. It is published on Open VSX, so it installs directly in VS
Code, Cursor, Windsurf, and VSCodium: open the Extensions panel and search for
**Modelith**, or install from the command line.

```bash
code --install-extension modelith.modelith-vscode        # VS Code
# cursor --install-extension modelith.modelith-vscode    # Cursor
```

### From source

To run the in-repo development version, or before the package reaches your index:

```bash
git clone https://github.com/dbose/modelith
cd modelith
uv tool install .        # builds and installs the local checkout as `mdl`
```

## Quickstart

```bash
mdl init my-model                          # scaffold a model repo
mdl new entity customer -m my-model        # add an entity (mints ULIDs)
mdl validate -m my-model                   # schema, refs, naming, ontology
mdl generate -m my-model -o warehouse      # emit contract-enforced dbt
mdl serve -m my-model                       # open the visual canvas
```

`mdl generate` writes dbt models with protected regions, a `schema.yml` carrying
contract constraints, and a three-way merge on regeneration, so your hand edits and
the generated blocks both survive.

```text
$ mdl validate -m my-model
validation passed

$ mdl generate -m my-model -o warehouse
  created          models/customer.sql
  created          models/schema.yml
wrote 2 files to warehouse
```

## The visual canvas

`mdl serve` opens an ER canvas in the browser (also embeddable as a VS Code webview).
It is a full editor, not a viewer: drag between entities to draw a relationship, edit
attributes and keys inline, align a term to an ontology, and commit from a git panel.
Every edit is a typed, comment-preserving mutation with optimistic concurrency, so two
people can work the same repo without clobbering each other.

![The entity inspector, showing attributes, named keys, an enumerated domain, user-defined properties, and relationships](docs/assets/inspector.png)

The inspector above shows one entity carrying an enumerated domain (`asset_class`),
a named primary key and a unique key, user-defined properties, and its relationships,
all first-class in the model.

## In VS Code

Install the extension from `vscode/modelith-vscode-0.1.0.vsix`:

```bash
code --install-extension vscode/modelith-vscode-0.1.0.vsix
```

The extension does not bundle its own copy of the canvas. It launches `mdl serve`
and embeds the live canvas, so whatever the CLI understands, the editor shows. The
model files stay in git; the extension is just another client over them.

**Side by side: YAML, SQL, and the canvas.** Right-click a model file in the
Explorer (or in the open editor) and choose **Modelith: Open Model Preview to the
Side**. The canvas opens in a split beside your file and follows the active editor:
open `instrument.yaml` and the canvas centers on that entity; switch to a generated
`.sql` file and the preview tracks it. Save the YAML and the preview re-renders. You
read and edit the model as text on the left, watch the diagram update on the right,
and keep the generated dbt SQL a tab away, all in one window.

![Split view in VS Code: the model YAML on the left, the live canvas preview on the right](docs/assets/vscode-split.png)

For a full editing session, **Modelith: Open Canvas** opens the editable canvas as
its own tab (drag-to-connect relationships, inline attribute and key editing,
git commit panel). The preview-to-the-side is the read-along companion while you work
in text; the full canvas is where you drive structural edits.

What the extension adds on top of the canvas:

- **Diagnostics on save.** `mdl validate` runs when you save a model YAML and surfaces
  `MDL-*` findings in the Problems panel, mapped to the file that declares the issue.
- **Language server.** `mdl lsp` drives drift and contract diagnostics on the generated
  dbt files, hover cards (glossary term, ontology IRI, owner), and code actions (adopt a
  column, lift a model, unmanage, declare a relationship).
- **Commands.** Generate the dbt project, check drift, lint and fix naming, scaffold a new
  entity, vendor an ontology, emit the semantic layer, all from the command palette.
- **YAML completion.** JSON Schemas exported from the model are registered with the Red Hat
  YAML extension, so authoring the YAML by hand is schema-checked and autocompleted.
- **Devcontainer-ready.** The extension declares `extensionKind: ["workspace"]`, so in a
  devcontainer the server and the `mdl` toolchain run next to dbt and your warehouse
  credentials, not on the laptop.

Detection resolves `mdl` in order: an explicit `modelith.mdlPath` setting, a project
`.venv`, `mdl` on PATH, the active conda or virtualenv, then the common per-user install
locations. A standard `uv tool install modelith-dbt` needs no configuration.

## What it models

Modelith represents the core data-modeling taxonomy as first-class, git-tracked objects:

| Concept | Support |
|---|---|
| Conceptual / logical / physical layers | Separate object kinds, referenced by immutable ULID |
| Entities and attributes | Name, domain, role (business key / surrogate / attribute / measure), nullability |
| Relationships | Four cardinalities, identifying vs non-identifying, optionality, crow's-foot rendering |
| Named keys | Primary, alternate, unique, and index key groups with ordered, composite members |
| Domains and reference data | Reusable domains, inline enumerations, and shared code sets that emit dbt `accepted_values` tests |
| Subtypes and supertypes | Category clusters with a discriminator and a physical materialization strategy (single-table or table-per-subtype) |
| User-defined properties | Extensible metadata on any object, flowing through to dbt `meta` |
| Subject areas | Diagram partitioning with color grouping on the canvas |
| Ontology alignment | Four-layer stack (industry / core / domain / specialised), SKOS predicates, FIBO out of the box |
| Design patterns | SCD2 and Data Vault (hub / link / satellite), emitting working SQL, not stubs |

## What it does

**Forward engineering.** Generate dbt-core models with enforced contracts, primary and
unique key constraints, relationship tests, and platform-specific types for DuckDB,
Snowflake, Redshift, Iceberg, and Trino. Regeneration runs a three-way merge so hand
edits survive.

**Reverse engineering.** Point `mdl reverse` at a compiled dbt project (`manifest.json`
plus `catalog.json`) and get a logical model back. It excludes staging and intermediate
models, collapses SCD2 column triples into a pattern, strips surrogate keys, detects Data
Vault structures, and infers relationships from tests and naming, recording every decision
in a reviewable ledger.

**Drift detection.** `mdl drift` compares the committed model to a compiled warehouse and
classifies each difference as breaking, additive, or cosmetic, with a CI gate mode and a
reconcile mode. A 400-model breaking change classifies in under thirty seconds.

**Governance sync.** A neutral governance graph maps to an external catalog through a
customer-owned Jinja profile. A Collibra adapter ships, along with OpenLineage emission
and a conformance kit that validates a bespoke mapping in CI.

**Semantic layer.** Emit MetricFlow semantic models and metrics, or OSI (version-isolated),
with joinability and fan-out validation.

**Collaboration.** A structural, ULID-keyed git merge driver lets two people add different
attributes to the same entity and merge cleanly. A change classifier routes pull requests
to the right reviewers, a debt valve records engineer-owned SQL with an expiry, and a
git-native glossary app lets subject-matter experts propose definitions as pull requests
without ever seeing git or a CLI.

## Surfaces

| Surface | What it is |
|---|---|
| `mdl` CLI | The full command set: init, validate, lint, generate, reverse, drift, serve, glossary, ontology, emit, export, import, gov, and more |
| Web canvas | `mdl serve` opens the ER editor; state stays in git |
| VS Code extension | Canvas beside your YAML (follows the active editor), full canvas tab, diagnostics on save, generate / drift / lint commands, YAML completion, devcontainer-ready. See [In VS Code](#in-vs-code). |
| Language server | `mdl lsp` (one server for VS Code, Cursor, Windsurf, JetBrains, and CI): drift and contract diagnostics on the dbt files, hover cards, code actions |
| Glossary app | `mdl glossary` serves a narrow, git-native glossary surface for subject-matter experts |

## CLI reference

```text
mdl init [--workspace] [--git-hooks]              scaffold a model repo or full topology
mdl new entity|term|subject-area <name>           add an object (ULIDs minted)
mdl delete entity <name> [--cascade]              remove an object, safely
mdl validate [--format json]                      schema, refs, ontology, naming
mdl lint [--fix]                                  naming-standards lint
mdl generate [--target] [--dry-run]               emit the dbt project
mdl reverse --project <manifest|schema.yml>       lift a dbt project into a model
mdl drift --manifest <m> [--check|--reconcile]    compare model to compiled warehouse
mdl serve [--read-only]                           web canvas + read API
mdl glossary [--read-only]                        SME glossary app
mdl ontology search|check|promote|vendor          vocabulary and alignment lifecycle
mdl emit semantic --format metricflow|osi         semantic layer
mdl export json-schema|rdf|shacl                  interchange out
mdl import osi|erwin                              interchange in
mdl gov plan|apply|pull|publish|import            catalog sync
mdl classify | unmanage | debt | decisions        collaboration and review
```

## Layout

```
packages/
  core/           IR, ULID identity, YAML round-trip, validator, merge engine (no in-repo deps)
  emit-dbt/       dbt-core emitter, platform adapters, SCD2 macros
  reverse/        manifest + catalog reader, drift, reverse engineering, decision ledger, erwin import
  ontology/       vocabulary registry, four-layer validation, RDF/OWL + SHACL export
  emit-semantic/  MetricFlow + OSI, joinability validation
  governance/     governance graph, Jinja mapping DSL, adapter SPI, conformance kit, OpenLineage
  adapters/
    collibra/     Collibra governance adapter
  server/         read API (FastAPI) + hosts the canvas build
  lsp/            language server (pygls)
  cli/            mdl
canvas/           web canvas source (Vite + React + React Flow)
vscode/           VS Code extension (TypeScript + esbuild)
profiles/         CI workflow templates and reference governance profiles
```

The layering rule is enforced: `core` depends on nothing else in the repo, and
`emit-dbt` depends only on `core`.

## Development

```bash
uv sync
uv run pytest                 # 215 tests
uv run ruff check packages/
```

Build the canvas and the extension (Node 20):

```bash
cd canvas && npm install && npm run build      # emits into the server static dir
cd vscode && npm install && npm run build && npm run package   # produces the .vsix
```

## Documentation

- [`docs/adoption-guide.md`](docs/adoption-guide.md): a step-by-step runbook for taking a
  team from an empty repo to a working practice (platform, then SMEs, architects, engineers).
- [`docs/collaboration-model.md`](docs/collaboration-model.md): the operating model, review
  routes, and the merge driver.
- [`modelith-spec.md`](modelith-spec.md): the full build specification.

## License

Apache-2.0.
