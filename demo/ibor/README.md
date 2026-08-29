# Demo: pension-fund IBoR

An Investment Book of Record modeled in Modelith and generated to real dbt. This is the
flagship demo: it shows the full loop, design the model, generate contract-enforced dbt,
build it against DuckDB, and catch drift, with no cloud warehouse.

## What this shows

- A seven-entity logical model (`portfolio`, `instrument` as SCD2, `position`,
  `transaction`, `price`, `benchmark`, `counterparty`) with relationships, named keys,
  and enumerated domains.
- Ontology-anchored modelling against a *remote* resolver: the model declares a
  `type: ols` ontology source, and `mdl serve` auto-starts a bundled mock OLS4 server
  (a small FIBO subset) so you can browse and align to industry terms live, offline.
- `mdl generate` emitting a dbt project with contracts and relationship tests.
- `dbt build` running green against the bundled `transform/warehouse/ibor.duckdb`.
- `mdl drift --check` classifying a hand-made schema change as breaking, additive, or
  cosmetic.

## Layout

- `model/` is the Modelith model (conceptual + logical YAML). This is the source of truth.
- `transform/warehouse/` is the generated dbt project, including a bundled DuckDB file so
  it builds on a laptop with no external database.
- `ols/` holds the bundled mock OLS4 server + a hand-curated FIBO subset
  (`terms.json`). `mdl serve` spawns it automatically (see `demo_ols:` in
  `model/mdl-project.yaml`); it is a demo convenience, not part of the model.
- `pension_ibor.code-workspace` opens `model/` and `transform/` side by side in VS Code.

## Run it

```bash
cd demo/ibor

# see the model as an ER canvas + browse/align to the (mock) FIBO ontology.
# mdl serve auto-starts the bundled mock OLS4 on :4901 — no extra command, offline.
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

## Browse and align to the ontology

With `mdl serve` running, open the ontology browser (the ⬡ toolbar button):

1. The source picker shows **FIBO (demo subset)** — the bundled mock OLS4 resolver.
   Pick it to scope search to that vocabulary, or search across all sources.
2. Search a term (e.g. "instrument", "party", "position"). Hits come back live from the
   resolver, tagged with their source, with definitions and a class hierarchy.
3. Select an entity, open its inspector, and use **Align…** to bind it to an industry
   term. The chosen URI is recorded in the entity YAML under `ontology_refs` with
   `resolved_via` provenance, and cached locally (gitignored) so it validates offline.

To point the demo at the **real public OLS4** instead of the bundled mock: in
`model/mdl-project.yaml`, delete the `demo_ols:` block and change the `fibo-ols` source
`url` to `https://www.ebi.ac.uk/ols4/api` (requires internet; returns real OBO/FIBO
terms).

## See drift get caught

Edit a generated column in `transform/warehouse/models/` to break a contract (for
example narrow a numeric type or drop a column the model declares), re-run
`dbt parse`, then `mdl drift --check`. Modelith reports it as breaking and exits non-zero,
the signal you would gate a pull request on.

See [docs/dogfooding-ibor.md](../../docs/dogfooding-ibor.md) for the full end-to-end run
this demo is built from.
