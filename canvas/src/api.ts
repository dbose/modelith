import type {
  CommandResponse,
  CoverageDoc,
  Decision,
  DiagnosticsDoc,
  GitStatus,
  ModelDoc,
  OntologySource,
  StackDoc,
  TermCard,
  TermDetail,
} from "./types";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await r.json()) as T & { error?: string };
  if (!r.ok) throw new ApiError(data.error ?? `${path}: ${r.status}`, r.status);
  return data;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export const fetchModel = () => get<ModelDoc>("/api/model");
export const fetchDiagnostics = () => get<DiagnosticsDoc>("/api/diagnostics");

// ontology (E1 + spec §4 two-phase browse)
export const ontologySearch = (q: string, within?: string) =>
  get<{
    results: TermCard[];
    loaded_terms: number;
    vocabularies: string[];
    within: string;
  }>(
    `/api/ontology/search?q=${encodeURIComponent(q)}` +
      (within ? `&within=${encodeURIComponent(within)}` : ""),
  );
export const ontologySources = () =>
  get<{ ontologies: OntologySource[] }>("/api/ontology/ontologies");
export const ontologyTerm = (ref: string) =>
  get<TermDetail>(`/api/ontology/term?ref=${encodeURIComponent(ref)}`);
export const ontologyStack = () => get<StackDoc>("/api/ontology/stack");
export const ontologyCoverage = () => get<CoverageDoc>("/api/ontology/coverage");

// mutation (E2)
export const sendCommand = (op: string, payload: Record<string, unknown>, fingerprint: string) =>
  post<CommandResponse>("/api/command", { op, payload, fingerprint });

// git panel
export const gitStatus = () => get<GitStatus>("/api/git/status");
export const gitDiff = () => get<{ diff: string; untracked: string[] }>("/api/git/diff");
export const gitCommit = (message: string) =>
  post<{ ok: boolean; sha?: string }>("/api/git/commit", { message });
export const gitDiscard = () => post<{ ok: boolean }>("/api/git/discard", {});

// decisions ledger
export const fetchDecisions = () => get<{ decisions: Decision[] }>("/api/decisions");
export const setDecisionVerdict = (signalKey: string, verdict: "accepted" | "rejected") =>
  post<{ ok: boolean }>(`/api/decisions/${signalKey}/verdict`, { verdict });

// glossary (SME app)
import type { GlossaryConfig, GlossaryDoc, GlossaryTerm, ProposeResult } from "./types";

export const fetchGlossary = (params: { subject_area?: string; q?: string } = {}) => {
  const qs = new URLSearchParams();
  if (params.subject_area) qs.set("subject_area", params.subject_area);
  if (params.q) qs.set("q", params.q);
  return get<GlossaryDoc>(`/api/glossary/terms?${qs.toString()}`);
};
export const fetchGlossaryTerm = (id: string) => get<GlossaryTerm>(`/api/glossary/term/${id}`);
export const fetchGlossaryConfig = () => get<GlossaryConfig>("/api/glossary/config");
export const fetchBranch = () => get<{ branch: string | null }>("/api/git/branch");
export const proposeChanges = (payload: {
  user: string;
  slug: string;
  title: string;
  body: string;
  changes: { op: string; payload: Record<string, unknown> }[];
}) => post<ProposeResult>("/api/git/propose", payload);
