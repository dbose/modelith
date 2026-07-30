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
| M3 | Reverse engineering + decision ledger + Snowflake/Redshift/Iceberg/Trino + property 2 | ⬜ |
| M4 | MetricFlow/OSI emit + OSI import + FIBO loader + RDF/SHACL + erwin XML | ⬜ |
| M5 | GovernanceGraph + mapping DSL + adapter SPI + Collibra + OpenLineage | ⬜ |
| M6 | Web canvas (read-only first) — **not before M5 per spec §14** | ⬜ |
| +   | VS Code extension (standalone + devcontainer) — see `docs/vscode-plan.md` | ⬜ planned |

## Layout

```
packages/
  core/       # IR, ULID, YAML round-trip, validator, round-trip/merge engine (no in-repo deps)
  emit-dbt/   # dbt-core emitter, duckdb platform adapter, SCD2 macro package
  reverse/    # version-tolerant manifest reader + drift classification + reconcile
  cli/        # `mdl`
profiles/
  ci/         # shippable CI workflow templates (mdl-validate, mdl-drift)
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
- Property 2 (`reverse(generate(M)) ~= M`) is marked `xfail` pending the reverse
  package in M3, per the milestone plan.

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
