# Modelith

**Modelith is the modeling layer dbt-core is missing: design entities and relationships
in git, generate contract-enforced dbt, and catch drift before it ships.**

Design your model as an entity-relationship diagram, generate dbt from it, reverse an
existing warehouse back into a model, and get told exactly what drifted when the
warehouse changes underneath you. The model lives in git as plain YAML, so every surface
(the `mdl` CLI, the web canvas, the VS Code extension, CI) is a client and none of them
owns state.

[![ci](https://github.com/dbose/modelith/actions/workflows/ci.yml/badge.svg)](https://github.com/dbose/modelith/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![dbt](https://img.shields.io/badge/dbt--core-1.7%2B-orange)](https://www.getdbt.com/)
[![license](https://img.shields.io/badge/license-Apache--2.0-lightgrey)](#license)
[![code style: ruff](https://img.shields.io/badge/lint-ruff-black)](https://docs.astral.sh/ruff/)

![Modelith canvas: a seven-entity pension IBoR model with crow's-foot relationships](docs/assets/canvas.png)

## Try it in 2 minutes

No cloud warehouse needed. The IBoR demo ships a model and a dbt project over a bundled
DuckDB, so it builds on a laptop.

```bash
uv tool install modelith-dbt
git clone https://github.com/dbose/modelith
cd modelith/demo/ibor

mdl validate -m model                 # the seven-entity model is valid
cd transform/warehouse && dbt build   # generated dbt builds green against DuckDB
cd ../.. && mdl studio -m model       # the model in a browser + the (bundled) FIBO ontology
```

Then break a generated column's contract, run `dbt parse`, and
`mdl drift --check -m model --manifest transform/warehouse/target/manifest.json` reports
it as breaking. Or open the ontology browser in the canvas and align an entity to a FIBO
term — the demo starts a local ontology server for you. See
[demo/ibor](demo/ibor/README.md) for the full walkthrough and
[Ontology and knowledge graph, step by step](#ontology-and-knowledge-graph-step-by-step)
for the ontology workflow.

## Why

Warehouse teams keep their meaning in three disconnected places: an ER tool that
never sees production, dbt SQL that drifts from the design, and a governance catalog
nobody edits. Modelith puts one model in git and makes every tool read and write it.
You get a real data modeler (entities, attributes, keys, domains, relationships,
subtypes) that generates dbt you can actually run, verifies the warehouse still
matches the model, and reads a legacy dbt project (or an erwin export) back into a
clean logical model.

Because the model is a single source of truth, it compiles to more than dbt. From
one definition, Modelith emits an ER canvas, contract-enforced dbt, an Open Data
Contract Standard (ODCS) contract, Pydantic models for your Python services, a Neo4j
graph schema, and RDF/OWL/SHACL, each generated deterministically. Model once, ship
the warehouse, the typed application code, the graph, and the governance contract
together.

Nothing here is a mockup. Every capability below is exercised by the test suite and,
where it touches SQL, by a real `dbt build` against DuckDB.

### How it compares

|  | Modelith | dbt alone | erwin / ER tool | Hand-rolled contracts |
|---|---|---|---|---|
| Visual ER model | Yes | No | Yes | No |
| Generates runnable dbt | Yes | n/a | No (DDL only) | No |
| Round-trip safe (keeps hand edits) | Yes | n/a | No | n/a |
| Drift caught + classified | Yes | No | No | Manual |
| Reverse an existing dbt project | Yes | No | No | No |
| Compiles one model to many targets | Yes | No | No (DDL only) | No |
| Lives in git, no server to run | Yes | Yes | No (desktop app) | Yes |
| Ontology / governance alignment | Yes | No | Partial | No |
| Model diff survives a rename | Yes (ULID-keyed) | n/a | No (name-keyed) | No |
| Subject areas / scoped views | Yes | No | Yes | No |

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
you the whole toolchain: the CLI, Studio (`mdl studio`), the language server,
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
mdl studio -m my-model                      # open Studio: the model in a browser
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

`mdl studio` opens the model in a browser; `mdl serve` opens the same ER canvas as the
standalone editor the VS Code extension embeds. Either way it is a full editor, not a
viewer: drag between entities to draw a relationship, edit attributes and keys inline,
align a term to an ontology, and land the change as a pull request (Studio) or a commit
(`mdl serve`, `mdl studio --direct`).
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
| Ontology alignment | Four-layer stack (industry / core / domain / specialised), SKOS predicates, a list of `ontology_refs` per object with provenance; local files, lockfile-pinned bundles, or live resolvers (OLS / OntoPortal / Collibra) |
| Design patterns | SCD2 and Data Vault (hub / link / satellite), emitting working SQL, not stubs |

## What it does

**Forward engineering.** Generate dbt-core models with enforced contracts, primary and
unique key constraints, relationship tests, and platform-specific types for DuckDB,
Snowflake, Redshift, Iceberg, and Trino. Regeneration runs a three-way merge so hand
edits survive.

**Compile targets.** The same model compiles to more than dbt, each target generated
deterministically from the one definition:

| Target | Command | What you get |
|---|---|---|
| dbt-core | `mdl generate` | Contract-enforced models with keys, tests, and platform types |
| Data contract | `mdl export contract` | An Open Data Contract Standard (ODCS v3) `datacontract.yaml`: schema, keys, valid values, and ownership |
| Pydantic | `mdl emit pydantic` | Pydantic v2 models for Python services and agents, with nullability and enum enforcement |
| Neo4j | `mdl export graph` | A Cypher schema: node-key, unique, and existence constraints, plus relationship types |
| Semantic layer | `mdl emit semantic` | MetricFlow semantic models and metrics, or OSI |
| Ontology | `mdl export rdf` / `shacl` | RDF/OWL with SKOS alignments, and SHACL shapes |
| Knowledge graph | `mdl export r2rml` | A W3C R2RML mapping: the deterministic term-map from warehouse rows to typed, ontology-aligned graph nodes; fails loud on any unmapped entity unless `--allow-unmapped` |

`mdl generate --emit-contract` (also `--emit-pydantic`, `--emit-graph`, `--emit-r2rml`)
turns Modelith into a contract factory: on every regeneration it drops a fresh, valid
artifact at the model root, so a git tag or CI step keeps the contract in lockstep with
the model.

**Optional knowledge graph.** If your team wants a first-class knowledge graph, the same
model emits a standards-correct **R2RML** mapping (`mdl export r2rml`); if not, ignore it.
R2RML is the W3C mapping standard whose durable idea is the term-map: a deterministic
function from primary-key columns to a node IRI, so a warehouse row and a graph node are
provably the same entity, aligned to the ontology IRIs the model already carries. Modelith
emits the mapping, not triples, so the warehouse stays the store. Feed the mapping to
[Ontop](https://ontop-vkg.org/) for a virtual SPARQL endpoint over the warehouse (no copy,
always fresh), or to [Morph-KGC](https://github.com/morph-kgc/morph-kgc) to materialize a
triple store. `mdl export graph` (Neo4j Cypher) is the property-graph sibling.

The term-map is customisable: set a project `kg_base_iri` for your own namespace, and add
per-entity or per-attribute `term_map` overrides (subject IRI template, class IRI,
predicate IRI, datatype), authored in YAML or through the canvas Inspector (and the VS Code
extension). Entities you have aligned to an ontology are typed with that aligned IRI
automatically. See [docs/knowledge-graph.md](docs/knowledge-graph.md).

**Reverse engineering.** Point `mdl reverse` at a compiled dbt project (`manifest.json`
plus `catalog.json`) and get a logical model back. It excludes staging and intermediate
models, collapses SCD2 column triples into a pattern, strips surrogate keys (keeping a
conformed dimension's natural key), marks reporting rollups unmanaged rather than minting
keyless entities, detects Data Vault structures, and infers relationships from tests and
naming, recording every decision in a reviewable ledger. After the run it prints a
classification summary (what was excluded, marked a rollup, or stripped) so a
misclassification on non-standard naming is visible immediately, not discovered at PR
time; the conventions it keys off are overridable via `mdl reverse --naming <file.yaml>`
(medallion `gold_`, `f_`/`d_`, non-English). See [Reverse engineering a real warehouse](#reverse-engineering-a-real-warehouse) below.

**Drift detection.** `mdl drift` compares the committed model to a compiled warehouse and
classifies each difference as breaking, additive, or cosmetic, with a CI gate mode and a
reconcile mode. A 400-model breaking change classifies in under thirty seconds.

**Ontology anchoring.** Bind your entities and attributes to industry and enterprise
ontology terms, browse and search those ontologies from the canvas or your editor, pin
them reproducibly, and export a knowledge graph that fails loudly on anything unmapped.
See [Ontology and knowledge graph, step by step](#ontology-and-knowledge-graph-step-by-step)
below.

**Governance sync.** A neutral governance graph maps to an external catalog through a
customer-owned Jinja profile. A Collibra adapter ships, along with OpenLineage emission
and a conformance kit that validates a bespoke mapping in CI.

## Reverse engineering a real warehouse

`mdl reverse` lifts a compiled dbt project into a logical model. On a real, organically
grown warehouse the heuristics do a lot automatically — but their conventions are
US-dbt/Kimball by default, so a shop with different naming (medallion `bronze_`/`gold_`,
`f_`/`d_` facts and dims, non-English) can be misclassified. Two things make that safe:
a **classification review** that surfaces every decision, and a **`--naming` override**
that teaches reverse your conventions.

### 1. Reverse and read the review

```bash
mdl reverse --project transform/target/manifest.json --out model
```

Reverse excludes staging/intermediate models, detects SCD2 dimensions, strips surrogate
keys (while keeping a conformed dimension's natural key, e.g. `date_key` on `dim_date`),
marks keyless reporting rollups (`mart_`/`rpt_`/`agg_`) as unmanaged rather than minting
junk entities, and infers relationships from tests. Every decision lands in a reviewable
ledger, and it prints a summary grouped by the rule that fired:

```text
Classification review

kept as business entities (2)
  dim_customers, dim_date
managed but no business key (check these) (1)
  gold_daily_kpi
excluded as staging/intermediate (1)
  stg_raw
surrogate keys stripped (1)
  dim_customers.customer_sk
```

The `managed but no business key (check these)` group is the tell: anything there is
likely a rollup or view whose naming reverse didn't recognise. (Use `--no-review` to
silence the summary in scripts.)

### 2. Override the naming conventions

If the review shows misses, point reverse at a YAML of overrides. **Everything is
additive** — merged with the built-in defaults — so you declare only what's
non-standard; `stg_`/`dim_`/`mart_`/`_sk`/`valid_from` keep working:

```yaml
# naming.yaml — reverse conventions for a German medallion warehouse
reverse:
  staging_prefixes: [bronze_, silver_]   # raw + cleansed layers -> excluded like stg_/int_
  rollup_prefixes:  [ber_]               # ber_ (Bericht = report) -> reporting rollup
  strong_surrogate_suffixes: [_hs]       # this shop hashes keys as _hs, not _sk
  scd2_from: [gueltig_ab]                # SCD2 validity columns in German
  scd2_to:   [gueltig_bis]
```

```bash
mdl reverse --project transform/target/manifest.json --out model --naming naming.yaml
```

Now `bronze_`/`silver_` are excluded, `ber_` tables become unmanaged rollups, `_hs`
columns are stripped as surrogate keys, and `gueltig_ab`/`gueltig_bis` are recognised as
an SCD2 pair. The overridable lists (all additive):

| Key | What it matches |
|---|---|
| `staging_prefixes` / `staging_tags` | models excluded as staging/intermediate |
| `rollup_prefixes` / `rollup_tags` | keyless reporting rollups kept unmanaged |
| `strong_surrogate_suffixes` | key suffixes always stripped as surrogates (`_sk`, `_hk`, …) |
| `surrogate_exact` | exact column names that are surrogate/hash columns |
| `hash_types` | physical types that mark a `_key` column as a hash surrogate |
| `scd2_from` / `scd2_to` / `scd2_current` | SCD2 validity column names |

You can also drop the same `reverse:` block into your project's `mdl-project.yaml` under
`naming:` — re-reverses then pick it up without the flag.

## Ontology and knowledge graph, step by step

Modelith treats an ontology the way a build treats a dependency: you declare a source,
pin it, and reference terms — nothing is copied into your repo by hand. A term can come
from a local file, a public registry, or an enterprise catalog, and it is bound and
pinned identically regardless of where it was found. This section walks the whole loop.

Everything below works offline against the bundled IBoR demo, which ships a small mock
FIBO server that `mdl serve` starts for you:

```bash
cd demo/ibor
mdl serve -m model        # opens the canvas AND auto-starts the demo ontology server
```

### 1. Declare an ontology source

A source is one entry in `ontology_stack` in `mdl-project.yaml`. It is either a **local
file** vocabulary or a **remote resolver** browsed live. All four types are configured
the same way:

```yaml
ontology_stack:
  - name: fibo                       # a local RDF/OWL/Turtle vocabulary
    layer: industry
    format: turtle
    path: ontologies/industry/fibo/2024.03
    prefixes:
      fibo: "https://spec.edmcouncil.org/fibo/ontology/"

  - name: ols                        # public OLS4 (no auth) — live search only
    layer: industry
    type: ols
    url: https://www.ebi.ac.uk/ols4/api

  - name: bioportal                  # OntoPortal / BioPortal (API key)
    layer: domain
    type: ontoportal
    url: https://data.bioontology.org
    apikey_env: BIOPORTAL_APIKEY

  - name: collibra                   # Collibra Ontology Domains (bearer token)
    layer: core
    type: collibra
    url: https://acme.collibra.com
    token_env: COLLIBRA_TOKEN
    domain_types: [Ontology]
```

`layer` places the source in the four-layer stack (industry / core / domain /
specialised). A remote resolver is strictly a live-lookup convenience — it is never a
build-time dependency, and its API keys/tokens are read from environment variables, never
stored in the repo.

### 2. Pin and fetch (reproducible builds)

For a source you want pinned to an exact version, lock it and fetch it. The lock records
a sha256; the content lands in a **gitignored** cache, never committed:

```bash
# pin an immutable file/URL (artifact mode), binding its prefix so IRIs resolve offline
mdl ontology lock industry https://spec.edmcouncil.org/.../fibo.ttl \
  --mode artifact --version 2024.03 \
  --prefix fibo --prefix-iri "https://spec.edmcouncil.org/fibo/ontology/"

# pin a live triple store as a point-in-time snapshot (endpoint_snapshot mode)
mdl ontology lock enterprise https://ontology.internal/sparql \
  --mode endpoint_snapshot --snapshot-tag 2026-08-15

# on a fresh clone / in CI: fetch + hash-verify every locked layer, fail-closed on drift
mdl ontology fetch
```

`mdl ontology fetch` is the `dbt deps` / `npm ci` of ontologies: it reproduces the exact
pinned content into `.mdl/ontology-cache/` and aborts if a hash no longer matches. For a
small private ontology you would rather review in git, `mdl ontology add <file>` vendors
it into the repo and wires the source entry for you instead.

### 3. Browse, search, and align

Open the canvas (`mdl serve`) and click the ontology browser (the ⬡ toolbar button):

1. Pick a **source** from the chips at the top (or "all sources"). Picking one scopes
   search to that vocabulary — essential when a catalog has millions of terms.
2. Type a query. Hits come back live, tagged with their source, with definitions and a
   class hierarchy you can drill into. Glossary terms and ontology classes are
   distinguished with a badge.
3. Select an entity, and in its inspector click **Align…**. Search, pick a term, choose a
   SKOS predicate (`exactMatch` / `closeMatch` / `broadMatch`), and confirm.

![The ontology browser: a source picker with "all sources" and "FIBO (demo subset)" chips, and live search results for "financial" tagged with their fibo-ols source and definitions](docs/assets/ontology-browser.jpg)

Every hit drills into a detail card with its definition, source, and class hierarchy:

![The term detail card for Party In Role, showing its definition, fibo-ols source, and a broader link to Party](docs/assets/ontology-term-detail.jpg)

The alignment is written into the entity's YAML under `ontology_refs` — a **list**, so an
object can carry several bindings — with `resolved_via` provenance recording which
resolver found it. A term picked from a remote resolver is also snapshotted into the local
cache so it still validates and exports offline.

![The entity inspector showing an accepted ontology alignment: layer core, fibo:FinancialInstrument with a skos:closeMatch predicate, resolved via fibo-ols](docs/assets/inspector-aligned.jpg)

You can also align in YAML directly, with autocomplete. In VS Code / Cursor / Windsurf (or
any LSP editor), typing under `ontology_refs:` on a `uri:` line offers ranked completions
from every configured resolver, showing the term label, source, and definition:

```yaml
ontology_layer: core
ontology_refs:
  - predicate: skos:exactMatch
    uri: fibo:FinancialInstrument     # <- autocompletes against your ontology sources
    resolved_via: ols
```

![The LSP completion popup on a `uri:` line in instrument.yaml, suggesting fibo:FinancialInstrument with its source and definition](docs/assets/lsp-autocomplete.png)

### 4. Reverse an existing project, then align in bulk

Reversing a dbt project (`mdl reverse`) lifts a logical model but leaves it unaligned. The
**alignment pass** proposes bindings for the whole model at once, matching each entity and
attribute against the *merged closure* of every configured source (public plus enterprise
extensions), and writes ranked candidates to the decision ledger — **nothing is
auto-applied**:

```bash
mdl ontology align                 # propose alignments -> .mdl/decisions.yaml
```

Each proposal carries a confidence and its candidate list. A subject-matter expert reviews
and accepts them in the glossary app (or `mdl ontology promote`), which is what writes the
final alignment and its audit trail (`resolved_by`, `approved_at`) into the model. An
object can end up with several bindings — an accepted one and a proposed one awaiting
review, each showing its predicate and source:

![The inspector showing two ontology refs on one entity: an accepted fibo:FinancialInstrument via ols4, and a proposed acme-core:TradableAsset via collibra with a Promote button](docs/assets/inspector-multi-ref.jpg)

### 5. Validate coverage

```bash
mdl ontology check                 # four-layer rules + industry-coverage report
mdl validate                       # includes ontology-layer diagnostics
```

`check` reports the CDO-facing coverage number (what share of core terms are aligned) and
flags downward alignments, non-SKOS predicates, exactMatch cycles, and unresolvable IRIs.

### 6. Export the knowledge graph (fail-loud)

With entities aligned, emit the R2RML mapping. By default it **fails loudly** and lists
anything still unmapped, rather than silently minting placeholder IRIs:

```bash
mdl export r2rml                   # fails if any managed entity/attribute is unmapped
mdl export r2rml --allow-unmapped  # mint fallback IRIs on the project base instead
```

Feed the mapping to [Ontop](https://ontop-vkg.org/) for a virtual SPARQL endpoint over
your warehouse, or to [Morph-KGC](https://github.com/morph-kgc/morph-kgc) to materialize a
triple store. The mapping, the lockfile, and the alignments are all git-tracked and
reproducible; the fetched ontology content is not. See
[docs/knowledge-graph.md](docs/knowledge-graph.md).

**Semantic layer.** Emit MetricFlow semantic models and metrics, or OSI (version-isolated),
with joinability and fan-out validation.

**Collaboration.** A structural, ULID-keyed git merge driver lets two people add different
attributes to the same entity and merge cleanly. A change classifier routes pull requests
to the right reviewers, a debt valve records engineer-owned SQL with an expiry, and a
git-native glossary app lets subject-matter experts propose definitions as pull requests
without ever seeing git or a CLI.

## Cross-repo model catalog

By default a Modelith project is one repo, worked on privately alongside its dbt — you
never need a catalog. When an org has *many* model repos, the catalog is the level above:
a browsable index of every published model, discovered without checking any of them out.

It is base-tier and decoupled from governance: it needs no Collibra, no profile, no
adapter, and works with zero configuration. (A governance adapter may optionally read the
same manifest, never the reverse.)

**Publish** — run in CI on merge to main, next to `mdl gov publish` where that's
configured, but with no governance config required:

```bash
mdl catalog publish            # writes one manifest entry: name, namespace, git
                               # remote+commit, ontology layers, published-at
```

The default backend is a **git-native manifest repo** — one human-readable YAML entry per
model, no database or server. Publishing is idempotent (same commit = no-op) and the
catalog is **rebuildable, not authoritative**: if it's ever lost, replaying `mdl catalog
publish` from every model repo's CI reconstructs it. Configure the catalog repo in
`.modelith/catalog.yaml`:

```yaml
catalog:
  backend: git
  remote: git@github.com:acme/model-catalog.git
```

**Browse** — one level above any repo, a searchable/filterable list of every model with
links out to each source repo@commit (pointers only; model detail is fetched from the
source on demand):

```bash
mdl catalog serve              # http://127.0.0.1:4811/catalog
mdl catalog list --search fibo # or from the terminal
```

The browse view lists each published model with its ontology-layer chips, short commit,
publish date, and a link out to the source repo@commit — searchable by name, namespace, or
layer, and filterable by layer chip.

Click a model and the catalog opens its **LDM canvas** in-app: the backend materialises
that entry's model (the git backend checks the source repo out at the pinned commit into a
local cache) and mounts a read-only canvas at `/view/<slug>`. The catalog stays a pointer
index — the checkout is a disposable cache, never a second source of truth, and the canvas
is read-only because editing happens in each model's own private workspace. Materialisation
is a backend concern: an S3 or DataHub backend fetches the model bundle its own way behind
the same interface, and a backend that can't materialise degrades to the source-repo link.

Additional backends (S3, DataHub, …) install as separate adapter packages behind the same
`CatalogBackend` interface; the git backend is the reference implementation.

**Try it with the bundled demos.** The repo ships three model demos (`demo/ibor`,
`demo/legacy-warehouse`, `demo/retail-dwh`). A seed script turns each into a throwaway
local git repo and publishes it, so the catalog lists all three and clicking any card
opens its LDM canvas:

```bash
uv run python scripts/catalog_demo_seed.py   # publish the 3 demos into a local catalog
uv run mdl catalog list                      # confirm: pension_ibor, legacy_reversed, retail_ldm
uv run mdl catalog serve                      # http://127.0.0.1:4811/catalog
```

Open http://127.0.0.1:4811/catalog and click a model — it checks that demo out at its
pinned commit and renders its read-only ER canvas at `/view/<slug>`. Everything the script
writes lives under `.catalog-demo/` and `~/.modelith/`, so it never touches your working
tree; remove it with `rm -rf .catalog-demo ~/.modelith/catalog-cache ~/.modelith/sources`.

## Subject areas, model diff, and the review flow

Three things a modeller coming from erwin expects, and where they live in Modelith.

| erwin | Modelith |
|---|---|
| Subject Area editor — Available/Included panes, "add related objects" | `mdl subject-area` + the workspace in `/sme` |
| Complete Compare — a side-by-side tree, matched by NAME | `mdl diff` + the review screen, matched by **ULID** |
| Mart lock, work on a copy, "complete merge" | a git branch and a pull request, made visible |

Everything below runs against the bundled demo. Copy it somewhere throwaway first,
so you can experiment freely:

```bash
cp -R demo/ibor/model /tmp/ibor-demo && cd /tmp/ibor-demo
git init -q . && git add -A && git commit -qm "the model as it stands"
```

### 1. Carve out a subject area

A subject area is a named sub-view of the model. Modelith keeps two ideas apart on
purpose: an entity's **home** area (`subject_area:` on the conceptual entity, one
per object, what colours the canvas) and an area's **member list** (`members:`,
many per object). That second one is what lets a use-case view and a domain view
both contain `Account`, exactly as erwin allows.

```bash
mdl subject-area list -m .
mdl new subject-area "UseCase-ApraStressTesting" -m .
mdl subject-area add UseCase-ApraStressTesting Position -m .
```

```text
Counterparty Management              0 member(s)    1 homed   01KZ265963K1SX5TK770VJEYHD
created subject area 'UseCase-ApraStressTesting' (01M1YX69Z70NQ1JSX2Z2AJXJ4W)
added 1 object(s) to 'UseCase-ApraStressTesting'
```

Names work anywhere a ULID does, so you never have to copy identifiers around.

### 2. Add the related objects

This is erwin's "add ancestors/descendants, N levels", and it **previews by
default** — naming the relationship each object arrived through, so a deep
expansion can't silently swallow the model:

```bash
mdl subject-area expand UseCase-ApraStressTesting --direction ancestors --levels 1 -m .
```

```text
2 object(s) related to 'UseCase-ApraStressTesting':
  + Instrument               via position_to_instrument  (ancestor, level 1)
  + Portfolio                via position_to_portfolio  (ancestor, level 1)

preview only — re-run with --apply to add them
```

Go two levels and commit it:

```bash
mdl subject-area expand UseCase-ApraStressTesting --direction ancestors --levels 2 --apply -m .
mdl subject-area show UseCase-ApraStressTesting -m .
```

```text
  + Benchmark                via portfolio_to_benchmark  (ancestor, level 2)
  + Counterparty             via instrument_to_counterparty  (ancestor, level 2)
added 4 object(s) to 'UseCase-ApraStressTesting'

UseCase-ApraStressTesting
  included (5)
    - Position
    - Instrument
    - Portfolio
    - Benchmark
    - Counterparty
```

*Direction follows the IR: `from` is the many side and `to` is the one side, so an
**ancestor** is the parent — from `Position` you reach `Instrument` and `Portfolio`,
the things its foreign keys already point at.*

`mdl subject-area show` also lists anything homed in the area but missing from its
member list (`MDL-W114`) — a nudge, not an error, since a repo may use the home tag
for colour alone.

### 3. See only that area on the canvas

```bash
mdl serve -m . --port 4800
```

Open `http://127.0.0.1:4800/?subject_area=<ULID>` (the ULID from step 1) and the
ER canvas is scoped to that view. The scope is the union of the area's members and
anything homed there; a relationship is kept only when **both** ends are in scope,
so you never get an edge dangling into nothing. The subject-area list itself is
never filtered — the picker still needs all of them.

For the demo view that means 5 entities and 4 relationships, out of 7 and 7.

### 4. Diff the model, not the YAML

`mdl diff` compares two models **keyed by ULID**. Rename an entity:

```bash
sed -i '' 's/^name: Counterparty$/name: Legal Entity/' conceptual/entities/counterparty.yaml
mdl diff -m .
```

```text
HEAD → working copy: 1 modified

⚪ ~ Counterparty → Legal Entity (conceptual entity)
    Renamed — Counterparty → Legal Entity
```

One cosmetic change. Erwin's Complete Compare matches objects by **name**, so the
same edit reads there as an entity removed plus a different entity added, with every
attribute listed twice. Because Modelith's ULIDs are immutable and file-borne, the
attributes underneath stay quiet.

Now try something that actually breaks downstream:

```bash
git checkout -- . && mdl diff -m .    # back to clean
# remove an attribute from logical/entities/position.yaml, then:
mdl diff -m . ; echo "exit: $?"
```

```text
HEAD → working copy: 1 modified · 1 breaking

🔴 ~ position (logical entity)
    Attribute removed: quantity — decimal · nullable

exit: 2
```

Severity is the same vocabulary as drift — breaking / additive / cosmetic — so the
word means one thing across `mdl diff`, `mdl drift --check` and CI. **Breaking
changes exit 2**, so `mdl diff` drops straight into a pipeline.

`--format markdown` renders the PR body; `--format json` is the same shape the web
API returns, so there is one serialiser and no drift between surfaces.

### 5. Review and propose, in the browser

```bash
mdl studio -m . --port 4810       # then open /sme
```

**Two people need different things from the same model, so Studio has two modes.**

| | Modeler / steward | Engineer / architect |
|---|---|---|
| Command | `mdl studio` | `mdl studio --direct` |
| An edit | is staged | writes the working tree |
| Lands via | one pull request | your own commit |
| Git | never in the way | the panel is right there |
| Also lives in | the browser | the editor and the CLI |

The **modeler** is the default. Someone who owns what the model *means* — a data
modeler, a steward, an architect who would rather not live in an editor — opens the
app, edits, reviews their own change, and submits. They never see a branch, a
commit or a merge conflict; the review screen is what they see instead.

The **engineer** already lives in git. `--direct` gives them the same surface with
the staging removed: edits hit the working tree immediately and they commit when
they are ready, exactly as the canvas has always worked. Most of the time they will
be in VS Code with the canvas beside their YAML — `--direct` is for the moments
when a diagram is easier than a diff.

It is one application either way, and a **standalone** one: its own Vite entry, so
it never loads the architect canvas bundle, and `mdl studio` does not serve that
canvas at all. Handing someone this URL hands them one app, not two. (`mdl serve`
still serves the architect canvas for the VS Code extension; `mdl glossary` is a
deprecated alias that prints a note.)
(`--with-canvas` serves both from one process when you want that.)

The app has five views. **Terms** is the glossary. **Model** is the full ER editor —
the same canvas the architect uses, with the inspector, attribute types and roles,
drawing relationships, and creating or deleting entities, scoped by subject area.
**Subject areas** is erwin's Available/Included picker, with an "add related objects"
preview that names the relationship each object arrived through. **My proposals**
lists your open proposal branches. Stage an edit and the tray takes you to
**Review**, which shows:

- the diff, grouped by object, each change as a sentence rather than a YAML hunk —
  definitions get a word-level intra-diff so only what changed is highlighted;
- for a breaking change, **the dbt models it will break**, named inline, at the
  moment you are about to do it;
- the review route, who reviews it, and which CI gates run;
- whether it still merges cleanly onto the base branch;
- a **Diagram** tab drawing the same diff *on the model* — changed entities tinted
  by severity, everything else dimmed to context. Erwin's Complete Compare is a
  tree and structurally cannot do this.

A banner states the git reality plainly. There is no lock file and no "claim this
area" button: the branch **is** the lock, so the banner says which branch you are
on, and greys out Submit with the reason when the working tree is dirty — rather
than letting the proposal fail after you've filled in the form.

The same information is available from the terminal, which is how the screens are
tested:

```bash
curl -s localhost:4810/api/git/classify | python3 -m json.tool
curl -s "localhost:4810/api/git/conflicts?base=main" | python3 -m json.tool
curl -s localhost:4810/api/git/proposals | python3 -m json.tool
```

```text
route: A Meaning
reviewers: ['@acme/glossary-council']      # from the repo's real .github/CODEOWNERS
gates: mdl validate, mdl ontology check
clean: True | behind: 0
```

Reviewers come from your **actual** `.github/CODEOWNERS` when the repo has one,
falling back to the route defaults — naming a placeholder team would be worse than
naming none.

**Browse many models.** `mdl studio --catalog` opens on a list of every published
model instead of a single model dir. Click one to view it; click **edit** and
Modelith checks that model's own repo out on a proposal branch, so your changes land
as a pull request *there*. The catalog stays a pointer index — it never becomes a
second source of truth, and there is no check-out/check-in to manage:

```bash
mdl catalog publish          # from each model repo's CI, on merge
mdl studio --catalog         # browse them, open one, edit, propose
```

Branching from the *published* commit rather than a moving `main` is deliberate:
your proposal is based on exactly the model you were shown, and if `main` has moved
the conflict banner says so instead of silently rebasing under you.

**Any git host.** Proposals work the same on GitHub, GitLab, Azure DevOps and
Bitbucket. The "open a pull request" link is built from your `origin` remote and the
branch name — ssh or https, with or without embedded credentials, including Azure's
`v3/org/project/repo` ssh form and the legacy `*.visualstudio.com` host. No provider
API, no `gh` dependency.

Self-hosted installs need one line, because a domain we do not recognise is not
something to guess at:

```yaml
git:
  provider: gitlab                # self-hosted GitLab at git.acme.internal
  # or, for a host whose URL shape we do not implement:
  # pr_url_template: "{repo}/newpr?from={branch}"
```

**Credentials stay yours.** Run `mdl studio` on your own machine and it pushes with
the git credentials you already have — SSH key, credential helper, whatever your
host expects. Nothing to configure, signed commits keep working, and SAML-protected
orgs are satisfied because it is *your* identity doing the push. A shared server
would need push rights of its own; running it locally avoids that question
entirely.

One thing to know about opening a model from the catalog: it always restarts from
the published commit rather than resuming a previous session's branch — the
checkout cache is disposable, and edits live in the browser's tray until you
propose.

**Distributing it.** The app is a client over the API, so it can be hosted anywhere:

```bash
mdl studio --export ./modeler-app       # ~6 files, ~190 KB
```

Serve that directory from any static host and proxy `/api/` to an `mdl studio`
process. The ER diagram is code-split, so the 190 KB is what loads up front and
React Flow arrives only when someone opens the Model tab.

Two details worth knowing:

- **Conflict preview runs the real merge driver.** It is not an approximation: the
  endpoint feeds three file versions through the same `merge_model_files` git would
  use, and keeps only the verdict. Two people adding *different* attributes to one
  entity comes back clean, where a textual merge would conflict. Nothing is written.
- **`gh` is optional.** Branch, title, date, merge state, ahead/behind and route
  all come from plain git, so "My proposals" is fully populated without it — only
  the PR review state is unavailable, and the cards say so and link to the compare
  view instead.

When you submit, Modelith branches to `sme/<you>/<slug>`, applies your changes
through the one mutation engine, commits with a `Co-authored-by` trailer, pushes if
there is a remote, and opens a PR if `gh` is available — degrading gracefully at
each step. It then **returns you to the branch you started on**, so the next person
to open the model sees the base branch rather than your unmerged proposal.

### 6. Structural objects from the terminal

Key groups, categories, domains and value sets used to be reachable only by hand-
editing YAML. They now have commands, with the referential guards you'd expect —
a rename rewrites everything that references the object by name, and a delete is
refused while something still uses it:

```bash
mdl new subject-area "Domain-03-ExposureManagement" -m .
# and via the API / canvas: create_key_group, create_category,
# create_domain, create_code_set, set_object_definition
```

`set_object_definition` fills the other gap: `LogicalEntity`, `Attribute`,
`Relationship`, `KeyGroup` and `Category` had nowhere to put documentation. Attribute
definitions now flow into the generated dbt `description`, so they reach your data
catalog.

## Surfaces

| Surface | What it is |
|---|---|
| Studio | `mdl studio` — the model in a browser, in two modes. Default: a **modeler** stages edits and proposes them as one pull request, never seeing git. `--direct`: an **engineer** writes the working tree and commits themselves. Terms, the ERD (scoped by subject area), the subject-area picker, a semantic diff and the proposal flow. Distributable on its own with `--export`. |
| VS Code extension | Where engineers actually live: the canvas beside your YAML, following the active editor, plus diagnostics on save and generate / drift / lint commands. See [In VS Code](#in-vs-code). |
| `mdl` CLI | The full command set: init, validate, lint, generate, reverse, drift, diff, studio, subject-area, ontology, emit, export, import, gov, catalog, and more |
| Language server | `mdl lsp` (one server for VS Code, Cursor, Windsurf, JetBrains, and CI): drift and contract diagnostics on the dbt files, hover cards, code actions |
| Architect canvas | `mdl serve` — the ER editor the VS Code extension embeds. `mdl studio --direct` is the same editing model with Studio's chrome. |

## CLI reference

```text
mdl init [--workspace] [--git-hooks]              scaffold a model repo or full topology
mdl new entity|term|subject-area <name>           add an object (ULIDs minted)
mdl delete entity <name> [--cascade]              remove an object, safely
mdl validate [--format json]                      schema, refs, ontology, naming
mdl lint [--fix]                                  naming-standards lint
mdl generate [--target] [--emit-contract] [...]   emit the dbt project (+ optional targets)
mdl reverse --project <manifest|schema.yml> [--naming <f>]  lift a dbt project into a model
mdl drift --manifest <m> [--check|--reconcile]    compare model to compiled warehouse
mdl diff [--base <ref>] [--format json|markdown]  semantic model diff (exit 2 on breaking)
mdl subject-area list|show|add|remove             scoped views of the model
mdl subject-area expand [--direction] [--levels]  add related objects (preview by default)
mdl studio [--read-only] [--with-canvas]          Studio: terms, ERD, subject areas, review, propose
mdl studio --direct                               engineer mode: edits write the working tree
mdl studio --catalog                              browse published models, open one to edit
mdl studio --export <dir>                         write its static files, to host anywhere
mdl serve [--read-only] [?subject_area=<ulid>]    architect ER canvas + read API (VS Code embeds this)
mdl ontology search|check                         browse; layer rules + coverage report
mdl ontology lock|fetch|add                       pin a source, fetch+verify, vendor a file
mdl ontology align|promote                        propose alignments (§2), accept them
mdl emit semantic --format metricflow|osi         semantic layer
mdl emit pydantic                                 Pydantic v2 data models
mdl export contract|graph                         ODCS data contract, Neo4j Cypher schema
mdl export r2rml [--allow-unmapped]               R2RML KG mapping (fails loud on unmapped)
mdl export json-schema|rdf|shacl                  interchange out
mdl import osi|erwin                              interchange in
mdl gov plan|apply|pull|publish|import            governance catalog sync
mdl catalog publish|list|serve                    cross-repo model catalog (one level above)
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
  catalog/        cross-repo model catalog: entry, backend SPI, git manifest backend
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
