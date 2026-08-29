"""R2RML mapping export: the logical model as a first-class knowledge graph.

R2RML (W3C Recommendation, 2012) is a mapping language, itself RDF, from a relational
database to RDF. The durable idea is the *term-map*: a deterministic function from key
columns to a node identity, so a warehouse row and a graph node are provably the same
entity. Modelith already carries everything a term-map needs (immutable ULIDs, ontology
IRIs, first-class keys and relationships), and `rdf_export` already emits the matching
TBox (classes and properties). This is the ABox mapping that pairs with it: the same
IRIs, so TBox and ABox agree by construction.

The warehouse stays the store. This emits the *mapping*, not triples. Downstream, an
engine consumes it two ways: virtually (Ontop rewrites SPARQL to SQL over the warehouse)
or materialized (Morph-KGC runs the same mapping into a triple store). Modelith's job
ends at the mapping.

Per managed logical entity, the emitter produces one rr:TriplesMap:
- rr:logicalTable -> the physical (dbt) table name for the target;
- rr:subjectMap with rr:template building the node IRI from the primary-key column(s)
  on the Modelith IRI base, plus rr:class = the entity's class IRI (aligned when present);
- one rr:predicateObjectMap per attribute (rr:column + rr:datatype);
- one rr:predicateObjectMap per relationship whose child is this entity, using
  rr:parentTriplesMap + rr:joinCondition (the FK join that becomes a graph edge).
"""

from __future__ import annotations

from rdflib import BNode, Graph, Literal, URIRef

from mdl_core.ir import Attribute, LogicalEntity, Model, PhysicalTable
from mdl_ontology._common import _XSD_FOR_BASE, RR, base_iri_for, bind, term_uri


def _resolve_iri(value: str | None, registry) -> str | None:
    """Resolve a possibly-prefixed IRI to an absolute URL via the registry."""
    if not value:
        return None
    if value.startswith("http"):
        return value
    if registry is not None:
        expanded = registry.expand(value)
        if isinstance(expanded, str) and expanded.startswith("http"):
            return expanded
    return None


def _entity_aligned_iri(model: Model, le: LogicalEntity, registry) -> str | None:
    """The ontology IRI the entity is aligned to, via its realised conceptual entity."""
    if not le.realises:
        return None
    ce = model.conceptual_entities.get(le.realises)
    if ce is None or not ce.aligned_uri:
        return None
    return _resolve_iri(ce.aligned_uri, registry)


def _physical_table_for(
    model: Model, le: LogicalEntity, target: str | None
) -> PhysicalTable | None:
    for pt in model.physical_tables.values():
        if pt.realises == le.id and (target is None or pt.target == target):
            return pt
    return None


def _table_name(le: LogicalEntity, pt: PhysicalTable | None) -> str:
    """The warehouse table name, matching what the dbt emitter produces."""
    return pt.name.lower() if pt is not None else le.name


def _column_name(attr: Attribute, pt: PhysicalTable | None) -> str:
    """The physical column name for an attribute, matching the dbt output."""
    if pt is not None:
        for col in pt.columns:
            if col.realises == attr.id:
                return col.name
    return attr.name


def _pk_attr_ids(model: Model, le: LogicalEntity) -> list[str]:
    """Ordered primary-key attribute ids. A `pk` KeyGroup is authoritative; else
    fall back to `role: business_key`, matching the dbt emitter exactly."""
    for kg in model.key_groups.values():
        if kg.entity == le.id and kg.type == "pk":
            return list(kg.members)
    return [a.id for a in le.attributes if a.role == "business_key"]


def _class_iri(model: Model, le: LogicalEntity, registry, base: str) -> URIRef:
    """The rr:class IRI for an entity. Precedence: an explicit term_map.class_iri,
    then the entity's aligned ontology IRI, then a minted IRI on the project base."""
    if le.term_map and le.term_map.class_iri:
        resolved = _resolve_iri(le.term_map.class_iri, registry)
        if resolved:
            return URIRef(resolved)
    aligned = _entity_aligned_iri(model, le, registry)
    if aligned:
        return URIRef(aligned)
    return term_uri(le.id, base)


def _predicate_iri(attr: Attribute, registry, base: str) -> URIRef:
    """The predicate IRI for an attribute. Precedence: an explicit
    term_map.predicate_iri, then the attribute's ontology alignment, then a minted IRI
    on the project base."""
    if attr.term_map and attr.term_map.predicate_iri:
        resolved = _resolve_iri(attr.term_map.predicate_iri, registry)
        if resolved:
            return URIRef(resolved)
    if attr.aligned_uri:
        resolved = _resolve_iri(attr.aligned_uri, registry)
        if resolved:
            return URIRef(resolved)
    return term_uri(attr.id, base)


def _subject_template(
    model: Model, le: LogicalEntity, pt: PhysicalTable | None, base: str
) -> str | None:
    """rr:template for the node IRI. An explicit term_map.subject_template wins;
    otherwise the project base IRI plus the entity ULID and the primary-key column
    value(s). Plain concatenation, per the R2RML identity rule, so the original key
    can be reconstructed and the same row always yields the same node."""
    if le.term_map and le.term_map.subject_template:
        return le.term_map.subject_template
    pk_ids = _pk_attr_ids(model, le)
    attr_by_id = {a.id: a for a in le.attributes}
    cols = [
        _column_name(attr_by_id[aid], pt)
        for aid in pk_ids
        if aid in attr_by_id
    ]
    if not cols:
        return None  # no declared key: caller emits a per-row blank-node subject
    return f"{base}{le.id}/" + "_".join("{" + c + "}" for c in cols)


def export_r2rml(
    model: Model,
    *,
    target: str | None = None,
    registry=None,
) -> Graph:
    """Emit an R2RML mapping (itself an RDF graph) from the logical + physical model.

    `target` selects the physical target (dbt target) whose table names to map; when
    None, the first physical table per entity (or the entity name) is used.
    """
    g = Graph()
    bind(g, registry, r2rml=True)
    base = base_iri_for(model)

    # A TriplesMap node per entity, reused for parentTriplesMap references.
    tm_of: dict[str, URIRef] = {}
    pt_of: dict[str, PhysicalTable | None] = {}
    managed = [le for le in model.logical_entities.values() if not le.unmanaged]
    for le in managed:
        tm_of[le.id] = URIRef(f"{base}mapping/{le.id}")
        pt_of[le.id] = _physical_table_for(model, le, target)

    for le in sorted(managed, key=lambda e: e.name):
        pt = pt_of[le.id]
        tm = tm_of[le.id]
        g.add((tm, RR.logicalTable, _logical_table(g, _table_name(le, pt))))

        # Subject: PK-based IRI template + class. Entities with no declared key get
        # a per-row blank-node subject (R2RML's correct handling for a keyless table:
        # distinct rows stay distinct nodes) rather than a collapsing constant IRI.
        subj = BNode()
        g.add((tm, RR.subjectMap, subj))
        tmpl = _subject_template(model, le, pt, base)
        if tmpl is not None:
            g.add((subj, RR.template, Literal(tmpl)))
        else:
            g.add((subj, RR.termType, RR.BlankNode))
        g.add((subj, RR["class"], _class_iri(model, le, registry, base)))

        # One predicateObjectMap per attribute.
        for attr in le.attributes:
            pom = BNode()
            g.add((tm, RR.predicateObjectMap, pom))
            g.add((pom, RR.predicate, _predicate_iri(attr, registry, base)))
            om = BNode()
            g.add((pom, RR.objectMap, om))
            g.add((om, RR.column, Literal(_column_name(attr, pt))))
            if attr.term_map and attr.term_map.datatype:
                g.add((om, RR.datatype, URIRef(attr.term_map.datatype)))
            else:
                dom = model.domain_by_name(attr.domain)
                bt = (dom.base_type if dom else attr.domain) or "string"
                g.add((om, RR.datatype, _XSD_FOR_BASE.get(bt.lower(), _XSD_FOR_BASE["string"])))

        # One predicateObjectMap per relationship whose child (many side) is this entity.
        for rel in sorted(model.relationships.values(), key=lambda r: r.name):
            if rel.from_.entity != le.id:
                continue
            parent = tm_of.get(rel.to.entity)
            if parent is None:
                continue  # points at an unmanaged/absent entity; drop the edge
            child_cols = _end_columns(model, rel.from_.entity, rel.from_.attributes, pt)
            parent_cols = _end_columns(
                model, rel.to.entity, rel.to.attributes, pt_of.get(rel.to.entity)
            )
            if not child_cols or not parent_cols:
                continue
            pom = BNode()
            g.add((tm, RR.predicateObjectMap, pom))
            g.add((pom, RR.predicate, term_uri(rel.id)))
            om = BNode()
            g.add((pom, RR.objectMap, om))
            g.add((om, RR.parentTriplesMap, parent))
            for c_col, p_col in zip(child_cols, parent_cols, strict=False):
                jc = BNode()
                g.add((om, RR.joinCondition, jc))
                g.add((jc, RR.child, Literal(c_col)))
                g.add((jc, RR.parent, Literal(p_col)))

    return g


def _logical_table(g: Graph, table_name: str) -> BNode:
    lt = BNode()
    g.add((lt, RR.tableName, Literal(table_name)))
    return lt


def _end_columns(model: Model, entity_id: str, attr_ids, pt: PhysicalTable | None) -> list[str]:
    """Physical column names for the FK attributes on one relationship end."""
    le = model.logical_entities.get(entity_id)
    if le is None:
        return []
    attr_by_id = {a.id: a for a in le.attributes}
    return [_column_name(attr_by_id[aid], pt) for aid in attr_ids if aid in attr_by_id]
