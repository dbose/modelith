"""Guard against the stale-install trap.

Modelith ships as ONE wheel (`modelith-dbt`) containing every `mdl_*` tree, but
develops as a uv workspace. When that mapping was a static `force-include` in
pyproject.toml it applied to the EDITABLE build too, so `uv sync` copied all
fourteen trees into site-packages where they SHADOWED `packages/*/src`. Editing
a source file then changed nothing Python imported, and this very suite could
pass green against code that no longer existed — the worst kind of failure,
because it is silent and it lies in the reassuring direction.

hatch_build.py now applies the mapping to the standard wheel only, and
`dev-mode-dirs` puts each member's src/ on sys.path for editable installs.
This test fails if that regresses.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# every module the root wheel force-includes
MODULES = [
    "mdl_core",
    "mdl_cli",
    "mdl_emit_dbt",
    "mdl_emit_contract",
    "mdl_emit_pydantic",
    "mdl_emit_graph",
    "mdl_reverse",
    "mdl_ontology",
    "mdl_emit_semantic",
    "mdl_governance",
    "mdl_catalog",
    "mdl_server",
    "mdl_lsp",
    "mdl_adapter_collibra",
]


def _is_workspace_checkout() -> bool:
    """Only meaningful in a source checkout; a wheel install legitimately imports
    from site-packages."""
    return (REPO_ROOT / "hatch_build.py").exists() and (REPO_ROOT / "packages").is_dir()


@pytest.mark.skipif(not _is_workspace_checkout(), reason="not a workspace checkout")
@pytest.mark.parametrize("name", MODULES)
def test_module_imports_from_source_not_a_copy(name: str) -> None:
    try:
        mod = importlib.import_module(name)
    except ImportError:  # pragma: no cover - an uninstalled optional member
        pytest.skip(f"{name} is not installed")
    resolved = Path(mod.__file__ or "").resolve()
    assert (REPO_ROOT / "packages") in resolved.parents, (
        f"{name} imported from {resolved}, not from packages/*/src.\n"
        "A copy in site-packages shadows the working tree, so source edits are "
        "silently ignored and tests can pass against stale code.\n"
        "Fix: rm -rf .venv/lib/python*/site-packages/mdl_* && "
        "uv sync --reinstall-package modelith-dbt\n"
        "If this persists, check that hatch_build.py still applies force_include "
        "only when version == 'standard'."
    )


@pytest.mark.skipif(not _is_workspace_checkout(), reason="not a workspace checkout")
def test_force_include_is_not_static_in_pyproject() -> None:
    """A static force-include would reintroduce the shadowing for editable installs."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    offending = [
        ln
        for ln in text.splitlines()
        if ln.strip().startswith("force-include") and not ln.strip().startswith("#")
    ]
    assert not offending, (
        "pyproject.toml declares a static force-include: it applies to the editable "
        "build too and will shadow packages/*/src. Keep the mapping in "
        "hatch_build.py, gated on version == 'standard'."
    )
