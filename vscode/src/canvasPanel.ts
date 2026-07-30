import * as cp from "node:child_process";
import * as net from "node:net";
import * as vscode from "vscode";
import { findMdl, type MdlBin } from "./mdl";

/** Manages one `mdl serve` process and shows the canvas per the
 * `modelith.canvas.display` setting:
 *   - "tab":      webview editor tab embedding the canvas (iframe)
 *   - "external": system browser via a forwarded port
 * Both paths resolve the URL through vscode.env.asExternalUri, so inside a
 * devcontainer / SSH remote VS Code tunnels the port automatically — the
 * extension host (and the server) run in the container (extensionKind:
 * workspace), the UI runs wherever the user is. */
export class CanvasManager {
  private proc: cp.ChildProcess | undefined;
  private port: number | undefined;
  private servedDir: string | undefined;
  private panel: vscode.WebviewPanel | undefined;

  constructor(private out: vscode.OutputChannel) {}

  async open(modelDir: string): Promise<void> {
    const cfg = vscode.workspace.getConfiguration("modelith");
    await this.ensureServer(modelDir);
    const local = vscode.Uri.parse(`http://127.0.0.1:${this.port}/`);
    const external = await vscode.env.asExternalUri(local);

    if (cfg.get<string>("canvas.display") === "external") {
      await vscode.env.openExternal(external);
      vscode.window.setStatusBarMessage(`Modelith canvas: ${external.toString()}`, 5000);
      return;
    }

    if (this.panel) {
      this.panel.reveal();
      return;
    }
    this.panel = vscode.window.createWebviewPanel(
      "modelithCanvas",
      "Modelith Canvas",
      vscode.ViewColumn.One,
      { enableScripts: true, retainContextWhenHidden: true },
    );
    this.panel.iconPath = undefined;
    this.panel.webview.html = this.html(external.toString());
    this.panel.onDidDispose(() => (this.panel = undefined));
  }

  private async ensureServer(modelDir: string): Promise<void> {
    if (this.proc && !this.proc.killed && this.servedDir === modelDir) return;
    this.stop();

    const cfg = vscode.workspace.getConfiguration("modelith");
    const configured = cfg.get<number>("canvas.port") ?? 0;
    this.port = configured > 0 ? configured : await freePort();
    const bin = await findMdl(modelDir);
    const args = ["serve", "-m", ".", "--port", String(this.port)];
    if (cfg.get<boolean>("canvas.readOnly")) args.push("--read-only");

    this.out.appendLine(`[canvas] ${bin.label} ${args.join(" ")} (cwd ${modelDir})`);
    this.proc = spawnMdl(bin, args, modelDir);
    this.servedDir = modelDir;
    this.proc.stdout?.on("data", (d) => this.out.append(`[serve] ${d}`));
    this.proc.stderr?.on("data", (d) => this.out.append(`[serve] ${d}`));
    this.proc.on("exit", (code) => {
      this.out.appendLine(`[canvas] server exited (${code})`);
      this.proc = undefined;
    });
    await waitForPort(this.port, 20000);
  }

  stop(): void {
    if (this.proc && !this.proc.killed) this.proc.kill();
    this.proc = undefined;
  }

  dispose(): void {
    this.stop();
    this.panel?.dispose();
  }

  private html(url: string): string {
    // The webview embeds the (possibly tunnelled) canvas URL. The iframe keeps
    // its own state; the toolbar link opens the same URL in a full browser tab.
    return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    html, body { height: 100%; margin: 0; padding: 0; background: #0b0f16; }
    .bar { display: flex; justify-content: flex-end; padding: 2px 8px; }
    .bar a { color: #5eead4; font: 11px sans-serif; text-decoration: none; }
    iframe { border: 0; width: 100%; height: calc(100% - 22px); }
  </style>
</head>
<body>
  <div class="bar"><a href="${url}" title="Open in browser">open in browser ↗</a></div>
  <iframe src="${url}" allow="clipboard-read; clipboard-write"></iframe>
</body>
</html>`;
  }
}

function spawnMdl(bin: MdlBin, args: string[], cwd: string): cp.ChildProcess {
  return cp.spawn(bin.cmd, [...bin.args, ...args], { cwd });
}

function freePort(): Promise<number> {
  return new Promise((res, rej) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      srv.close(() => (port ? res(port) : rej(new Error("no free port"))));
    });
    srv.on("error", rej);
  });
}

function waitForPort(port: number, timeoutMs: number): Promise<void> {
  const start = Date.now();
  return new Promise((res, rej) => {
    const tryOnce = () => {
      const sock = net.connect(port, "127.0.0.1");
      sock.once("connect", () => {
        sock.destroy();
        res();
      });
      sock.once("error", () => {
        sock.destroy();
        if (Date.now() - start > timeoutMs) rej(new Error(`canvas server did not start on :${port}`));
        else setTimeout(tryOnce, 300);
      });
    };
    tryOnce();
  });
}
