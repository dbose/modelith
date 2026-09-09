"""Cross-repo catalog: entry, git backend, config resolution (spec §1-§3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mdl_catalog import (
    CATALOG_SCHEMA_VERSION,
    CatalogConfig,
    CatalogEntry,
    GitBackend,
    MaterializeNotSupported,
    MockGitRunner,
    RealGitRunner,
    entry_from_repo,
    make_backend,
)

# --- CatalogEntry ----------------------------------------------------------


def test_entry_roundtrip():
    e = CatalogEntry(
        model="Pension Warehouse", namespace_ulid="01J", remote="git@x:o/p.git",
        commit="a3f9c21", ontology_layers=["fibo", "pension"],
        published_at="2026-09-03T04:00:00Z",
    )
    e2 = CatalogEntry.from_doc(e.to_doc())
    assert e2.model == e.model
    assert e2.commit == "a3f9c21"
    assert e2.ontology_layers == ["fibo", "pension"]
    assert e2.modelith_schema_version == CATALOG_SCHEMA_VERSION


def test_entry_slug_is_filename_safe():
    assert CatalogEntry(model="Pension Warehouse!").slug() == "pension-warehouse-"


def test_entry_matches_search():
    e = CatalogEntry(model="claims", ontology_layers=["acord", "enterprise-fin"])
    assert e.matches("acord")
    assert e.matches("CLAIMS")
    assert e.matches("")  # empty query matches all
    assert not e.matches("fibo")


# --- git backend -----------------------------------------------------------


def _backend(tmp_path: Path, **kw) -> GitBackend:
    return GitBackend(work_dir=tmp_path / "cat", remote=None, runner=MockGitRunner(), **kw)


def test_publish_then_list(tmp_path):
    be = _backend(tmp_path)
    be.publish(CatalogEntry(model="a", commit="1", ontology_layers=["fibo"]))
    be.publish(CatalogEntry(model="b", commit="2"))
    names = sorted(e.model for e in be.list())
    assert names == ["a", "b"]


def test_publish_same_commit_is_noop(tmp_path):
    runner = MockGitRunner()
    be = GitBackend(work_dir=tmp_path / "cat", remote=None, runner=runner)
    e = CatalogEntry(model="a", commit="1")
    be.publish(e)
    commits_after_first = sum(1 for c in runner.calls if "commit" in c)
    be.publish(e)  # identical -> idempotent
    commits_after_second = sum(1 for c in runner.calls if "commit" in c)
    assert commits_after_first == 1
    assert commits_after_second == 1  # no new commit


def test_publish_new_commit_updates_entry(tmp_path):
    be = _backend(tmp_path)
    be.publish(CatalogEntry(model="a", commit="1"))
    be.publish(CatalogEntry(model="a", commit="2"))  # same model, new commit
    entries = be.list()
    assert len(entries) == 1  # updated, not duplicated
    assert entries[0].commit == "2"


def test_search_filters(tmp_path):
    be = _backend(tmp_path)
    be.publish(CatalogEntry(model="pension", ontology_layers=["fibo"]))
    be.publish(CatalogEntry(model="claims", ontology_layers=["acord"]))
    assert [e.model for e in be.search("fibo")] == ["pension"]
    assert sorted(e.model for e in be.search("")) == ["claims", "pension"]


def test_push_retries_on_rejection(tmp_path):
    runner = MockGitRunner(reject_pushes=2)  # fail first 2 pushes
    be = GitBackend(work_dir=tmp_path / "cat", remote="git@x:o/cat.git", runner=runner)
    be.publish(CatalogEntry(model="a", commit="1"))
    pushes = sum(1 for c in runner.calls if c and c[0] == "push")
    assert pushes >= 3  # retried past the 2 rejections


# --- materialize (browse-view canvas) --------------------------------------


def _init_source_repo(root: Path) -> str:
    """Create a git repo holding a minimal Modelith model; return its HEAD commit."""
    import subprocess

    root.mkdir(parents=True, exist_ok=True)
    (root / "mdl-project.yaml").write_text("name: demo_model\n")

    def git(*args):
        subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t.local")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True
    )
    return out.stdout.strip()


def test_materialize_checks_out_model_dir(tmp_path):
    src = tmp_path / "src"
    head = _init_source_repo(src)
    be = GitBackend(work_dir=tmp_path / "cat" / "cat", runner=RealGitRunner())
    entry = CatalogEntry(model="demo_model", remote=str(src), commit=head)

    model_dir = be.materialize(entry)
    assert (model_dir / "mdl-project.yaml").is_file()

    # Idempotent: a second call reuses the checkout, same dir.
    assert be.materialize(entry) == model_dir


def test_materialize_finds_model_under_workspace_subdir(tmp_path):
    # Workspace layout: the model lives under model/, not the repo root.
    src = tmp_path / "src"
    head = _init_source_repo(src / "model")
    # Move the .git up so the repo root is `src` with the model under model/.
    import shutil

    (src).mkdir(exist_ok=True)
    shutil.move(str(src / "model" / ".git"), str(src / ".git"))
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=str(src), capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "ws"],
        cwd=str(src), capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(src), capture_output=True, text=True
    ).stdout.strip()

    be = GitBackend(work_dir=tmp_path / "cat" / "cat", runner=RealGitRunner())
    model_dir = be.materialize(CatalogEntry(model="demo_model", remote=str(src), commit=head))
    assert model_dir.name == "model"
    assert (model_dir / "mdl-project.yaml").is_file()


def test_materialize_without_remote_raises(tmp_path):
    be = _backend(tmp_path)
    with pytest.raises(MaterializeNotSupported, match="no source remote"):
        be.materialize(CatalogEntry(model="a", commit="1"))


# --- config resolution -----------------------------------------------------


def test_config_defaults_to_git_backend(tmp_path):
    cfg = CatalogConfig.resolve(tmp_path)  # no config file anywhere reachable
    assert cfg.backend == "git"


def test_config_from_repo_file(tmp_path):
    d = tmp_path / ".modelith"
    d.mkdir()
    (d / "catalog.yaml").write_text(
        "catalog:\n  backend: git\n  remote: git@x:o/catalog.git\n  branch: main\n"
    )
    cfg = CatalogConfig.resolve(tmp_path)
    assert cfg.remote == "git@x:o/catalog.git"
    assert cfg.branch == "main"


def test_make_backend_unknown_type_raises(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="unknown catalog backend"):
        make_backend(CatalogConfig(backend="datahub"), tmp_path)


# --- entry from a real model repo ------------------------------------------


def test_entry_from_repo_reads_config(tmp_path):
    model = tmp_path / "model"
    (model / "conceptual" / "subject-areas").mkdir(parents=True)
    (model / "mdl-project.yaml").write_text(
        "name: pension_ibor\n"
        "ontology_stack:\n"
        "  - name: fibo\n    layer: industry\n    type: ols\n    url: http://x\n"
    )
    (model / "conceptual" / "subject-areas" / "sa.yaml").write_text(
        "id: 01KZ265963K1SX5TK770VJEYHD\nkind: subject_area\nname: Core\n"
    )
    e = entry_from_repo(model, remote="git@x:o/p.git", commit="deadbeef")
    assert e.model == "pension_ibor"
    assert e.remote == "git@x:o/p.git"
    assert e.commit == "deadbeef"
    assert e.ontology_layers == ["fibo"]  # from ontology_stack name
    assert e.namespace_ulid == "01KZ265963K1SX5TK770VJEYHD"


# --- editable materialisation (opening a model from the Mart) ---------------------


def _source_repo(tmp_path):
    """A model in its own git repo, standing in for one an org has published."""
    import subprocess

    src = tmp_path / "src"
    (src / "model").mkdir(parents=True)
    (src / "model" / "mdl-project.yaml").write_text("name: pension_ibor\ndbt_target: duckdb\n")
    for cmd in (
        ["init", "-q", "."],
        ["config", "user.email", "t@t.co"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "base"],
        ["branch", "-M", "main"],
    ):
        subprocess.run(["git", "-C", str(src), *cmd], check=True)
    sha = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return src, sha


def _branch_of(path):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_checkout_branch_makes_the_cache_proposable(tmp_path):
    """`git checkout <commit>` leaves a DETACHED HEAD, which is perfectly branchable
    — and branching from the pinned commit is better than branching from a moving
    main, because the proposal is based on exactly the model the user was shown."""
    from mdl_catalog.entry import CatalogEntry
    from mdl_catalog.git_backend import GitBackend, RealGitRunner

    src, sha = _source_repo(tmp_path)
    be = GitBackend(work_dir=tmp_path / "work", remote=None, runner=RealGitRunner())
    entry = CatalogEntry(model="pension_ibor", remote=str(src), commit=sha)

    model_dir = be.checkout_branch(entry, "sme/a-hough/edit")
    assert _branch_of(model_dir.parent) == "sme/a-hough/edit"
    # the full clone means origin is present, so a proposal can be pushed home
    import subprocess

    assert (
        subprocess.run(
            ["git", "-C", str(model_dir.parent), "remote", "get-url", "origin"],
            capture_output=True,
        ).returncode
        == 0
    )


def test_reopening_starts_from_the_pinned_commit_again(tmp_path):
    """The cache is keyed slug@commit and otherwise reused as-is, so without the
    refresh a second visit would resume whatever branch the last one left behind."""
    from mdl_catalog.entry import CatalogEntry
    from mdl_catalog.git_backend import GitBackend, RealGitRunner

    src, sha = _source_repo(tmp_path)
    be = GitBackend(work_dir=tmp_path / "work", remote=None, runner=RealGitRunner())
    entry = CatalogEntry(model="pension_ibor", remote=str(src), commit=sha)

    be.checkout_branch(entry, "sme/a-hough/first")
    model_dir = be.checkout_branch(entry, "sme/b-other/second")
    assert _branch_of(model_dir.parent) == "sme/b-other/second"


def test_a_proposal_pushes_back_to_the_models_own_repo(tmp_path):
    """The point of the whole thing: the catalog stays a pointer index, and an edit
    made from it lands as a branch on the model's own repo."""
    import subprocess

    from mdl_catalog.entry import CatalogEntry
    from mdl_catalog.git_backend import GitBackend, RealGitRunner

    src, sha = _source_repo(tmp_path)
    be = GitBackend(work_dir=tmp_path / "work", remote=None, runner=RealGitRunner())
    entry = CatalogEntry(model="pension_ibor", remote=str(src), commit=sha)

    model_dir = be.checkout_branch(entry, "sme/a-hough/edit")
    (model_dir / "mdl-project.yaml").write_text("name: pension_ibor\ndbt_target: duckdb\n# edited\n")
    root = model_dir.parent
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "edit"], check=True)
    subprocess.run(["git", "-C", str(root), "push", "-q", "origin", "sme/a-hough/edit"], check=True)

    branches = subprocess.run(
        ["git", "-C", str(src), "branch", "--list", "sme/*"], capture_output=True, text=True
    ).stdout
    assert "sme/a-hough/edit" in branches
