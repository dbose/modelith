# Knowledge graphs from a Modelith model (optional)

Some teams want their logical model to also be a first-class W3C knowledge graph; many
do not. This is entirely opt-in. If you never run `mdl export r2rml`, nothing changes and
you pull in no extra dependency.

## The idea: emit the mapping, not the triples

Modelith emits an [R2RML](https://www.w3.org/TR/r2rml/) mapping, the W3C standard for
mapping a relational source to RDF. The durable idea in R2RML is the **term-map**: a
deterministic function from primary-key columns to a node IRI. Because the IRI is built
from the key by plain concatenation, the same warehouse row always yields the same graph
node, and a foreign key becomes a reference from one node to exactly one other, so a join
in the table survives as an edge in the graph. That identity guarantee, not the triples,
is the point.

Modelith already carries everything the term-map needs: an immutable identity on every
object, first-class keys and relationships, and ontology (SKOS/OWL) alignments. So the
R2RML mapping reuses the same IRIs as `mdl export rdf`; the mapping (ABox) and the ontology
(TBox) agree by construction.

The warehouse (the dbt models Modelith generates) stays the store. Modelith emits the
mapping; where you run it is your choice.

## Generate the mapping

```bash
mdl export r2rml -m model -o mapping.r2rml.ttl
# or in lockstep with the dbt project:
mdl generate -m model --emit-r2rml
```

The output is a Turtle document of `rr:TriplesMap`s: one per managed entity, with a
`rr:subjectMap` template built from the primary key, a `rr:predicateObjectMap` per
attribute, and a `rr:parentTriplesMap` + `rr:joinCondition` per relationship.

## Customising the term-map

By default the emitter mints node IRIs on the `modelith.dev` namespace and types each
node with a Modelith-minted class IRI. A real knowledge graph wants your own vocabulary
and identity scheme, so the term-map is customisable at two levels.

**Project base IRI.** Set `kg_base_iri` in `mdl-project.yaml` to your own namespace:

```yaml
name: sales
kg_base_iri: https://acme.com/id/
```

Every default subject template and minted IRI is then built on that base instead of
`modelith.dev`.

**Per-entity and per-attribute overrides.** Add a `term_map` block to a logical entity
(for the subject IRI template and the class IRI) or to an attribute (for the predicate
IRI and datatype):

```yaml
# logical/entities/customer.yaml
term_map:
  subject_template: https://acme.com/id/customer/{customer_id}
  class_iri: https://acme.com/Customer      # or a prefix:local CURIE the registry knows
attributes:
  - name: email
    term_map:
      predicate_iri: https://schema.org/email
      datatype: http://www.w3.org/2001/XMLSchema#string
```

**Class IRI precedence** (highest to lowest):

1. an explicit `term_map.class_iri` on the entity;
2. the entity's ontology alignment (`ontology.aligns_to`), resolved through the registry
   (so an entity you already aligned to a FIBO class is typed as that class);
3. a minted IRI on the project base IRI.

You can author all of this in the canvas Inspector's **Knowledge Graph mapping** section
(and therefore in the VS Code extension, which embeds the canvas), or in YAML with schema
completion. `mdl validate` checks that a subject template only references columns that
exist and that the IRIs are well-formed.

## Deploy it: virtual or materialized

The same mapping feeds either path. Pick per source.

### Virtual (recommended default): a live SPARQL endpoint over the warehouse

[Ontop](https://ontop-vkg.org/) (Apache-2.0) reads the R2RML mapping and rewrites
incoming SPARQL into SQL against the warehouse at query time. Nothing is copied, the graph
is always as fresh as the warehouse, and provenance back to the source is literal. Ontop
has connectors for Snowflake, BigQuery, DuckDB, Trino/Athena, Databricks, and Redshift, so
it points straight at the tables `mdl generate` produced.

Use when: you want a knowledge graph view without a second datastore, and your questions
are mostly lookups and shallow joins.

### Materialized: a triple store you load and query

[Morph-KGC](https://github.com/morph-kgc/morph-kgc) (or SDM-RDFizer) runs the same R2RML
mapping over the warehouse data and produces RDF triples you load into GraphDB, Fuseki, or
Oxigraph. Run it as a dbt post-hook so the graph refreshes when the models rebuild.

Use when: you need fast multi-hop graph traversal, SPARQL reasoning, or a graph decoupled
from warehouse query load. The cost is staleness (needs a refresh job) and a second store.

## Property graph sibling

`mdl export graph` emits a Neo4j Cypher schema from the same model (node-key, unique, and
existence constraints, plus relationship types). RDF and the labelled property graph are
different models; [neosemantics (n10s)](https://neo4j.com/labs/neosemantics/) bridges them
if you need to move between a Neo4j property graph and RDF.

## Standards note

R2RML (W3C Recommendation, 2012) is the frozen, universally-consumed mapping standard and
the correct target for a relational/warehouse source. RML, its modular successor
(CSV/JSON/RDF-star), is a Community Group draft rather than a ratified Recommendation;
Modelith may add it later if non-SQL sources appear.
