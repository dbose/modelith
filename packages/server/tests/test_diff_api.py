"""The diff / classify / conflicts / context / proposals endpoints (plan §L)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
def _clear_cache():
    from mdl_server.git_models import clear_cache

    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def repo(model_dir):
    _init(model_dir)
    return model_dir


@pytest.fixture
def client(repo):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    return TestClient(create_app(repo))


def _rename_counterparty(repo: Path) -> None:
    f = repo / "conceptual" / "entities" / "counterparty.yaml"
    f.write_text(f.read_text().replace("name: Counterparty", "name: Legal Entity"), "utf-8")


def test_diff_model_reports_a_rename_as_one_change(client, repo):
    _rename_counterparty(repo)
    r = client.get("/api/git/diff/model")
    assert r.status_code == 200
    doc = r.json()
    assert doc["ok"] and len(doc["objects"]) == 1
    o = doc["objects"][0]
    assert o["renamed"] and o["name_after"] == "Legal Entity"
    assert o["object_kind_label"] == "conceptual entity"
    assert o["path"] == "conceptual/entities/counterparty.yaml"  # filled server-side


def test_diff_model_is_empty_on_a_clean_tree(client):
    doc = client.get("/api/git/diff/model").json()
    assert doc["ok"] and doc["objects"] == []


def test_diff_of_a_bad_ref_is_422_not_a_traceback(client):
    r = client.get("/api/git/diff/model", params={"base": "no-such-ref"})
    assert r.status_code == 422 and r.json()["ok"] is False


def test_breaking_change_names_the_dbt_models_it_breaks(client, repo):
    """The lineage warning is the whole value proposition — assert it reaches the
    wire, not just that the diff is breaking."""
    from mdl_core.repo import ModelRepo

    tables = repo / "physical" / "duckdb_dev" / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    m = ModelRepo.load(repo).model
    le = next(e for e in m.logical_entities.values() if e.name == "counterparty")
    (tables / "dim_counterparty.yaml").write_text(
        "id: 01KZPHYS000000000000000001\n"
        "kind: physical_table\n"
        "name: dim_counterparty\n"
        "target: duckdb_dev\n"
        f"realises: {le.id}\n"
        "materialization: table\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add physical")

    # drop the legal_name attribute via the mutation engine rather than by
    # slicing YAML, so the file stays valid
    from mdl_core.commands import apply_command

    attr = next(a for a in le.attributes if a.name == "legal_name")
    apply_command(repo, "delete_attribute", {"entity_id": le.id, "attribute_id": attr.id})

    doc = client.get("/api/git/diff/model").json()
    entity = next(o for o in doc["objects"] if o["object_kind"] == "logical_entity")
    breaking = [
        f
        for child in entity["children"]
        for f in child["fields"]
        if f["severity"] == "breaking"
    ]
    assert breaking, entity
    assert any(b.get("breaks") for b in breaking)
    assert breaking[0]["breaks"][0]["name"] == "dim_counterparty"


def test_classify_uses_repo_root_relative_paths(tmp_path_factory):
    """classify_paths matches on `model/conceptual/...`, but git -C returns paths
    relative to the model dir. Passing those raw puts everything in `unmatched`
    and the SME sees "no route" — so this must be tested with the model dir as a
    SUBDIRECTORY, which is the standard workspace layout."""
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    root = tmp_path_factory.mktemp("ws")
    nested = root / "model"
    nested.mkdir()
    write_model(nested)
    (root / "transform").mkdir()
    (root / "transform" / "dbt_project.yml").write_text("name: w\n")
    _init(root)

    f = nested / "conceptual" / "entities" / "counterparty.yaml"
    f.write_text(f.read_text().replace("name: Counterparty", "name: Legal Entity"), "utf-8")

    doc = TestClient(create_app(nested)).get("/api/git/classify").json()
    assert doc["ok"]
    assert doc["paths"] == ["model/conceptual/entities/counterparty.yaml"]
    assert doc["primary"] == "A" and doc["primary_name"] == "Meaning"
    assert doc["unmatched"] == []


def test_classify_reads_real_codeowners(tmp_path_factory):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    root = tmp_path_factory.mktemp("ws_owners")
    nested = root / "model"
    nested.mkdir()
    write_model(nested)
    (root / ".github").mkdir()
    (root / ".github" / "CODEOWNERS").write_text(
        "/model/conceptual/  @acme/glossary-council\n"
    )
    _init(root)
    f = nested / "conceptual" / "entities" / "counterparty.yaml"
    f.write_text(f.read_text().replace("name: Counterparty", "name: X"), "utf-8")

    doc = TestClient(create_app(nested)).get("/api/git/classify").json()
    assert doc["reviewers_actual"] == ["@acme/glossary-council"]


def test_conflicts_clean_when_two_branches_touch_different_attributes(client, repo):
    """Proves the SEMANTIC driver is doing the work: a textual merge of the same
    file would conflict; the ULID-keyed union does not."""
    le = repo / "logical" / "entities" / "counterparty.yaml"
    original = le.read_text()

    _git(repo, "checkout", "-q", "-b", "theirs")
    le.write_text(
        original.replace(
            "    nullable: true\n",
            "    nullable: true\n  - id: 01KZATTR00000000000000THR\n"
            "    name: lei_code\n    domain: id_bigint\n    role: attribute\n"
            "    nullable: true\n",
            1,
        )
    )
    _git(repo, "commit", "-qam", "theirs adds lei_code")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "ours")
    le.write_text(
        original.replace(
            "    nullable: true\n",
            "    nullable: true\n  - id: 01KZATTR00000000000000OUR\n"
            "    name: country\n    domain: id_bigint\n    role: attribute\n"
            "    nullable: true\n",
            1,
        )
    )
    _git(repo, "commit", "-qam", "ours adds country")

    doc = client.get("/api/git/conflicts", params={"base": "theirs"}).json()
    assert doc["ok"] and doc["clean"] is True, doc


def test_conflicts_dirty_when_both_edit_the_same_definition(client, repo):
    ce = repo / "conceptual" / "entities" / "counterparty.yaml"
    original = ce.read_text()

    _git(repo, "checkout", "-q", "-b", "theirs2")
    ce.write_text(original.replace("A legal person", "THEIR definition"))
    _git(repo, "commit", "-qam", "theirs")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "ours2")
    ce.write_text(original.replace("A legal person", "OUR definition"))
    _git(repo, "commit", "-qam", "ours")

    doc = client.get("/api/git/conflicts", params={"base": "theirs2"}).json()
    assert doc["clean"] is False
    assert any("definition" in c for f in doc["files"] for c in f["conflicts"]), doc


def test_conflicts_writes_nothing(client, repo):
    before = _git(repo, "status", "--porcelain")
    client.get("/api/git/conflicts", params={"base": "main"})
    assert _git(repo, "status", "--porcelain") == before


def test_context_reports_branch_and_dirtiness(client, repo):
    # The trusted git-config identity (t@t.co) overrides the query-string user,
    # so the branch prefix reflects who the server actually thinks you are (§17).
    doc = client.get("/api/git/context", params={"user": "a.hough"}).json()
    assert doc["git"] and doc["branch"] == "main" and doc["on_base"] is True
    assert doc["dirty"] is False and doc["can_propose"] is True
    assert doc["sme_branch_prefix"] == "sme/t-t-co/"

    (repo / "conceptual" / "entities" / "counterparty.yaml").write_text("broken: true\n")
    doc = client.get("/api/git/context").json()
    # a dirty tree makes propose 409 later, so the banner says so up front
    assert doc["dirty"] is True and doc["can_propose"] is False


def test_proposals_are_fully_populated_without_gh(client, repo, monkeypatch):
    import mdl_server.git_api as ga

    monkeypatch.setattr(ga.shutil, "which", lambda _n: None)  # no gh on PATH

    _git(repo, "checkout", "-q", "-b", "sme/t-t-co/clarify")
    ce = repo / "conceptual" / "entities" / "counterparty.yaml"
    ce.write_text(ce.read_text().replace("A legal person", "Restated"))
    _git(repo, "commit", "-qam", "Clarify Counterparty definition")
    _git(repo, "checkout", "-q", "main")

    doc = client.get("/api/git/proposals").json()
    assert doc["ok"] and doc["gh"] is False
    p = next(x for x in doc["proposals"] if x["branch"] == "sme/t-t-co/clarify")
    # everything but the PR row survives the absence of gh
    assert p["title"] == "Clarify Counterparty definition"
    assert p["pushed"] is False and p["merged"] is False and p["ahead"] == 1
    assert p["pr"] is None


def test_merged_proposal_is_reported_as_merged(client, repo):
    _git(repo, "checkout", "-q", "-b", "sme/t-t-co/syn")
    ce = repo / "conceptual" / "entities" / "counterparty.yaml"
    ce.write_text(ce.read_text() + "\n")
    _git(repo, "commit", "-qam", "Add synonym")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "sme/t-t-co/syn")

    doc = client.get("/api/git/proposals").json()
    p = next(x for x in doc["proposals"] if x["branch"] == "sme/t-t-co/syn")
    assert p["merged"] is True


# --- the read-only gating regression guard --------------------------------------


@pytest.fixture
def ro_client(repo):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    return TestClient(create_app(repo, read_only=True))


@pytest.mark.parametrize(
    "path",
    [
        "/api/git/status",
        "/api/git/branch",
        "/api/git/diff",
        "/api/git/diff/model",
        "/api/git/classify",
        "/api/git/conflicts",
        "/api/git/context",
        "/api/git/proposals",
    ],
)
def test_reads_are_available_in_read_only_mode(ro_client, path):
    """The catalog mounts every model read-only, and `mdl glossary --read-only` is
    the "SME just looking" mode. Both still need to see git state."""
    assert ro_client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/api/git/commit", "/api/git/discard", "/api/git/propose"])
def test_writes_are_absent_in_read_only_mode(ro_client, path):
    assert ro_client.post(path, json={}).status_code in (404, 405)
