# Modelith — World-class reverse engineering

## Goal

Make Modelith's reverse-engineering capability world-class: match what erwin / ER/Studio do
(reverse from a **live database** and from **raw DDL**, with inference and review), while
keeping Modelith's unique edges — ULID identity that survives round-trips, a git-native
decision ledger, and dbt-native fidelity. Crucially, **serve non-dbt teams**: a team with only
a warehouse (no dbt) should be able to reverse it by installing dbt purely as a *connection
layer* (`profiles.yml`), nothing else.

---

## Part 1 — Survey: what exists today

### The two disconnected paths

Reverse-engineering in Modelith is split across two code paths that don't share machinery:

| Input | Command | Reader | Goes through the reverse engine? |
|---|---|---|---|
| `manifest.json` (+ `catalog.json`) | `mdl reverse` | `read_manifest` (`packages/reverse/.../manifest.py:71`) | **Yes** — classification, confidence, ledger |
| emitted `schema.yml` | `mdl reverse` | `read_schema_yml` (`schema_reader.py:23`) | Yes |
| erwin XML | `mdl import erwin` | `import_erwin` (`erwin.py:101`) | No — direct Model build |
| **SQL DDL text** | `mdl import sql` | `parse_sql_ddl` (`emit-erd/.../imports/parse_ddl.py:65`, sqlglot) | **No** — mutation commands only |
| Mermaid / JSON-Schema / OSI | `mdl import …` | `mdl_emit_erd.imports.*` | No |

**The crux:** there is **no live-database path anywhere** (dependency audit + full grep: no
`sqlalchemy`/`psycopg`/`snowflake-connector`/`create_engine`/`.connect()`). `mdl reverse`
consumes **dbt artifacts only**. Raw DDL *can* be consumed — but through `mdl import sql`, a
separate command in a separate package that bypasses the reverse engine's classification,
confidence bands, and decision ledger entirely.

### The reverse engine (dbt path) — genuinely strong

`reverse()` (`packages/reverse/.../reverse.py:125`) is side-effect-free, returns a real
`Model` + proposals + exclusions, and does sophisticated classification (`lifting.py`):
- **Surrogate-key stripping** (`is_surrogate_key`): `_sk`/`_hashkey`/`_hk`/`_pk`, hash columns,
  with `_key` disambiguated by stem/type.
- **Staging exclusion** (`is_staging`): `stg_`/`int_`/`base_` prefixes, tags, paths.
- **Reporting-rollup detection** (`is_reporting_rollup`): `mart_`/`rpt_`/`agg_` + no business key → kept for lineage, marked `unmanaged`.
- **SCD2 and Data Vault detection** (`detect_scd2`, `detect_data_vault`) → sets `pattern`.
- **Relationship inference**: dbt `relationships` tests → **high** confidence, auto-accepted;
  name+type FK heuristic (`*_id`) → **medium**, proposed-only.

**Confidence + review** (`ledger.py`): every low-confidence decision is a `Decision` with a
`signal_key` (sha256 of kind+signal+evidence), persisted to `.mdl/decisions.yaml` (git). A
rejected proposal is never re-asked; a *changed* signal is re-proposed. Review happens three
ways: `--interactive` (y/n per proposal), `--review` (a grouped classification summary so
misfires are visible at reverse time), and out-of-band `mdl decisions list|accept|reject`.

**Naming config** (`ReverseNaming.merged`): a user teaches reverse their conventions
(medallion `gold_`, `f_`/`d_`, non-English prefixes) via a `reverse:` block in
`mdl-project.yaml` or `--naming <file>`; overrides are additive over the defaults.

**Catalog fidelity** (`manifest.py:87`): `mdl reverse` merges `catalog.json` (from
`dbt docs generate`) over the manifest — the catalog is authoritative for the real column set
and types, so undocumented physical columns (surrogate keys, SCD2 tracking) become visible.
**This is the closest thing to live-DB fidelity today, and it is entirely mediated by dbt's
own artifact — Modelith never connects to the warehouse.**

**Round-trip** (`test_reverse_roundtrip.py`): generate → reverse → generate is semantically
empty except a documented lossy set; entity AND attribute ULIDs survive. This is a real edge
erwin doesn't have (erwin re-keys by name).

### The gaps (what stops it being world-class)

1. **No live-database reverse.** Every serious tool (erwin, ER/Studio, Redgate, MySQL
   Workbench) reverses from a live connection. Modelith cannot.
2. **No raw-DDL reverse.** DDL only enters via `mdl import sql`, which skips classification,
   confidence, and the ledger — so a `.sql` dump gets none of the surrogate-stripping / SCD2 /
   review intelligence that a dbt manifest gets.
3. **Not surfaced in VS Code or chat.** `mdl reverse` — the flagship capability — has **no VS
   Code command, no view, no chat surface.** The extension only exposes `liftModel` (single-SQL
   lift via LSP) and the canvas `import` wizard. An engineer cannot reverse a warehouse from the
   editor.
4. **Nullability not inferred** (`reverse.py:258` hardcodes `nullable=True`) — even when the
   source (catalog, DDL, information_schema) knows it.
5. **erwin/DDL paths don't share the engine** — no classification, no ULID identity, no review.

---

## Part 2 — How the field does it (and the non-dbt insight)

erwin / ER/Studio / Redgate / MySQL Workbench all offer **two input sources**: a **live
database connection** (query the system catalog) and a **DDL script**. Consistent features
worth matching:

- **Object types**: tables, views, PKs, indexes, FKs (Modelith already does entities/attrs/keys/rels).
- **Filtering**: reverse only selected tables / by schema / by owner. Essential at warehouse scale.
- **Inference when not declared**: infer PKs from unique indexes; infer relationships from
  matching PK column names between tables. Modelith already does the relationship heuristic;
  the PK-from-index and FK-from-naming inference generalizes it.
- **Case conversion**: physical `UPPER_SNAKE` → logical names, with a naming-standard glossary.
  Modelith has `ReverseNaming` + the naming lint — the pieces exist.

Lightweight web tools (dbdiagram, drawSQL) take pasted DDL only, no live inspection — that's the
floor, and Modelith's DDL path already meets it via sqlglot.

**The non-dbt-team insight (the user's, and it's the right architecture):** Modelith should
**not write database drivers.** dbt already has a mature, warehouse-covering adapter ecosystem,
and **`dbt-codegen`'s `generate_source` macro introspects a live database** (via the adapter +
`profiles.yml`) and scaffolds real tables/columns. So the live-DB path is:

> install dbt + the relevant adapter, write a `profiles.yml` (a *structured connection config,
> nothing else*), and let dbt's adapter do the introspection. Modelith consumes the result.

This means a non-dbt team gets live-DB reverse **without Modelith taking on driver
maintenance, credential handling, or dialect-specific catalog SQL** — dbt owns all of that.
And `sqlglot>=25` is *already* a top-level dependency, so the raw-DDL path is nearly free.

---

## Part 3 — The design: one reverse engine, three front doors

Unify everything behind the existing `reverse()` engine (classification + confidence + ledger
+ ULID identity), and give it three input adapters that all normalize to the engine's
`ManifestProjection`. The engine, review flow, and writer stay unchanged — only new readers and
surfaces are added.

```
                    ┌─────────────────────────────────────────┐
  dbt artifacts ───▶│                                         │
  raw SQL DDL ─────▶│  normalize → ManifestProjection         │──▶ reverse() ──▶ Model
  live database ───▶│  (surrogate/SCD2/rollup classification, │    (unchanged)   + ledger
                    │   confidence, ULID identity, ledger)    │
                    └─────────────────────────────────────────┘
```

### Front door A — Raw SQL DDL through the reverse engine (S–M)

Route `.sql` DDL into `reverse()` instead of the classification-free `mdl import sql`. The
sqlglot parser (`parse_sql_ddl`) already yields tables/columns/PKs/FKs; add a thin adapter
`ddl_projection(ddl, dialect) -> ManifestProjection` so DDL flows through the SAME engine as a
manifest — gaining surrogate stripping, SCD2 detection, staging exclusion, confidence bands,
and the review ledger. Nullability is available in DDL (`NOT NULL`), so infer it here (fixing
gap #4 for this path). New: `mdl reverse --ddl schema.sql [--dialect …]`.

### Front door B — Live database via dbt as a connection layer (M–L, the flagship)

**Modelith writes no drivers.** dbt already has a mature, warehouse-covering adapter ecosystem;
dbt-codegen's `generate_source` macro *introspects a live database* and emits a `sources.yml`
with real tables, columns, and (with `include_data_types: true`) types. Modelith consumes that.
A new `mdl reverse --connect` flow:

1. **Detect or scaffold `profiles.yml`.** If dbt is already configured, use it; else
   `mdl reverse --init-profile <adapter>` writes a minimal `profiles.yml` template (a structured
   connection config — account/database/schema/warehouse/role, credentials via `env_var(...)`)
   and prints the adapter install (`pip install dbt-snowflake`, etc.). Modelith never stores or
   reads credentials — env vars resolve inside dbt.
2. **Discover the schema (the introspection step).** Shell out:
   `dbt --quiet run-operation generate_source --args '{schema_name, database_name, table_names?,
   generate_columns: true, include_data_types: true}'` → capture stdout, which is a `sources.yml`
   (`version: 2`, `sources: [{name, tables: [{name, columns: [{name, data_type}]}]}]`). This is
   the live column set + types, discovered without any pre-existing dbt models. Requires the
   dbt-codegen package (Modelith writes a tiny throwaway `packages.yml` + `dbt deps` if absent, or
   documents adding it).
3. **Read `sources.yml` → `ManifestProjection`.** A small `read_sources_yml` (the existing
   `read_schema_dict` in `schema_reader.py:32` only walks a `models:` block; the `sources:` shape
   is different — one table per `sources[].tables[]`, columns carry `data_type`). This is the one
   genuinely new reader.
4. **Filter** by `--schema` / `--select <pattern>` before lifting — essential at warehouse scale
   (matches erwin's owner/schema filtering; maps to `generate_source`'s `table_pattern`/`exclude`).
5. Feed into `reverse()`. Engine, classification, ledger unchanged; the warehouse's real column
   set + types feed inference — the same fidelity as the `catalog.json` merge, now sourced live.

**Design caveat that shapes expectations:** `generate_source` gives tables/columns/types but
**not PKs/FKs** — most warehouses don't enforce them and `information_schema` FK metadata is
inconsistent across Snowflake/BigQuery/Redshift. So the live-DB path leans harder on Modelith's
*inference* (name-based FK heuristic → medium-confidence proposals, business-key candidates) than
the DDL path, where keys are explicit. This is exactly what the confidence ledger + review tree
(R1) are for: the live reverse produces more medium-confidence proposals a human triages. Where a
warehouse *does* expose constraints (Postgres, some Snowflake setups), a later enhancement can
pull them via an extra `run-operation`; v1 relies on inference + review.

This gives a non-dbt team a live-DB reverse with **only a `profiles.yml`**, and a dbt team gets
it for free from their existing profile. dbt owns credentials, drivers, and dialects.

*(Alternative/fallback for teams that refuse dbt entirely: a direct `information_schema` reader
behind an optional `modelith[connect]` extra. Not planned — the dbt-adapter path covers the long
tail without Modelith owning driver code, which is the whole point.)*

### Front door C — Unify erwin/DDL identity & review

Give the erwin path (and DDL import) the option to run through the confidence/ledger machinery
too, so any structural import can be reviewed and keeps ULID identity on re-import. Lower
priority than A/B but closes gap #5.

### Surfacing in VS Code and chat (gap #3 — the biggest UX miss)

Reverse is the flagship capability with **zero editor presence.** Give it the full treatment,
reusing the drift-surfacing patterns just shipped:

- **`Modelith: Reverse a Warehouse`** command — a QuickPick wizard: pick a source (dbt project /
  DDL file / live connection), then options (target, schema filter, naming profile). Runs
  `mdl reverse`, opens the result.
- **A Reverse Review view** (TreeView, like the Drift view) over `.mdl/decisions.yaml`'s pending
  proposals, grouped by kind (relationships / surrogate strips / SCD2 / rollups) with confidence
  badges. Inline **Accept / Reject** actions write verdicts via `mdl decisions accept|reject` —
  turning the CLI's `--interactive` prompt into a proper review surface. This is the
  "explain + recommend a verdict over a ledger Decision" component the AI-features roadmap
  already anticipated.
- **`@modelith /reverse`** (chat) — explains the classification summary and, per low-confidence
  proposal, why it was classified that way and a recommended verdict (grounded on the ledger
  evidence; the engine decides, the LLM explains). Fails closed to the deterministic
  `classification_summary`.
- **MCP `review_classifications()`** tool — the same for an agent.

### Fidelity fixes (small, high-value)

- **Infer nullability** from the source everywhere it's known (DDL `NOT NULL`, catalog/
  information_schema), instead of hardcoding `nullable=True` (`reverse.py:258`).
- **Infer PKs from unique indexes / constraints** (DDL + information_schema), matching erwin.
- **Case conversion**: apply the project's `naming.physical_case`→logical mapping during
  lift, so `CUSTOMER_ID` becomes `customer_id` per the project's standard.

---

## Build order

| Phase | Contents | Effort |
|---|---|---|
| **R0** | `ddl_projection()` adapter → `mdl reverse --ddl` (front door A); infer nullability + PK-from-DDL. Reuses sqlglot + the whole engine. | S–M |
| **R1** | VS Code **Reverse a Warehouse** command + **Reverse Review** tree (accept/reject over the ledger). Surfaces the existing engine — highest UX leverage. | M |
| **R2** | Live-DB via dbt: `--init-profile`, `--connect` (shell to dbt-codegen/`dbt docs generate`), `--schema`/`--select` filtering. The flagship. | M–L |
| **R3** | `@modelith /reverse` + MCP `review_classifications()`; unify erwin/DDL through the ledger (front door C). | M |

Each phase leaves the suite green and is independently useful: R0 makes DDL first-class; R1
puts the flagship in the editor; R2 opens the live-DB front door; R3 adds the AI review layer.

## Critical files

- `packages/reverse/src/mdl_reverse/reverse.py` — the engine (unchanged); fix `nullable` inference (`:258`).
- `packages/reverse/src/mdl_reverse/manifest.py` — `ManifestProjection` + `catalog.json` merge (`:87`); the normalize target for all front doors.
- `packages/emit-erd/src/mdl_emit_erd/imports/parse_ddl.py` — sqlglot DDL parser to reuse for front door A.
- `packages/reverse/src/mdl_reverse/ddl_projection.py` (NEW) — DDL → `ManifestProjection`.
- `packages/reverse/src/mdl_reverse/connect.py` (NEW) — dbt-profile scaffold + introspection shell-out (front door B).
- `packages/cli/src/mdl_cli/main.py` — `reverse` command (`~:289`): add `--ddl`, `--connect`, `--init-profile`, `--schema`, `--select`.
- `packages/reverse/src/mdl_reverse/ledger.py` — the review seam the VS Code tree + chat read.
- `vscode/src/reverseView.ts` (NEW) — Reverse Review tree (mirror `driftView.ts`); `vscode/src/extension.ts` — the command + wizard.
- `vscode/src/chatParticipant.ts` — `/reverse`; `packages/mcp/src/mdl_mcp/server.py` — `review_classifications()`.

## Verification

- **Python (`uv run pytest && uv run ruff check packages/`):**
  - R0: a `CREATE TABLE` DDL with a `*_sk` column, a `NOT NULL` column, and an FK →
    `mdl reverse --ddl` strips the surrogate, infers nullability, and records the FK as a
    relationship proposal (reusing the existing classification tests as the contract).
  - R2: with a fixture `catalog.json`/generated source, the live path produces the same
    `ManifestProjection` the manifest path does (assert front-door parity, no warehouse needed
    in CI — mock the dbt shell-out).
  - Nullability + PK-from-DDL inference are covered by new lifting tests.
- **Extension (`cd vscode && npx tsc --noEmit && node esbuild.mjs`, Node 20):** the Reverse
  command + review tree register; accept/reject write ledger verdicts.
- **Manual:** reverse the IBoR demo's dbt project from VS Code; review a medium-confidence
  relationship in the tree and accept it; `@modelith /reverse` explains the classification.
  Then reverse a raw `.sql` DDL dump and confirm it gets the same surrogate-stripping + review.
