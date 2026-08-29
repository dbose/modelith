// Wire types mirroring packages/server/src/mdl_server/projection.py

// Back-compat single-alignment view, derived from the primary ref (spec §1).
export interface Ontology {
  aligns_to: string | null;
  alignment: string | null;
  layer: string | null;
  status?: string | null;
}

// One ontology binding (spec §1). A modelled object may carry several.
export interface OntologyRef {
  predicate: string | null;
  uri: string | null;
  layer: string | null;
  resolved_via: string | null;
  resolved_by?: string | null;
  confidence?: number | null;
  resolved_at?: string | null;
  approved_at?: string | null;
  status: string | null;
}

export interface Stewardship {
  owner: string | null;
  steward: string | null;
}

// R2RML term-map override (knowledge-graph mapping). All optional.
export interface TermMap {
  subject_template?: string | null;
  class_iri?: string | null;
  predicate_iri?: string | null;
  datatype?: string | null;
}

export interface SubjectAreaRef {
  id: string;
  name: string;
}

export interface Conceptual {
  id: string;
  name: string;
  definition: string | null;
  synonyms: string[];
  subject_area: SubjectAreaRef | null;
  ontology_layer: string | null;
  no_industry_equivalent?: boolean;
  ontology_refs: OntologyRef[];
  ontology: Ontology | null; // back-compat, derived from the primary ref
  stewardship: Stewardship | null;
}

export interface AttributeRow {
  id: string;
  name: string;
  domain: string | null;
  role: "business_key" | "surrogate_key" | "attribute" | "measure";
  nullable: boolean;
  ontology_iri: string | null;
  ontology_refs?: OntologyRef[];
  enum_values?: (string | number)[] | null;
  term_map?: TermMap | null;
  udp?: Record<string, string | number | boolean> | null;
}

export interface KeyGroupRow {
  id: string;
  name: string;
  type: "pk" | "alternate" | "unique" | "index";
  members: string[]; // attribute ids
}

export interface Entity {
  id: string;
  name: string;
  pattern: string | null;
  conceptual: Conceptual | null;
  attributes: AttributeRow[];
  key_groups?: KeyGroupRow[];
  term_map?: TermMap | null;
  udp?: Record<string, string | number | boolean> | null;
  category?: {
    role: "supertype" | "subtype";
    category: string;
    materialization?: "single_table" | "table_per_subtype";
  } | null;
}

export interface RelationshipEnd {
  entity: string;
  attributes: string[];
}

export interface Relationship {
  id: string;
  name: string;
  from: RelationshipEnd;
  to: RelationshipEnd;
  cardinality: "one_to_one" | "one_to_many" | "many_to_one" | "many_to_many";
  identifying: boolean;
  optionality: "mandatory" | "optional";
}

export interface PhysicalTable {
  id: string;
  target: string;
  realises: string;
  name: string;
  materialization: string;
}

export interface ModelDoc {
  project: {
    name: string;
    dbt_target: string | null;
    platform_targets: string[];
    kg_base_iri?: string | null;
  };
  subject_areas: { id: string; name: string; definition: string | null }[];
  entities: Entity[];
  relationships: Relationship[];
  physical: PhysicalTable[];
  counts: { entities: number; relationships: number; attributes: number };
  fingerprint: string;
  read_only: boolean;
  domains: string[];
}

// --- ontology (E1) ---

export interface TermCard {
  iri: string;
  prefixed: string;
  label: string;
  definition: string | null;
  source: string;
}

export interface TermDetail extends TermCard {
  broader: { iri: string; prefixed: string; label: string }[];
  narrower: { iri: string; prefixed: string; label: string }[];
}

export interface StackAlignedRef {
  ref: string;
  predicate: string | null;
  resolved: boolean;
  resolved_via?: string | null;
  status?: string | null;
  label: string;
}

export interface StackTerm {
  id: string;
  name: string;
  kind: string;
  definition: string | null;
  no_industry_equivalent: boolean;
  aligned_to: StackAlignedRef | null; // primary alignment (back-compat)
  aligned_refs?: StackAlignedRef[]; // the full list
}

export interface StackDoc {
  layers: Record<"industry" | "core" | "domain" | "specialised", StackTerm[]>;
  external_terms: TermCard[];
  vocabularies: string[];
}

export interface CoverageDoc {
  coverage_pct: number;
  total_core: number;
  core_with_industry: number;
  core_exempt: number;
  core_uncovered: string[];
}

// --- editing (E2/E3) ---

export interface CommandResponse {
  ok: boolean;
  fingerprint: string;
  created_id: string | null;
  diagnostics: Diagnostic[];
  error?: string;
}

export interface GitStatus {
  git: boolean;
  clean?: boolean;
  dirty: { state: string; path: string }[];
}

export interface Decision {
  signal_key: string;
  kind: string;
  signal: string;
  confidence: string;
  subject: string;
  verdict: "proposed" | "accepted" | "rejected";
}

// --- glossary (SME app) ---

export interface WhereUsed {
  logical_entity: string;
  logical_id: string;
  unmanaged: boolean;
  physical: { name: string; target: string; materialization: string }[];
}

export interface GlossaryTerm {
  id: string;
  kind: "conceptual_entity" | "term";
  name: string;
  definition: string | null;
  synonyms: string[];
  subject_area: { id: string; name?: string } | null;
  stewardship: { owner: string | null; steward: string | null } | null;
  ontology_layer?: string | null;
  ontology_refs?: OntologyRef[];
  ontology: {
    aligns_to: string | null;
    alignment: string | null;
    layer: string | null;
    status: string | null;
  } | null;
  where_used: WhereUsed[];
}

export interface GlossaryDoc {
  terms: GlossaryTerm[];
  subject_areas: { id: string; name: string; definition: string | null }[];
}

export interface GlossaryConfig {
  source_of_truth: "git" | "collibra";
  catalog_url: string | null;
  catalog_name: string;
  catalog_owned_fields: string[]; // e.g. ["definition","synonyms","stewardship"] when catalog masters
}

export interface ProposeResult {
  ok: boolean;
  branch?: string;
  applied?: number;
  pushed?: boolean;
  pr_url?: string | null;
  compare_url?: string | null;
  message?: string;
  error?: string;
}

export interface Diagnostic {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
  path: string | null;
}

export interface DiagnosticsDoc {
  items: Diagnostic[];
  has_errors: boolean;
}
