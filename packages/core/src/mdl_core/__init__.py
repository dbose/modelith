"""Modelith core: IR, YAML round-trip, validator, round-trip/merge engine.

Layering rule (spec §1.3): this package depends on nothing else in the repo.
"""

from mdl_core.ids import ULID, new_ulid
from mdl_core.ir import (
    Attribute,
    ConceptualEntity,
    Domain,
    GlossaryConfig,
    LogicalEntity,
    Model,
    ObjectKind,
    OntologyAlignment,
    PhysicalColumn,
    PhysicalTable,
    ProjectConfig,
    Relationship,
    SubjectArea,
    Term,
)
from mdl_core.repo import ModelRepo

__all__ = [
    "ULID",
    "new_ulid",
    "ObjectKind",
    "OntologyAlignment",
    "SubjectArea",
    "ConceptualEntity",
    "Term",
    "Domain",
    "Attribute",
    "LogicalEntity",
    "Relationship",
    "PhysicalColumn",
    "PhysicalTable",
    "ProjectConfig",
    "GlossaryConfig",
    "Model",
    "ModelRepo",
]
