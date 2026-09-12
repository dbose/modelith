"""Modelith MCP server.

Exposes the model as MCP tools for an AI agent (VS Code Copilot Chat in *agent*
mode, Claude Desktop, Cursor, …). Reads come from `mdl_core.query`; writes go
through `mdl_core.commands.apply_command`, so they take the same validation and
concurrency path as every other Modelith write. Run it with `mdl mcp --repo <dir>`.
"""

from mdl_mcp.server import build_server, run

__all__ = ["build_server", "run"]
