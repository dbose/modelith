"""Commands must run from anywhere inside a model, not only from the exact folder
holding mdl-project.yaml (issue #7)."""

from __future__ import annotations

import pytest

from mdl_core.commands import apply_command
from mdl_core.repo import ModelRepo, find_project_root

from model_builders import write_model


@pytest.fixture
def project(tmp_path):
    """The issue's layout: a project root with the model one level down in my-model/."""
    model = tmp_path / "my-model"
    model.mkdir()
    write_model(model)
    return tmp_path, model


def test_find_root_from_model_dir(project):
    _, model = project
    assert find_project_root(model) == model.resolve()


def test_find_root_from_nested_dir(project):
    _, model = project
    nested = model / "conceptual" / "entities"
    assert find_project_root(nested) == model.resolve()


def test_load_from_the_model_dir(project):
    _, model = project
    assert ModelRepo.load(model).model.config.name == "testmodel"


def test_load_from_a_nested_dir_walks_up(project):
    """The core of issue #7: standing in conceptual/entities/ must still load the
    model, instead of failing because mdl-project.yaml is not in that exact dir."""
    _, model = project
    nested = model / "logical" / "entities"
    assert ModelRepo.load(nested).model.config.name == "testmodel"


def test_add_entity_from_a_nested_dir_walks_up(project):
    """Adding an entity — the command the issue says only worked from a specific dir —
    now works from anywhere inside the model."""
    _, model = project
    nested = model / "conceptual" / "entities"
    result = apply_command(nested, "create_entity", {"name": "Widget"})
    assert result.ok and result.created_id
    # it landed in the real model at the resolved root, not in the nested dir
    reloaded = ModelRepo.load(model).model
    assert any(e.name == "Widget" for e in reloaded.conceptual_entities.values())
    # and nothing was written into the nested working dir
    assert not (nested / "mdl-project.yaml").exists()


def test_helpful_message_when_run_from_the_project_root_above(project):
    """Running from the root ABOVE the model (where walking up finds nothing) gives a
    message that points into the model, not a bare 'no mdl-project.yaml in .'."""
    root, _ = project
    with pytest.raises(FileNotFoundError) as ei:
        ModelRepo.load(root)
    msg = str(ei.value)
    assert "my-model" in msg  # names the subdir to go into
    assert "-m my-model" in msg  # and how to point at it


def test_plain_message_when_no_model_anywhere(tmp_path):
    with pytest.raises(FileNotFoundError) as ei:
        ModelRepo.load(tmp_path)
    msg = str(ei.value)
    assert "no mdl-project.yaml" in msg
    assert "my-model" not in msg  # nothing to suggest
