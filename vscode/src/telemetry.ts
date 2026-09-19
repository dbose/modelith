import * as vscode from "vscode";

// Anonymous, opt-in editor-funnel telemetry for the Modelith extension (GTM §4.3).
//
// Distinct from the CLI's telemetry: this captures EDITOR-specific funnel points the
// CLI can't see (walkthrough opened, a step completed, the canvas opened in-editor).
// It is:
//   - Gated on `vscode.env.isTelemetryEnabled` — the user's own VS Code telemetry
//     setting is the consent surface, so we never add a second prompt and always
//     respect a user who turned editor telemetry off.
//   - Dynamic — a listener on `onDidChangeTelemetryEnabled` updates the cached gate
//     immediately, so toggling telemetry mid-session takes effect at once (turning
//     it off stops the very next event; turning it on resumes).
//   - Anonymous — a machine-scoped id (vscode.env.machineId is already non-PII and
//     stable per install), no workspace names/paths/content ever.
//   - Fail-soft — a fire-and-forget fetch with a short timeout; never blocks or
//     throws into the extension.
//
// Same PostHog EU project as the CLI, so editor + CLI events share one funnel keyed
// on the machine/install. Uses global fetch (Node 18+, which the extension targets)
// so there is NO new dependency.

const POSTHOG_HOST = "https://eu.i.posthog.com";
const POSTHOG_KEY = "phc_kKkkkNu4rjarbd2VgWn5dBiEmEGJynAp878ML5uaVHJT";

// Cached enabled state, kept in sync via the change-event listener below.
let telemetryEnabled = false;
let out: vscode.OutputChannel | undefined;

/**
 * Initialize telemetry gating. Seeds the cached state from the current setting and
 * subscribes to `onDidChangeTelemetryEnabled` so a mid-session toggle is honored
 * immediately. Call once from activate(); the listener is disposed with the context.
 */
export function initTelemetry(ctx: vscode.ExtensionContext, channel?: vscode.OutputChannel): void {
  out = channel;
  try {
    telemetryEnabled = vscode.env.isTelemetryEnabled;
    ctx.subscriptions.push(
      vscode.env.onDidChangeTelemetryEnabled((isEnabled) => {
        telemetryEnabled = isEnabled;
        out?.appendLine(
          `[telemetry] usage analytics ${isEnabled ? "enabled" : "disabled"} (VS Code setting changed)`,
        );
      }),
    );
  } catch {
    telemetryEnabled = false; // a host without the API -> stay off
  }
}

/** Fire one anonymous event to PostHog, best-effort. Never throws, never blocks. */
export function track(event: string, properties: Record<string, unknown> = {}): void {
  if (!telemetryEnabled) return;
  const body = {
    api_key: POSTHOG_KEY,
    event,
    // machineId is a stable, non-PII per-install id VS Code already exposes.
    distinct_id: vscode.env.machineId,
    properties: {
      ...properties,
      surface: "vscode",
      $process_person_profile: false,
    },
  };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 1500);
  // Fire and forget — swallow everything.
  void fetch(`${POSTHOG_HOST}/capture/`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: ctl.signal,
  })
    .catch(() => {})
    .finally(() => clearTimeout(timer));
}
