"""The MCP server tools: reads over the query layer, writes through apply_command.

The FastMCP tool-call API is async; we drive it with a small asyncio.run helper so
the suite needs no async-test plugin (the project has none, and adding one is a
separate decision)."""

from __future__ import annotations

import asyncio
import json

from mdl_mcp.server import build_server


def _call(srv, name, args):
    """Invoke a FastMCP tool synchronously and return its parsed JSON result."""

    async def _run():
        res = await srv.call_tool(name, args)
        content = res[0] if isinstance(res, tuple) else res
        return content[0].text if content and hasattr(content[0], "text") else str(content)

    return json.loads(asyncio.run(_run()))


def _tool_names(srv):
    return {t.name for t in asyncio.run(srv.list_tools())}


def test_tools_are_registered(model_dir):
    names = _tool_names(build_server(model_dir))
    assert {
        "list_entities",
        "get_entity",
        "get_model_context",
        "search_ontology",
        "validate",
        "create_entity",
        "update_entity",
    } <= names


def test_get_entity_tool_reads(model_dir):
    srv = build_server(model_dir)
    e = _call(srv, "get_entity", {"name": "counterparty"})
    assert e["conceptual"]["name"] == "Counterparty"


def test_get_entity_tool_unknown_is_error_object(model_dir):
    srv = build_server(model_dir)
    e = _call(srv, "get_entity", {"name": "nope"})
    assert "error" in e


def test_create_entity_writes_to_disk(model_dir):
    srv = build_server(model_dir)
    r = _call(srv, "create_entity", {"name": "region", "definition": "A geographic region."})
    assert r["ok"] is True
    assert r["created_id"]
    # visible to the very next read (the server reloads per call)
    e = _call(srv, "get_entity", {"name": "region"})
    assert e["name"] == "region"
    assert (model_dir / "logical" / "entities" / "region.yaml").exists()


def test_update_entity_add_attribute_and_definition(model_dir):
    srv = build_server(model_dir)
    _call(srv, "create_entity", {"name": "region"})
    r = _call(
        srv,
        "update_entity",
        {
            "name": "region",
            "changes": {
                "definition": "A named geographic area.",
                "add_attribute": {"name": "region_code", "domain": "string", "nullable": False},
            },
        },
    )
    assert r["ok"] is True
    assert "definition" in r["applied"]
    e = _call(srv, "get_entity", {"name": "region"})
    assert e["definition"] == "A named geographic area."
    assert any(a["name"] == "region_code" for a in e["attributes"])


def test_update_entity_unknown_is_error(model_dir):
    srv = build_server(model_dir)
    r = _call(srv, "update_entity", {"name": "ghost", "changes": {"definition": "x"}})
    assert "error" in r


def test_validate_tool_reports_ok(model_dir):
    srv = build_server(model_dir)
    r = _call(srv, "validate", {})
    assert r["ok"] is True
