"""Compatibility shim — the mutation engine moved to core so the HTTP server and
the LSP server share one implementation (there is exactly one merge/mutation
engine in this codebase, per the LSP architecture decision)."""

from mdl_core.commands import (  # noqa: F401
    COMMANDS,
    CommandError,
    CommandResult,
    StaleModelError,
    apply_command,
    dir_fingerprint,
    has_errors,
)
