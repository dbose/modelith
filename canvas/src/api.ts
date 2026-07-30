import type { DiagnosticsDoc, ModelDoc } from "./types";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

export const fetchModel = () => get<ModelDoc>("/api/model");
export const fetchDiagnostics = () => get<DiagnosticsDoc>("/api/diagnostics");
