import * as vscode from "vscode";
import { findMdl, findModelDir, runMdl } from "./mdl";

/** The `@modelith` chat participant, for Copilot Chat's ASK mode — where MCP tools
 * structurally cannot run (no agentic loop to invoke them from). It answers about
 * the model itself: entities, relationships, ontology alignment.
 *
 * Intent handling is LLM-backed. The chat-participant API hands us `request.model`
 * — the user's own already-authenticated Copilot model — so for free-text asks we
 * GROUND with the real model facts (fetched through the same `mdl model` query
 * layer the MCP server uses) and let that model compose the answer, pinned to the
 * facts so it can't invent entities. Exact `/list` and `/explain <name>` keep a
 * fast deterministic template; anything else — a question, a typo'd name, a
 * multi-part ask — goes through the model. If no model is available (rare in
 * Copilot Chat), a heuristic name match is the fallback, so the participant still
 * works. One query layer in `packages/core`, two frontends, no drift. */

interface ModelContext {
  project: string;
  counts: { entities: number; relationships: number; subject_areas: number; terms: number };
  subject_areas: { name: string; definition: string | null; entities: string[] }[];
  unassigned_entities: string[];
  relationships: { name: string; from: string; to: string }[];
}

interface EntityRow {
  name: string;
  definition: string | null;
  attribute_count: number;
  subject_area: string | null;
}

interface EntityDetail {
  name: string;
  definition: string | null;
  pattern: string | null;
  conceptual: { name: string; ontology: string | null; ontology_layer: string | null } | null;
  keys: { name: string; type: string; columns: string[] }[];
  attributes: { name: string; domain: string | null; nullable: boolean; ontology: string | null }[];
  relationships: { name: string; from: string; to: string }[];
}

interface EntitiesDetail {
  project: string;
  total: number;
  shown: number;
  truncated: boolean;
  entities: {
    name: string;
    attributes: string[];
    keys: { type: string; columns: string[] }[];
    relationships: string[];
    ontology: string | null;
  }[];
}

/** A model-WIDE question needs every entity's attributes + keys, not just named
 * ones — normalization ("BCNF", "3NF", "normalis"), coverage ("which entities",
 * "any entity", "all"/"every"/"each"), or simply a question that names no entity at
 * all. Everything else is targeted and grounds on the mentioned entities. */
function isModelWide(prompt: string, mentioned: string[]): boolean {
  const p = prompt.toLowerCase();
  if (/\b(bcnf|[1-6]nf|normal(is|iz)|denormal)/.test(p)) return true;
  if (/\b(which|any|all|every|each|no)\b.*\b(entit|model|table|key|attribute)/.test(p)) return true;
  return mentioned.length === 0;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Run an `mdl model …` read and parse its JSON stdout. Throws with the CLI's own
 * message on a non-zero exit so the handler can surface it. */
async function readModel<T>(dir: string, args: string[]): Promise<T> {
  const bin = await findMdl(dir);
  const r = await runMdl(bin, [...args, "-m", dir], dir);
  if (r.code !== 0) {
    try {
      const obj = JSON.parse(r.stdout);
      if (obj?.error) throw new Error(obj.error);
    } catch {
      /* not a JSON error object — fall through */
    }
    // A too-old `mdl` (a stale global install predating these commands) doesn't
    // know `mdl model …` and prints Typer's usage dump. Turn that into an
    // actionable message instead of leaking the raw usage text to chat.
    const blob = (r.stdout + r.stderr).toLowerCase();
    if (blob.includes("no such command") || blob.includes("usage: mdl")) {
      throw new Error(
        "Your installed `mdl` is out of date — it doesn't have the `model` command " +
          "these features need. Upgrade it (`uv tool install --force modelith-dbt`, or " +
          "reinstall from your working tree), then reload VS Code.",
      );
    }
    throw new Error(r.stderr.trim() || r.stdout.trim() || `mdl ${args.join(" ")} failed`);
  }
  return JSON.parse(r.stdout) as T;
}

export function registerChatParticipant(ctx: vscode.ExtensionContext): void {
  // The chat API is stable from VS Code 1.90+, but guard so activation never
  // crashes on an older host that lacks it (as the MCP guard does for 1.99+).
  if (!vscode.chat?.createChatParticipant) return;

  const handler: vscode.ChatRequestHandler = async (request, _context, stream, token) => {
    const dir = await findModelDir();
    if (!dir) {
      stream.markdown(
        "No Modelith model found in this workspace (looked for `mdl-project.yaml`). " +
          "Set `modelith.modelDir` if it lives somewhere unusual.",
      );
      return;
    }

    try {
      if (request.command === "list") {
        await renderList(dir, stream);
        return;
      }

      const prompt = request.prompt.trim();
      const rows = await readModel<EntityRow[]>(dir, ["model", "entities"]);
      const names = rows.map((r) => r.name);

      // Fast path: the prompt (after an optional /explain) IS an exact entity name.
      // Render the deterministic card without an LLM round-trip.
      const exact = names.find((n) => n.toLowerCase() === prompt.toLowerCase());
      if (exact) {
        await renderEntity(dir, exact, stream);
        return;
      }

      // Everything else — a question, a name buried in a sentence, a typo — is
      // resolved by the user's Copilot model, grounded in the real model facts.
      if (request.model) {
        await answerWithModel(dir, request, rows, stream, token);
        return;
      }

      // No language model available: fall back to a heuristic name match, then a
      // structural summary, so the participant still does something useful.
      const guessed = guessEntity(prompt, names);
      if (guessed) {
        await renderEntity(dir, guessed, stream);
        return;
      }
      await renderSummary(dir, stream);
    } catch (e) {
      stream.markdown(`\n\n⚠️ ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const participant = vscode.chat.createChatParticipant("modelith.chat", handler);
  participant.iconPath = undefined;
  ctx.subscriptions.push(participant);
}

/** Ground the user's Copilot model with the model's facts and stream its answer.
 *
 * We assemble a compact fact sheet — the entity roster, the condensed model
 * context, and (when the question clearly concerns specific entities) their full
 * detail — and instruct the model to answer ONLY from it. Deterministic data,
 * natural-language reasoning: it handles "what connects to instrument?", "which
 * entities have no ontology alignment?", and a fuzzy "explain the instrument
 * entity, everything about it" alike. */
async function answerWithModel(
  dir: string,
  request: vscode.ChatRequest,
  rows: EntityRow[],
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<void> {
  const names = rows.map((r) => r.name);
  const context = await readModel<ModelContext>(dir, ["model", "context"]);

  // Two grounding strategies, chosen by scope. A model-wide question (normalization,
  // "which entities…", "all/every…") needs every entity's attributes + keys; a
  // targeted one needs only the named entities in full. We never dump the whole
  // model at full fidelity — `mdl model detail` is compact and capped, so a 150-
  // entity model stays inside the context window (with a truncation notice).
  const mentioned = names.filter((n) =>
    new RegExp(`\\b${escapeRegExp(n)}\\b`, "i").test(request.prompt),
  );
  let facts: string;
  let truncationNote = "";
  if (isModelWide(request.prompt, mentioned)) {
    const detail = await readModel<EntitiesDetail>(dir, ["model", "detail", "--limit", "120"]);
    if (detail.truncated) {
      truncationNote =
        `\n\n_(This model has ${detail.total} entities; I grounded on the first ` +
        `${detail.shown}. For a complete answer, ask about a subject area or specific ` +
        "entities.)_";
    }
    facts = JSON.stringify({ context, entities_detail: detail }, null, 2);
  } else {
    const details: EntityDetail[] = [];
    for (const n of mentioned.slice(0, 6)) {
      try {
        details.push(await readModel<EntityDetail>(dir, ["model", "entity", n]));
      } catch {
        /* skip an entity that vanished between the list and the read */
      }
    }
    facts = JSON.stringify({ entities: names, context, detail: details }, null, 2);
  }

  const system =
    "You are Modelith, a data-modeling assistant answering about ONE specific model " +
    "inside VS Code. Answer the user's question using ONLY the JSON facts provided — " +
    "the model's entities, subject areas, relationships, attributes and keys. Attribute " +
    "strings are `name:domain` with a trailing `?` for nullable. Keys list pk / unique / " +
    "alternate key groups; a pk marked inferred came from the legacy business_key " +
    "convention but is still the entity's primary key. For normalization questions: you " +
    "can reason from candidate keys (e.g. an entity whose only key is a single attribute, " +
    "with all other attributes dependent on it, is trivially in BCNF for key-determined " +
    "dependencies), but the model does NOT capture arbitrary functional dependencies — so " +
    "say plainly that a full BCNF/3NF proof needs FDs the model doesn't record, and give " +
    "the key-based assessment you can. Do not invent entities, attributes, keys, or " +
    "alignments that are not in the facts. Be concise, use Markdown, and prefer the " +
    "model's own names verbatim. This is read-only: for editing, point the user to " +
    "Copilot Agent mode where the Modelith tools run.";

  const messages = [
    vscode.LanguageModelChatMessage.User(`${system}\n\nMODEL FACTS:\n${facts}`),
    vscode.LanguageModelChatMessage.User(request.prompt),
  ];

  try {
    const res = await request.model.sendRequest(messages, {}, token);
    for await (const chunk of res.text) stream.markdown(chunk);
    if (truncationNote) stream.markdown(truncationNote);
  } catch (e) {
    // The model call can fail (consent not granted, quota, offline). Fall back to
    // the heuristic so the user still gets an answer, not a dead end.
    const guessed = guessEntity(request.prompt, names);
    if (guessed) {
      await renderEntity(dir, guessed, stream);
      return;
    }
    await renderSummary(dir, stream);
    stream.markdown(
      `\n\n_(Couldn't reach a language model — ${e instanceof Error ? e.message : String(e)} — ` +
        "so I answered from the model's structure directly.)_",
    );
  }
}

/** Heuristic entity pick for the no-LLM fallback: the longest entity name that
 * appears as a word-ish substring of the prompt. Deliberately simple. */
function guessEntity(prompt: string, names: string[]): string | undefined {
  const p = prompt.toLowerCase();
  return names
    .filter((n) => p.includes(n.toLowerCase()))
    .sort((a, b) => b.length - a.length)[0];
}

async function renderEntity(
  dir: string,
  name: string,
  stream: vscode.ChatResponseStream,
): Promise<void> {
  const e = await readModel<EntityDetail>(dir, ["model", "entity", name]);
  stream.markdown(`### ${e.name}\n`);
  if (e.definition) stream.markdown(`${e.definition}\n\n`);
  if (e.conceptual?.ontology) {
    stream.markdown(`**Aligned to** \`${e.conceptual.ontology}\``);
    if (e.conceptual.ontology_layer) stream.markdown(` _(${e.conceptual.ontology_layer} layer)_`);
    stream.markdown("\n\n");
  }
  if (e.keys?.length) {
    stream.markdown("**Keys**\n");
    for (const k of e.keys) {
      stream.markdown(`- ${k.type}: ${k.columns.join(", ") || "—"} (${k.name})\n`);
    }
    stream.markdown("\n");
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
}

async function renderList(dir: string, stream: vscode.ChatResponseStream): Promise<void> {
  const rows = await readModel<EntityRow[]>(dir, ["model", "entities"]);
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

async function renderSummary(dir: string, stream: vscode.ChatResponseStream): Promise<void> {
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
}
