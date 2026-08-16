# Demo: pension-fund IBoR

An Investment Book of Record modeled in Modelith and generated to real dbt. This is the
flagship demo: it shows the full loop, design the model, generate contract-enforced dbt,
build it against DuckDB, and catch drift, with no cloud warehouse.

## What this shows

- A seven-entity logical model (`portfolio`, `instrument` as SCD2, `position`,
  `transaction`, `price`, `benchmark`, `counterparty`) with relationships, named keys,
  enumerated domains, and FIBO ontology alignment.
- `mdl generate` emitting a dbt project with contracts and relationship tests.
- `dbt build` running green against the bundled `transform/warehouse/ibor.duckdb`.
- `mdl drift --check` classifying a hand-made schema change as breaking, additive, or
  cosmetic.

## Layout

- `model/` is the Modelith model (conceptual + logical YAML). This is the source of truth.
- `transform/warehouse/` is the generated dbt project, including a bundled DuckDB file so
  it builds on a laptop with no external database.
- `ontologies/` holds the vendored FIBO subset the model aligns to.
- `pension_ibor.code-workspace` opens `model/` and `transform/` side by side in VS Code.

## Run it

```bash
cd demo/ibor

# see the model as an ER canvas in the browser
mdl serve -m model

# validate the model (schema, refs, naming, ontology)
mdl validate -m model

# build the generated dbt project against the bundled DuckDB
cd transform/warehouse
dbt build            # expect PASS, zero errors

# from the model dir, check the built warehouse against the model
cd ../..
dbt parse --project-dir transform/warehouse
mdl drift --check -m model --manifest transform/warehouse/target/manifest.json
```

## See drift get caught

Edit a generated column in `transform/warehouse/models/` to break a contract (for
example narrow a numeric type or drop a column the model declares), re-run
`dbt parse`, then `mdl drift --check`. Modelith reports it as breaking and exits non-zero,
the signal you would gate a pull request on.

See [docs/dogfooding-ibor.md](../../docs/dogfooding-ibor.md) for the full end-to-end run
this demo is built from.
