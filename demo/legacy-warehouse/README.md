# Demo: reverse-engineer a legacy warehouse

This demo starts where most teams actually are: an existing dbt project on top of a
warehouse, with no data model. It shows `mdl reverse` lifting that project into a clean
logical model you can then own, validate, and regenerate from.

## What this shows

- A pre-built dbt project over a bundled DuckDB warehouse (`legacy.duckdb`).
- `mdl reverse` reading the dbt manifest and catalog to produce a logical model:
  entities and attributes lifted, relationships inferred from foreign-key signals and
  tests, SCD2 and surrogate-key patterns recognized, every inference recorded in a
  decision ledger you can correct.
- The result checked in under `reversed-model/` so you can see the output without
  running anything.

## Layout

- `models/`, `seeds/`, `macros/`, `dbt_project.yml` are the legacy dbt project.
- `legacy.duckdb` is the bundled warehouse, so this runs on a laptop with no external
  database.
- `reversed-model/` is the logical model Modelith lifted from the project (conceptual +
  logical YAML). This is the artifact `mdl reverse` produces.

## Run it

```bash
cd demo/legacy-warehouse

# build the legacy project so a manifest and catalog exist
dbt build
dbt docs generate           # produces target/catalog.json

# reverse the project into a fresh logical model
mdl reverse \
  --manifest target/manifest.json \
  --catalog target/catalog.json \
  --out reversed-model

# validate what was lifted
mdl validate -m reversed-model

# review the inferences Modelith made
mdl decisions -m reversed-model

# see it as an ER canvas
mdl serve -m reversed-model
```

The checked-in `reversed-model/` lets you inspect the expected output directly if you do
not want to run dbt.
