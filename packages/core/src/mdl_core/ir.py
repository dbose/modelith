"""Model IR (spec §2.3).

One object graph, three layer views (conceptual / logical / physical), all
referencing each other by immutable ULID. pydantic v2 models define the schema;
`Model` is the in-memory graph that the loader (repo.py) populates and the
validator/emitters consume.

Design notes:
- ULID references are plain strings (the `ULID` alias). Referential integrity is
  checked by the validator, not by pydantic, so a partially-loaded repo is still
  representable and reportable.
- `model_config = ConfigDict(extra="forbid")` on authored objects catches typos in
  YAML keys as validation errors rather than silently dropping them.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mdl_core.ids import ULID


class ObjectKind(str, Enum):
    subject_area = "subject_area"
    conceptual_entity = "conceptual_entity"
    term = "term"
    logical_entity = "logical_entity"
    domain = "domain"
    code_set = "code_set"
    relationship = "relationship"
    key_group = "key_group"
    category = "category"
    physical_table = "physical_table"


# SKOS alignment predicates (spec §3.1 rule 2).
Alignment = Literal[
    "skos:exactMatch",
    "skos:closeMatch",
    "skos:broadMatch",
    "skos:narrowMatch",
    "skos:relatedMatch",
]

# Ontology layers (spec §3.1).
OntologyLayer = Literal["industry", "core", "domain", "specialised"]

Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]

Pattern = Literal["scd2", "hub", "link", "satellite", "bridge"]

# Key-group kinds (erwin: Key Group / Candidate Key). `pk` is the primary key;
# `alternate`/`unique` are candidate/unique keys; `index` is a secondary index.
KeyGroupType = Literal["pk", "alternate", "unique", "index"]

# User-defined properties (erwin: UDPs). A small, typed property bag attachable to
# core objects — a scalar value per name. Flows into dbt `meta:` on generation and
# governance writeback. Kept explicit (not extra="allow") so typos elsewhere are
# still caught.
UdpValue = str | int | float | bool
Udp = dict[str, UdpValue]

# Subtype/supertype materialization (erwin category → physical strategy).
# single_table: one table for the supertype + all subtype columns (a discriminator
# column selects the subtype). table_per_subtype: the supertype's own table plus one
# per subtype.
CategoryMaterialization = Literal["single_table", "table_per_subtype"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OntologyAlignment(_Base):
    aligns_to: str | None = None  # prefixed IRI, e.g. fibo-fnd-pty-pty:PartyInRole
    alignment: Alignment | None = None
    layer: OntologyLayer | None = None
    no_industry_equivalent: bool = False
    rationale: str | None = None
    # SME-proposed alignments await architect promotion (collab model §5.1);
    # None is treated as accepted for models predating the field.
    status: Literal["proposed", "accepted"] | None = None


class Stewardship(_Base):
    owner: str | None = None
    steward: str | None = None


class SubjectArea(_Base):
    id: ULID
    kind: Literal[ObjectKind.subject_area] = ObjectKind.subject_area
    name: str
    definition: str | None = None


class ConceptualEntity(_Base):
    id: ULID
    kind: Literal[ObjectKind.conceptual_entity] = ObjectKind.conceptual_entity
    name: str
    subject_area: ULID | None = None
    definition: str | None = None
    ontology: OntologyAlignment | None = None
    stewardship: Stewardship | None = None
    synonyms: list[str] = Field(default_factory=list)
    # Derived, not authored: logical entities that realise this concept.
    realised_by: list[ULID] = Field(default_factory=list)
    udp: Udp | None = None  # user-defined properties (erwin UDPs)


class Term(_Base):
    id: ULID
    kind: Literal[ObjectKind.term] = ObjectKind.term
    name: str
    definition: str | None = None
    ontology: OntologyAlignment | None = None
    synonyms: list[str] = Field(default_factory=list)


class Domain(_Base):
    """Reusable attribute domain (logical/domains/*.yaml).

    Beyond a base type, a domain may be an *enumeration*: either inline
    `allowed_values` or a reference to a shared `CodeSet` (`value_set`, by name).
    Enumerated domains emit dbt `accepted_values` tests automatically (erwin: a
    domain with a valid-values list).
    """

    id: ULID
    kind: Literal[ObjectKind.domain] = ObjectKind.domain
    name: str
    base_type: str  # abstract logical type, e.g. "bigint", "string", "lei_code"
    definition: str | None = None
    allowed_values: list[str | int] | None = None  # inline enumeration
    value_set: str | None = None  # name-ref to a CodeSet for shared enumerations
    udp: Udp | None = None  # user-defined properties (erwin UDPs)


class CodeValue(_Base):
    code: str | int
    label: str | None = None


class CodeSet(_Base):
    """A shared, reusable value list / reference data set (logical/value-sets/).

    Named enumerations referenced by many domains — e.g. country, currency,
    order_status. Each value has a code plus an optional human label.
    """

    id: ULID
    kind: Literal[ObjectKind.code_set] = ObjectKind.code_set
    name: str
    definition: str | None = None
    values: list[CodeValue] = Field(default_factory=list)


class Attribute(_Base):
    id: ULID
    name: str
    domain: str | None = None  # name-ref to a Domain object
    role: Literal["business_key", "surrogate_key", "attribute", "measure"] = "attribute"
    nullable: bool = True
    ontology: OntologyAlignment | None = None
    udp: Udp | None = None  # user-defined properties (erwin UDPs)


class LogicalEntity(_Base):
    id: ULID
    kind: Literal[ObjectKind.logical_entity] = ObjectKind.logical_entity
    name: str
    realises: ULID | None = None  # conceptual entity ULID
    attributes: list[Attribute] = Field(default_factory=list)
    subtypes: list[ULID] = Field(default_factory=list)
    pattern: Pattern | None = None
    # True => the emitter does NOT emit this model's SQL/contract; the file is
    # engineer-owned forever (spec `mdl unmanage`). Entity stays in the model.
    unmanaged: bool | None = None
    udp: Udp | None = None  # user-defined properties (erwin UDPs)


class RelationshipEnd(_Base):
    entity: ULID
    attributes: list[ULID] = Field(default_factory=list)


class RelationshipEnforce(_Base):
    physical_constraint: bool = True
    dbt_test: Literal["relationships"] | None = "relationships"


class Relationship(_Base):
    id: ULID
    kind: Literal[ObjectKind.relationship] = ObjectKind.relationship
    name: str
    from_: RelationshipEnd = Field(alias="from")  # many side
    to: RelationshipEnd  # one side
    cardinality: Cardinality = "many_to_one"
    identifying: bool = False
    optionality: Literal["mandatory", "optional"] = "mandatory"
    enforce: RelationshipEnforce = Field(default_factory=RelationshipEnforce)
    udp: Udp | None = None  # user-defined properties (erwin UDPs)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class KeyGroup(_Base):
    """A named key on a logical entity (erwin Key Group / Candidate Key).

    Makes a primary key first-class: a `pk` KeyGroup names the (possibly composite)
    primary key and is the authoritative source; `alternate`/`unique` capture
    candidate/unique keys; `index` a secondary index. `members` is ordered — key
    column order matters. When a `pk` KeyGroup is present it wins over the legacy
    per-attribute `role: business_key` convention (which stays supported).
    """

    id: ULID
    kind: Literal[ObjectKind.key_group] = ObjectKind.key_group
    entity: ULID  # logical entity this key belongs to
    name: str
    type: KeyGroupType = "pk"
    members: list[ULID] = Field(default_factory=list)  # ordered attribute ULIDs
    udp: Udp | None = None  # user-defined properties (erwin UDPs)


class Category(_Base):
    """A subtype/supertype cluster (erwin category / generalization).

    A `supertype` entity generalises one or more `subtypes`. `discriminator` is the
    supertype attribute whose value selects the subtype. `complete` = every instance
    belongs to some enumerated subtype; `exclusive` = an instance is exactly one
    subtype (vs. possibly several). `materialization` chooses the physical strategy
    the dbt emitter honours.
    """

    id: ULID
    kind: Literal[ObjectKind.category] = ObjectKind.category
    name: str
    supertype: ULID  # logical entity
    subtypes: list[ULID] = Field(default_factory=list)  # logical entities
    discriminator: ULID | None = None  # supertype attribute selecting the subtype
    complete: bool = False  # all subtypes enumerated (total specialisation)
    exclusive: bool = True  # disjoint subtypes (an instance is exactly one)
    materialization: CategoryMaterialization = "single_table"
    udp: Udp | None = None


class PhysicalColumn(_Base):
    realises: ULID | None = None  # attribute ULID
    name: str
    data_type: str


class PhysicalTable(_Base):
    id: ULID
    kind: Literal[ObjectKind.physical_table] = ObjectKind.physical_table
    target: str
    realises: ULID  # logical entity ULID
    name: str
    materialization: Literal["table", "view", "incremental", "ephemeral"] = "table"
    platform: dict = Field(default_factory=dict)
    columns: list[PhysicalColumn] = Field(default_factory=list)


class NamingStandards(_Base):
    """First-class naming config (spec §2.4)."""

    model_config = ConfigDict(extra="allow")

    logical_case: Literal["snake", "camel", "pascal", "any"] = "snake"
    physical_case: Literal["snake", "upper_snake", "any"] = "upper_snake"
    abbreviations: dict[str, str] = Field(default_factory=dict)  # word -> approved abbrev


class GlossaryConfig(_Base):
    """Who masters the business glossary. Decides the direction of the Collibra
    bridge and whether the /sme app's meaning-fields are writable.

    - ``git`` (default): git is the source of truth. The /sme app authors
      definitions/synonyms/stewardship as PRs; ``mdl governance publish`` mirrors
      them out to Collibra on merge.
    - ``collibra``: the catalog masters those fields. The /sme app shows them
      read-only with a deep link, leaving only alignment *proposals* (which are
      Modelith's to own); ``mdl governance import`` brings catalog edits back in
      as a reviewable bot PR.
    """

    model_config = ConfigDict(extra="allow")

    source_of_truth: Literal["git", "collibra"] = "git"
    catalog_url: str | None = None  # e.g. https://acme.collibra.com — for deep links
    catalog_name: str = "Collibra"  # display name for banners


class ProjectConfig(_Base):
    model_config = ConfigDict(extra="allow")

    name: str
    dbt_target: str | None = None
    platform_targets: list[str] = Field(default_factory=list)
    # Each entry is a vocabulary declaration (name/layer/prefixes/path/modules);
    # see mdl_ontology.registry.VocabularySource. Kept as dicts here so `core`
    # stays free of any ontology dependency (layering §1.3).
    ontology_stack: list[dict] = Field(default_factory=list)
    naming: NamingStandards = Field(default_factory=NamingStandards)
    glossary: GlossaryConfig = Field(default_factory=GlossaryConfig)


# --- The in-memory graph ---------------------------------------------------

# Union of all authored object kinds, for the loader to dispatch on `kind`.
AnyObject = (
    SubjectArea
    | ConceptualEntity
    | Term
    | Domain
    | CodeSet
    | LogicalEntity
    | Relationship
    | KeyGroup
    | Category
    | PhysicalTable
)

_KIND_TO_CLASS: dict[str, type[BaseModel]] = {
    ObjectKind.subject_area.value: SubjectArea,
    ObjectKind.conceptual_entity.value: ConceptualEntity,
    ObjectKind.term.value: Term,
    ObjectKind.domain.value: Domain,
    ObjectKind.code_set.value: CodeSet,
    ObjectKind.logical_entity.value: LogicalEntity,
    ObjectKind.relationship.value: Relationship,
    ObjectKind.key_group.value: KeyGroup,
    ObjectKind.category.value: Category,
    ObjectKind.physical_table.value: PhysicalTable,
}


def object_class_for_kind(kind: str) -> type[BaseModel]:
    if kind not in _KIND_TO_CLASS:
        raise ValueError(f"unknown object kind: {kind!r}")
    return _KIND_TO_CLASS[kind]


class Model:
    """The whole model graph, indexed by ULID.

    This is a plain container, not a pydantic model, because it holds the raw
    ruamel round-trip nodes alongside the parsed objects so the loader can
    re-serialise with comments intact.
    """

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.subject_areas: dict[ULID, SubjectArea] = {}
        self.conceptual_entities: dict[ULID, ConceptualEntity] = {}
        self.terms: dict[ULID, Term] = {}
        self.domains: dict[ULID, Domain] = {}
        self.code_sets: dict[ULID, CodeSet] = {}
        self.logical_entities: dict[ULID, LogicalEntity] = {}
        self.relationships: dict[ULID, Relationship] = {}
        self.key_groups: dict[ULID, KeyGroup] = {}
        self.categories: dict[ULID, Category] = {}
        self.physical_tables: dict[ULID, PhysicalTable] = {}

    def add(self, obj: BaseModel) -> None:
        table = self._table_for(obj)
        table[obj.id] = obj  # type: ignore[attr-defined]

    def _table_for(self, obj: BaseModel) -> dict:
        mapping = {
            SubjectArea: self.subject_areas,
            ConceptualEntity: self.conceptual_entities,
            Term: self.terms,
            Domain: self.domains,
            CodeSet: self.code_sets,
            LogicalEntity: self.logical_entities,
            Relationship: self.relationships,
            KeyGroup: self.key_groups,
            Category: self.categories,
            PhysicalTable: self.physical_tables,
        }
        for cls, tbl in mapping.items():
            if isinstance(obj, cls):
                return tbl
        raise TypeError(f"cannot index object of type {type(obj).__name__}")

    def all_objects(self) -> list[BaseModel]:
        out: list[BaseModel] = []
        for tbl in (
            self.subject_areas,
            self.conceptual_entities,
            self.terms,
            self.domains,
            self.code_sets,
            self.logical_entities,
            self.relationships,
            self.key_groups,
            self.categories,
            self.physical_tables,
        ):
            out.extend(tbl.values())
        return out

    def domain_by_name(self, name: str | None) -> Domain | None:
        """Domains are referenced from attributes by name, but indexed by ULID.
        This resolves the name -> Domain object."""
        if name is None:
            return None
        for dom in self.domains.values():
            if dom.name == name:
                return dom
        return None

    def code_set_by_name(self, name: str | None) -> CodeSet | None:
        """Resolve a code-set name-reference (from Domain.value_set) to the object."""
        if name is None:
            return None
        for cs in self.code_sets.values():
            if cs.name == name:
                return cs
        return None

    def get(self, ulid: ULID) -> BaseModel | None:
        for tbl in (
            self.subject_areas,
            self.conceptual_entities,
            self.terms,
            self.domains,
            self.code_sets,
            self.logical_entities,
            self.relationships,
            self.key_groups,
            self.categories,
            self.physical_tables,
        ):
            if ulid in tbl:
                return tbl[ulid]
        return None

    def all_ulids(self) -> set[ULID]:
        ids: set[ULID] = set()
        for obj in self.all_objects():
            ids.add(obj.id)  # type: ignore[attr-defined]
            if isinstance(obj, LogicalEntity):
                ids.update(a.id for a in obj.attributes)
        return ids
