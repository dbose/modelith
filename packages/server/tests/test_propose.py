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
    # the git-config identity (t@t.co) now authors the proposal, overriding the
    # self-asserted "a.hough" — this is the §17 identity behaviour.
    assert data["branch"] == "sme/t-t-co/clarify-counterparty"
    # no origin remote -> graceful message, not an error
    assert data["pushed"] is False
    assert "no `origin` remote" in data["message"]

    # the branch exists and carries the commit, but the working tree is back on the
    # base branch: leaving it on the proposal branch meant the next reader saw an
    # un-merged proposal as truth, and a second propose stacked onto the first.
    assert data["returned_to"] == "main"
    assert _git(git_model_dir, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "sme/t-t-co/clarify-counterparty" in _git(git_model_dir, "branch", "--list", "sme/*")
    # the commit lives on the branch, so read the log from there
    log = _git(git_model_dir, "log", "-1", "--pretty=%B", "sme/t-t-co/clarify-counterparty")
    assert "Co-authored-by: t <t@t.co>" in log
    assert "prospective parties" in log
    # the edit actually landed
    from mdl_core.repo import ModelRepo

    _git(git_model_dir, "checkout", "sme/t-t-co/clarify-counterparty")
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


# --- pull-request links, per provider ---------------------------------------------
#
# Built from the remote rather than scraped from push output: the host prints its
# link only on the FIRST push, so scraping silently yields nothing exactly when
# someone amends a proposal.


@pytest.mark.parametrize(
    ("label", "remote", "expected"),
    [
        (
            "github ssh",
            "git@github.com:acme/repo.git",
            "https://github.com/acme/repo/pull/new/sme/a/x",
        ),
        (
            "github https",
            "https://github.com/acme/repo.git",
            "https://github.com/acme/repo/pull/new/sme/a/x",
        ),
        (
            "gitlab",
            "git@gitlab.com:acme/repo.git",
            "https://gitlab.com/acme/repo/-/merge_requests/new"
            "?merge_request%5Bsource_branch%5D=sme%2Fa%2Fx",
        ),
        (
            "bitbucket",
            "https://bitbucket.org/acme/repo.git",
            "https://bitbucket.org/acme/repo/pull-requests/new?source=sme%2Fa%2Fx",
        ),
        (
            "azure https",
            "https://dev.azure.com/org/proj/_git/repo",
            "https://dev.azure.com/org/proj/_git/repo/pullrequestcreate?sourceRef=sme%2Fa%2Fx",
        ),
        (
            "azure https with user",
            "https://org@dev.azure.com/org/proj/_git/repo",
            "https://dev.azure.com/org/proj/_git/repo/pullrequestcreate?sourceRef=sme%2Fa%2Fx",
        ),
        (
            "azure ssh (v3, no _git)",
            "git@ssh.dev.azure.com:v3/org/proj/repo",
            "https://dev.azure.com/org/proj/_git/repo/pullrequestcreate?sourceRef=sme%2Fa%2Fx",
        ),
        (
            "azure legacy host",
            "https://org.visualstudio.com/proj/_git/repo",
            "https://org.visualstudio.com/proj/_git/repo/pullrequestcreate?sourceRef=sme%2Fa%2Fx",
        ),
    ],
)
def test_pr_url_is_built_from_the_remote(label, remote, expected):
    from mdl_server.git_api import _provider_pr_url

    assert _provider_pr_url(remote, "sme/a/x") == expected, label


def test_github_keeps_branch_slashes_but_others_encode_them():
    """GitHub carries the branch in the PATH (`sme/a/x` is three segments); everyone
    else puts it in a query parameter, where the slashes must be encoded."""
    from mdl_server.git_api import _provider_pr_url

    gh = _provider_pr_url("git@github.com:acme/repo.git", "sme/a.hough/clarify")
    assert gh.endswith("/pull/new/sme/a.hough/clarify")
    gl = _provider_pr_url("git@gitlab.com:acme/repo.git", "sme/a.hough/clarify")
    assert "sme%2Fa.hough%2Fclarify" in gl


def test_an_unknown_host_gets_no_invented_url():
    """A self-hosted install we cannot identify needs `git.provider` in the project
    file. Guessing would send someone to a URL that does not exist."""
    from mdl_server.git_api import _provider_pr_url

    assert _provider_pr_url("git@git.acme.internal:team/model.git", "f") is None
    assert _provider_pr_url("/srv/git/repo.git", "f") is None


def test_credentials_in_a_remote_never_reach_the_link():
    from mdl_server.git_api import parse_remote

    ref = parse_remote("https://user:ghp_secret@github.com/acme/repo.git")
    assert ref is not None
    assert "ghp_secret" not in ref.web
    assert ref.web == "https://github.com/acme/repo"


def test_a_self_hosted_provider_can_be_declared(git_model_dir):
    """A self-hosted GitLab is one line of config, not a code change."""
    import subprocess

    from mdl_server.git_api import _compare_url

    proj = git_model_dir / "mdl-project.yaml"
    proj.write_text(proj.read_text() + "\ngit:\n  provider: gitlab\n")
    subprocess.run(
        ["git", "-C", str(git_model_dir), "remote", "add", "origin",
         "git@git.acme.internal:team/model.git"],
        check=True,
    )
    url = _compare_url(git_model_dir, "sme/a/x")
    assert url == (
        "https://git.acme.internal/team/model/-/merge_requests/new"
        "?merge_request%5Bsource_branch%5D=sme%2Fa%2Fx"
    )


def test_a_template_overrides_everything(git_model_dir):
    """The escape hatch for a host with a shape we do not implement."""
    import subprocess

    from mdl_server.git_api import _compare_url

    proj = git_model_dir / "mdl-project.yaml"
    proj.write_text(
        proj.read_text() + "\ngit:\n  pr_url_template: '{repo}/newpr?from={branch}'\n"
    )
    subprocess.run(
        ["git", "-C", str(git_model_dir), "remote", "add", "origin",
         "https://git.acme.internal/t/m.git"],
        check=True,
    )
    assert (
        _compare_url(git_model_dir, "sme/a/x")
        == "https://git.acme.internal/t/m/newpr?from=sme/a/x"
    )


def test_the_hosts_own_link_is_still_preferred_when_present():
    """A host that tells us the exact URL beats our reconstruction of it — but it
    only does so on a first push, which is why it cannot be the mechanism."""
    from mdl_server.git_api import _pr_url_from_push

    assert (
        _pr_url_from_push(
            "remote: Create a pull request for 'f' on GitHub by visiting:\n"
            "remote:      https://github.com/acme/repo/pull/new/f\n"
        )
        == "https://github.com/acme/repo/pull/new/f"
    )
    # a re-push prints no such line — this is the case that made scraping unsafe
    assert _pr_url_from_push("To github.com:acme/repo.git\n   abc..def  f -> f\n") is None


# --- identity: the server-resolved author overrides a self-asserted string (§17) ---


def test_propose_trusts_proxy_identity_over_client_user(git_model_dir, monkeypatch):
    """A proxy-authenticated user proposing with a DIFFERENT self-asserted body.user
    must produce a commit attributed to the proxy identity, not the spoofed string."""
    from fastapi.testclient import TestClient
    from mdl_server import create_app
    from mdl_server.identity import ENV_USER_HEADER

    monkeypatch.setenv(ENV_USER_HEADER, "X-Auth-Request-Email")
    client = TestClient(create_app(git_model_dir))

    doc = client.get("/api/glossary/terms").json()
    cpty = next(t for t in doc["terms"] if t["name"] == "Counterparty")

    resp = client.post(
        "/api/git/propose",
        headers={"X-Auth-Request-Email": "real.user@corp.com"},
        json={
            "user": "impostor",  # self-asserted; must be ignored
            "slug": "clarify",
            "title": "Clarify",
            "changes": [
                {"op": "set_definition", "payload": {"id": cpty["id"], "definition": "x."}}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    branch = resp.json()["branch"]
    # branch prefix derives from the proxy identity, not "impostor"
    assert branch.startswith("sme/real-user-corp-com/")
    assert "impostor" not in branch

    body = _git(git_model_dir, "log", branch, "-1", "--format=%b")
    assert "real.user@corp.com" in body
    assert "impostor" not in body


def test_api_model_reports_proxy_identity(git_model_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from mdl_server import create_app
    from mdl_server.identity import ENV_USER_HEADER

    monkeypatch.setenv(ENV_USER_HEADER, "X-Auth-Request-Email")
    client = TestClient(create_app(git_model_dir))
    ident = client.get(
        "/api/model", headers={"X-Auth-Request-Email": "anita.hough@corp.com"}
    ).json()["identity"]
    assert ident["source"] == "proxy"
    assert ident["name"] == "Anita Hough"


def test_api_model_reports_git_identity_solo(client):
    """No proxy configured: /api/model greets the git-config user (the fixture sets
    user.name=t / user.email=t@t.co)."""
    ident = client.get("/api/model").json()["identity"]
    assert ident["source"] == "git"
    assert ident["email"] == "t@t.co"


def test_propose_solo_still_attributes_without_proxy(client, git_model_dir):
    """Regression: with no proxy policy, propose attributes to the git-config identity
    and the flow still succeeds."""
    doc = client.get("/api/glossary/terms").json()
    cpty = next(t for t in doc["terms"] if t["name"] == "Counterparty")
    resp = client.post(
        "/api/git/propose",
        json={
            "user": "a.hough",
            "slug": "solo-change",
            "title": "Solo change",
            "changes": [
                {"op": "set_definition", "payload": {"id": cpty["id"], "definition": "y."}}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    branch = resp.json()["branch"]
    # git config identity (t@t.co) wins over the self-asserted "a.hough"
    assert branch.startswith("sme/t-t-co/")
    body = _git(git_model_dir, "log", branch, "-1", "--format=%b")
    assert "t@t.co" in body
