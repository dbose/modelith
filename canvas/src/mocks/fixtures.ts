/** Fixture data for the Model Git-Ops mocks (plan §M1–M6).
 *
 * Shapes mirror the real wire formats these screens will consume:
 *   ModelDiffDoc   <- GET /api/git/diff/model   (diff_render.render_json)
 *   ClassificationDoc <- GET /api/git/classify  (Classification.to_dict)
 *   ConflictDoc    <- GET /api/git/conflicts
 *   ProposalDoc[]  <- GET /api/git/proposals
 * Names and stewards are the real demo/ibor model, so the mocks read true.
 */

export type Severity = "breaking" | "additive" | "cosmetic";
export type ChangeType = "added" | "removed" | "modified";

export interface FieldChangeDoc {
  field: string;
  kind: string;
  severity: Severity;
  label: string;
  detail: string;
  before: string | null;
  after: string | null;
  /** where_used, populated only when the change is breaking */
  breaks?: { model: string; target: string; materialization: string }[];
}

export interface ObjectChangeDoc {
  ulid: string;
  object_kind: string;
  change: ChangeType;
  name_before: string | null;
  name_after: string | null;
  renamed: boolean;
  severity: Severity;
  path: string;
  fields: FieldChangeDoc[];
}

export interface ModelDiffDoc {
  base: { ref: string; sha: string; label: string };
  head: { ref: string | null; label: string };
  counts: Record<string, number>;
  max_severity: Severity;
  has_breaking: boolean;
  objects: ObjectChangeDoc[];
}

export const DIFF: ModelDiffDoc = {
  base: { ref: "main", sha: "a1b2c3d", label: "last committed" },
  head: { ref: null, label: "your working copy" },
  counts: { added: 0, removed: 0, modified: 3, breaking: 1, additive: 1, cosmetic: 3 },
  max_severity: "breaking",
  has_breaking: true,
  objects: [
    {
      ulid: "01KZ2659638XNEK72JK6PGQA7P",
      object_kind: "conceptual_entity",
      change: "modified",
      name_before: "Counterparty",
      name_after: "Legal Entity",
      renamed: true,
      severity: "cosmetic",
      path: "conceptual/entities/legal-entity.yaml",
      fields: [
        {
          field: "name",
          kind: "object_renamed",
          severity: "cosmetic",
          label: "Renamed",
          detail: "Counterparty → Legal Entity",
          before: "Counterparty",
          after: "Legal Entity",
        },
        {
          field: "definition",
          kind: "definition_changed",
          severity: "cosmetic",
          label: "Definition changed",
          detail: "",
          before:
            "A legal person with whom the firm has or may have a contractual obligation.",
          after:
            "A legal person with whom the firm has, or may come to have, a contractual obligation arising from a trade.",
        },
      ],
    },
    {
      ulid: "01KZ2CKNG2D1NKYJP72NC3HPVK",
      object_kind: "conceptual_entity",
      change: "modified",
      name_before: "Account",
      name_after: "Account",
      renamed: false,
      severity: "additive",
      path: "conceptual/entities/account.yaml",
      fields: [
        {
          field: "members",
          kind: "subject_area_members_changed",
          severity: "additive",
          label: "Added to 3 subject areas",
          detail:
            "Domain-03-ExposureManagement, Domain-04-PrivateMarkets, UseCase-ApraStressTesting",
          before: "1 subject area",
          after: "4 subject areas",
        },
      ],
    },
    {
      ulid: "01KZ2Z6NHHYYZ8DQ5DRMRKHXZ6",
      object_kind: "logical_entity",
      change: "modified",
      name_before: "trade",
      name_after: "trade",
      renamed: false,
      severity: "breaking",
      path: "logical/entities/trade.yaml",
      fields: [
        {
          field: "attributes[01KZ2Z70D8HAHT89DVSHBQ7S8E]",
          kind: "attribute_removed",
          severity: "breaking",
          label: "Attribute settle_dt removed",
          detail: "date · nullable",
          before: "settle_dt",
          after: null,
          breaks: [
            { model: "dim_trade", target: "warehouse", materialization: "table" },
            { model: "fct_settlement", target: "warehouse", materialization: "incremental" },
          ],
        },
      ],
    },
  ],
};

export interface ClassificationDoc {
  routes: string[];
  primary: string;
  primary_name: string;
  reviewers: string[];
  reviewers_actual: string[] | null;
  gates: string[];
}

/** Route B wins: _PRECEDENCE is B > E > C > A, so a mixed PR inherits the
 *  strictest gate — which is what the split advice in M6 is about. */
export const CLASSIFICATION: ClassificationDoc = {
  routes: ["A", "B"],
  primary: "B",
  primary_name: "Structure",
  reviewers: ["data-architects", "analytics-engineers"],
  reviewers_actual: ["@acme/data-architecture"],
  gates: ["mdl validate", "mdl generate --dry-run", "mdl drift --check"],
};

/** The meaning-only subset, for the "propose separately" affordance. */
export const CLASSIFICATION_A: ClassificationDoc = {
  routes: ["A"],
  primary: "A",
  primary_name: "Meaning",
  reviewers: ["data-stewards"],
  reviewers_actual: ["@acme/glossary-council"],
  gates: ["mdl validate", "mdl ontology check"],
};

export interface ConflictDoc {
  base: string;
  stale: boolean;
  ahead: number;
  behind: number;
  clean: boolean;
  files: { path: string; clean: boolean; conflicts: string[] }[];
}

export const CONFLICTS_CLEAN: ConflictDoc = {
  base: "main",
  stale: true,
  ahead: 2,
  behind: 4,
  clean: true,
  files: [],
};

export interface ProposalDoc {
  branch: string;
  title: string;
  created: string;
  pushed: boolean;
  merged: boolean;
  ahead: number;
  behind: number;
  summary: string;
  route: string;
  route_name: string;
  conflicts: boolean;
  conflict_detail?: string;
  pr: { number: number; url: string; state: string; reviews: string | null } | null;
}

export const PROPOSALS: ProposalDoc[] = [
  {
    branch: "sme/a.hough/clarify-counterparty",
    title: "Clarify Counterparty definition",
    created: "2 days ago",
    pushed: true,
    merged: false,
    ahead: 1,
    behind: 4,
    summary: "1 definition changed in Counterparty",
    route: "A",
    route_name: "Meaning",
    conflicts: false,
    pr: {
      number: 41,
      url: "https://github.com/acme/pension-ibor/pull/41",
      state: "OPEN",
      reviews: "CHANGES_REQUESTED",
    },
  },
  {
    branch: "sme/a.hough/apra-stress-testing",
    title: "Stress-testing use-case view",
    created: "5 days ago",
    pushed: true,
    merged: false,
    ahead: 3,
    behind: 11,
    summary: "12 objects added to UseCase-ApraStressTesting",
    route: "A",
    route_name: "Meaning",
    conflicts: true,
    conflict_detail:
      "main has changed and your edit to Counterparty's definition now conflicts.",
    pr: { number: 44, url: "https://github.com/acme/pension-ibor/pull/44", state: "OPEN", reviews: null },
  },
  {
    branch: "sme/a.hough/cpty-synonym",
    title: "Add CPTY synonym",
    created: "4 Sep",
    pushed: true,
    merged: true,
    ahead: 0,
    behind: 0,
    summary: "1 synonym added to Counterparty",
    route: "A",
    route_name: "Meaning",
    conflicts: false,
    pr: { number: 38, url: "https://github.com/acme/pension-ibor/pull/38", state: "MERGED", reviews: "APPROVED" },
  },
  {
    branch: "sme/a.hough/position-notes",
    title: "Document position measures",
    created: "just now",
    pushed: false,
    merged: false,
    ahead: 1,
    behind: 0,
    summary: "2 definitions added on position",
    route: "A",
    route_name: "Meaning",
    conflicts: false,
    pr: null,
  },
];

/** The rename comparison (M4): the same change, both ways. */
export const RAW_GIT_DIFF = `diff --git a/conceptual/entities/counterparty.yaml b/conceptual/entities/legal-entity.yaml
similarity index 68%
rename from conceptual/entities/counterparty.yaml
rename to conceptual/entities/legal-entity.yaml
--- a/conceptual/entities/counterparty.yaml
+++ /dev/null
-id: 01KZ2659638XNEK72JK6PGQA7P
-kind: conceptual_entity
-name: Counterparty
-subject_area: 01KZ265963K1SX5TK770VJEYHD
-definition: >
-  A legal person with whom the firm has or
-  may have a contractual obligation.
-stewardship:
-  owner: risk-data-office
-  steward: a.hough
-synonyms: [Counterparty, CPTY]
--- /dev/null
+++ b/conceptual/entities/legal-entity.yaml
+id: 01KZ2659638XNEK72JK6PGQA7P
+kind: conceptual_entity
+name: Legal Entity
+subject_area: 01KZ265963K1SX5TK770VJEYHD
+definition: >
+  A legal person with whom the firm has, or may
+  come to have, a contractual obligation arising
+  from a trade.
+stewardship:
+  owner: risk-data-office
+  steward: a.hough
+synonyms: [Counterparty, CPTY]`;

/** Minimal ERD fixture for the Diagram tab (M3). */
export interface MockNode {
  id: string;
  name: string;
  x: number;
  y: number;
  severity: Severity | null; // null = unchanged context
  ghost?: boolean; // removed -> dashed outline
  note?: string;
}

export const ERD_NODES: MockNode[] = [
  { id: "benchmark", name: "Benchmark", x: 40, y: 150, severity: null, ghost: true, note: "removed" },
  { id: "legal_entity", name: "Legal Entity", x: 300, y: 40, severity: "cosmetic", note: "renamed" },
  { id: "portfolio", name: "Portfolio", x: 580, y: 40, severity: null },
  { id: "trade", name: "Trade", x: 300, y: 240, severity: "breaking", note: "− settle_dt" },
  { id: "position", name: "Position", x: 580, y: 240, severity: null },
];

export const ERD_EDGES: [string, string][] = [
  ["benchmark", "legal_entity"],
  ["legal_entity", "portfolio"],
  ["legal_entity", "trade"],
  ["trade", "position"],
  ["portfolio", "position"],
];
