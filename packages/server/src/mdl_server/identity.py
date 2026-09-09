"""Who is this request from? — a thin, IdP-agnostic identity seam (spec §17).

Two personas, one rule each:

  solo       Nobody logs in. The acting user is whoever the model dir's git config
             says — exactly like a normal `git commit`. Zero configuration, zero
             friction. This is the default and needs nothing set up.

  enterprise A reverse proxy (oauth2-proxy, Pomerium, cloudflared, nginx
             auth_request, Azure App Proxy, …) authenticates against the org IdP —
             OIDC, SAML, whatever — and injects an identity header. Modelith trusts
             THAT header and implements no OAuth/OIDC/SAML flow of its own, so every
             IdP "just works" without Modelith owning redirect URIs, client secrets,
             token validation, or four provider code paths.

Safe by default: a raw request header is NEVER trusted unless the operator explicitly
named it via MDL_AUTH_TRUSTED_USER_HEADER. An app accidentally exposed to the internet
with no config will not honour a client-supplied X-Forwarded-User — it resolves to the
server's git identity, or to anonymous. Trusted-header auth is only sound when the app
is not directly reachable and only the proxy can reach it (bind to localhost / a
private network; the proxy terminates public traffic). Modelith cannot verify network
topology, so it documents the contract and fails closed.

Identity is a WRITE-time concern: it decides who a proposal is attributed to. It never
gates a read, and it is not authorization — who may MERGE stays with git (branch
protection + CODEOWNERS).
"""

from __future__ import annotations

import hmac
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Env var names, namespaced MDL_AUTH_* (see spec §17.2).
ENV_USER_HEADER = "MDL_AUTH_TRUSTED_USER_HEADER"
ENV_NAME_HEADER = "MDL_AUTH_TRUSTED_NAME_HEADER"
ENV_SECRET_HEADER = "MDL_AUTH_PROXY_SECRET_HEADER"
ENV_SECRET_VALUE = "MDL_AUTH_PROXY_SECRET"
ENV_REQUIRE = "MDL_AUTH_REQUIRE"

# Values that turn the write gate ON (case-insensitive). Anything else — including
# unset — leaves it off, so solo stays frictionless.
_TRUTHY = {"1", "true", "yes", "on", "require"}


@dataclass(frozen=True)
class Identity:
    """The resolved actor. `source` records HOW we know who this is, so the propose
    flow can decide whether to trust it over a self-asserted client string."""

    name: str  # display / Co-authored-by name, e.g. "Anita Hough"
    email: str  # for the git author/trailer; may be synthesized from the name
    source: str  # "proxy" | "git" | "anonymous"

    @property
    def is_trusted(self) -> bool:
        """A proxy- or git-established identity outranks anything the client sends."""
        return self.source in ("proxy", "git")


ANONYMOUS = Identity(name="", email="", source="anonymous")


@dataclass(frozen=True)
class IdentityPolicy:
    """Resolved ONCE at create_app time from the environment, then immutable. The
    presence of a trusted user header is the master switch for header auth."""

    trusted_user_header: str | None
    trusted_name_header: str | None
    trusted_secret_header: str | None
    trusted_secret_value: str | None
    # When True, a write by an anonymous (unestablished) identity is refused with 403
    # rather than falling back to a self-asserted author. For shared/enterprise
    # deployments that want to hard-fail instead of silently accepting "you". Off by
    # default, so a solo user with no git identity is never blocked from proposing.
    require_identity: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> IdentityPolicy:
        env = os.environ if env is None else env
        user_header = (env.get(ENV_USER_HEADER) or "").strip() or None
        name_header = (env.get(ENV_NAME_HEADER) or "").strip() or None
        secret_header = (env.get(ENV_SECRET_HEADER) or "").strip() or None
        secret_value = env.get(ENV_SECRET_VALUE) or None
        require = (env.get(ENV_REQUIRE) or "").strip().lower() in _TRUTHY

        # Fail closed: a configured secret GATE with no secret VALUE to check against
        # would trust the user header on nothing more than "a header named X exists".
        # Rather than trust it, disable header auth entirely and let the caller warn.
        if secret_header and not secret_value:
            user_header = None
            name_header = None
            secret_header = None
            secret_value = None

        return cls(
            trusted_user_header=user_header,
            trusted_name_header=name_header,
            trusted_secret_header=secret_header,
            trusted_secret_value=secret_value,
            require_identity=require,
        )

    @property
    def header_auth_enabled(self) -> bool:
        return bool(self.trusted_user_header)

    def misconfigured_secret(self, env: Mapping[str, str] | None = None) -> bool:
        """True when the operator set a secret HEADER but no secret VALUE — the
        fail-closed case from_env swallowed. Surfaced only so create_app can log a
        one-line startup warning; resolution itself is already safe."""
        env = os.environ if env is None else env
        return bool((env.get(ENV_SECRET_HEADER) or "").strip()) and not (
            env.get(ENV_SECRET_VALUE) or None
        )


def _name_from_email(email: str) -> str:
    """Best-effort display name from an email local-part: "anita.hough@corp.com"
    -> "Anita Hough". Falls back to the whole string when there is no local-part."""
    local = email.split("@", 1)[0] if "@" in email else email
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p]
    return " ".join(p.capitalize() for p in parts) if parts else email


def _from_trusted_header(headers: Mapping[str, str], policy: IdentityPolicy) -> Identity | None:
    """The proxy path. Returns None (never raises) unless header auth is on, the
    optional shared secret matches, and a non-empty user header is present."""
    if not policy.header_auth_enabled:
        return None

    # Prove the request came THROUGH the proxy, not from a client spoofing the user
    # header directly. Constant-time compare so a timing side-channel can't probe it.
    if policy.trusted_secret_header:
        presented = headers.get(policy.trusted_secret_header, "")
        if not hmac.compare_digest(presented, policy.trusted_secret_value or ""):
            return None

    assert policy.trusted_user_header is not None  # header_auth_enabled guarantees it
    user = (headers.get(policy.trusted_user_header) or "").strip()
    if not user:
        return None

    name = ""
    if policy.trusted_name_header:
        name = (headers.get(policy.trusted_name_header) or "").strip()
    email = user if "@" in user else ""
    if not name:
        name = _name_from_email(user) if email else user
    if not email:
        # A bare user id with no name header: synthesize a noreply address so the
        # commit trailer is still well-formed.
        email = f"{user}@users.noreply"
    return Identity(name=name, email=email, source="proxy")


def _from_git_config(model_dir: Path) -> Identity | None:
    """The solo path: whoever this laptop's git config says. None if git config is
    empty (or git is absent) — then we degrade to anonymous."""

    def _cfg(key: str) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", str(model_dir), "config", key],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""

    name = _cfg("user.name")
    email = _cfg("user.email")
    if not name and not email:
        return None
    if not name:
        name = _name_from_email(email)
    if not email:
        email = "unknown@users.noreply"
    return Identity(name=name, email=email, source="git")


def resolve_identity(
    headers: Mapping[str, str], policy: IdentityPolicy, model_dir: Path
) -> Identity:
    """Trusted header (only if opted in) -> git config -> anonymous.

    A missing/blank trusted header when header auth is on is not an error: it means
    "the proxy did not authenticate this request", and we fall through to git/anonymous
    rather than trusting anything raw."""
    ident = _from_trusted_header(headers, policy)
    if ident is not None:
        return ident
    ident = _from_git_config(model_dir)
    if ident is not None:
        return ident
    return ANONYMOUS


def effective_author(ident: Identity, client_user: str) -> Identity:
    """Who a proposal is attributed to.

    A trusted identity (proxy or git) WINS over the self-asserted client string —
    that is the whole point: the commit author becomes trustworthy. Only when nothing
    is established (anonymous) do we fall back to the client-supplied name, preserving
    today's zero-config behaviour so nothing regresses."""
    if ident.is_trusted:
        return ident
    user = (client_user or "").strip()
    if not user:
        return ANONYMOUS
    email = user if "@" in user else f"{_slug(user)}@users.noreply.github.com"
    name = user if "@" not in user else _name_from_email(user)
    return Identity(name=name, email=email, source="anonymous")


def write_denied_reason(ident: Identity, policy: IdentityPolicy) -> str | None:
    """When a write should be refused, the message to return (403); otherwise None.

    Only fires when the operator set MDL_AUTH_REQUIRE and the request has no
    established identity — the shared-server "no anonymous writes" gate. A trusted
    proxy/git identity always passes; an unconfigured (solo) server always passes."""
    if policy.require_identity and not ident.is_trusted:
        return (
            "this deployment requires an authenticated identity to make changes "
            "(MDL_AUTH_REQUIRE is set) and none was established for this request"
        )
    return None


def _slug(s: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")
    return s or "change"
