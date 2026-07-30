# Modelith: Build Specification v0.1

**Working name:** Modelith. **CLI:** `mdl`. **License target:** Apache 2.0.

**One line:** an ontology-anchored, git-native data modeling tool that replaces erwin for dbt-core teams, emits dbt contracts, MetricFlow semantic models and OSI interchange files, and syncs to enterprise governance catalogs through a configurable adapter.

This document is written to be handed to Claude Code as the build brief. Sections 2 and 3 are the load-bearing ones. Do not start on the canvas until Section 2 passes its property tests.

---

## 0. Two risks that shape the whole build

### 0.1 Round-trip fidelity

Every tool in this category dies here. Regeneration either clobbers hand-written SQL or silently diverges from the model until people stop trusting it. Mitigations, all mandatory before any UI work:

1. **Generation is content-addressed.** Every emitted file carries a `mdl-fingerprint` of the model subgraph that produced it. Regeneration compares fingerprints before writing.
2. **Protected regions are the default, not an escape hatch.** Generated blocks are explicitly delimited. Everything outside a delimiter is user territory and is never rewritten.
3. **Three-way merge, not overwrite.** Base is the last generation snapshot stored in `.mdl/state/`, ours is the working tree, theirs is the new generation. Conflicts surface as conflicts, not as data loss.
4. **Idempotence is a tested property.** `generate` run twice on an unchanged model must produce a byte-identical tree. This is a CI gate, not an aspiration.
5. **Every inference is a recorded decision.** Reverse engineering never guesses silently. It proposes, the human accepts or rejects, and the choice is persisted so the next run does not re-ask.
6. **Drift detection is the product.** The most valuable single feature is a CI check that compares the committed model against the live `manifest.json` and fails or opens a PR. Ship it in M2, before anything visual.

### 0.2 Governance integration variance

Collibra operating models differ per tenant. A hard-coded connector turns every deployment into a services engagement. Therefore:

1. **Core has no Collibra code.** Core emits a neutral `GovernanceGraph`. Adapters consume it.
2. **The mapping is a declarative artifact the customer owns**, versioned in their repo, not in ours.
3. **A conformance test kit** ships with the adapter SPI so a customer or partner can validate a bespoke mapping without our involvement.
4. **Three reference profiles ship in-repo**: Collibra out-of-the-box operating model, a "dbt-heavy analytics engineering" profile, and a minimal profile. These are starting points to fork.
5. **Dry-run diff before every push.** No adapter writes to a catalog without an approved plan file.

---

## 1. Architecture

### 1.1 Repo layout

```
modelith/
  packages/
    core/              # model IR, parser, validator, merge engine   (Python 3.11+)
    ontology/          # ontology stack, IRI resolution, SHACL, RDF export
    reverse/           # dbt manifest/catalog + SQL reverse engineering
    emit-dbt/          # dbt-core project emitter
    emit-semantic/     # MetricFlow + OSI emitters
    platforms/         # PDM adapters: snowflake, redshift, iceberg, trino, duckdb
    governance/        # GovernanceGraph + adapter SPI + conformance kit
    adapters/
      collibra/
      openmetadata/
      datahub/
    cli/               # `mdl`
    server/            # read API for the canvas (M5+)
    canvas/            # web UI (M6+, TypeScript, do not start early)
  profiles/            # reference governance mapping profiles
  ontologies/          # bundled FIBO subsets, core ontology seed, SHACL shapes
  corpus/              # golden test projects for round-trip regression
```

### 1.2 Language and dependency choices

- Core, reverse, emitters, CLI: **Python 3.11+**. Non-negotiable, because it must import `dbt-artifacts-parser` and run in the same environment as dbt-core.
- Parsing/validation: `pydantic v2` for the IR, `ruamel.yaml` for round-trip-preserving YAML (comment preservation matters).
- SQL analysis: `sqlglot` for dialect-aware parsing and column-level lineage.
- Graph: `networkx` for the model graph and impact analysis.
- Ontology: `rdflib` plus `pyshacl`.
- Canvas: TypeScript, React, a graph renderer that handles 1000+ nodes. Deferred.

### 1.3 Layering rule

`core` depends on nothing else in the repo. `ontology`, `reverse`, `emit-*`, `governance` depend only on `core`. Adapters depend only on `governance`. Any import that violates this fails CI.

---

## 2. Canonical model format

### 2.1 Principles

- Text first. YAML, one file per entity, human-diffable, three-way mergeable.
- **Stable identity.** Every object carries a ULID assigned at creation and never changed. Renames are name changes against a fixed ULID, so lineage, governance links and ontology alignments survive refactoring. This is the single most important schema decision in the tool.
- One object graph, three layer views. Conceptual, logical and physical objects live in the same graph and reference each other by ULID.

### 2.2 Directory shape

The model and the transformation code are siblings at the repo root, not nested. The model is the durable asset and the dbt project is one consumer of it, so the layout should not imply that the model belongs to dbt.

```
acme-analytics/                    # one repo
  models/                          # canonical Modelith model  (see naming note below)
    mdl-project.yaml               # config: model dirs, dbt targets, platform targets, ontology stack
    conceptual/
      subject-areas/*.yaml
      entities/*.yaml
      terms/*.yaml                 # glossary
    logical/
      entities/*.yaml
      domains/*.yaml               # reusable attribute domains
      relationships/*.yaml
    physical/
      <target>/                    # e.g. snowflake_prod/
        tables/*.yaml
    semantic/
      metrics/*.yaml
    patterns/*.yaml                # SCD2, hub/link/sat, bridge templates
  transform/                       # dbt project(s) live here
    warehouse/
      dbt_project.yml
      models/                      # dbt SQL, partly generated
      macros/
      target/                      # gitignored
  ontologies/                      # vendored FIBO subset, core ontology, SHACL shapes
  governance-profile.yaml
  .mdl/
    state/                         # generation snapshots, sharded per artifact
    decisions.yaml                 # inference decision ledger
    debt.yaml                      # unmanaged blocks with expiry
    lock.yaml                      # pinned spec versions: dbt, OSI, FIBO, profiles
  .code-workspace                  # multi-root: architects open all, engineers open transform/
```

**Naming note, flagged once.** `models/` at the root and `transform/warehouse/models/` inside the dbt project are two different things sharing a name at different depths. This will cost real time in CODEOWNERS globs, CI path filters, grep, and every verbal conversation that starts with "which models folder". `model/` singular, or `semantics/`, or `domain/` removes the ambiguity at zero cost. If the name is settled, it must at minimum be **configuration, not convention**: the emitter and CLI resolve every path from `mdl-project.yaml`, never from a hard-coded literal, so a rename later is a one-line change.

### 2.3 Path configuration

Because the model and the dbt project are siblings, every path is declared. One model may govern several dbt projects in the same repo.

```yaml
# models/mdl-project.yaml
name: acme_analytics
model_root: .                       # relative to this file

targets:
  - name: snowflake_prod
    platform: snowflake
    dbt_project_dir: ../transform/warehouse
    dbt_version: "1.9"
    manifest_path: ../transform/warehouse/target/manifest.json
    emit:
      models_subdir: marts          # generated SQL lands in transform/warehouse/models/marts
      schema_yml: per_directory     # per_directory | per_model
  - name: iceberg_lake
    platform: iceberg
    dbt_project_dir: ../transform/lakehouse
    dbt_version: "1.9"

ontology:
  stack: [industry, core, domain, specialised]
  industry:
    source: ../ontologies/industry/fibo
    modules: [fnd, fbc, sec, der]
```

Resolution rules: all paths in `mdl-project.yaml` are relative to that file, resolved to absolute at load, and validated to sit inside the repo root. `mdl` discovers the project by walking up for `mdl-project.yaml` or `.mdl/`, so the CLI works from any subdirectory including inside `transform/`.

### 2.3 Object schemas

**Conceptual entity**

```yaml
id: 01J8ZQ7X4K5N9P2R3S6T8V0W1Y     # ULID, immutable
kind: conceptual_entity
name: Counterparty
subject_area: 01J8ZQ...            # ULID ref
definition: >
  A legal person with whom the firm has or may have a contractual obligation.
ontology:
  aligns_to: fibo-fnd-pty-pty:PartyInRole
  alignment: skos:exactMatch        # or closeMatch / broader / narrower
  layer: industry                   # industry | core | domain | specialised
stewardship:
  owner: risk-data-office
  steward: a.hough
synonyms: [Counterparty, CPTY, Trading Partner]
realised_by: [01J8ZR...]            # logical entity ULIDs, derived not authored
```

**Logical entity**

```yaml
id: 01J8ZR...
kind: logical_entity
name: counterparty
realises: 01J8ZQ7X4K5N9P2R3S6T8V0W1Y
attributes:
  - id: 01J8ZS...
    name: counterparty_id
    domain: identifier_bigint        # ref to a domain object
    role: business_key
    nullable: false
    ontology:
      aligns_to: fibo-fnd-rel-rel:hasIdentity
  - id: 01J8ZT...
    name: legal_entity_identifier
    domain: lei_code
    nullable: true
subtypes: []
pattern: null                        # or scd2 / hub / link / satellite / bridge
```

**Relationship**

```yaml
id: 01J8ZU...
kind: relationship
name: trade_has_counterparty
from: {entity: 01J8ZV..., attributes: [01J8ZW...]}   # many side
to:   {entity: 01J8ZR..., attributes: [01J8ZS...]}   # one side
cardinality: many_to_one
identifying: false
optionality: mandatory
enforce:
  physical_constraint: true          # emit FK constraint where platform supports
  dbt_test: relationships            # emit test where it does not
```

**Physical table** is generated by default and only hand-edited for platform overrides:

```yaml
id: 01J8ZX...
kind: physical_table
target: snowflake_prod
realises: 01J8ZR...
name: DIM_COUNTERPARTY
materialization: incremental
platform:
  cluster_by: [counterparty_id]
  transient: false
columns:
  - realises: 01J8ZS...
    name: COUNTERPARTY_ID
    data_type: NUMBER(38,0)
```

### 2.4 Validation

`mdl validate` runs, in order: schema validation, referential integrity of ULIDs, naming standard lint, ontology alignment check (Section 3), pattern conformance, and semantic joinability (Section 6). Exit codes must distinguish error from warning so CI can gate on severity.

**Naming standards are a first-class, enforceable config**, not documentation. erwin has this and every open-source competitor skips it. Support abbreviation dictionaries, casing rules per layer, prefix/suffix rules per pattern, and a `--fix` mode.

---

## 3. Ontology stack

### 3.1 The four layers

Every conceptual object declares which ontology layer it sits in and what it aligns to in the layer above.

| Layer | Source | Governance | Example |
|---|---|---|---|
| `industry` | External standard, read-only, vendored | Upstream body | FIBO, ACORD, FHIR, ISO 20022, GS1 |
| `core` | Enterprise-wide, derived from industry | Central architecture | `acme-core:Counterparty` |
| `domain` | Business domain, derived from core | Domain data owner | `acme-credit:Obligor` |
| `specialised` | Product or project, derived from domain | Delivery team | `acme-credit-ifrs9:StagedObligor` |

Rules the validator enforces:

1. An object may only align **upward** to the immediately adjacent layer or higher. A specialised term aligns to a domain term, which aligns to a core term, which aligns to an industry term.
2. Alignment predicates are SKOS: `exactMatch`, `closeMatch`, `broadMatch`, `narrowMatch`, `relatedMatch`. `exactMatch` is transitive-checked and must not create a cycle.
3. Every `core` term must have at least one industry alignment or an explicit, reviewed `no_industry_equivalent: true` with a rationale. This produces the coverage report that sells the tool to a CDO.
4. A specialised term that duplicates an existing domain term by definition similarity raises a warning with the candidate it duplicates. Cheap embedding similarity is acceptable here; do not over-engineer.

### 3.2 FIBO handling

- Vendor a pinned FIBO release under `ontologies/industry/fibo/<version>/` as Turtle. Pin the version in `.mdl/lock.yaml`. FIBO is large, so ship a loader that materialises only the modules declared in `mdl-project.yaml` (`fnd`, `fbc`, `sec`, `der`, `be`, `cae`, `loan`, `md`).
- Resolve prefixed IRIs (`fibo-fnd-pty-pty:PartyInRole`) against the loaded graph. Unresolvable IRIs are a validation error, not a warning.
- Provide `mdl ontology search "counterparty"` returning ranked FIBO classes with definitions, so modellers align without leaving the CLI.

### 3.3 Outputs

- **RDF/OWL export** of the conceptual and logical layers, with SKOS alignments intact. `mdl export rdf --layer conceptual --format turtle`.
- **SHACL shapes generated from the logical model**, so the model can validate instance data or be handed to a graph platform.
- Ontology IRIs flow into every downstream artifact: dbt `meta`, MetricFlow descriptions, OSI `ai_context`, and the governance graph. This is what makes the model portable and machine-readable rather than a picture.

---

## 4. OSI alignment

### 4.1 What OSI actually is, as of this writing

Open Semantic Interchange is a vendor-neutral YAML specification for semantic models, launched September 2025 by Snowflake with Salesforce, dbt Labs, BlackRock and RelationalAI, Apache 2.0, repo at `github.com/open-semantic-interchange/OSI`.

**Version caution, verify before coding.** Vendor blog posts in January 2026 describe the specification as finalized. The repository's `core-spec/spec.md` is marked DRAFT, version `0.2.0.dev0`, with `0.1.1` released 2025-12-11 and an explicit warning not to depend on the dev version in production. Treat the marketing "final" and the repo state as different things. First task: read the repo, pin the exact tag in `.mdl/lock.yaml`, and generate the IR mapping from the published JSON schema rather than hand-transcribing it.

### 4.2 Object mapping

| Modelith | OSI |
|---|---|
| Model package | `semantic_model` |
| Logical entity with a physical realisation | `datasets[]` entry, `source` from the physical target |
| Business key / unique keys | `primary_key`, `unique_keys` |
| Attribute | `fields[]`, with `expression.dialects[]` per platform target |
| Time attribute | `fields[].dimension.is_time: true` |
| Relationship | `relationships[]`, `from` on the many side, `to` on the one side, ordered `from_columns` / `to_columns` |
| Metric | `metrics[]` with dialect expressions |
| Synonyms, glossary definition, ontology IRI | `ai_context` (`instructions`, `synonyms`, `examples`) |
| Anything Modelith-specific | `custom_extensions` with `vendor_name: MODELITH` |

### 4.3 Implementation notes

- Modelith already holds multi-target physical models, so emitting **multi-dialect expressions** is close to free. Emit `ANSI_SQL` plus one entry per configured platform target. This is a differentiator: most tools will only ever emit one dialect.
- Ontology IRIs and glossary definitions go into `ai_context.instructions` in a structured, parseable form, plus the full IRI in `custom_extensions` so nothing is lost.
- OSI import is required, not optional. `mdl import osi <file>` must lift an OSI model into logical entities and relationships so teams with an existing semantic layer are not starting over.
- Because the spec is mutable, isolate all OSI knowledge behind `emit_semantic/osi/v<version>/`. Adding support for a new spec version must not touch the IR.

---

## 5. Round-trip engine

This is the heart. Build it first, in `core`.

### 5.1 Protected regions

Generated dbt SQL:

```sql
-- mdl:generated-begin id=01J8ZR... fingerprint=sha256:ab12... spec=v1
{{ config(materialized='incremental', unique_key='counterparty_id') }}
with source as (...)
-- mdl:generated-end

-- mdl:user-begin
-- anything here survives regeneration, forever
-- mdl:user-end
```

Rules:

- Content between `generated-begin` and `generated-end` is owned by Modelith and replaced on regeneration **only if the fingerprint no longer matches** the model subgraph.
- If the fingerprint matches but the content has changed, the user has edited generated code. Do not overwrite. Emit a `MDL-E201 generated block modified` error with a diff and offer `mdl adopt` to fold the edit back into the model where representable, or `mdl unmanage` to convert the block to a user block permanently.
- YAML files (`schema.yml`) use the same markers as comments and are merged key-wise, not textually, so hand-added tests and descriptions persist.

### 5.2 Generation state

`.mdl/state/generation.json` records, per emitted file: path, the ULIDs contributing to it, the subgraph fingerprint, the emitted content hash, the emitter version and the spec versions in force. This is the merge base. It is committed to git.

### 5.3 Merge algorithm

```
for each target file:
    base   = state.content_hash          # what we last emitted
    ours   = working tree
    theirs = freshly generated
    if hash(ours) == base:      write theirs                # clean
    elif theirs == base:        keep ours                   # model unchanged
    else:                       three-way merge per region  # conflict-aware
```

Conflicts are written with standard git conflict markers plus an `MDL-C3xx` code and listed in the `mdl generate` summary. Never silently resolve.

### 5.4 Drift detection

`mdl drift --manifest target/manifest.json` compares the committed model to the compiled dbt project and classifies every difference:

| Severity | Example | CI default |
|---|---|---|
| `breaking` | column dropped, type narrowed, PK changed, relationship removed | fail |
| `additive` | new column, new model, new test | warn, offer PR |
| `cosmetic` | description changed, tag added | info |
| `unmanaged` | dbt model with no model counterpart | warn, offer reverse-engineer |

Two modes: `--check` for the CI gate, and `--reconcile` which opens a PR against the model repo with the additive and cosmetic deltas already applied. The reconcile PR is the adoption mechanism: teams that never open the modeling tool still keep the model current.

### 5.5 Property tests, all mandatory in CI

1. `generate(generate(M)) == generate(M)` byte-identical.
2. `reverse(generate(M))` is semantically equal to `M` modulo documented lossy fields, and the lossy set is asserted explicitly.
3. Arbitrary user edits inside user regions survive N regenerations unchanged. Property-based with `hypothesis`.
4. Rename an entity by ULID: all downstream artifacts, governance links and ontology alignments follow, zero orphans.
5. Golden corpus regression: `corpus/` holds at least five real-shaped dbt projects (jaffle_shop, a Data Vault project, a 400-model finance project, a project with heavy Jinja macros, a project with no tests at all). Every corpus project round-trips on every commit.

---

## 6. Reverse engineering

### 6.1 Inputs

- `manifest.json` and `catalog.json` via `dbt-artifacts-parser`, version-tolerant.
- Raw model SQL parsed with `sqlglot` for column-level lineage where the manifest is silent.
- Optional live warehouse connection for data-driven relationship inference.
- Must work against `dbt compile --empty` output, so no warehouse is required for the basic path.

### 6.2 Relationship inference, ranked

| Signal | Confidence | Notes |
|---|---|---|
| Model contract `foreign_key` constraints (dbt 1.9+, manifest v12+) | high | Authoritative, accept by default |
| `relationships` data tests | high | Authoritative |
| MetricFlow / OSI entity definitions | high | |
| Name plus type heuristic (`*_id` matching a PK column) | medium | Propose, never auto-accept |
| Data profiling: inclusion dependency check on a sample | medium-high | Requires connection, opt-in |

Every proposal is written to `.mdl/decisions.yaml` with its signal, confidence and the human verdict. Rejected proposals are never re-proposed unless the underlying signal changes. This file is committed. It is what makes the second run fast and the tenth run trustworthy.

### 6.3 Physical to logical lifting

Automatic lifting is roughly 60 percent right, so it is interactive by design. `mdl reverse --interactive` proposes and the human confirms:

- Strip surrogate keys, promote business keys.
- Collapse SCD2 column pairs (`valid_from`/`valid_to`/`is_current`) into a single logical entity with `pattern: scd2`.
- Detect hub/link/satellite naming and lift to Data Vault patterns.
- Exclude staging and intermediate models from the business view by path or tag config.
- Cluster remaining models into candidate subject areas by lineage community detection, for the modeller to name.

### 6.4 erwin migration

Read `.erwin` XML exports (XMLSchema and the erwin XML format) and DDL. Map subject areas, entities, attributes, domains, relationships and naming standards into the IR. Nobody migrates greenfield, so this is the go-to-market path, not a nice-to-have. Schedule it in M4, not M8.

---

## 7. dbt emission and platform adapters

### 7.1 What gets emitted

- `<dbt_project_dir>/models/<models_subdir>/**/*.sql` with protected regions, path resolved from the target config in section 2.3, never hard-coded. For a pattern-bearing entity, emit **working transformation logic**, not a stub. An `scd2` dim emits the full incremental merge; a hub emits the hash-key insert. An empty `select 1` is a failed emitter.
- `models/**/schema.yml` with contract blocks: `contract.enforced`, column `data_type`, `constraints` (`not_null`, `primary_key`, `foreign_key`, `check`), plus `relationships` tests where the platform cannot enforce a constraint.
- `meta` carrying ULID, ontology IRI, glossary term, owner, steward, classification.
- Target dbt version declared in `mdl-project.yaml`. Contract semantics differ between 1.5, 1.9 and later, so emitters are versioned and downgrades warn loudly.

### 7.2 Platform adapter interface

```python
class PlatformAdapter(Protocol):
    name: str
    def map_domain(self, domain: Domain) -> PhysicalType: ...
    def constraint_support(self) -> ConstraintCapabilities: ...   # enforced vs informational
    def physical_options(self, entity: LogicalEntity) -> dict: ... # cluster_by, dist/sort, partition spec
    def ddl(self, table: PhysicalTable) -> str: ...
    def dialect(self) -> str: ...                                  # sqlglot + OSI dialect enum
```

Ship: `snowflake` (clustering keys, transient, dynamic tables), `redshift` (dist/sort keys), `iceberg` (partition spec, sort order, table properties), `trino` (connector capability matrix), `duckdb` (for tests and local dev).

Constraint capability matters: most warehouses accept FK constraints as informational only. The adapter declares this, and the emitter automatically compensates with a dbt test so the model's intent is actually verified.

---

## 8. Semantic layer emission

- Emit MetricFlow `semantic_models` and `metrics` YAML from the logical model. The entity graph already holds the join graph MetricFlow needs, so this is a projection, not a new authoring surface.
- Emit OSI per Section 4, from the same IR.
- **Joinability validation** before emission: every declared relationship must be traversable, and fan-out risk (many-to-many paths, unaggregated measures across a one-to-many join) is flagged as an error. Catching a fan-out at model time is worth more than any diagram.
- Metric lineage in both directions: upward to the conceptual glossary term and its ontology IRI, downward to physical source columns. `mdl lineage metric net_revenue` answers it from the model.

---

## 9. Governance adapter framework

### 9.1 Core emits a neutral graph

```python
@dataclass
class GovernanceAsset:
    external_id: str          # deterministic, derived from ULID: "mdl:01J8ZR..."
    modelith_kind: str        # conceptual_entity | logical_entity | attribute | metric | term | ...
    name: str
    attributes: dict          # definition, ontology_iri, layer, classification, ...
    relations: list[GovernanceRelation]
```

Deterministic `external_id` from the immutable ULID is what makes sync idempotent. Re-running updates, it never duplicates. This one decision removes the most common failure mode of catalog integrations.

### 9.2 Mapping DSL

The customer owns a `governance-profile.yaml` in their repo:

```yaml
profile: collibra
version: 1
community: "Data Governance Council"
domains:
  logical_model: {name: "Enterprise Logical Model", type: "Business Asset Domain"}
  physical:      {name: "Snowflake PROD",           type: "Physical Data Dictionary"}
  glossary:      {name: "Enterprise Glossary",      type: "Glossary"}

asset_types:
  conceptual_entity:
    target: "Business Asset"
    domain: logical_model
    attributes:
      Definition: "{{ definition }}"
      "Ontology IRI": "{{ ontology.aligns_to }}"
      "Ontology Layer": "{{ ontology.layer }}"
  logical_entity:
    target: "Data Entity"
    domain: logical_model
  physical_table:
    target: "Table"
    domain: physical
  attribute:
    target: "Column"
    domain: physical
  metric:
    target: "Business Metric"     # many tenants use Data Product instead: fork this line
    domain: glossary
  term:
    target: "Business Term"
    domain: glossary

relations:
  realises:      {type: "represents / represented by",  direction: source_to_target}
  aligns_to:     {type: "is classified by / classifies"}
  has_attribute: {type: "is part of / contains"}

responsibilities:
  owner:   {role: "Owner"}
  steward: {role: "Data Steward"}

writeback:                          # governance-owned fields flow back into the model
  - collibra_attribute: "Data Classification"
    model_path: "governance.classification"
  - collibra_attribute: "Retention Period"
    model_path: "governance.retention"
```

Templating is Jinja over the `GovernanceAsset`. No Python required to customise a tenant.

### 9.3 Adapter SPI

```python
class GovernanceAdapter(Protocol):
    def plan(self, graph: GovernanceGraph, profile: Profile) -> SyncPlan: ...   # never writes
    def apply(self, plan: SyncPlan) -> SyncResult: ...
    def pull(self, profile: Profile) -> WritebackSet: ...
    def capabilities(self) -> AdapterCapabilities: ...
```

`plan` is mandatory and `apply` refuses a plan it did not produce. `mdl gov plan` writes a human-reviewable diff; `mdl gov apply plan.json` executes it. No surprise writes to a production catalog, ever.

### 9.4 Collibra adapter specifics

- Uses the Collibra Import API with idempotent external IDs. Batch, resumable, rate-limit aware.
- Writeback pulls steward, classification, sensitivity and retention into the model, which then flow into dbt `meta` and `tags`, which downstream generates masking and row-access policies. That loop is the actual commercial argument.
- Workflow hook: a model change touching a certified asset raises a Collibra workflow task and blocks the dbt PR until approved.

### 9.5 Conformance kit

`mdl gov conformance --profile my-profile.yaml` runs a fixture model through the mapping and asserts: every asset type resolves, every relation type exists in the target operating model, no duplicate external IDs, all required attributes populated, writeback paths resolve. A customer validates a bespoke mapping in a CI job without contacting us. This is what keeps the mapping layer a product rather than a services line.

### 9.6 Lineage

Emit OpenLineage alongside the governance push. One lineage payload then feeds DataHub, OpenMetadata, Purview or Unity Catalog without rewriting the integration.

---

## 10. CLI surface

```
mdl init                                     # scaffold a model repo
mdl validate [--severity error|warn]
mdl ontology search <term> [--source fibo]
mdl ontology check                           # layer/alignment rules, coverage report
mdl reverse --project ../dbt --interactive
mdl generate [--target snowflake_prod] [--dry-run]
mdl drift --manifest target/manifest.json [--check|--reconcile]
mdl diff <ref-a> <ref-b> [--format text|json|mermaid]
mdl impact --attribute <ulid|name>
mdl emit semantic --format metricflow|osi
mdl export rdf|shacl|dbml|mermaid|erwin-xml
mdl import osi <file> | erwin <file> | ddl <file>
mdl gov plan|apply|pull|conformance
mdl adopt <file>                             # fold a user edit back into the model
mdl unmanage <file|block>                    # hand a generated block to the user permanently
mdl lint --fix                               # naming standards
```

Exit codes: 0 ok, 1 validation error, 2 drift breaking, 3 merge conflict, 4 adapter/plan failure.

---

## 11. CI workflows shipped as templates

1. **`mdl-validate`** on every model PR: validate, ontology check, conformance kit.
2. **`mdl-drift`** on every dbt PR: fail on breaking drift, comment the classified diff.
3. **`mdl-reconcile`** nightly: open a PR with additive drift applied.
4. **`mdl-gov-sync`** on merge to main: plan, require approval on breaking governance changes, apply.

The PR comment format matters more than it sounds. Render added/dropped/type-changed columns with a breaking-change verdict against declared contracts, plus a Mermaid diff of the affected subgraph. Reviewers who never open the tool still see the model.

---

## 12. Milestones and acceptance criteria

**M0, foundations.** IR, ULID identity, YAML parse/serialise with comment preservation, validator, `mdl init|validate`.
*Accept:* round-trip a model repo through parse and serialise with zero diff, including comments and key order.

**M1, round-trip engine.** Protected regions, generation state, three-way merge, dbt emitter for one platform (duckdb), property tests 1 through 4.
*Accept:* all five property tests green on the golden corpus. This gate is absolute. Nothing proceeds until it passes.

**M2, drift.** `mdl drift` with severity classification, `--check` and `--reconcile`, CI templates 1 and 2.
*Accept:* on a 400-model corpus project, a deliberately injected column drop is classified breaking and fails CI in under 30 seconds.

**M3, reverse and platforms.** Reverse engineering with the decision ledger, interactive lifting, Snowflake/Redshift/Iceberg/Trino adapters.
*Accept:* reverse a real dbt project, regenerate it, and produce a semantically empty diff.

**M4, semantics and ontology.** MetricFlow and OSI emitters, OSI import, FIBO loader, four-layer validation, RDF/SHACL export, erwin XML import.
*Accept:* emitted OSI validates against the pinned upstream JSON schema; a FIBO-aligned conceptual model produces a coverage report.

**M5, governance.** GovernanceGraph, mapping DSL, adapter SPI, conformance kit, Collibra adapter, three reference profiles, OpenLineage emission.
*Accept:* a bespoke profile written by someone outside the team passes conformance and produces a clean plan against a Collibra sandbox.

**M6, canvas.** Read-only visual first, then editing on the same files. Not before M5.

---

## 13. Open decisions to resolve in week one

1. **OSI version pinning.** Read the repo, decide whether to target `0.1.1` or track `0.2.0.dev0` behind a feature flag. Do not build against a blog post.
2. **YAML vs a custom DSL.** Spec assumes YAML for tooling ubiquity. If ergonomics prove bad in M1, an Azimutt-AML-style surface syntax compiling to the same IR is the fallback, but the IR does not change.
3. **Where SCD2 logic lives.** Emitted into each model, or emitted as a call to a shipped dbt macro package? Macro package is smaller and more upgradable; inlined is more transparent and survives our tool being removed. Recommend macro package with an `--inline` escape hatch.
4. **Embedding model for duplicate-term detection.** Local sentence-transformer keeps the tool offline-capable. Prefer offline: enterprise buyers will not accept a modeling tool that phones out.
5. **Multi-tenant server or CLI-only.** CLI-only through M5. A server exists to serve the canvas, not to own state. State stays in git.

---

## 14. Anti-goals

- No proprietary binary model file, ever.
- No orchestration. dbt runs the models.
- No warehouse-side execution beyond optional profiling for inference.
- No LLM in the critical path. AI assists naming, term matching and description drafting; it never decides a relationship or writes to a catalog without a recorded human verdict.
- No canvas before M5.
