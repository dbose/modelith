"""Anonymous, opt-in usage telemetry for the `mdl` CLI (GTM §4.3).

This is the OPEN-SOURCE product-analytics emitter: it records which command ran and
whether it succeeded, so we can compute an activation funnel (first `init` ->
`validate` passing within 24h). It is:

- **Opt-in, off by default.** Nothing is sent until a user answers "yes" to a
  one-time prompt, which only appears on an interactive terminal.
- **Anonymous.** The only identifier is a locally generated UUID stored *hashed*
  (sha256); the raw UUID is never persisted or sent. No model contents, schema,
  file paths, project names, repo URLs, arguments, or error messages ever leave the
  machine — only: command name (from an allowlist), success bool, exit code, mdl
  version, and coarse OS/timestamp.
- **Fail-soft.** Every function degrades to a silent no-op on any error and must
  never break a command or add perceptible latency (short timeout, best-effort).

This is deliberately SEPARATE from the per-org OpenTelemetry *audit log*
(`mdl_server/audit.py`), which records named governance WRITE events to the
customer's own OTLP collector. Different purpose, identity, and destination.

The event is built OTel-log-shaped (a stable name + attributes + a distinct_id) and
handed to a pluggable sink; today the sink is PostHog `/capture`, but the event
shape is transport-neutral so an OTLP sink can be added later without changing it.

Disable entirely with `MODELITH_TELEMETRY_DISABLED=1`, `DO_NOT_TRACK=1`, or by
editing `~/.modelith/telemetry.json`.
"""

from __future__ import annotations

import hashlib
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --- Constants (shippable, NOT secret) --------------------------------------------
# PostHog project write key: a CLIENT-side key, safe to ship in open source (it can
# only WRITE events, never read). Replace with the real project key before launch;
# with the placeholder, events are simply dropped by PostHog.
POSTHOG_HOST = "https://eu.i.posthog.com"
POSTHOG_PROJECT_API_KEY = "phc_kKkkkNu4rjarbd2VgWn5dBiEmEGJynAp878ML5uaVHJT"
POSTHOG_CAPTURE_PATH = "/capture/"
TELEMETRY_TIMEOUT = 1.5  # seconds; the POST is best-effort with a short cap

STATE_DIR = Path.home() / ".modelith"  # same user dir as the catalog cache
STATE_FILE = STATE_DIR / "telemetry.json"
SCHEMA_VERSION = 1

# Allowlist for command-name sanitization. Top-level commands plus the sub-app names
# whose second token is also meaningful ("ontology search" etc.). Anything not here
# records as "<unknown>" so a stray/garbage first arg never leaks a path or string.
_TOP_LEVEL = frozenset(
    {
        "init", "validate", "lint", "generate", "reverse", "merge-driver", "classify",
        "unmanage", "drift", "diff", "serve", "studio", "lsp", "mcp",
    }
)
_SUB_APPS = frozenset(
    {"ontology", "emit", "export", "import", "gov", "catalog", "debt",
     "subject-area", "new", "delete", "decisions", "model"}
)


def sanitize_command(argv: list[str]) -> str:
    """Map argv (sys.argv[1:]) to a safe, low-cardinality command label.

    ``["ontology","search","x"]`` -> ``"ontology search"``;
    ``["validate","-m","."]`` -> ``"validate"``; ``["bogus"]`` -> ``"<unknown>"``;
    ``[]`` -> ``"<none>"``. Never returns a path or free-form value.
    """
    if not argv:
        return "<none>"
    # The command must be the FIRST token. If argv starts with an option, there is
    # no command (e.g. `mdl -m x` is not a command) -> "<none>", so an option value
    # like a path can never be mistaken for a command.
    if argv[0].startswith("-"):
        return "<none>"
    first = argv[0]
    if first in _SUB_APPS:
        second = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else ""
        # Only emit the pair if the second token is a real word (sub-commands are
        # lowercase identifiers); otherwise just the sub-app name.
        return f"{first} {second}".strip() if second.isidentifier() or "-" in second else first
    if first in _TOP_LEVEL:
        return first
    return "<unknown>"


# --- State I/O (fail-soft) --------------------------------------------------------
def _load_state() -> dict:
    try:
        import json

        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - missing/corrupt state degrades to empty
        return {}


def _save_state(state: dict) -> None:
    try:
        import json

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_FILE)  # atomic
    except Exception:  # noqa: BLE001 - never fail a command over telemetry state
        pass


def _install_id(state: dict) -> str:
    """A stable, anonymous per-install id: the sha256 hex of a locally generated
    uuid4. Only the HASH is stored/sent — the raw uuid is never persisted."""
    existing = state.get("install_id")
    if isinstance(existing, str) and len(existing) == 64:
        return existing
    raw = uuid.uuid4().hex
    hashed = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    state["install_id"] = hashed  # raw is discarded here, never written
    return hashed


# --- Kill-switches / enablement ---------------------------------------------------
def _forced_off() -> bool:
    """Env kill-switches that force telemetry off with no prompt and no state write."""
    return bool(
        os.environ.get("MODELITH_TELEMETRY_DISABLED")
        or os.environ.get("DO_NOT_TRACK")
        or os.environ.get("CI")
    )


def is_enabled() -> tuple[bool, dict]:
    """Return (enabled, state). Off if a kill-switch is set or consent not granted."""
    if _forced_off():
        return False, {}
    state = _load_state()
    return bool(state.get("enabled")), state


def ensure_consent() -> bool:
    """One-time consent gate. Returns whether telemetry is enabled for this run.

    Prompts only on the first INTERACTIVE command (a real TTY) that hasn't been
    answered yet. Non-TTY invocations (CI, piped, the VS Code extension's constant
    `mdl` shell-outs) never prompt and never emit — this is what keeps telemetry
    off by default.
    """
    if _forced_off():
        return False
    state = _load_state()
    if state.get("consented"):
        return bool(state.get("enabled"))
    # Only prompt on an interactive terminal; otherwise stay silent (off).
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False

    import typer

    typer.echo("")
    typer.secho(
        "Help improve Modelith? It can send anonymous usage stats (which command "
        "ran and whether it passed).",
        fg=typer.colors.CYAN,
    )
    typer.echo("No model contents, schema, names, or paths are ever sent. Opt out "
               "anytime with DO_NOT_TRACK=1.")
    enabled = typer.confirm("Enable anonymous usage telemetry?", default=False)

    new_state = {
        "schema_version": SCHEMA_VERSION,
        "consented": True,
        "enabled": bool(enabled),
        "install_id": _install_id(state),
        "consent_ts": datetime.now(UTC).replace(minute=0, second=0, microsecond=0).isoformat(),
    }
    _save_state(new_state)
    return bool(enabled)


# --- Event build + sink -----------------------------------------------------------
def _mdl_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    for dist in ("modelith-dbt", "modelith-cli"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "unknown"


# AARRR / activation event taxonomy. Distinct semantic events give clean funnels in
# PostHog (each step is its own named event) and let us see WHERE and WHY users drop
# off. A command maps to a semantic event on SUCCESS; failures/other commands fall
# back to the catch-all `command_run`. Every event stays anonymous — no PII, no model
# contents, no paths — only the coarse shape of usage.
#
# Funnel (Acquisition -> Activation -> Retention):
#   cli_installed  (once)      -> Acquisition
#   demo_scaffolded / model_initialized / warehouse_reversed  -> Activation: intent
#   model_validated            -> Activation: first value (the north-star)
#   canvas_opened              -> Activation: the "wow" (emitted at serve/studio start)
#   dbt_generated              -> Activation: output
#   drift_checked              -> Retention signal
#   validation_failed          -> a DROP-OFF reason (with a coarse error_class)
_EVENT_FOR_COMMAND = {
    "validate": "model_validated",
    "reverse": "warehouse_reversed",
    "generate": "dbt_generated",
    "drift": "drift_checked",
    # `init` splits into demo vs real at emit time; `serve`/`studio` emit at startup
    # via emit("canvas_opened") from inside the command, not here.
}


def _base_props() -> dict[str, Any]:
    import platform

    return {
        "mdl_version": _mdl_version(),
        "os": platform.system(),
        "$process_person_profile": False,  # anonymous: no person profile in PostHog
    }


def _post(event_name: str, distinct_id: str, props: dict[str, Any]) -> None:
    """POST one event to PostHog /capture, best-effort (matches client.capture()).

    Swallows its OWN errors so that when several events are sent in one invocation
    (e.g. command_run + model_validated), a single failed/slow POST never blocks the
    others — a network blip must not drop the north-star `model_validated` just
    because an earlier post timed out."""
    try:
        import httpx

        httpx.post(
            f"{POSTHOG_HOST}{POSTHOG_CAPTURE_PATH}",
            json={
                "api_key": POSTHOG_PROJECT_API_KEY,
                "event": event_name,
                "distinct_id": distinct_id,
                "timestamp": datetime.now(UTC)
                .replace(minute=0, second=0, microsecond=0)
                .isoformat(),
                "properties": {**_base_props(), **props},
            },
            timeout=TELEMETRY_TIMEOUT,
        )
    except Exception:  # noqa: BLE001 - one failed post must not block the rest
        pass


def emit(event_name: str, props: dict[str, Any] | None = None) -> None:
    """Emit ONE named funnel event, fire-and-forget. Use for semantic events fired
    from inside a command (e.g. `canvas_opened` at serve startup). No-op unless
    telemetry is enabled. Never raises."""
    try:
        enabled, state = is_enabled()
        if not enabled:
            return
        _post(event_name, _install_id(state), props or {})
    except Exception:  # noqa: BLE001 - best-effort, never fatal
        pass


def _maybe_emit_installed(state: dict, install_id: str) -> None:
    """Fire `cli_installed` exactly once per install, the first time telemetry emits.
    Marked in telemetry.json so it never repeats (Acquisition = first appearance)."""
    if state.get("installed_sent"):
        return
    _post("cli_installed", install_id, {})
    state["installed_sent"] = True
    _save_state(state)


def record(command: str, exit_code: int) -> None:
    """Fire-and-forget, called once per invocation from main(). Emits the Acquisition
    marker on first run, then the semantic activation event for the command (on
    success), always plus the catch-all `command_run` for retention/coverage.

    Entirely wrapped in try/except — telemetry must never break a command."""
    try:
        enabled, state = is_enabled()
        if not enabled:
            return
        install_id = _install_id(state)
        success = exit_code == 0

        # Acquisition: cli_installed once, ever.
        _maybe_emit_installed(state, install_id)

        # Catch-all for retention/coverage — every command, success or not.
        _post("command_run", install_id, {"command": command, "success": success,
                                          "exit_code": exit_code})

        # Semantic activation event (success only), so funnels read cleanly.
        if success:
            if command == "init":
                # demo vs a real model start — very different intent.
                is_demo = "--demo" in sys.argv[1:]
                _post("demo_scaffolded" if is_demo else "model_initialized", install_id, {})
            elif command in _EVENT_FOR_COMMAND:
                _post(_EVENT_FOR_COMMAND[command], install_id, {})
        elif command == "validate":
            # A drop-off reason: validation failed. error_class stays coarse.
            _post("validation_failed", install_id, {})
    except Exception:  # noqa: BLE001 - telemetry is best-effort, never fatal
        pass
