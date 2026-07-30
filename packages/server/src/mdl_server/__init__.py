"""Modelith read API (spec §13.5).

The server exists to serve the canvas, not to own state. State stays in git: every
request re-reads the model directory, so an external `git pull` or editor save is
reflected on refresh with no cache invalidation protocol.
"""

from mdl_server.app import create_app

__all__ = ["create_app"]
