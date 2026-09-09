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

    # the branch exists and carries the commit, but the working tree is back on the
    # base branch: leaving it on the proposal branch meant the next reader saw an
    # un-merged proposal as truth, and a second propose stacked onto the first.
    assert data["returned_to"] == "main"
    assert _git(git_model_dir, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "sme/a-hough/clarify-counterparty" in _git(git_model_dir, "branch", "--list", "sme/*")
    # the commit lives on the branch, so read the log from there
    log = _git(git_model_dir, "log", "-1", "--pretty=%B", "sme/a-hough/clarify-counterparty")
    assert "Co-authored-by: a.hough" in log
    assert "prospective parties" in log
    # the edit actually landed
    from mdl_core.repo import ModelRepo

    _git(git_model_dir, "checkout", "sme/a-hough/clarify-counterparty")
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


# --- the proposable-ops allow-list ------------------------------------------------


def _propose(client, ops):
    return client.post(
        "/api/git/propose",
        json={
            "user": "a.hough",
            "title": "t",
            "body": "",
            "changes": [{"op": op, "payload": p} for op, p in ops],
        },
    )


@pytest.mark.parametrize(
    "op",
    ["promote_alignment", "set_kg_base_iri", "delete_domain", "delete_code_set", "delete_subject_area"],
)
def test_refused_ops_are_rejected_with_a_reason(client, git_model_dir, op):
    """The UI hiding a control is not a boundary — a scripted client can post
    anything, so the server has to be the one that says no."""
    r = _propose(client, [(op, {"id": "01NOPE"})])
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert op in body["rejected_ops"]
    assert body["error"] and op in body["error"]


def test_a_rejected_proposal_leaves_no_branch_behind(client, git_model_dir):
    """Rejection happens BEFORE the checkout, so a refused proposal is not merely
    harmless — it leaves no trace to clean up."""
    before = _git(git_model_dir, "branch", "--list", "sme/*")
    r = _propose(client, [("set_kg_base_iri", {"kg_base_iri": "https://evil.example"})])
    assert r.status_code == 422
    assert _git(git_model_dir, "branch", "--list", "sme/*") == before
    assert _git(git_model_dir, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_one_bad_op_rejects_the_whole_proposal(client, git_model_dir):
    """A proposal is atomic: a smuggled op must not ride along with valid ones."""
    from mdl_core.repo import ModelRepo

    ce = next(iter(ModelRepo.load(git_model_dir).model.conceptual_entities))
    r = _propose(
        client,
        [
            ("set_definition", {"id": ce, "definition": "legitimate"}),
            ("set_kg_base_iri", {"kg_base_iri": "https://evil.example"}),
        ],
    )
    assert r.status_code == 422
    # and the legitimate change was not applied either
    assert ModelRepo.load(git_model_dir).model.conceptual_entities[ce].definition != "legitimate"


def test_an_unknown_op_is_rejected_too(client, git_model_dir):
    r = _propose(client, [("drop_everything", {})])
    assert r.status_code == 422 and "drop_everything" in r.json()["rejected_ops"]


def test_the_editing_surface_is_proposable(client, git_model_dir):
    """Everything the modeler app can emit must be accepted, or the UI offers
    controls whose changes cannot be submitted."""
    from mdl_server.git_api import _PROPOSABLE_OPS

    modeler_ops = {
        "set_definition", "set_stewardship", "set_subject_area", "set_pattern",
        "rename_entity", "add_attribute", "update_attribute", "delete_attribute",
        "set_alignment", "clear_alignment", "set_term_map", "clear_term_map",
        "update_synonyms", "set_object_definition", "set_subject_area_members",
        # phase 3
        "create_entity", "delete_entity", "create_relationship",
        "update_relationship", "delete_relationship", "rename_relationship",
    }
    assert modeler_ops <= _PROPOSABLE_OPS


def test_every_command_is_classified(client):
    """No op should be able to appear without a deliberate decision about it."""
    from mdl_server.git_api import _NOT_PROPOSABLE_REASON, _PROPOSABLE_OPS

    from mdl_core.commands import COMMANDS

    unclassified = [
        c for c in COMMANDS if c not in _PROPOSABLE_OPS and c not in _NOT_PROPOSABLE_REASON
    ]
    assert not unclassified, f"new commands need an allow/deny decision: {unclassified}"


# --- provider-agnostic pull-request links -----------------------------------------
#
# Every hosting service prints a "create a pull request" link on push. Reading the
# link the SERVICE gave us works everywhere, where a per-provider URL builder is a
# maintenance surface that never quite covers self-hosted installs.


@pytest.mark.parametrize(
    ("provider", "stderr", "expected"),
    [
        (
            "github",
            "remote: Create a pull request for 'f' on GitHub by visiting:\n"
            "remote:      https://github.com/acme/repo/pull/new/f\n",
            "https://github.com/acme/repo/pull/new/f",
        ),
        (
            "gitlab",
            "remote: To create a merge request for f, visit:\n"
            "remote:   https://gitlab.com/acme/repo/-/merge_requests/new?x=f\n",
            "https://gitlab.com/acme/repo/-/merge_requests/new?x=f",
        ),
        (
            "azure devops",
            "remote: Create a pull request for 'f' on Azure Repos:\n"
            "remote:      https://dev.azure.com/o/p/_git/r/pullrequestcreate?sourceRef=f\n",
            "https://dev.azure.com/o/p/_git/r/pullrequestcreate?sourceRef=f",
        ),
        (
            "bitbucket server",
            "remote: Create pull request for f:\n"
            "remote:   https://bb.acme.com/projects/P/repos/r/compare/commits?sourceBranch=f\n",
            "https://bb.acme.com/projects/P/repos/r/compare/commits?sourceBranch=f",
        ),
        (
            "gitea",
            "remote: Create a new pull request for 'f':\n"
            "remote:   https://git.acme.com/acme/repo/compare/main...f\n",
            "https://git.acme.com/acme/repo/compare/main...f",
        ),
    ],
)
def test_pr_link_is_read_from_the_push_output(provider, stderr, expected):
    from mdl_server.git_api import _pr_url_from_push

    assert _pr_url_from_push(stderr) == expected, provider


def test_a_plain_push_offers_no_link():
    """A bare or self-managed remote prints no create-PR line, and inventing one
    would send the user somewhere that does not exist."""
    from mdl_server.git_api import _pr_url_from_push

    assert (
        _pr_url_from_push(
            "To /srv/git/repo.git\n * [new branch]      f -> f\n"
            "branch 'f' set up to track 'origin/f'.\n"
        )
        is None
    )


def test_web_base_is_structural_not_provider_specific():
    """Self-hosted hosts have to work the same as public ones."""
    from mdl_server.git_api import _web_base

    assert _web_base("git@github.com:acme/repo.git") == "https://github.com/acme/repo"
    assert _web_base("git@git.acme.internal:team/model.git") == "https://git.acme.internal/team/model"
    assert _web_base("https://dev.azure.com/org/proj/_git/repo") == "https://dev.azure.com/org/proj/_git/repo"
    # credentials embedded in a remote must not leak into a link we show or log
    assert _web_base("https://user:tok@git.acme.com/t/m.git") == "https://git.acme.com/t/m"
    assert _web_base("/srv/git/repo.git") is None


def test_a_configured_template_wins(tmp_path, git_model_dir):
    """A provider we have not seen is a few lines of config, not a code change."""
    import subprocess

    from mdl_server.git_api import _compare_url

    proj = git_model_dir / "mdl-project.yaml"
    proj.write_text(
        proj.read_text() + "\ngit:\n  pr_url_template: '{repo}/pullrequestcreate?sourceRef={branch}'\n"
    )
    subprocess.run(
        ["git", "-C", str(git_model_dir), "remote", "add", "origin",
         "https://dev.azure.com/org/proj/_git/repo"],
        check=True,
    )
    assert (
        _compare_url(git_model_dir, "sme/a/x")
        == "https://dev.azure.com/org/proj/_git/repo/pullrequestcreate?sourceRef=sme/a/x"
    )
