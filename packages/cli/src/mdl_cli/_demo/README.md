# Modelith demo — a 7-entity investment book-of-record (IBoR)

This folder was scaffolded by `mdl init --demo`. It has two halves:

- **`model/`** — the Modelith model: 7 business entities (price, instrument, position,
  counterparty, transaction, portfolio, benchmark) as conceptual + logical YAML.
- **`transform/warehouse/`** — a dbt (DuckDB) project. It ships the raw seeds and the
  **staging** models (`stg_*`, thin `select *` wrappers over the seeds). The governed
  **mart** models — one per entity — are written by `mdl generate`.

## Run it — in this order

```bash
# 1. Validate the model (offline, no config)
mdl validate -m model

# 2. See the ER canvas
mdl serve -m model            # then open the printed URL

# 3. Generate the dbt mart models from the model  ← DO THIS BEFORE DRIFT
mdl generate -m model -o transform/warehouse

# 4. Build the warehouse
cd transform/warehouse && dbt build && dbt docs generate && cd -

# 5. Check drift between the model and the built warehouse
mdl drift --manifest transform/warehouse/target/manifest.json -m model
```

## Why `mdl generate` before drift

A Modelith **entity maps to a dbt mart model** — dbt's "entity layer", the governed
model that carries the entity's columns, contract, and relationship tests. In this demo
those marts (`benchmark`, `price`, …) don't exist until you run **`mdl generate`**; only
the raw `stg_*` staging wrappers ship pre-built.

So if you run **Check Drift before generating**, drift correctly finds that the mart
models aren't there yet and reports them as *removed* — which looks alarming but just
means "not generated yet". Run `mdl generate` first and drift is clean: each entity
matches its mart, and the `stg_*` models are recognised as upstream staging and ignored.

If your **own** warehouse names its models differently (e.g. `dim_price`, or a `stg_`/
medallion convention that IS the governed layer), teach Modelith the mapping in
`model/mdl-project.yaml` under a `reverse:` block — or right-click a *model removed* item
in the VS Code **Drift** panel and choose **Map to dbt model…**.
