"""CLI wiring for `mdl ontology lock` / `fetch` + gitignore (spec §3)."""

from __future__ import annotations

from typer.testing import CliRunner

from mdl_cli.main import app
from mdl_cli.scaffold import scaffold

runner = CliRunner()

_TTL = """\
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix acme: <https://acme.example/onto/> .
acme:Party a skos:Concept ; skos:prefLabel "Party" .
"""


def test_scaffold_gitignores_ontology_cache(tmp_path):
    files = scaffold(tmp_path, project_name="t")
    assert ".mdl/ontology-cache/" in files[".gitignore"]


def test_lock_then_fetch_cli(tmp_path):
    src = tmp_path / "acme.ttl"
    src.write_text(_TTL, encoding="utf-8")

    r = runner.invoke(
        app,
        ["ontology", "lock", "core", str(src), "-m", str(tmp_path)],
    )
    assert r.exit_code == 0, r.output
    assert "pinned core" in r.output
    assert (tmp_path / ".mdl" / "ontology-cache" / "core" / "core.ttl").exists()

    r2 = runner.invoke(app, ["ontology", "fetch", "-m", str(tmp_path)])
    assert r2.exit_code == 0, r2.output
    assert "cached" in r2.output  # served from the matching cache entry


def test_fetch_with_no_layers_is_noop(tmp_path):
    r = runner.invoke(app, ["ontology", "fetch", "-m", str(tmp_path)])
    assert r.exit_code == 0
    assert "no ontology layers pinned" in r.output


def test_lock_rejects_bad_mode(tmp_path):
    src = tmp_path / "acme.ttl"
    src.write_text(_TTL, encoding="utf-8")
    r = runner.invoke(
        app,
        ["ontology", "lock", "core", str(src), "-m", str(tmp_path), "--mode", "bogus"],
    )
    assert r.exit_code == 1
    assert "bad --mode" in r.output
