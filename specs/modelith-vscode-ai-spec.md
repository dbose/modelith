# Modelith — VS Code MCP Server + Chat Participant Spec

Two separate extension integrations, aimed at Copilot Chat's two different
modes. Both ship inside the Modelith VS Code extension; neither requires
the user to create or edit a config file.

---

## 1. Bundled MCP server (Agent mode)

Registers automatically on extension activation — no `.vscode/mcp.json`
needed.

**Registration:**

```jsonc
// package.json
"contributes": {
  "mcpServerDefinitionProviders": [
    { "id": "modelith.mcp", "label": "Modelith" }
  ]
}
```

```typescript
// extension.ts — activate()
context.subscriptions.push(
  vscode.lm.registerMcpServerDefinitionProvider('modelith.mcp', {
    provideMcpServerDefinitions: async () => {
      const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      return [
        new vscode.McpStdioServerDefinition(
          'modelith', 'node',
          [context.asAbsolutePath('dist/mcp/server.js'), '--repo', workspaceRoot]
        )
      ];
    }
  })
);
```

- Bundle the server module inside the extension's own `dist/`, compiled
  directly against `packages/core`. Do not shell out to a separately
  installed `mdl` CLI — that would reintroduce an install step through the
  back door and defeat the point of bundling.
- Pass the active workspace folder explicitly via `--repo`. A
  programmatically registered server has no implicit repo scope the way a
  committed `.vscode/mcp.json` does — `provideMcpServerDefinitions` must
  read it from `vscode.workspace.workspaceFolders` itself.
- Guard the registration call. `registerMcpServerDefinitionProvider` only
  exists from VS Code 1.99+. Use
  `vscode.lm?.registerMcpServerDefinitionProvider` (optional chaining) or
  set `engines.vscode: "^1.99.0"`. An unguarded call has been observed
  crashing extension activation entirely on older hosts, taking down the
  LSP and canvas features alongside MCP, not just MCP itself.

**Tools to expose** (writes reuse the LSP's existing validation path, no
separate write logic):

| Tool | Purpose |
|---|---|
| `list_entities(subject_area?)` | Grounding — what already exists |
| `get_entity(name)` | Full entity detail incl. relationships, `ontology_refs` |
| `search_ontology(text, layer?)` | Proxies the `OntologyResolver` interface |
| `create_entity(draft)` | Direct local write, validated before commit |
| `update_entity(name, changes)` | Direct local write, validated before commit |
| `get_model_context()` | Condensed model summary sized for a chat context window |
| `validate(entity)` | Inline lint feedback before anything is written |

- Direct local write is intentional here, not PR-gated — this is the
  engineer's own checkout, a different trust boundary than the SME app.
- Tools only function when Copilot Chat is in **Agent mode** — tool
  invocation is architecturally a plan/act/observe loop that Ask mode
  doesn't have, so there's no mode-detection code to write. Document the
  requirement (e.g. first-run notification) rather than trying to work
  around it.

---

## 2. `@modelith` chat participant (Ask mode)

Separate extension point, aimed specifically at Ask mode, where MCP tools
structurally cannot run (no agentic loop to invoke them from).

**Registration:**

```jsonc
// package.json
"contributes": {
  "chatParticipants": [{
    "id": "modelith.chat",
    "name": "modelith",
    "fullName": "Modelith",
    "description": "Ask about entities, relationships, and ontology alignment in this model."
  }]
}
```

```typescript
// extension.ts — activate()
const participant = vscode.chat.createChatParticipant('modelith.chat', handler);
```

```typescript
const handler: vscode.ChatRequestHandler = async (
  request: vscode.ChatRequest,
  context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken
) => {
  // parse request.prompt / request.command, call the same core query
  // functions the MCP tools in §1 use, stream the answer back
};
```

- The handler is **solely responsible** for the response once invoked —
  no blended reasoning with Copilot's own tool-picking the way Agent-mode
  tools work. Implement basic intent handling directly rather than
  expecting the model to route sub-tasks for you.
- Optional slash commands for common asks, e.g. `@modelith /explain
  pension_warehouse`.
- **Share the query layer with §1, don't duplicate it.** Both the MCP
  tools and this handler should call the same underlying functions in
  `packages/core` (`list_entities`, `get_entity`, ontology resolution,
  etc.) — one query layer, two frontends. This keeps behavior consistent
  regardless of which mode the user happens to be in, and avoids having
  to reconcile two implementations later when one drifts from the other.

## Build order

1. Confirm/stabilize the core query functions both integrations will call
   (`list_entities`, `get_entity`, `get_model_context`, ontology search) —
   build this once in `packages/core`, not inside either integration.
2. MCP server (§1) — wraps the query layer as tools, adds the write path
   (`create_entity`/`update_entity`/`validate`).
3. `@modelith` chat participant (§2) — wraps the same query layer as a
   request handler. Built last and on top of §1's query layer specifically
   so there's nothing left to duplicate.
