"""Subcommand plugin seam — lets an installed package add `mdl` subcommands.

This is the CLI counterpart of the config-resolver seam in
`mdl_reverse/remote_config.py`: core exposes a stable entry-point group, and a
separately-installed package (e.g. the paid `modelith-enterprise`) registers
into it WITHOUT any edit to core. The core never imports the plugin; discovery
is one-way.

A plugin publishes an entry point in the `modelith.commands` group pointing at a
callable `register(app)` that receives the root Typer app and adds commands or
sub-apps to it:

    # in the plugin's pyproject.toml
    [project.entry-points."modelith.commands"]
    ci = "modelith_enterprise.ci:register"

    # modelith_enterprise/ci.py
    def register(app):
        app.add_typer(ci_app, name="ci")

Failures are swallowed (logged at debug): a broken or incompatible plugin must
never brick the free CLI. `load_command_plugins` is called once from `main()`
after the built-in commands are declared, so a plugin can see — and, if it must,
override — them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typer

log = logging.getLogger("mdl.plugins")

COMMAND_GROUP = "modelith.commands"

_loaded = False


def load_command_plugins(app: typer.Typer) -> list[str]:
    """Discover and apply subcommand plugins on the `modelith.commands` group.

    Each entry point loads to a `register(app)` callable; we call it with the root
    Typer `app`. Returns the names of the plugins that registered successfully (for
    tests / a debug line); the list is empty when none are installed, which is the
    normal case for the free CLI. Idempotent: only runs its discovery once per process.
    """
    global _loaded
    if _loaded:
        return []
    _loaded = True

    applied: list[str] = []
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        group = (
            eps.select(group=COMMAND_GROUP)
            if hasattr(eps, "select")
            else eps.get(COMMAND_GROUP, [])
        )
        for ep in group:
            try:
                register = ep.load()
                register(app)
                applied.append(ep.name)
            except Exception:  # noqa: BLE001 - a bad plugin must never break the CLI
                log.debug("skipping command plugin %r: it failed", ep.name, exc_info=True)
                continue
    except Exception:  # noqa: BLE001 - discovery itself must never break the CLI
        log.debug("command-plugin discovery failed", exc_info=True)
    return applied


def _reset_for_tests() -> None:
    """Allow a test to re-run discovery after installing a fake entry point."""
    global _loaded
    _loaded = False
