# Demo: retail data warehouse

A retail warehouse with both a logical model (`ldm/`) and a generated dbt project over a
bundled DuckDB (`retail.duckdb`). Use it to explore a second, non-finance domain and to
try the round-trip on a project that is already built.

## What this shows

- A logical model for a retail domain (`ldm/`), separate from the finance-flavored IBoR
  demo, so you can see Modelith on a different shape of data.
- A dbt project that builds against the bundled `retail.duckdb`, no external warehouse.
- The same generate, build, drift loop as the IBoR demo, on a fresh domain.

## Layout

- `ldm/` is the Modelith logical model (conceptual + logical YAML, plus
  `mdl-project.yaml`). This is the source of truth.
- `models/`, `seeds/`, `macros/`, `dbt_project.yml` are the dbt project.
- `retail.duckdb` is the bundled warehouse.

## Run it

```bash
cd demo/retail-dwh

# validate the logical model
mdl validate -m ldm

# see it as an ER canvas
mdl serve -m ldm

# build the dbt project against the bundled DuckDB
dbt build           # expect PASS, zero errors

# check the built warehouse against the model
dbt parse
mdl drift --check -m ldm --manifest target/manifest.json
```

For the fully narrated end-to-end walkthrough, see the IBoR demo
([demo/ibor](../ibor/README.md)) and [docs/dogfooding-ibor.md](../../docs/dogfooding-ibor.md).
