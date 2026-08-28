"""Shared RDF helpers for the ontology emitters (rdf_export, r2rml_export).

Modelith's own namespace, the ULID -> IRI term function, the base-type -> XSD map,
and the prefix binding all live here so the TBox emitter (rdf_export) and the R2RML
mapping emitter (r2rml_export) mint the same IRIs and agree by construction.
"""

from __future__ import annotations

import re

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, SH, SKOS, XSD

# Modelith's own namespace for minted terms (stable, ULID-based).
MDL = Namespace("https://modelith.dev/ontology/")

# W3C R2RML vocabulary.
RR = Namespace("http://www.w3.org/ns/r2rml#")

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


def term_uri(ulid: str, base: str | None = None) -> URIRef:
    """The stable IRI for a Modelith object, keyed by its ULID, on the given base
    (defaults to the modelith.dev namespace)."""
    return URIRef((base or str(MDL)) + ulid)


def base_iri_for(model) -> str:
    """Resolve the knowledge-graph base IRI for a model. Precedence: the project's
    explicit `kg_base_iri`, else a URN namespace derived from the project name
    (`urn:<project-slug>:`). The default carries no vendor host, so an unhosted
    model gets a clean, project-scoped identifier the user replaces by setting an
    explicit base. Always ends with a separator so `<base><ULID>` is well-formed."""
    cfg = getattr(model, "config", None)
    explicit = getattr(cfg, "kg_base_iri", None) if cfg else None
    if explicit:
        return explicit if explicit.endswith(("/", "#", ":")) else explicit + "/"
    name = getattr(cfg, "name", None) if cfg else None
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", name or "").strip("-").lower()
    return f"urn:{slug or 'modelith-model'}:"


def bind(g: Graph, registry=None, *, r2rml: bool = False) -> None:
    """Bind the standard prefixes on a graph; optionally the r2rml prefix."""
    g.bind("mdl", MDL)
    g.bind("skos", SKOS)
    g.bind("owl", OWL)
    g.bind("sh", SH)
    if r2rml:
        g.bind("rr", RR)
    if registry is not None:
        for pfx, ns in registry.prefixes.items():
            g.bind(pfx.replace(":", "_"), Namespace(ns), replace=True)


def serialize(g: Graph, fmt: str = "turtle") -> str:
    fmt_map = {"turtle": "turtle", "ttl": "turtle", "xml": "xml", "jsonld": "json-ld", "nt": "nt"}
    return g.serialize(format=fmt_map.get(fmt.lower(), "turtle"))
