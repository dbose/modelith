// Wire types mirroring packages/server/src/mdl_server/projection.py

export interface Ontology {
  aligns_to: string | null;
  alignment: string | null;
  layer: string | null;
}

export interface Stewardship {
  owner: string | null;
  steward: string | null;
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
  ontology: Ontology | null;
  stewardship: Stewardship | null;
}

export interface AttributeRow {
  id: string;
  name: string;
  domain: string | null;
  role: "business_key" | "surrogate_key" | "attribute" | "measure";
  nullable: boolean;
  ontology_iri: string | null;
}

export interface Entity {
  id: string;
  name: string;
  pattern: string | null;
  conceptual: Conceptual | null;
  attributes: AttributeRow[];
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
  project: { name: string; dbt_target: string | null; platform_targets: string[] };
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

export interface StackTerm {
  id: string;
  name: string;
  kind: string;
  definition: string | null;
  no_industry_equivalent: boolean;
  aligned_to: { ref: string; predicate: string | null; resolved: boolean; label: string } | null;
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
