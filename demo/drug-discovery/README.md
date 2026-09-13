# Demo: drug-discovery warehouse — reverse-engineer + ontology-align

A large, **real, runnable** dbt warehouse for a drug-discovery / translational-genomics
domain (genes, proteins, pathways, diseases, phenotypes, drugs, targets, variants, GWAS
associations, gene-regulatory edges, assays, trials). This demo shows Modelith:

1. **reverse-engineering** the whole warehouse into a governed logical model — with
   surrogate-key stripping, staging exclusion, SCD2 detection, and high-confidence
   relationship recovery, and
2. **aligning entities to biomedical ontologies discovered live** through the EBI
   Ontology Lookup Service (OLS) — MONDO, GO, HP, EFO — no API key, searchable from the
   canvas UI.

## What this shows

| | |
|---|---|
| **dbt warehouse** | 77 models over DuckDB, builds clean (`dbt build` → 145 nodes PASS): 20 dimensions (3 SCD2), 24 association facts forming a real biological graph, 8 rollup marts, 20 staging + 5 intermediate. Natural keys are real biomedical IDs (`ensembl_gene_id`, `uniprot_id`, `chembl_id`, `mondo_id`, `hp_id`, `reactome_id`). |
| **reversed model** | 52 logical entities, 46 relationships — validates clean. 25 staging/intermediate excluded; every `*_sk` surrogate stripped; `dim_drug`/`dim_target`/`dim_trial` detected as SCD2; `agg_*` marts marked unmanaged rollups; all 46 FKs recovered from dbt `relationships` tests at high confidence. |
| **ontology alignment** | 7 entities aligned to live-OLS terms: Disease→MONDO, Gene→SO, Pathway→GO, Phenotype→HP, Protein→PR, Drug→CHEBI, Variant→SO. |

## Layout

```
demo/drug-discovery/
  warehouse/          a real dbt-duckdb project (source only; target/ built on demand)
    dbt_project.yml
    profiles.yml      DuckDB, file-based, zero-setup
    seeds/            raw_*.csv  (synthetic biomedical rows)
    models/
      staging/        stg_*   (excluded by reverse)
      intermediate/   int_*   (excluded by reverse)
      core/           dim_* / fct_*   (lifted to entities)
      marts/          mart_* / rpt_* / agg_*   (rollups → unmanaged)
      schema.yml      contracts + relationships tests
  model/              the reversed + ontology-aligned Modelith model
  docs/               screenshots
```

## Run it end-to-end

All commands from the repo root. Uses the in-repo `mdl` (`.venv/bin/mdl` after `uv sync`,
or a global `mdl`).

### 1. Build the warehouse (real dbt over DuckDB)

```bash
cd demo/drug-discovery/warehouse
dbt build --profiles-dir .          # seeds + models + tests: 145 nodes PASS
dbt docs generate --profiles-dir .  # writes target/manifest.json + target/catalog.json
cd -
```

`dbt docs generate` is what produces `catalog.json` — the real warehouse column set and
types, which is what lets reverse see undocumented physical columns (the `*_sk`
surrogates, the SCD2 tracking columns) to strip/detect.

### 2. Reverse-engineer it

```bash
mdl reverse \
  --project demo/drug-discovery/warehouse/target/manifest.json \
  -o demo/drug-discovery/model --name drug_discovery --target duckdb_dev
```

Output (abridged):

```
reversed 52 entities (25 staging/intermediate excluded); 0 proposals pending review
  ✓ [medium-high] model 'dim_drug' looks like SCD2 (valid_from, valid_to, is_current)
  ✓ [medium-high] strip surrogate_key dim_gene.gene_sk from the logical view
  ✓ [medium-high] model 'agg_target_tractability' looks like a reporting rollup; kept unmanaged
  ... (every *_sk stripped, all 3 SCD2 dims detected, marts → unmanaged)
```

Everything high-confidence auto-accepts (the FK relationships come from dbt
`relationships` tests). Anything ambiguous would land in `.mdl/decisions.yaml` for review
(see **VS Code** below).

```bash
mdl validate -m demo/drug-discovery/model      # validation passed
```

### 3. Discover a biomedical ontology — live, no API key

Declare the EBI OLS source in the reversed model's `mdl-project.yaml`:

```yaml
ontology_stack:
  - type: ols
    name: ols
    layer: industry
    url: https://www.ebi.ac.uk/ols4/api
    ontologies: [mondo, efo, go, hp]
```

Then search — through the CLI or the canvas — and it hits OLS live:

```bash
mdl ontology search diabetes -m demo/drug-discovery/model --limit 5
#   MONDO:0005148  [ols]   A type of diabetes mellitus characterized by insulin resistance …
#   MONDO:0011668  [ols]   Monogenic diabetes caused by inactivating mutation(s) in NEUROD1 …
```

In the canvas (`mdl serve -m demo/drug-discovery/model`), the **Ontology browser** shows
source chips for each declared ontology — *all sources / Experimental Factor Ontology /
Gene Ontology / Human Phenotype Ontology / Mondo Disease Ontology* — and, searching
"diabetes", returns live results tagged `OLS`: **type 2 diabetes mellitus (MONDO:0005148)**
and **maturity-onset diabetes of the young type 6 (MONDO:0011668)**, each with its full
definition. (Screenshot: `docs/ontology-browser-ols.png` — capture the pane to include it.)

### 4. Align entities to the ontology

Align the `Disease` conceptual entity to MONDO's type-2-diabetes term (this is what the
canvas Align modal does):

```bash
mdl reverse ...   # (already done)
# via the API/command engine or the canvas Align modal:
#   set_alignment Disease -> MONDO:0005148  (skos:closeMatch, layer industry, via ols4)
```

The result on `model/conceptual/entities/disease.yaml`:

```yaml
name: Disease
ontology_layer: industry
ontology_refs:
  - uri: MONDO:0005148
    predicate: skos:closeMatch
    resolved_via: ols4
```

This demo aligns 7 entities: Disease→MONDO, Gene→`SO:0000704`, Pathway→`GO:0008150`,
Phenotype→`HP:0000118`, Protein→`PR:000000001`, Drug→`CHEBI:23888`, Variant→`SO:0001060`.

### 5. See it in the canvas

```bash
mdl serve -m demo/drug-discovery/model      # http://127.0.0.1:4800
```

The reversed 52-entity / 46-relationship graph loads and is marked **valid** in the top
bar — the biological graph of dimensions and association facts. (Screenshot:
`docs/canvas-reversed.png`.)

## Regenerate the warehouse

The warehouse is produced by a generator (kept in the scratchpad, not the repo). To
rebuild a fresh copy, re-run `dbt build` — the seeds and models are committed. The
generator emits referentially-valid facts (FK values selected from the dimensions they
reference) so every `relationships` test passes on a real build.

## Notes / honest findings

- **Bulk `mdl ontology align` against a *live remote* source is slow** — it queries OLS
  per entity/attribute, so on a 52-entity model it can exceed a couple of minutes.
  Targeted per-entity alignment (as in step 4) is instant. For bulk work, vendor a local
  ontology subset (`mdl ontology add <file>.ttl`) or expect a longer run.
- **Auto-layout is dense at this scale** — a 52-node / 46-edge graph doesn't spread as
  cleanly as a small model; use search + fit-view to navigate.
