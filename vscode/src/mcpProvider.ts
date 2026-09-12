import * as vscode from "vscode";
import { findMdl, findModelDir } from "./mdl";

/** Register the bundled Modelith MCP server for Copilot Chat AGENT mode.
 *
 * Unlike the spec's Node-module sketch, the server IS the Python `mdl mcp`
 * subcommand — `packages/core` is Python, so a Node module cannot compile against
 * it, and every other integration here (LSP, canvas) already runs by spawning the
 * `mdl` CLI. We register an stdio server that runs `mdl mcp --repo <workspace>`,
 * resolving the binary through the same `findMdl` detection the LSP uses. One
 * engine, invoked the one way this extension invokes it everywhere.
 *
 * A programmatically registered server has no implicit repo scope (a committed
 * .vscode/mcp.json would), so we pass the workspace model dir explicitly.
 *
 * `registerMcpServerDefinitionProvider` only exists from VS Code 1.99+. An
 * unguarded call has been seen crashing activation on older hosts and taking the
 * LSP + canvas down with it — so optional-chain the API and no-op when absent. */
export function registerMcpProvider(ctx: vscode.ExtensionContext): void {
  const lm = vscode.lm as typeof vscode.lm & {
    registerMcpServerDefinitionProvider?: (
      id: string,
      provider: {
        provideMcpServerDefinitions: () => vscode.ProviderResult<unknown[]>;
      },
    ) => vscode.Disposable;
  };
  const Stdio = (
    vscode as unknown as { McpStdioServerDefinition?: new (...a: unknown[]) => unknown }
  ).McpStdioServerDefinition;
  if (!lm?.registerMcpServerDefinitionProvider || !Stdio) return;

  ctx.subscriptions.push(
    lm.registerMcpServerDefinitionProvider("modelith.mcp", {
      provideMcpServerDefinitions: async () => {
        const dir = (await findModelDir()) ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!dir) return [];
        // Resolve the mdl invocation the same way the LSP does (explicit setting →
        // .venv → PATH → well-known → `uv run mdl`). `bin.args` is the prefix (e.g.
        // ["run","mdl"] for uv); the server command follows.
        let cmd: string;
        let args: string[];
        try {
          const bin = await findMdl(dir);
          cmd = bin.cmd;
          args = [...bin.args, "mcp", "--repo", dir];
        } catch {
          return []; // mdl not found — the LSP path already surfaces this to the user
        }
        return [new (Stdio as new (...a: unknown[]) => unknown)("modelith", cmd, args)];
      },
    }),
  );
}
