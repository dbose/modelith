# Modelith

Ontology-anchored, git-native data modeling for dbt-core teams. CLI: `mdl`.

See [`modelith-spec.md`](modelith-spec.md) for the full build specification.

## Status

Built to the spec's milestone gates (§12). **M0 and M1 are complete and tested** —
the load-bearing foundation the spec requires before anything else proceeds.

| Milestone | Scope | State |
|---|---|---|
| **M0** | IR + ULID identity + comment-preserving YAML round-trip + validator + `mdl init/validate/lint` | ✅ done, tested |
| **M1** | Protected regions + generation state + three-way merge + dbt/duckdb emitter + property tests 1,3,4 | ✅ done, tested |
| **M2** | `mdl drift --check/--reconcile` + severity classification + PR-comment/Mermaid render + CI templates | ✅ done, tested |
| **M3** | Reverse engineering + decision ledger + interactive lifting + Snowflake/Redshift/Iceberg/Trino adapters + property 2 | ✅ done, tested |
| **M4** | Generic ontology registry (FIBO = one example) + four-layer validation + RDF/SHACL + OSI 0.1.1 emit/import + MetricFlow + erwin XML import | ✅ done, tested |
| M5 | GovernanceGraph + mapping DSL + adapter SPI + Collibra + OpenLineage | ⬜ |
| M6 | Web canvas (read-only first) — **not before M5 per spec §14** | ⬜ |
| +   | VS Code extension (standalone + devcontainer) — see `docs/vscode-plan.md` | ⬜ planned |

## Layout

```
packages/
  core/           # IR, ULID, YAML round-trip, validator, round-trip/merge engine (no in-repo deps)
  emit-dbt/       # dbt-core emitter, platform adapters (duckdb/snowflake/redshift/iceberg/trino), SCD2 macros
  reverse/        # manifest reader, drift + reconcile, reverse engineering, decision ledger, erwin import
  ontology/       # generic vocabulary registry, four-layer validation, RDF/OWL + SHACL export, lock
  emit-semantic/  # MetricFlow + OSI (version-isolated v0_1_1), OSI import, joinability/fan-out validation
  cli/            # `mdl`
profiles/
  ci/             # shippable CI workflow templates (mdl-validate, mdl-drift)
```

The layering rule (spec §1.3) is honored: `core` depends on nothing else in-repo;
`emit-dbt` depends only on `core`.

## Quickstart

```bash
uv sync
uv run mdl init my-model --name my_model
uv run mdl validate -m my-model
uv run mdl generate -m my-model -o my-model/target/dbt
```

Run the suite (the M1 property-test gate, spec §5.5):

```bash
uv run pytest
uv run ruff check packages/
```

## What the M1 gate proves

- **Idempotence** (property 1): `generate` twice on an unchanged model is byte-identical.
- **User-region survival** (property 3): arbitrary edits inside `-- mdl:user-*`
  blocks survive N regenerations (property-based via `hypothesis`).
- **ULID-rename propagation** (property 4): renaming by ULID leaves zero orphans.
- **Content-addressed regeneration**: every generated block carries a fingerprint;
  hand-edits to generated code raise `MDL-E201`; a model+hand change raises a
  conflict (`MDL-C301`) with git-style markers — never silent data loss (spec §5).
- **Reverse round-trip** (property 2, M3): `reverse(generate(M))` is semantically
  equal to `M` modulo an explicitly-asserted lossy set (ontology IRIs → M4,
  stewardship → M5, nullability → interactive), and `generate(reverse(generate(M)))`
  is a semantically-empty diff. ULID identity survives via `meta.mdl_ulid`.

Exit codes follow spec §10: `0` ok, `1` validation error, `2` breaking drift,
`3` merge conflict.

## Drift detection (M2)

```bash
# compile your dbt project (no warehouse needed), then:
mdl drift --manifest target/manifest.json -m model --check            # CI gate, exit 2 on breaking
mdl drift --manifest target/manifest.json -m model --format mermaid   # PR comment w/ subgraph diff
mdl drift --manifest target/manifest.json -m model --reconcile        # fold additive/cosmetic into model
```

Differences are classified `breaking` / `additive` / `cosmetic` / `unmanaged`
(spec §5.4). The 400-model acceptance test (§12) — inject a column drop, classify
breaking, fail in <30s — runs in ~2s. CI templates are in `profiles/ci/`.

## Reverse engineering (M3)

```bash
# from a compiled manifest, or (warehouse-free) an emitted schema.yml:
mdl reverse --project target/manifest.json --out model --interactive
```

Lifts a dbt project into the Modelith model (spec §6): excludes staging/intermediate,
strips surrogate keys, collapses SCD2 column triples into `pattern: scd2`, detects
Data Vault hub/link/sat, and infers relationships by ranked signal
(dbt `relationships` tests → high, accepted by default; `*_id` name/type heuristic →
medium, proposed only). Every inference is recorded in `.mdl/decisions.yaml`; a
rejected proposal is never re-asked unless its signal changes (§6.2).

Platform adapters (spec §7.2) ship for `duckdb`, `snowflake` (clustering/transient),
`redshift` (dist/sort), `iceberg` (partition/sort/format), `trino`.

## Ontology + semantic layer (M4)

```bash
mdl ontology search "counterparty"           # rank loaded vocabulary classes (§3.2)
mdl ontology check -m model                   # four-layer rules + CDO coverage report (§3.1)
mdl emit semantic --format metricflow -m model
mdl emit semantic --format osi -m model       # OSI 0.1.1, multi-dialect, ai_context (§4)
mdl export rdf --layer all --format turtle    # RDF/OWL with SKOS alignments (§3.3)
mdl export shacl                              # SHACL shapes from the logical model (§3.3)
mdl import osi model.osi.yaml --out model     # lift an existing OSI semantic layer (§4.3)
mdl import erwin model.xml --out model        # migrate from erwin (§6.4)
```

**Ontology-agnostic by design.** FIBO is only one example of an `industry`-layer
vocabulary — ACORD, FHIR, ISO 20022, GS1, or a customer's own RDF/OWL/Turtle
vocabulary plug in by declaration in `mdl-project.yaml` (`ontology_stack`), no code
changes. IRIs are resolved against the loaded graph; unresolvable IRIs are errors.

**OSI is version-isolated.** All OSI knowledge lives behind
`emit-semantic/.../osi/v0_1_1/`, pinned to the tagged `osi-0.1.1-rc1` release
(the repo's `main` is `0.2.0.dev0` DRAFT — intentionally not targeted, per §4.1).
`.mdl/lock.yaml` pins the OSI tag, dbt version, and each vocabulary version.
Joinability + fan-out risk (many-to-many, unaggregated measures across one-to-many)
is validated before any semantic emission (§8).
