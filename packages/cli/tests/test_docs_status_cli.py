"""CLI: `mdl docs generate`, `mdl status`, and the server /mdl-docs route."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from mdl_cli.main import app

_CORE_TESTS = Path(__file__).resolve().parents[2] / "core" / "tests"
sys.path.insert(0, str(_CORE_TESTS))
from model_builders import write_model  # noqa: E402

runner = CliRunner()


def test_docs_generate_writes_site(tmp_path):
    write_model(tmp_path)
    r = runner.invoke(app, ["docs", "generate", "-m", str(tmp_path)])
    assert r.exit_code == 0, r.output
    site = tmp_path / "target" / "mdl-docs"
    assert (site / "index.html").is_file()
    assert (site / "glossary.html").is_file()
    assert (site / "assets" / "mermaid.min.js").is_file()
    assert list((site / "entities").glob("*.html"))


def test_docs_generate_custom_out_and_radius(tmp_path):
    write_model(tmp_path)
    out = tmp_path / "custom-docs"
    r = runner.invoke(
        app,
        ["docs", "generate", "-m", str(tmp_path), "-o", str(out), "--neighbourhood-radius", "1"],
    )
    assert r.exit_code == 0, r.output
    assert (out / "index.html").is_file()


def test_status_text(tmp_path):
    write_model(tmp_path)
    r = runner.invoke(app, ["status", "-m", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "Next actions" in r.output


def test_commands_run_from_repo_root_above_model(tmp_path):
    """The reported UX bug: from a repo root whose model is in model/, `mdl docs
    generate` / `mdl status` must just work (auto-descend), not error."""
    model = tmp_path / "model"
    model.mkdir()
    write_model(model)
    # generate from the ROOT, not the model dir
    r = runner.invoke(app, ["docs", "generate", "-m", str(tmp_path)])
    assert r.exit_code == 0, r.output
    # docs land under the resolved model dir, where the server also looks
    assert (model / "target" / "mdl-docs" / "index.html").is_file()
    assert not (tmp_path / "target").exists()  # NOT beside the repo root
    r2 = runner.invoke(app, ["status", "-m", str(tmp_path)])
    assert r2.exit_code == 0, r2.output


def test_status_json_shape(tmp_path):
    write_model(tmp_path)
    r = runner.invoke(app, ["status", "-m", str(tmp_path), "--format", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["entity_count"] > 0
    assert "next_actions" in data and isinstance(data["next_actions"], list)
    # docs not generated yet -> the generate action is offered
    assert any(a["id"] == "docs-generate" for a in data["next_actions"])


def test_server_serves_generated_docs(tmp_path):
    write_model(tmp_path)
    assert runner.invoke(app, ["docs", "generate", "-m", str(tmp_path)]).exit_code == 0

    from fastapi.testclient import TestClient
    from mdl_server.app import create_app

    client = TestClient(create_app(tmp_path, read_only=True))
    assert client.get("/mdl-docs/").status_code == 200
    assert client.get("/mdl-docs/glossary.html").status_code == 200
    # path traversal is refused (URL-encoded so it reaches the handler unnormalised)
    escaped = client.get("/mdl-docs/%2e%2e%2f%2e%2e%2fmdl-project.yaml")
    assert escaped.status_code == 404
    # a genuinely missing docs page is a 404, not a fallthrough
    assert client.get("/mdl-docs/entities/does-not-exist.html").status_code == 404


def test_server_docs_absent_is_404(tmp_path):
    write_model(tmp_path)  # no docs generated
    from fastapi.testclient import TestClient
    from mdl_server.app import create_app

    client = TestClient(create_app(tmp_path, read_only=True))
    assert client.get("/mdl-docs/").status_code == 404
