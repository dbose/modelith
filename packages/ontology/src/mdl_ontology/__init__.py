"""Modelith ontology stack (spec §3).

Vocabulary-agnostic: FIBO is one reference bundle; ACORD/FHIR/ISO 20022/GS1/custom
vocabularies plug in by declaration. Depends only on `modelith-core` (§1.3).

Public names are exported LAZILY (PEP 562). Most of this package's surface pulls in the
RDF backend (pyoxigraph, the optional `[ontology]` extra), but a few members — notably
`Lock` (`.mdl/lock.yaml`, used by `mdl init` / import scaffolding) — do not. Eagerly
importing everything here meant that merely reaching `mdl_ontology.lock` dragged in
pyoxigraph, so `mdl init` / `mdl import erwin` crashed on an install without the extra —
breaking the promise that the core CLI runs without it. Resolving names on first access
instead keeps the pyoxigraph-free members reachable on a core-only install; a name that
does need the backend raises the ModuleNotFoundError only when it is actually used.
"""

# The TYPE_CHECKING block below re-imports every public name purely so type checkers and
# IDEs resolve them; at runtime __getattr__ does the real (lazy) import. Those imports read
# as unused to the linter, so F401 is suppressed for this file.
# ruff: noqa: F401

from __future__ import annotations

from typing import TYPE_CHECKING

# public name -> submodule it lives in. Resolved on first attribute access.
_EXPORTS = {
    "align_model": "align",
    "AlignmentProposal": "align",
    "Candidate": "align",
    "Matcher": "align",
    "LexicalMatcher": "align",
    "confidence_band": "align",
    "FetchError": "fetch",
    "FetchResult": "fetch",
    "compute_lock": "fetch",
    "fetch_all": "fetch",
    "fetch_layer": "fetch",
    "save_ontology_upload": "ingest",
    "CoverageReport": "layers",
    "check_layers": "layers",
    "coverage_report": "layers",
    "CACHE_REL": "lock",
    "LOCK_MODES": "lock",
    "Lock": "lock",
    "OntologyLayerLock": "lock",
    "cache_from_registry": "providers.cache",
    "cache_resolved_term": "providers.cache",
    "R2RMLCoverage": "r2rml_export",
    "UnmappedError": "r2rml_export",
    "export_r2rml": "r2rml_export",
    "r2rml_coverage": "r2rml_export",
    "export_rdf": "rdf_export",
    "export_shacl": "rdf_export",
    "serialize": "rdf_export",
    "OntologyRegistry": "registry",
    "ResolvedTerm": "registry",
    "VocabularySource": "registry",
    "build_registry": "registry",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    """PEP 562 lazy export: import the owning submodule only when the name is accessed."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(f"{__name__}.{module}")
    return getattr(mod, name)


def __dir__() -> list[str]:
    return sorted(__all__)


if TYPE_CHECKING:  # keep static analysers / IDEs seeing the real symbols
    # These are re-exports resolved lazily at runtime via __getattr__; the block exists
    # only so type checkers and IDEs see the real symbols (hence the file-level noqa: F401).
    from mdl_ontology.align import (
        AlignmentProposal,
        Candidate,
        LexicalMatcher,
        Matcher,
        align_model,
        confidence_band,
    )
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
    from mdl_ontology.providers.cache import cache_from_registry, cache_resolved_term
    from mdl_ontology.r2rml_export import (
        R2RMLCoverage,
        UnmappedError,
        export_r2rml,
        r2rml_coverage,
    )
    from mdl_ontology.rdf_export import export_rdf, export_shacl, serialize
    from mdl_ontology.registry import (
        OntologyRegistry,
        ResolvedTerm,
        VocabularySource,
        build_registry,
    )
