"""CLI: `mdl model tree` — the JSON the VS Code Model Explorer consumes (entity →
attributes, with a source file per entity for click-to-open)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from mdl_cli.main import app

runner = CliRunner()


def _demo(tmp_path):
    d = tmp_path / "proj"
    assert runner.invoke(app, ["init", "--demo", str(d)]).exit_code == 0
    return d / "model"


def test_model_tree_json_shape(tmp_path):
    model = _demo(tmp_path)
    r = runner.invoke(app, ["model", "tree", "-m", str(model), "--format", "json"])
    assert r.exit_code == 0, r.output
    tree = json.loads(r.stdout)
    assert "entities" in tree and tree["entities"]
    e = tree["entities"][0]
    assert {"name", "attributes", "file"} <= set(e)
    assert isinstance(e["attributes"], list)


def test_model_tree_files_resolve_on_disk(tmp_path):
    # every entity's `file` must be a real path relative to the model dir — this is the
    # exact path the extension opens when a user clicks an entity in the tree.
    model = _demo(tmp_path)
    tree = json.loads(runner.invoke(app, ["model", "tree", "-m", str(model)]).stdout)
    for e in tree["entities"]:
        assert e["file"], f"{e['name']} has no source file"
        assert (model / e["file"]).exists(), f"{e['file']} does not exist"


def test_model_tree_attributes_have_pk_flag(tmp_path):
    model = _demo(tmp_path)
    tree = json.loads(runner.invoke(app, ["model", "tree", "-m", str(model)]).stdout)
    # at least one entity should have a pk attribute (the demo has keyed entities)
    all_attrs = [a for e in tree["entities"] for a in e["attributes"]]
    assert any(a["is_pk"] for a in all_attrs)
    # every attribute carries the fields the tree renders
    for a in all_attrs:
        assert {"name", "domain", "role", "nullable", "is_pk"} <= set(a)


def test_model_tree_text_format(tmp_path):
    model = _demo(tmp_path)
    r = runner.invoke(app, ["model", "tree", "-m", str(model), "--format", "text"])
    assert r.exit_code == 0, r.output
    assert "(pk)" in r.output  # the text fallback marks primary keys
