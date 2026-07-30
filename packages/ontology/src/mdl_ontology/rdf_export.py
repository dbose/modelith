"""RDF/OWL + SHACL export (spec §3.3).

- RDF/OWL export of the conceptual and logical layers with SKOS alignments intact.
  Each entity/term becomes an owl:Class (or skos:Concept), carrying rdfs:label,
  skos:definition, and its declared skos:*Match to the aligned IRI.
- SHACL shapes generated from the logical model: one sh:NodeShape per logical
  entity, one sh:PropertyShape per attribute (datatype + minCount for non-null),
  so the model can validate instance data or be handed to a graph platform.

Ontology IRIs from the model flow into both, making the model machine-readable
rather than a picture.
"""

from __future__ import annotations

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SH, SKOS, XSD

from mdl_core.ir import Model

# Modelith's own namespace for minted terms (stable, ULID-based).
MDL = Namespace("https://modelith.dev/ontology/")

_XSD_FOR_BASE = {
    "bigint": XSD.long,
    "int": XSD.integer,
    "integer": XSD.integer,
    "string": XSD.string,
    "text": XSD.string,
    "boolean": XSD.boolean,
    "date": XSD.date,
    "timestamp": XSD.dateTime,
    "decimal": XSD.decimal,
    "identifier_bigint": XSD.long,
    "lei_code": XSD.string,
}

_SKOS_PRED = {
    "skos:exactMatch": SKOS.exactMatch,
    "skos:closeMatch": SKOS.closeMatch,
    "skos:broadMatch": SKOS.broadMatch,
    "skos:narrowMatch": SKOS.narrowMatch,
    "skos:relatedMatch": SKOS.relatedMatch,
}


def _term_uri(ulid: str) -> URIRef:
    return URIRef(str(MDL) + ulid)


def _bind(g: Graph, registry=None) -> None:
    g.bind("mdl", MDL)
    g.bind("skos", SKOS)
    g.bind("owl", OWL)
    g.bind("sh", SH)
    if registry is not None:
        for pfx, ns in registry.prefixes.items():
            g.bind(pfx.replace(":", "_"), Namespace(ns), replace=True)


def export_rdf(model: Model, *, layer: str = "conceptual", registry=None) -> Graph:
    """Export the given layer(s) as an RDF graph. layer in {conceptual, logical, all}."""
    g = Graph()
    _bind(g, registry)

    if layer in ("conceptual", "all"):
        for ce in model.conceptual_entities.values():
            _emit_concept(g, ce.id, ce.name, ce.definition, ce.ontology, registry)
            for syn in ce.synonyms:
                g.add((_term_uri(ce.id), SKOS.altLabel, Literal(syn)))
        for term in model.terms.values():
            _emit_concept(g, term.id, term.name, term.definition, term.ontology, registry)

    if layer in ("logical", "all"):
        for le in model.logical_entities.values():
            uri = _term_uri(le.id)
            g.add((uri, RDF.type, OWL.Class))
            g.add((uri, RDFS.label, Literal(le.name)))
            if le.realises:
                g.add((uri, MDL.realises, _term_uri(le.realises)))
            for attr in le.attributes:
                a_uri = _term_uri(attr.id)
                g.add((a_uri, RDF.type, OWL.DatatypeProperty))
                g.add((a_uri, RDFS.label, Literal(attr.name)))
                g.add((a_uri, RDFS.domain, uri))
                if attr.ontology and attr.ontology.aligns_to and registry:
                    tgt = registry.expand(attr.ontology.aligns_to) or attr.ontology.aligns_to
                    pred = _SKOS_PRED.get(attr.ontology.alignment or "", SKOS.closeMatch)
                    g.add((a_uri, pred, URIRef(tgt)))
    return g


def _emit_concept(g: Graph, ulid, name, definition, ontology, registry) -> None:
    uri = _term_uri(ulid)
    g.add((uri, RDF.type, OWL.Class))
    g.add((uri, RDF.type, SKOS.Concept))
    g.add((uri, SKOS.prefLabel, Literal(name)))
    g.add((uri, RDFS.label, Literal(name)))
    if definition:
        g.add((uri, SKOS.definition, Literal(definition)))
    if ontology and ontology.aligns_to:
        tgt = None
        if registry is not None:
            tgt = registry.expand(ontology.aligns_to)
        tgt = tgt or (ontology.aligns_to if ontology.aligns_to.startswith("http") else None)
        if tgt:
            pred = _SKOS_PRED.get(ontology.alignment or "", SKOS.closeMatch)
            g.add((uri, pred, URIRef(tgt)))


def export_shacl(model: Model) -> Graph:
    """Generate SHACL NodeShapes from the logical model (spec §3.3)."""
    g = Graph()
    _bind(g)
    for le in model.logical_entities.values():
        shape = URIRef(str(MDL) + f"shape/{le.id}")
        target = _term_uri(le.id)
        g.add((shape, RDF.type, SH.NodeShape))
        g.add((shape, SH.targetClass, target))
        g.add((shape, RDFS.label, Literal(f"{le.name} shape")))
        for attr in le.attributes:
            prop = URIRef(str(MDL) + f"shape/{le.id}/{attr.id}")
            g.add((shape, SH.property, prop))
            g.add((prop, SH.path, _term_uri(attr.id)))
            g.add((prop, SH.name, Literal(attr.name)))
            xsd_t = _XSD_FOR_BASE.get((attr.domain or "string").lower(), XSD.string)
            g.add((prop, SH.datatype, xsd_t))
            if not attr.nullable:
                g.add((prop, SH.minCount, Literal(1)))
            if attr.role == "business_key":
                g.add((prop, SH.maxCount, Literal(1)))
    return g


def serialize(g: Graph, fmt: str = "turtle") -> str:
    fmt_map = {"turtle": "turtle", "ttl": "turtle", "xml": "xml", "jsonld": "json-ld", "nt": "nt"}
    return g.serialize(format=fmt_map.get(fmt.lower(), "turtle"))
