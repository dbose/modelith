"""The MCP server tools: reads over the query layer, writes through apply_command.

The FastMCP tool-call API is async; we drive it with a small asyncio.run helper so
the suite needs no async-test plugin (the project has none, and adding one is a
separate decision)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

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
        "explain_drift",
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


def test_explain_drift_missing_manifest_is_error(model_dir):
    srv = build_server(model_dir)
    r = _call(srv, "explain_drift", {"manifest": "/nope/manifest.json"})
    assert "error" in r


def test_explain_drift_reports_annotated_items(model_dir, tmp_path):
    # Build a manifest from the model, add a column (additive) + drop one (breaking),
    # write it, and point the tool at it. Reuses the reverse tests' manifest builder.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "reverse" / "tests"))
    from mdl_core.repo import ModelRepo

    from manifest_fixtures import manifest_from_model

    repo = ModelRepo.load(model_dir)
    raw = manifest_from_model(repo.model, "duckdb_dev")
    node = raw["nodes"]["model.testproj.counterparty"]
    node["columns"]["loyalty_tier"] = {"name": "loyalty_tier", "data_type": "VARCHAR"}
    node["columns"].pop("legal_name", None)
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps(raw))

    r = _call(build_server(model_dir), "explain_drift", {"manifest": str(man)})
    assert r["has_breaking"] is True
    assert r["safe_count"] >= 1
    assert r["breaking_count"] >= 1
    # the additive column is reconcilable with an action; no breaking item is
    assert any(i["reconcile_action"] and "loyalty_tier" in i["reconcile_action"] for i in r["items"])
    assert all(not i["reconcilable"] for i in r["items"] if i["severity"] == "breaking")
