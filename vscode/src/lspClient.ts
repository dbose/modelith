import * as vscode from "vscode";
import {
  LanguageClient,
  TransportKind,
  type LanguageClientOptions,
  type ServerOptions,
} from "vscode-languageclient/node";
import { findMdl, type MdlBin } from "./mdl";

/** Start `mdl lsp` (stdio) — the same server serves Cursor/Windsurf/JetBrains.
 * The extension host runs in the container under devcontainers, so the server
 * does too, next to mdl/dbt. */
export async function startLsp(root: string): Promise<LanguageClient> {
  const bin: MdlBin = await findMdl(root);
  const serverOptions: ServerOptions = {
    command: bin.cmd,
    args: [...bin.args, "lsp"],
    options: { cwd: root },
    transport: TransportKind.stdio,
  };
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "yaml" },
      { scheme: "file", language: "sql" },
      { scheme: "file", language: "jinja-sql" },
    ],
    synchronize: {
      fileEvents: vscode.workspace.createFileSystemWatcher(
        "**/{*.yaml,*.yml,*.sql,manifest.json}",
      ),
    },
  };
  const client = new LanguageClient("modelith", "Modelith", serverOptions, clientOptions);
  await client.start();
  return client;
}

/** Run a server-side command (mdl.lift / mdl.adoptColumn / …). */
export function executeLspCommand(
  client: LanguageClient,
  command: string,
  args: unknown[],
): Thenable<unknown> {
  return client.sendRequest("workspace/executeCommand", { command, arguments: args });
}
