"""Propose-as-PR flow (SME app): branch -> apply commands -> commit -> (push/gh)."""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture
def git_model_dir(model_dir):
    """The fixture model in a git repo with an initial commit on `main`."""
    subprocess.run(["git", "init", "-q", str(model_dir)], check=True)
    subprocess.run(["git", "-C", str(model_dir), "config", "user.email", "t@t.co"], check=True)
    subprocess.run(["git", "-C", str(model_dir), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(model_dir), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(model_dir), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(model_dir), "branch", "-M", "main"], check=True)
    return model_dir


@pytest.fixture
def client(git_model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    return TestClient(create_app(git_model_dir))


def _git(d, *a):
    return subprocess.run(
        ["git", "-C", str(d), *a], capture_output=True, text=True
    ).stdout.strip()


def test_propose_creates_branch_and_coauthored_commit(client, git_model_dir):
    # find the counterparty conceptual entity to edit its definition
    doc = client.get("/api/glossary/terms").json()
    cpty = next(t for t in doc["terms"] if t["name"] == "Counterparty")

    resp = client.post(
        "/api/git/propose",
        json={
            "user": "a.hough",
            "slug": "clarify-counterparty",
            "title": "Clarify Counterparty definition",
            "body": "Counterparty now explicitly includes prospective parties.",
            "changes": [
                {
                    "op": "set_definition",
                    "payload": {
                        "id": cpty["id"],
                        "definition": "A legal person with whom the firm has or may have a contractual obligation.",
                    },
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] and data["applied"] == 1
    assert data["branch"] == "sme/a-hough/clarify-counterparty"
    # no origin remote -> graceful message, not an error
    assert data["pushed"] is False
    assert "no `origin` remote" in data["message"]

    # the branch exists, is checked out, and the commit carries the trailer
    assert _git(git_model_dir, "rev-parse", "--abbrev-ref", "HEAD") == "sme/a-hough/clarify-counterparty"
    log = _git(git_model_dir, "log", "-1", "--pretty=%B")
    assert "Co-authored-by: a.hough" in log
    assert "prospective parties" in log
    # the edit actually landed
    from mdl_core.repo import ModelRepo

    repo = ModelRepo.load(git_model_dir)
    ce = repo.model.conceptual_entities[cpty["id"]]
    assert "may have" in (ce.definition or "")


def test_propose_refuses_dirty_tree(client, git_model_dir):
    (git_model_dir / "mdl-project.yaml").write_text(
        (git_model_dir / "mdl-project.yaml").read_text() + "# dirty\n"
    )
    resp = client.post(
        "/api/git/propose",
        json={"user": "sme", "title": "x", "changes": []},
    )
    assert resp.status_code == 409
    assert "uncommitted" in resp.json()["error"]


def test_propose_invalid_command_rolls_back(client, git_model_dir):
    resp = client.post(
        "/api/git/propose",
        json={
            "user": "sme",
            "title": "bad",
            "changes": [{"op": "set_definition", "payload": {"id": "01NOPE", "definition": "x"}}],
        },
    )
    assert resp.status_code == 422
    # tree is clean again (rolled back), still on the sme branch or main
    assert _git(git_model_dir, "status", "--porcelain") == ""


def test_branch_endpoint(client):
    assert client.get("/api/git/branch").json()["branch"] == "main"
