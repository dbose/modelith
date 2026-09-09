"""Identity resolution + safe defaults + author precedence (spec §17).

Pure unit tests — no server. The one rule under test everywhere: a raw request
header is trusted ONLY when the operator opted in, and a trusted identity overrides
any self-asserted client string.
"""

from __future__ import annotations

import subprocess

import pytest
from mdl_server.identity import (
    ENV_NAME_HEADER,
    ENV_REQUIRE,
    ENV_SECRET_HEADER,
    ENV_SECRET_VALUE,
    ENV_USER_HEADER,
    IdentityPolicy,
    _name_from_email,
    effective_author,
    resolve_identity,
    write_denied_reason,
)


def _off() -> IdentityPolicy:
    """A policy with header auth OFF — the default a solo user runs under."""
    return IdentityPolicy.from_env({})


@pytest.fixture
def git_dir(tmp_path):
    """A dir with a local git config identity (the solo path)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Solo Sam"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "sam@laptop.local"], check=True
    )
    return tmp_path


@pytest.fixture
def bare_dir(tmp_path, monkeypatch):
    """A dir with NO git identity at all — the true anonymous case. We isolate git
    from any global/system config on the test machine (a developer's ~/.gitconfig
    would otherwise supply a real identity and mask 'anonymous'), which is the
    point: on a laptop WITH a global gitconfig the solo path correctly resolves to
    that identity — see the git_dir tests — so 'anonymous' can only be exercised by
    isolating it."""
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    d = tmp_path / "work"
    d.mkdir()
    return d


# --- the safe default: a raw header is never trusted without opt-in ---------------


def test_raw_header_ignored_when_auth_off(bare_dir):
    """The single most important test: header auth off + a spoofed X-Forwarded-User
    header => anonymous, NEVER proxy. An app exposed with no config is safe."""
    ident = resolve_identity(
        {"X-Forwarded-User": "attacker@evil.com"}, _off(), bare_dir
    )
    assert ident.source == "anonymous"
    assert ident.name == "" and ident.email == ""


def test_git_config_used_when_no_proxy(git_dir):
    ident = resolve_identity({"X-Forwarded-User": "ignored@x.com"}, _off(), git_dir)
    assert ident.source == "git"
    assert ident.name == "Solo Sam"
    assert ident.email == "sam@laptop.local"


def test_anonymous_when_nothing_known(bare_dir):
    assert resolve_identity({}, _off(), bare_dir).source == "anonymous"


# --- the proxy path (opted in) ----------------------------------------------------


def test_proxy_header_trusted_when_opted_in(bare_dir):
    policy = IdentityPolicy.from_env({ENV_USER_HEADER: "X-Auth-Request-Email"})
    ident = resolve_identity(
        {"X-Auth-Request-Email": "anita.hough@corp.com"}, policy, bare_dir
    )
    assert ident.source == "proxy"
    assert ident.email == "anita.hough@corp.com"
    assert ident.name == "Anita Hough"  # derived from the local-part


def test_proxy_name_header_wins_over_derived(bare_dir):
    policy = IdentityPolicy.from_env(
        {ENV_USER_HEADER: "X-Auth-Request-Email", ENV_NAME_HEADER: "X-Auth-Request-User"}
    )
    ident = resolve_identity(
        {"X-Auth-Request-Email": "ah@corp.com", "X-Auth-Request-User": "Dr Anita Hough"},
        policy,
        bare_dir,
    )
    assert ident.name == "Dr Anita Hough"


def test_proxy_falls_through_when_header_blank(git_dir):
    """Header auth on but the proxy sent no user header on this request => not an
    error, fall through to git config."""
    policy = IdentityPolicy.from_env({ENV_USER_HEADER: "X-Auth-Request-Email"})
    ident = resolve_identity({}, policy, git_dir)
    assert ident.source == "git"


# --- the shared-secret gate -------------------------------------------------------


def _secret_policy() -> IdentityPolicy:
    return IdentityPolicy.from_env(
        {
            ENV_USER_HEADER: "X-User",
            ENV_SECRET_HEADER: "X-Proxy-Secret",
            ENV_SECRET_VALUE: "s3cr3t",
        }
    )


def test_secret_match_trusts_header(bare_dir):
    ident = resolve_identity(
        {"X-User": "a@b.com", "X-Proxy-Secret": "s3cr3t"}, _secret_policy(), bare_dir
    )
    assert ident.source == "proxy"


def test_secret_mismatch_does_not_trust(bare_dir):
    ident = resolve_identity(
        {"X-User": "a@b.com", "X-Proxy-Secret": "wrong"}, _secret_policy(), bare_dir
    )
    assert ident.source == "anonymous"


def test_secret_missing_does_not_trust(bare_dir):
    ident = resolve_identity({"X-User": "a@b.com"}, _secret_policy(), bare_dir)
    assert ident.source == "anonymous"


def test_secret_header_without_value_fails_closed(bare_dir):
    """A secret HEADER configured with no secret VALUE must disable header auth
    entirely rather than trust the user header on a header's mere existence."""
    policy = IdentityPolicy.from_env(
        {ENV_USER_HEADER: "X-User", ENV_SECRET_HEADER: "X-Proxy-Secret"}
    )
    assert policy.header_auth_enabled is False
    assert policy.misconfigured_secret(
        {ENV_USER_HEADER: "X-User", ENV_SECRET_HEADER: "X-Proxy-Secret"}
    )
    ident = resolve_identity({"X-User": "a@b.com"}, policy, bare_dir)
    assert ident.source == "anonymous"


# --- author precedence ------------------------------------------------------------


def test_effective_author_proxy_overrides_client(bare_dir):
    policy = IdentityPolicy.from_env({ENV_USER_HEADER: "X-User"})
    ident = resolve_identity({"X-User": "real@corp.com"}, policy, bare_dir)
    author = effective_author(ident, "totally.someone.else")
    assert author.source == "proxy"
    assert author.email == "real@corp.com"  # the spoofed client string is ignored


def test_effective_author_git_overrides_client(git_dir):
    ident = resolve_identity({}, _off(), git_dir)
    author = effective_author(ident, "someone.else")
    assert author.source == "git"
    assert author.email == "sam@laptop.local"


def test_effective_author_falls_back_to_client_when_anonymous(bare_dir):
    ident = resolve_identity({}, _off(), bare_dir)
    author = effective_author(ident, "a.hough")
    assert author.source == "anonymous"
    assert author.name == "a.hough"
    assert author.email == "a-hough@users.noreply.github.com"


def test_effective_author_anonymous_and_empty_stays_anonymous(bare_dir):
    ident = resolve_identity({}, _off(), bare_dir)
    author = effective_author(ident, "")
    assert author.source == "anonymous"
    assert author.name == ""


def test_name_from_email():
    assert _name_from_email("anita.hough@corp.com") == "Anita Hough"
    assert _name_from_email("a_b-c@x.io") == "A B C"
    assert _name_from_email("plainuser") == "Plainuser"


# --- the MDL_AUTH_REQUIRE write gate (spec §18 M8) --------------------------------


def test_require_off_by_default(bare_dir):
    policy = IdentityPolicy.from_env({})
    assert policy.require_identity is False
    ident = resolve_identity({}, policy, bare_dir)  # anonymous
    assert write_denied_reason(ident, policy) is None  # solo never blocked


def test_require_blocks_anonymous_write(bare_dir):
    policy = IdentityPolicy.from_env({ENV_REQUIRE: "1"})
    assert policy.require_identity is True
    ident = resolve_identity({}, policy, bare_dir)
    assert ident.source == "anonymous"
    reason = write_denied_reason(ident, policy)
    assert reason and "authenticated identity" in reason


def test_require_allows_proxy_write(bare_dir):
    policy = IdentityPolicy.from_env({ENV_REQUIRE: "yes", ENV_USER_HEADER: "X-User"})
    ident = resolve_identity({"X-User": "a@b.com"}, policy, bare_dir)
    assert ident.source == "proxy"
    assert write_denied_reason(ident, policy) is None


def test_require_allows_git_write(git_dir):
    policy = IdentityPolicy.from_env({ENV_REQUIRE: "true"})
    ident = resolve_identity({}, policy, git_dir)
    assert ident.source == "git"
    assert write_denied_reason(ident, policy) is None


def test_require_various_truthy_values():
    for v in ("1", "true", "TRUE", "Yes", "on", "require"):
        assert IdentityPolicy.from_env({ENV_REQUIRE: v}).require_identity is True
    for v in ("0", "false", "no", "", "off"):
        assert IdentityPolicy.from_env({ENV_REQUIRE: v}).require_identity is False
