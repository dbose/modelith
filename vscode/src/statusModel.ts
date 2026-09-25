import * as vscode from "vscode";
import { type MdlBin, findMdl, findManifestPath, findModelDir, runMdl } from "./mdl";

/** Mirrors `mdl_core.status.NextAction` / `WorkspaceStatus` (the JSON that
 * `mdl status --format json` prints). One assessor, both surfaces. */
export interface NextAction {
  id: string;
  title: string;
  detail: string;
  severity: "info" | "recommended" | "attention";
  command: string | null;
  cli: string | null;
}

export interface WorkspaceStatus {
  stage: string;
  summary: string;
  entity_count: number;
  pending_count: number;
  pending_by_confidence: Record<string, number>;
  has_manifest: boolean;
  docs_generated: boolean;
  docs_stale: boolean;
  missing_definitions: number;
  unaligned_terms: number;
  next_actions: NextAction[];
}

/**
 * Runs `mdl status --format json` and caches the result, so the Reverse, Drift, and
 * Config views all render next-actions from ONE assessment rather than each guessing.
 * Fail-soft: any error leaves `status` undefined and the views fall back to their own
 * resting text. Fires `onDidChange` after every refresh so subscribed trees redraw.
 */
export class StatusModel {
  private readonly emitter = new vscode.EventEmitter<WorkspaceStatus | undefined>();
  readonly onDidChange = this.emitter.event;
  private current: WorkspaceStatus | undefined;

  constructor(private readonly out: vscode.OutputChannel) {}

  get status(): WorkspaceStatus | undefined {
    return this.current;
  }

  /** True once at least one successful assessment has completed (so a view can tell
   * "not assessed yet" apart from "assessed, nothing to do"). */
  get ready(): boolean {
    return this.current !== undefined;
  }

  async refresh(modelDir?: string): Promise<void> {
    const dir = modelDir ?? (await findModelDir());
    if (!dir) {
      this.current = undefined;
      this.emitter.fire(undefined);
      return;
    }
    try {
      const bin: MdlBin = await findMdl(dir);
      const args = ["status", "--format", "json", "-m", "."];
      // Feed the manifest so the drift next-action is offered when a compiled project
      // is present; harmless when absent.
      const manifest = await findManifestPath();
      if (manifest) args.push("--manifest", manifest);
      const r = await runMdl(bin, args, dir);
      if (r.code === 0 && r.stdout.trim()) {
        this.current = JSON.parse(r.stdout) as WorkspaceStatus;
      } else {
        // A CLI too old to have `mdl status` exits non-zero; degrade quietly.
        this.current = undefined;
        if (r.stderr.trim()) this.out.appendLine(`[status] ${r.stderr.trim()}`);
      }
    } catch (e) {
      this.out.appendLine(`[status] could not assess workspace: ${e}`);
      this.current = undefined;
    }
    this.emitter.fire(this.current);
  }

  /** The single highest-priority next action, if any — what a resting row should show. */
  topAction(): NextAction | undefined {
    return this.current?.next_actions?.[0];
  }

  dispose(): void {
    this.emitter.dispose();
  }
}
