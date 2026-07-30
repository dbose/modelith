"""Modelith ontology stack (spec §3).

Vocabulary-agnostic: FIBO is one reference bundle; ACORD/FHIR/ISO 20022/GS1/custom
vocabularies plug in by declaration. Depends only on `modelith-core` (§1.3).
"""

from mdl_ontology.layers import CoverageReport, check_layers, coverage_report
from mdl_ontology.lock import Lock
from mdl_ontology.rdf_export import export_rdf, export_shacl, serialize
from mdl_ontology.registry import (
    OntologyRegistry,
    ResolvedTerm,
    VocabularySource,
    build_registry,
)

__all__ = [
    "OntologyRegistry",
    "VocabularySource",
    "ResolvedTerm",
    "build_registry",
    "check_layers",
    "coverage_report",
    "CoverageReport",
    "export_rdf",
    "export_shacl",
    "serialize",
    "Lock",
]
