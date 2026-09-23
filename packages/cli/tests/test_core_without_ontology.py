"""The core CLI must install and run without the ontology extra (rdflib/pyoxigraph).

Enterprise mirrors (Nexus firewall) quarantine the `rdflib` coordinate, which used to
block *every* `mdl` command because `mdl_cli.main` imported the ontology stack at module
load. The RDF stack now ships only with the `[ontology]` extra, so:

  * importing the CLI and running core commands (help, validate, reverse, generate) must
    work with the RDF backend absent, and
  * ontology / RDF-export commands must fail with a clear "install modelith-dbt[ontology]"
    hint and a non-zero exit, not a raw ImportError traceback.

Absence is simulated by blocking `rdflib` (and `pyoxigraph`, the future backend) at import
time via a meta-path finder, and evicting any cached copies + `mdl_ontology` so the guard
in `mdl_cli.main._ontology()` re-runs the import.
"""

from __future__ import annotations

import builtins
import importlib
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mdl_cli.demo import scaffold_demo
from mdl_cli.main import app

runner = CliRunner()

# The RDF backend module names the ontology stack pulls in. Blocking these makes the
# `[ontology]` extra look uninstalled without actually uninstalling anything.
_BACKENDS = ("rdflib", "pyoxigraph")


_MANAGED_ROOTS = (*_BACKENDS, "mdl_ontology", "mdl_cli")


class _blocked_backend:
    """Context manager: make the RDF backend(s) unimportable, and drop the ontology
    modules from the cache so a fresh import attempt (and its ImportError) is observed.

    Snapshots and fully restores `sys.modules` for the managed roots on exit, so this
    process-global surgery cannot leak a half-imported (or evicted) `mdl_ontology` /
    `mdl_cli` into later tests sharing the interpreter — the cause of spooky
    cross-test failures if left un-restored.
    """

    def __init__(self) -> None:
        self._real_import = builtins.__import__
        self._saved: dict[str, object] = {}

    def _fake_import(self, name, *args, **kwargs):
        root = name.split(".", 1)[0]
        if root in _BACKENDS:
            raise ImportError(f"{root} quarantined (simulated for test)")
        return self._real_import(name, *args, **kwargs)

    def __enter__(self):
        # Snapshot every managed module so __exit__ can restore the exact prior state.
        for mod in list(sys.modules):
            if mod.split(".", 1)[0] in _MANAGED_ROOTS:
                self._saved[mod] = sys.modules[mod]
        # Evict backend + ontology + cli modules so imports actually re-run under the block.
        for mod in list(sys.modules):
            if mod.split(".", 1)[0] in _MANAGED_ROOTS:
                del sys.modules[mod]
        builtins.__import__ = self._fake_import
        return self

    def __exit__(self, *exc):
        builtins.__import__ = self._real_import
        # Drop anything imported under the block, then restore the original modules
        # verbatim so the shared interpreter looks untouched to later tests.
        for mod in list(sys.modules):
            if mod.split(".", 1)[0] in _MANAGED_ROOTS:
                del sys.modules[mod]
        sys.modules.update(self._saved)
        return False


def test_backend_is_actually_blocked():
    with _blocked_backend():
        with pytest.raises(ImportError):
            importlib.import_module("rdflib")


def test_cli_imports_without_rdflib():
    """`import mdl_cli.main` must succeed with the RDF backend absent."""
    with _blocked_backend():
        mod = importlib.reload(importlib.import_module("mdl_cli.main"))
        assert hasattr(mod, "app")


def test_help_works_without_rdflib():
    with _blocked_backend():
        mod = importlib.reload(importlib.import_module("mdl_cli.main"))
        r = runner.invoke(mod.app, ["--help"])
    assert r.exit_code == 0, r.output
    assert "reverse" in r.output  # a core command is still wired


def _demo_model(root: Path) -> Path:
    """Scaffold the bundled demo under `root` and return the model dir (the demo places
    the project at `<root>/model/mdl-project.yaml`)."""
    scaffold_demo(root)
    return root / "model"


def test_core_commands_work_without_rdflib(tmp_path: Path):
    """validate + generate on a real demo model run with no RDF backend installed."""
    model = _demo_model(tmp_path)
    with _blocked_backend():
        mod = importlib.reload(importlib.import_module("mdl_cli.main"))
        v = runner.invoke(mod.app, ["validate", "-m", str(model)])
        g = runner.invoke(mod.app, ["generate", "-m", str(model), "-o", str(tmp_path / "out")])
    assert v.exit_code == 0, v.output
    assert g.exit_code == 0, g.output


@pytest.mark.parametrize(
    "argv",
    [
        ["export", "rdf", "-m", "."],
        ["export", "shacl", "-m", "."],
        ["export", "r2rml", "-m", "."],
        ["ontology", "check", "-m", "."],
        ["ontology", "search", "party", "-m", "."],
    ],
)
def test_ontology_commands_fail_with_install_hint(argv, tmp_path: Path):
    """Ontology / RDF-export commands must fail loud with the extra-install hint and
    exit code 4 (adapter/plan-failure class) — never a bare ImportError traceback."""
    model = _demo_model(tmp_path)
    argv = [*argv[:-1], str(model)] if argv[-1] == "." else argv
    with _blocked_backend():
        mod = importlib.reload(importlib.import_module("mdl_cli.main"))
        r = runner.invoke(mod.app, argv)
    combined = r.output + str(r.stderr or "")
    assert r.exit_code == 4, f"expected exit 4, got {r.exit_code}: {combined}"
    assert "modelith-dbt[ontology]" in combined, combined


def test_ontology_still_works_when_backend_present(tmp_path: Path):
    """Sanity: with the backend installed (the normal test env), an ontology command is
    NOT gated — it runs. Guards against the helper over-blocking."""
    model = _demo_model(tmp_path)
    # No _blocked_backend() here: the RDF backend is installed in the dev/test env, so
    # the top-level `app` is used as-is (no reload needed).
    r = runner.invoke(app, ["ontology", "check", "-m", str(model)])
    # exit 0 (clean) or 1 (validation diagnostics) are both "ran"; 4 would mean gated.
    assert r.exit_code in (0, 1), r.output
