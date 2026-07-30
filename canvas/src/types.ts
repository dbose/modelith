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
