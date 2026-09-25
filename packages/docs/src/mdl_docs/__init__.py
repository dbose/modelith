"""Modelith static documentation site generator.

Renders a self-contained, offline HTML site for a model (dbt-docs style): a persistent
left tree-view of logical models grouped by subject area, a structured detail page per
entity (metadata, attributes, keys, relationships, ontology alignment, stewardship,
where-used), a per-entity Mermaid neighbourhood diagram, an overview with the full ERD,
and a glossary. No server and no network are needed to view the output.
"""

from mdl_docs.render import neighbourhood, render_site

__all__ = ["render_site", "neighbourhood"]
