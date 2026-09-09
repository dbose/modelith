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
  definition?: string | null;
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
  definition?: string | null;
  type: "pk" | "alternate" | "unique" | "index";
  members: string[]; // attribute ids
}

export interface Entity {
  id: string;
  name: string;
  definition?: string | null;
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
  definition?: string | null;
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
  /** the subject area this doc is scoped to, or null for the whole model */
  scope?: string | null;
  /** true when the server runs in engineer mode: edits write the working tree */
  direct?: boolean;
  project: {
    name: string;
    dbt_target: string | null;
    platform_targets: string[];
    kg_base_iri?: string | null;
  };
  subject_areas: {
    id: string;
    name: string;
    definition: string | null;
    members?: string[];
    member_count?: number;
  }[];
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
  source: string; // resolver / vocabulary that produced it (== resolved_via)
  source_kind?: string; // "ontology-class" | "glossary-term"
}

// One browsable vocabulary a source exposes (two-phase browse, spec §4).
export interface OntologySource {
  id: string;
  name: string;
  description: string | null;
  namespace: string | null;
  count: number | null;
  layer: string | null;
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
  subject_areas: {
    id: string;
    name: string;
    definition: string | null;
    members?: string[];
    member_count?: number;
  }[];
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

// --- model git-ops (plan §L) ------------------------------------------------------
// These mirror the server's wire shapes exactly: ModelDiffDoc is
// mdl_core.diff_render.render_json, ClassificationDoc is Classification.to_dict.

export type Severity = "breaking" | "additive" | "cosmetic" | "unmanaged";
export type ChangeType = "added" | "removed" | "modified";

/** A dbt model a breaking change would break, from projection.where_used. */
export interface BreakRef {
  name: string;
  target: string;
  materialization: string;
}

export interface FieldChangeDoc {
  field: string;
  kind: string;
  severity: Severity;
  label: string;
  detail: string;
  before: unknown;
  after: unknown;
  breaks?: BreakRef[];
}

export interface ObjectChangeDoc {
  ulid: string;
  object_kind: string;
  object_kind_label: string;
  change: ChangeType;
  name_before: string | null;
  name_after: string | null;
  renamed: boolean;
  severity: Severity;
  path: string | null;
  fields: FieldChangeDoc[];
  children: ObjectChangeDoc[];
}

export interface ModelDiffDoc {
  ok: boolean;
  error?: string;
  base: { ref: string; sha: string; label: string };
  head: { ref: string | null; label: string };
  counts: Record<string, number>;
  max_severity: Severity | null;
  has_breaking: boolean;
  objects: ObjectChangeDoc[];
  config: FieldChangeDoc[];
}

export interface ClassificationDoc {
  ok: boolean;
  routes: string[];
  primary: string | null;
  primary_name: string | null;
  reviewers: string[];
  /** parsed from the repo's real .github/CODEOWNERS, when it has one */
  reviewers_actual: string[] | null;
  gates: string[];
  paths: string[];
  unmatched: string[];
}

export interface ConflictDoc {
  ok: boolean;
  base: string;
  head: string;
  stale: boolean;
  ahead: number;
  behind: number;
  clean: boolean;
  files: { path: string; clean: boolean; conflicts: string[] }[];
}

export interface GitContext {
  ok: boolean;
  git: boolean;
  branch?: string;
  base_branch?: string;
  on_base?: boolean;
  dirty?: boolean;
  ahead?: number;
  behind?: number;
  remote?: string | null;
  sme_branch_prefix?: string;
  read_only?: boolean;
  can_propose: boolean;
}

export interface ProposalDoc {
  branch: string;
  title: string;
  created: string;
  pushed: boolean;
  merged: boolean;
  ahead: number;
  behind: number;
  compare_url: string | null;
  pr: { number: number; url: string; state: string; reviews: string | null } | null;
}

export interface ProposalsDoc {
  ok: boolean;
  git: boolean;
  base?: string;
  /** false when `gh` is absent, unauthed or timed out — the list is still full */
  gh: boolean;
  proposals: ProposalDoc[];
}

/** POST /api/preview — the staged world, its diff against disk, and diagnostics. */
export interface PreviewDoc {
  ok: boolean;
  model: ModelDoc;
  diff: ModelDiffDoc;
  diagnostics: Diagnostic[];
  /** change index -> the ULID it created */
  created_ids: Record<string, string>;
  /** index of the first change that failed, or null */
  failed_index: number | null;
  error: string | null;
  fingerprint: string;
  read_only: boolean;
}

// --- subject-area workspace --------------------------------------------------------

export interface SubjectAreaRow {
  id: string;
  name: string;
  definition: string | null;
  members: string[];
  member_count: number;
}

export interface SubjectAreaDetail {
  ok: boolean;
  id: string;
  name: string;
  definition: string | null;
  members: string[];
  /** the two panes of the picker */
  included: GlossaryTerm[];
  available: GlossaryTerm[];
  /** objects whose home area is this one */
  homed_here: string[];
  /** homed here but NOT members — the MDL-W114 warning, surfaced as an affordance */
  inconsistent: string[];
}

/** One step of "add related objects", carrying WHY it was reached. */
export interface ClosureHop {
  id: string;
  name: string;
  via: string;
  via_name: string;
  from_id: string;
  from_name: string;
  direction: string;
  level: number;
}

export interface ExpandDoc {
  ok: boolean;
  hops: ClosureHop[];
  /** hops not already members */
  added: string[];
  error?: string;
}
