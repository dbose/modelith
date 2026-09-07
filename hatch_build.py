"""Build hook: one distributable wheel, without shadowing the sources in dev.

Modelith develops as a uv workspace (`packages/*/src/mdl_*`) but ships as ONE
distribution, `modelith-dbt`, containing every `mdl_*` tree. Expressing that as a
static `[tool.hatch.build.targets.wheel] force-include` had a nasty side effect:
force-include applies to the EDITABLE build too, so `uv sync` copied all fourteen
trees into site-packages, where they SHADOWED the real sources. Editing
`packages/core/src/mdl_core/*.py` then changed nothing that Python imported —
`uv run pytest` kept exercising the stale copies and could pass green against
code that no longer existed. Only rebuilding the root package picked edits up.

So the mapping lives here instead, applied only to the standard (real) wheel.
For an editable install the hook adds nothing and `dev-mode-dirs` in
pyproject.toml puts each member's `src` directory on `sys.path` via a .pth file,
so imports resolve straight to the working tree.

Keep FORCE_INCLUDE and dev-mode-dirs in step when adding a workspace member.
"""

from __future__ import annotations

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# on-disk path -> in-wheel destination, so every module lands importable at the
# top level of the single `modelith-dbt` wheel. The mdl_server tree carries its
# static/ canvas bundles because those are real files under that directory.
FORCE_INCLUDE = {
    "packages/core/src/mdl_core": "mdl_core",
    "packages/cli/src/mdl_cli": "mdl_cli",
    "packages/emit-dbt/src/mdl_emit_dbt": "mdl_emit_dbt",
    "packages/emit-contract/src/mdl_emit_contract": "mdl_emit_contract",
    "packages/emit-pydantic/src/mdl_emit_pydantic": "mdl_emit_pydantic",
    "packages/emit-graph/src/mdl_emit_graph": "mdl_emit_graph",
    "packages/reverse/src/mdl_reverse": "mdl_reverse",
    "packages/ontology/src/mdl_ontology": "mdl_ontology",
    "packages/emit-semantic/src/mdl_emit_semantic": "mdl_emit_semantic",
    "packages/governance/src/mdl_governance": "mdl_governance",
    "packages/catalog/src/mdl_catalog": "mdl_catalog",
    "packages/server/src/mdl_server": "mdl_server",
    "packages/lsp/src/mdl_lsp": "mdl_lsp",
    "packages/adapters/collibra/src/mdl_adapter_collibra": "mdl_adapter_collibra",
}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        # version == "editable" for `pip/uv install -e`; skip it there so the
        # dev-mode-dirs .pth wins and nothing shadows the working tree.
        if self.target_name == "wheel" and version == "standard":
            build_data["force_include"].update(FORCE_INCLUDE)
