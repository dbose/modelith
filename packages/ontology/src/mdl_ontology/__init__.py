"""Modelith ontology stack (spec §3).

Vocabulary-agnostic: FIBO is one reference bundle; ACORD/FHIR/ISO 20022/GS1/custom
vocabularies plug in by declaration. Depends only on `modelith-core` (§1.3).
"""

from mdl_ontology.fetch import (
    FetchError,
    FetchResult,
    compute_lock,
    fetch_all,
    fetch_layer,
)
from mdl_ontology.ingest import save_ontology_upload
from mdl_ontology.layers import CoverageReport, check_layers, coverage_report
from mdl_ontology.lock import CACHE_REL, LOCK_MODES, Lock, OntologyLayerLock
from mdl_ontology.r2rml_export import export_r2rml
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
    "export_r2rml",
    "serialize",
    "Lock",
    "OntologyLayerLock",
    "LOCK_MODES",
    "CACHE_REL",
    "fetch_all",
    "fetch_layer",
    "compute_lock",
    "FetchError",
    "FetchResult",
    "save_ontology_upload",
]
