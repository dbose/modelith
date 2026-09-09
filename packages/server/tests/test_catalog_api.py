"""Catalog browse server: API shape + source-link derivation (spec §4)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from mdl_catalog import CatalogEntry, GitBackend, MockGitRunner
from mdl_server.catalog_app import _repo_link, create_catalog_app


def _backend(tmp_path):
    be = GitBackend(work_dir=tmp_path / "cat", remote=None, runner=MockGitRunner())
    be.publish(CatalogEntry(model="pension", commit="a3f9c21",
                            remote="git@github.com:acme/pension.git",
                            ontology_layers=["fibo"]))
    be.publish(CatalogEntry(model="claims", commit="bbb",
                            remote="https://gitlab.com/acme/claims.git",
                            ontology_layers=["acord"]))
    return be


def test_catalog_list_endpoint(tmp_path):
    client = TestClient(create_catalog_app(_backend(tmp_path)))
    r = client.get("/api/catalog/list")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    models = {e["model"] for e in body["entries"]}
    assert models == {"pension", "claims"}


def test_catalog_search_query(tmp_path):
    client = TestClient(create_catalog_app(_backend(tmp_path)))
    body = client.get("/api/catalog/list", params={"q": "fibo"}).json()
    assert [e["model"] for e in body["entries"]] == ["pension"]


def test_source_link_github_ssh():
    assert (
        _repo_link("git@github.com:acme/pension.git", "a3f9c21")
        == "https://github.com/acme/pension/tree/a3f9c21"
    )


def test_source_link_gitlab_https():
    assert (
        _repo_link("https://gitlab.com/acme/claims.git", "bbb")
        == "https://gitlab.com/acme/claims/tree/bbb"
    )


def test_source_link_none_when_no_remote():
    assert _repo_link(None, "abc") is None


# --- open (materialise + mount canvas) -------------------------------------


def _source_repo(root, name="demo_model"):
    """A git repo with a minimal Modelith model; returns HEAD sha."""
    import subprocess

    root.mkdir(parents=True, exist_ok=True)
    (root / "mdl-project.yaml").write_text(f"name: {name}\n")

    def git(*a):
        subprocess.run(["git", *a], cwd=str(root), capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True
    ).stdout.strip()


def test_open_materializes_and_mounts_canvas(tmp_path):
    from mdl_catalog import GitBackend, RealGitRunner

    src = tmp_path / "src"
    head = _source_repo(src)
    be = GitBackend(work_dir=tmp_path / "cat" / "cat", runner=RealGitRunner())
    be.publish(CatalogEntry(model="demo_model", remote=str(src), commit=head))

    client = TestClient(create_catalog_app(be))
    r = client.post("/api/catalog/open/demo_model")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["url"] == "/view/demo_model/"

    # The mounted canvas answers the model API under its /view prefix.
    m = client.get("/view/demo_model/api/model")
    assert m.status_code == 200
    assert m.json()["project"]["name"] == "demo_model"


def test_open_unknown_slug_is_404(tmp_path):
    from mdl_catalog import GitBackend, MockGitRunner

    be = GitBackend(work_dir=tmp_path / "cat", runner=MockGitRunner())
    client = TestClient(create_catalog_app(be))
    assert client.post("/api/catalog/open/nope").status_code == 404


def test_open_without_remote_degrades_to_source_link(tmp_path):
    from mdl_catalog import GitBackend, MockGitRunner

    be = GitBackend(work_dir=tmp_path / "cat", runner=MockGitRunner())
    be.publish(CatalogEntry(model="noremote", commit="1"))
    client = TestClient(create_catalog_app(be))
    body = client.post("/api/catalog/open/noremote").json()
    assert body["ok"] is False
    assert "reason" in body


# --- opening a model from the Mart ------------------------------------------------


def _real_backend(tmp_path):
    """A published model in its own git repo — materialize needs a real remote."""
    import subprocess

    from mdl_catalog import RealGitRunner

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

    be = GitBackend(work_dir=tmp_path / "cat", remote=None, runner=RealGitRunner())
    be.publish(CatalogEntry(model="pension_ibor", commit=sha, remote=str(src)))
    return be


def test_open_serves_the_modeler_app_not_the_architect_canvas(tmp_path):
    """Someone browsing a catalog of models wants to look at a MODEL. Handing them
    the architect canvas hands them a second application they did not ask for."""
    client = TestClient(create_catalog_app(_real_backend(tmp_path)))
    r = client.post("/api/catalog/open/pension_ibor", json={})
    assert r.status_code == 200 and r.json()["ok"], r.json()
    page = client.get("/view/pension_ibor/").text
    assert "/assets/sme-" in page
    assert "/assets/main-" not in page


def test_open_is_read_only_by_default_and_writable_when_editing(tmp_path):
    """A slug opened read-only and then reopened for editing needs a NEW mount:
    Starlette matches the first route whose prefix fits, so a stale mount would keep
    serving the old mode."""
    client = TestClient(create_catalog_app(_real_backend(tmp_path)))

    client.post("/api/catalog/open/pension_ibor", json={})
    assert client.get("/view/pension_ibor/api/model").json()["read_only"] is True

    r = client.post("/api/catalog/open/pension_ibor", json={"edit": True, "user": "a.hough"})
    assert r.json()["edit"] is True
    assert client.get("/view/pension_ibor/api/model").json()["read_only"] is False
