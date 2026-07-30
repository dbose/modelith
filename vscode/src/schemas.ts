import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { findMdl, runMdl } from "./mdl";

/** Export the pydantic-derived JSON Schemas and register them with the Red Hat
 * YAML extension (if installed) so model files get completion + inline
 * validation. Registration is workspace-scoped and idempotent. */
export async function registerSchemas(
  ctx: vscode.ExtensionContext,
  modelDir: string,
): Promise<void> {
  const schemaDir = path.join(ctx.globalStorageUri.fsPath, "schemas");
  fs.mkdirSync(schemaDir, { recursive: true });
  try {
    const bin = await findMdl(modelDir);
    const r = await runMdl(bin, ["export", "json-schema", "-o", schemaDir], modelDir);
    if (r.code !== 0) return;
  } catch {
    return; // schemas are enhancement, never block activation
  }

  const yamlExt = vscode.extensions.getExtension("redhat.vscode-yaml");
  if (!yamlExt) return; // no YAML extension — diagnostics-on-save still covers validation

  const map: Record<string, string> = {};
  const glob = (rel: string) => `**/${rel}`;
  const uri = (name: string) =>
    vscode.Uri.file(path.join(schemaDir, `${name}.schema.json`)).toString();
  map[uri("conceptual_entity")] = glob("conceptual/entities/*.yaml");
  map[uri("subject_area")] = glob("conceptual/subject-areas/*.yaml");
  map[uri("term")] = glob("conceptual/terms/*.yaml");
  map[uri("logical_entity")] = glob("logical/entities/*.yaml");
  map[uri("domain")] = glob("logical/domains/*.yaml");
  map[uri("relationship")] = glob("logical/relationships/*.yaml");
  map[uri("physical_table")] = glob("physical/*/tables/*.yaml");
  map[uri("project")] = glob("mdl-project.yaml");

  const cfg = vscode.workspace.getConfiguration("yaml");
  const existing = cfg.get<Record<string, unknown>>("schemas") ?? {};
  await cfg.update(
    "schemas",
    { ...existing, ...map },
    vscode.ConfigurationTarget.Workspace,
  );
}
