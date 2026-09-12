import * as vscode from "vscode";
import { findMdl, findModelDir, runMdl } from "./mdl";

/** The `@modelith` chat participant, for Copilot Chat's ASK mode — where MCP tools
 * structurally cannot run (no agentic loop to invoke them from). It answers about
 * the model itself: entities, relationships, ontology alignment.
 *
 * The handler is solely responsible for the response, so it does basic intent
 * routing here rather than expecting the model to pick sub-tasks. It reaches the
 * model through the SAME query layer the MCP server exposes, via `mdl model …`
 * JSON reads — one query layer in `packages/core`, two frontends, no drift. */

interface ModelContext {
  project: string;
  counts: { entities: number; relationships: number; subject_areas: number; terms: number };
  subject_areas: { name: string; definition: string | null; entities: string[] }[];
  unassigned_entities: string[];
  relationships: { name: string; from: string; to: string }[];
}

/** Run an `mdl model …` read and parse its JSON stdout. Throws with the CLI's own
 * message on a non-zero exit so the handler can surface it. */
async function readModel<T>(dir: string, args: string[]): Promise<T> {
  const bin = await findMdl(dir);
  const r = await runMdl(bin, [...args, "-m", dir], dir);
  if (r.code !== 0) {
    // read commands print an {error} object on stdout, else the message is on stderr
    try {
      const obj = JSON.parse(r.stdout);
      if (obj?.error) throw new Error(obj.error);
    } catch {
      /* fall through */
    }
    throw new Error(r.stderr.trim() || `mdl ${args.join(" ")} failed`);
  }
  return JSON.parse(r.stdout) as T;
}

export function registerChatParticipant(ctx: vscode.ExtensionContext): void {
  // The chat API is stable from VS Code 1.90+, but guard so activation never
  // crashes on an older host that lacks it (as the MCP guard does for 1.99+).
  if (!vscode.chat?.createChatParticipant) return;

  const handler: vscode.ChatRequestHandler = async (request, _context, stream, _token) => {
    const dir = await findModelDir();
    if (!dir) {
      stream.markdown(
        "No Modelith model found in this workspace (looked for `mdl-project.yaml`). " +
          "Set `modelith.modelDir` if it lives somewhere unusual.",
      );
      return;
    }

    try {
      if (request.command === "explain") {
        await explainEntity(dir, request.prompt.trim(), stream);
        return;
      }
      if (request.command === "list") {
        await listEntities(dir, stream);
        return;
      }
      // No slash command: infer intent from the prompt. A single entity name that
      // matches the model is treated as "explain it"; otherwise summarise + point
      // at the commands. Kept deliberately simple — Ask mode has no tool loop.
      const named = request.prompt.trim();
      if (named && (await tryExplain(dir, named, stream))) return;
      await summarise(dir, request.prompt, stream);
    } catch (e) {
      stream.markdown(`\n\n⚠️ ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const participant = vscode.chat.createChatParticipant("modelith.chat", handler);
  participant.iconPath = undefined;
  ctx.subscriptions.push(participant);
}

async function explainEntity(
  dir: string,
  name: string,
  stream: vscode.ChatResponseStream,
): Promise<void> {
  if (!name) {
    stream.markdown("Usage: `@modelith /explain <entity name>`");
    return;
  }
  if (!(await tryExplain(dir, name, stream))) {
    stream.markdown(`No entity named **${name}** in the model. Try \`@modelith /list\`.`);
  }
}

/** Render one entity if it exists; return false if it does not. */
async function tryExplain(
  dir: string,
  name: string,
  stream: vscode.ChatResponseStream,
): Promise<boolean> {
  interface Entity {
    name: string;
    definition: string | null;
    pattern: string | null;
    conceptual: { name: string; ontology: string | null; ontology_layer: string | null } | null;
    attributes: { name: string; domain: string | null; nullable: boolean; ontology: string | null }[];
    relationships: { name: string; from: string; to: string }[];
  }
  let e: Entity;
  try {
    e = await readModel<Entity>(dir, ["model", "entity", name]);
  } catch {
    return false; // unknown entity → let the caller decide the message
  }
  stream.markdown(`### ${e.name}\n`);
  if (e.definition) stream.markdown(`${e.definition}\n\n`);
  if (e.conceptual?.ontology) {
    stream.markdown(`**Aligned to** \`${e.conceptual.ontology}\``);
    if (e.conceptual.ontology_layer) stream.markdown(` _(${e.conceptual.ontology_layer} layer)_`);
    stream.markdown("\n\n");
  }
  if (e.attributes.length) {
    stream.markdown("**Attributes**\n");
    for (const a of e.attributes) {
      const bits = [a.domain ?? "—", a.nullable ? "nullable" : "required"];
      if (a.ontology) bits.push(`→ \`${a.ontology}\``);
      stream.markdown(`- \`${a.name}\` (${bits.join(", ")})\n`);
    }
    stream.markdown("\n");
  }
  if (e.relationships.length) {
    stream.markdown("**Relationships**\n");
    for (const r of e.relationships) stream.markdown(`- ${r.from} → ${r.to} (${r.name})\n`);
  }
  return true;
}

async function listEntities(dir: string, stream: vscode.ChatResponseStream): Promise<void> {
  interface Row {
    name: string;
    definition: string | null;
    attribute_count: number;
    subject_area: string | null;
  }
  const rows = await readModel<Row[]>(dir, ["model", "entities"]);
  if (!rows.length) {
    stream.markdown("The model has no entities yet.");
    return;
  }
  stream.markdown(`The model has **${rows.length}** entities:\n\n`);
  for (const r of rows) {
    const area = r.subject_area ? ` _(${r.subject_area})_` : "";
    stream.markdown(`- **${r.name}**${area} — ${r.attribute_count} attribute(s)\n`);
  }
}

async function summarise(
  dir: string,
  prompt: string,
  stream: vscode.ChatResponseStream,
): Promise<void> {
  const ctx = await readModel<ModelContext>(dir, ["model", "context"]);
  stream.markdown(
    `**${ctx.project}** — ${ctx.counts.entities} entities, ` +
      `${ctx.counts.relationships} relationships, ${ctx.counts.subject_areas} subject area(s).\n\n`,
  );
  if (ctx.subject_areas.length) {
    stream.markdown("**Subject areas**\n");
    for (const sa of ctx.subject_areas) {
      stream.markdown(`- **${sa.name}**: ${sa.entities.join(", ") || "(empty)"}\n`);
    }
    stream.markdown("\n");
  }
  if (ctx.unassigned_entities.length) {
    stream.markdown(`_Not in any subject area:_ ${ctx.unassigned_entities.join(", ")}\n\n`);
  }
  stream.markdown(
    "Ask about a specific entity by name, or use `@modelith /explain <entity>` and " +
      "`@modelith /list`. For editing, switch Copilot Chat to **Agent mode** — the " +
      "Modelith tools (create/update entities, ontology search) run there.",
  );
  if (prompt.trim()) {
    stream.markdown(`\n\n_(I answer from the model directly, so I focused on its structure.)_`);
  }
}
