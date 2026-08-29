# Modelith — Ontology / Knowledge Graph Integration Spec

Build spec for the ontology and R2RML/VKG layer described below. Assumes the
existing Modelith repo topology (`model/` + `transform/` sibling folders,
ULID-stable YAML per entity, VS Code LSP as primary interface, SME web app
writing back only via PRs).

---

## 1. Ontology reference binding (authoring time)

**Feature:** `ontology_refs` field on every entity and attribute in the LDM YAML.

```yaml
ontology_refs:
  - predicate: skos:exactMatch   # or closeMatch / broadMatch
    uri: fibo:MonetaryAmount
    resolved_via: ols4            # which resolver found it — see §4
    resolved_at: 2026-08-29
```

- Applies at entity level (class match) and attribute level (property match).
- LSP autocompletes against the currently loaded ontology layer(s) while a
  modeller is naming an entity/attribute — needs a local queryable index
  (rdflib or oxigraph), not a live SPARQL round-trip per keystroke.
- Four-layer stack (industry → enterprise → domain → specialised) is
  configured per project; each layer can resolve independently (see §4).

## 2. Reverse engineering + enterprise-ontology alignment

**Feature:** two-pass workflow for existing dbt projects.

- **Pass 1 (existing scope):** reverse-engineer LDM from dbt models — no
  change from current spec.
- **Pass 2 (new):** alignment pass, run separately, non-blocking.
  - Candidate matching: string + embedding similarity between
    column/model names and ontology `rdfs:label` / `skos:altLabel`.
  - Output: ranked candidate `ontology_refs` per entity/attribute with a
    confidence score, **not** auto-committed.
  - Review surfaces through the existing SME-web-app → PR flow. Each
    accepted match writes `resolved_via`, `resolved_by`, `confidence`,
    `approved_at` into the entity YAML — this is the audit trail Collibra
    governance already needs, so no separate audit mechanism required.
  - **Critical:** match against the *merged closure* (public ontology +
    enterprise `owl:imports`/subclass/restriction extension), not the raw
    public ontology alone — enterprise renames/restrictions otherwise
    produce false negatives.

## 3. Ontology pinning (`ontology.lock`)

**Feature:** lockfile, not vendored files. Same principle as not committing
Docker images or `node_modules`.

```yaml
# ontology.lock
layers:
  industry:
    mode: artifact                 # pinned file/URL, immutable
    source: https://spec.edmcouncil.org/fibo/.../fibo.owl
    version: 2026Q2
    sha256: <hash>
  enterprise:
    mode: endpoint_snapshot        # live store, point-in-time export
    source: https://ontology.internal/sparql
    snapshot_tag: 2026-08-15T00:00Z
    sha256: <hash of exported snapshot>
```

- Two resolution modes are required, not one:
  - **`artifact`** — immutable file at a URL/Artifactory coordinate, hash
    covers the file directly (public ontologies like FIBO usually fit this).
  - **`endpoint_snapshot`** — enterprise ontology lives in a live triple
    store (GraphDB/Stardog/internal SPARQL) that's edited in place; hash
    covers a point-in-time export, not the endpoint itself. Needed so
    alignment-pass approvals (§2) reference a fixed ontology commit, not a
    moving target.

    Although it can be argued that live stores are needed for query execution. 
    You still need some KG index / metadata discovery layer. An ontology browser should be able to connect to a number of such services - OLS4 (public) or internal OLS compatible or Full Colibra-hosted “Ontology Domains” (this may vary across enterprise thus has to be configurable)
- Resolved copies land in a **gitignored** cache: `.modelith/ontology-cache/`.
- `modelith ontology fetch` — fetch + hash-verify all layers, fail closed on
  mismatch. Analogous to `dbt deps` / `npm ci`.
- No ontology files committed to the repo. `model/_ontology/` (proposed
  earlier) is dropped in favor of lockfile + cache.

## 4. OntologyResolver interface (browse/search, authoring UX only)

**Feature:** pluggable resolver interface, configurable per layer.

```ts
interface OntologyResolver {
  search(text: string): Candidate[]
  resolve(uri: string): { label, definition, parents, ontology_id }
  list(ontology_id: string): Term[]
}
```

Adapters:
- `OLS4Resolver` — public OLS4 instance.
- `OLSCompatibleResolver` — generic, for internal OLS-compatible deployments.
- `CollibraOntologyDomainsResolver` — reuses the existing Collibra
  connection already scoped for governance meta-model mapping, pointed at
  Ontology Domains instead.

- Each of the four ontology layers picks its own resolver in project config
  (industry → public OLS4, enterprise → Collibra, domain → internal OLS,
  etc. — mix and match).
- **Resolver is strictly a live-lookup/UX concern** (autocomplete,
  search-as-you-type, hover definitions in the LSP). It is never a build-time
  dependency. Whatever URI it surfaces gets written into `ontology_refs` and
  pinned via `ontology.lock` (§3) exactly the same way regardless of which
  resolver found it — a term found live through Collibra today is bound and
  pinned identically to one pulled from a vendored FIBO file.

## 5. R2RML / Virtual Knowledge Graph export

**Feature:** new PDM export target, sitting next to the existing dbt/SQL DDL
export — not new modelling machinery, a serialization of data already
captured in §1 and the existing LDM→PDM column binding.

- `rr:TriplesMap` per entity.
- `rr:subjectMap` from entity's primary `ontology_refs` class + PDM primary
  key column.
- `rr:predicateObjectMap` per attribute: predicate from `ontology_refs`,
  object from the bound PDM column.
- `rr:joinCondition` generated from existing FK relationships in the LDM for
  cross-entity joins.
- Output pairs with **Ontop** for query-time SPARQL federation over the RDB
  — no materialization. Modelith stays a design/mapping tool; it does not
  need to implement a SPARQL engine.
- Precondition: entity/attribute must carry a resolved `ontology_refs` (from
  either §1 authoring or §2 alignment) — R2RML generation should fail
  loudly (not silently skip) on unmapped entities, with a report of what's
  missing.

---

## Build order (suggested)

1. `ontology_refs` schema + YAML validation (§1)
2. `ontology.lock` format + `modelith ontology fetch` (§3) — needed before
   resolver/autocomplete has anything real to index against
3. `OntologyResolver` interface + OLS4 adapter (§4) — ship one adapter first,
   prove the interface, add OLS-compatible and Collibra adapters after
4. LSP autocomplete wired to resolver + lock cache (§1 UX)
5. Alignment pass tooling (§2) — candidate matching + PR-review surface
6. R2RML export target (§5) — depends on §1 data being populated by either
   §1 or §2
