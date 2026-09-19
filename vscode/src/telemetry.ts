import { randomUUID, createHash } from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";

// Anonymous editor-funnel telemetry for the Modelith extension (GTM §4.3), UNIFIED
// with the CLI so both surfaces form ONE product funnel.
//
// Identity is SHARED: both the CLI and the extension use the same hashed install id
// from ~/.modelith/telemetry.json as the PostHog distinct_id, so a user's editor
// journey (walkthrough -> demo) and their CLI work (validate/reverse/generate) join
// one funnel. The extension mints that id (sha256 of a uuid4, matching the CLI's
// format) if the file doesn't exist yet, so an extension-first user still shares an
// id with any later CLI usage.
//
// Consent is per-surface, both honored:
//   - Extension: `vscode.env.isTelemetryEnabled` — the editor's own telemetry
//     setting IS the consent. A user with VS Code telemetry on has opted into
//     anonymous editor analytics; if they turn it off, we send nothing.
//   - CLI: its own interactive opt-in (`enabled` in the same file) — unchanged.
//   Writing the id (plumbing) is independent of the CLI's `enabled` flag (consent),
//   so sharing an id never sends CLI data the CLI didn't consent to.
//
// Never sends model contents, schema, paths, names. Fail-soft: a fire-and-forget
// fetch, short timeout, never blocks or throws. Global fetch (Node 18+) — no dep.

const POSTHOG_HOST = "https://eu.i.posthog.com";
const POSTHOG_KEY = "phc_kKkkkNu4rjarbd2VgWn5dBiEmEGJynAp878ML5uaVHJT";

const STATE_DIR = path.join(os.homedir(), ".modelith");
const STATE_FILE = path.join(STATE_DIR, "telemetry.json");

let telemetryEnabled = false; // vscode.env.isTelemetryEnabled, kept live
let out: vscode.OutputChannel | undefined;
let cachedId: string | undefined;

/** The shared anonymous install id: sha256 hex of a uuid4 (same format as the CLI).
 * Reuses the id already in ~/.modelith/telemetry.json if present; otherwise mints
 * one and writes it back so the CLI and extension share it. Returns undefined only
 * if the id can't be read or written (then we stay silent). */
function sharedInstallId(): string | undefined {
  if (cachedId) return cachedId;
  try {
    const state = readState();
    const existing = state.install_id;
    if (typeof existing === "string" && existing.length === 64) {
      cachedId = existing;
      return cachedId;
    }
    // Mint an id in the CLI's exact format (sha256 of a uuid4; raw uuid discarded).
    const hashed = createHash("sha256").update(randomUUID()).digest("hex");
    state.install_id = hashed;
    writeState(state);
    cachedId = hashed;
    return cachedId;
  } catch {
    return undefined;
  }
}

function readState(): Record<string, unknown> {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  } catch {
    return {};
  }
}

function writeState(state: Record<string, unknown>): void {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), "utf8");
}

/**
 * Flow VS Code's telemetry consent through to the shared CLI state so that `mdl`
 * runs (including in-VS-Code terminal and standalone) inherit it — the user opted
 * into anonymous telemetry via VS Code, and we honor that across both surfaces.
 *
 * Writes `enabled`/`consented` only to grant (never to REVOKE the CLI's own
 * interactive opt-in): if the CLI already recorded a decision (`consented: true`),
 * we leave it untouched — a user who explicitly answered the CLI prompt owns that
 * choice. We only fill in consent for a file that has none yet, and only to enable.
 * Kill-switches (DO_NOT_TRACK / MODELITH_TELEMETRY_DISABLED) still force the CLI off
 * regardless, so there is always a documented escape hatch.
 */
function syncSharedConsent(enabled: boolean): void {
  try {
    const state = readState();
    // Never override an explicit CLI decision.
    if (state.consented === true) return;
    // Only propagate ENABLE (VS Code telemetry on -> grant). If VS Code telemetry is
    // off we simply don't grant here; we never write enabled:false over an
    // un-decided file, leaving the CLI free to prompt later.
    if (!enabled) return;
    state.enabled = true;
    state.consented = true;
    state.consent_source = "vscode";
    if (typeof state.install_id !== "string" || (state.install_id as string).length !== 64) {
      state.install_id = createHash("sha256").update(randomUUID()).digest("hex");
    }
    writeState(state);
    cachedId = state.install_id as string;
  } catch {
    /* best-effort */
  }
}

/**
 * Initialize gating. Seeds VS Code's telemetry setting, flows that consent through
 * to the shared CLI state, and subscribes to changes so a mid-session toggle takes
 * effect at once. Call once from activate().
 */
export function initTelemetry(ctx: vscode.ExtensionContext, channel?: vscode.OutputChannel): void {
  out = channel;
  try {
    telemetryEnabled = vscode.env.isTelemetryEnabled;
    syncSharedConsent(telemetryEnabled); // grant CLI consent from the editor setting
    ctx.subscriptions.push(
      vscode.env.onDidChangeTelemetryEnabled((isEnabled) => {
        telemetryEnabled = isEnabled;
        syncSharedConsent(isEnabled);
        out?.appendLine(
          `[telemetry] usage analytics ${isEnabled ? "enabled" : "disabled"} (VS Code setting changed)`,
        );
      }),
    );
  } catch {
    telemetryEnabled = false;
  }
}

/** Fire one anonymous event to PostHog under the SHARED install id, best-effort.
 * Emits only when VS Code telemetry is on; never throws, never blocks. */
export function track(event: string, properties: Record<string, unknown> = {}): void {
  if (!telemetryEnabled) return;
  const distinctId = sharedInstallId();
  if (!distinctId) return;
  const body = {
    api_key: POSTHOG_KEY,
    event,
    distinct_id: distinctId, // SHARED with the CLI -> unified funnel
    properties: {
      ...properties,
      surface: "vscode",
      $process_person_profile: false,
    },
  };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 1500);
  void fetch(`${POSTHOG_HOST}/capture/`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal: ctl.signal,
  })
    .catch(() => {})
    .finally(() => clearTimeout(timer));
}
