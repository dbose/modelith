"""Ontology source providers.

A `SourceProvider` is one place terms come from: a local file vocabulary, or a
remote enterprise registry / data catalog (OLS, OntoPortal, Collibra). The
`OntologyRegistry` is a facade that fans search/browse across the configured
providers and merges the results, so a caller never cares whether a term lives in
the repo or in an enterprise catalog.
"""

from mdl_ontology.providers.base import (
    OntologyRef,
    ResolvedTerm,
    SourceProvider,
    TermCard,
    local_name,
    score,
)
from mdl_ontology.providers.local import LocalFileProvider

__all__ = [
    "SourceProvider",
    "ResolvedTerm",
    "OntologyRef",
    "TermCard",
    "LocalFileProvider",
    "local_name",
    "score",
]
