"""Materialising a Model at a git ref (plan §K)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from mdl_server.git_models import (
    RefLoadError,
    ahead_behind,
    clear_cache,
    file_at_ref,
    merge_base,
    model_at_ref,
    model_at_working_tree,
    repo_prefix,
    resolve_sha,
)

from model_builders import write_model


def _git(d: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, check=True)
    return p.stdout.strip()


def _init(d: Path) -> None:
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    _git(d, "config", "user.email", "t@t.co")
    _git(d, "config", "user.name", "t")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    _git(d, "branch", "-M", "main")


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def repo(model_dir):
    _init(model_dir)
    return model_dir


def test_head_matches_a_clean_working_tree(repo):
    at_head = model_at_ref(repo, "HEAD")
    live = model_at_working_tree(repo)
    assert at_head is not None
    assert set(at_head.logical_entities) == set(live.logical_entities)
    assert set(at_head.conceptual_entities) == set(live.conceptual_entities)


def test_uncommitted_edit_shows_against_head(repo):
    from mdl_core.diff import diff_models

    ce_path = next((repo / "conceptual" / "entities").glob("counterparty.yaml"))
    ce_path.write_text(
        ce_path.read_text().replace("name: Counterparty", "name: Legal Entity"),
        encoding="utf-8",
    )
    d = diff_models(model_at_ref(repo, "HEAD"), model_at_working_tree(repo))
    assert len(d.objects) == 1 and d.objects[0].renamed


def test_model_dir_as_a_repo_subdirectory(tmp_path_factory):
    """The whole `mdl init --workspace` layout is model/ beside transform/, so the
    archive must be scoped by prefix rather than extracting the whole repo."""
    root = tmp_path_factory.mktemp("workspace")
    nested = root / "model"
    nested.mkdir()
    write_model(nested)
    (root / "transform").mkdir()
    (root / "transform" / "dbt_project.yml").write_text("name: w\n")
    _init(root)

    assert repo_prefix(nested) == "model/"
    m = model_at_ref(nested, "HEAD")
    assert m is not None and m.logical_entities


def test_ref_predating_the_model_dir_returns_none(tmp_path_factory):
    root = tmp_path_factory.mktemp("ws2")
    (root / "README.md").write_text("first\n")
    _init(root)
    first = _git(root, "rev-parse", "HEAD")

    nested = root / "model"
    nested.mkdir()
    write_model(nested)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "add model")

    assert model_at_ref(nested, "HEAD") is not None
    assert model_at_ref(nested, first) is None  # no model there yet — not an error


def test_bad_ref_raises_rather_than_traceback(repo):
    with pytest.raises(RefLoadError, match="cannot resolve"):
        model_at_ref(repo, "no-such-ref")


def test_same_sha_is_cached(repo, monkeypatch):
    import mdl_server.git_models as gm

    calls = {"n": 0}
    real = gm._git_bytes

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(gm, "_git_bytes", counting)
    gm.clear_cache()
    model_at_ref(repo, "HEAD")
    model_at_ref(repo, "HEAD")
    assert calls["n"] == 1  # second call served from the SHA cache


def test_resolve_merge_base_and_ahead_behind(repo):
    base_sha = resolve_sha(repo, "main")
    assert base_sha and len(base_sha) == 40

    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "note.md").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature work")

    assert merge_base(repo, "main", "feature") == base_sha
    ahead, behind = ahead_behind(repo, "main", "feature")
    assert (ahead, behind) == (1, 0)


def test_file_at_ref(repo):
    text = file_at_ref(repo, "HEAD", "conceptual/entities/counterparty.yaml")
    assert text and "Counterparty" in text
    assert file_at_ref(repo, "HEAD", "nope.yaml") is None
