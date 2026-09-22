"""Fetch a shared reverse: config from a source, via a PLUGGABLE scheme-resolver registry.

`mdl reverse-config import <src>` accepts a local file, an https URL, a GitHub/GitLab blob
URL or a `github:`/`gitlab:` shorthand — and is EXTENSIBLE: a source is dispatched to the
resolver registered for its scheme. The open CLI ships `file`, `http(s)`, `github` and
`gitlab` resolvers. A paid/enterprise package can register a new scheme — e.g. an
`mdl://acme/standards/finance@v2` Global-Standards registry — by exposing a resolver on the
`modelith.config_resolvers` entry-point group; installing the package makes `mdl://` imports
just work, with zero change to the open CLI. That is the whole point of the seam.

A resolver takes the source string + a small context (token, allow_insecure, timeout) and
returns the config TEXT (YAML/JSON). Guards (https-only, size cap, HTML rejection) live in
the built-in http resolver; a custom resolver owns its own transport and guards.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB — any real config is KB; larger is a mistake.

_GH_BLOB = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/(.+)$")
_GL_BLOB = re.compile(r"^https?://gitlab\.com/([^/]+)/([^/]+)/-/blob/(.+)$")
_SHORT = re.compile(r"^(github|gitlab):([^/]+)/([^/@]+)/(.+?)(?:@([^@]+))?$")
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://")


class ImportError_(Exception):
    """A user-facing import failure (bad source, insecure, too big, not a config)."""


@dataclass
class ResolveContext:
    """What a resolver may need beyond the source string."""

    token: str | None = None
    allow_insecure: bool = False
    timeout: float = 15.0


@runtime_checkable
class ConfigResolver(Protocol):
    """Resolve a source of a given scheme to the config text. `schemes` are the URL
    schemes (or shorthand prefixes) this resolver claims, e.g. ("https","http") or
    ("mdl",). `resolve` returns the text or raises ImportError_."""

    schemes: tuple[str, ...]

    def resolve(self, src: str, ctx: ResolveContext) -> str: ...


# --- scheme registry ---------------------------------------------------------

_REGISTRY: dict[str, ConfigResolver] = {}


def register_resolver(resolver: ConfigResolver) -> None:
    """Register a resolver for its scheme(s). Later registrations win (so a plugin can
    override a built-in if it must). Called at import time by built-ins and, via the
    entry-point discovery below, by installed plugin packages."""
    for scheme in resolver.schemes:
        _REGISTRY[scheme.lower()] = resolver


def _scheme_of(src: str) -> str:
    """The scheme that dispatches `src`: an explicit `<scheme>://`, a `github:`/`gitlab:`
    shorthand prefix, or `file` for a bare path."""
    m = _SCHEME.match(src)
    if m:
        return m.group(1).lower()
    m = _SHORT.match(src)
    if m:
        return m.group(1).lower()
    return "file"


_plugins_loaded = False


def _load_plugins() -> None:
    """Discover resolver plugins exposed on the `modelith.config_resolvers` entry-point
    group. Each entry point loads to a callable returning a ConfigResolver (or the class
    itself). Failures are swallowed — a broken plugin must never block a normal import."""
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        group = (
            eps.select(group="modelith.config_resolvers")
            if hasattr(eps, "select")
            else eps.get("modelith.config_resolvers", [])
        )
        for ep in group:
            try:
                obj = ep.load()
                resolver = obj() if callable(obj) and not isinstance(obj, ConfigResolver) else obj
                register_resolver(resolver)
            except Exception:  # noqa: BLE001 - a bad plugin must not break import
                continue
    except Exception:  # noqa: BLE001
        return


# --- built-in resolvers ------------------------------------------------------


def normalize_source(src: str) -> str:
    """Turn a human-facing git URL / shorthand into a raw-file URL (see the git resolver).
    A raw URL, a plain path, or an unrecognised URL passes through unchanged. Kept as a
    module function because tests and the git resolver both use it."""
    m = _GH_BLOB.match(src)
    if m:
        owner, repo, rest = m.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"
    m = _GL_BLOB.match(src)
    if m:
        owner, repo, rest = m.groups()
        return f"https://gitlab.com/{owner}/{repo}/-/raw/{rest}"
    m = _SHORT.match(src)
    if m:
        host, owner, repo, path, ref = m.groups()
        ref = ref or "HEAD"
        if host == "github":
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        return f"https://gitlab.com/{owner}/{repo}/-/raw/{ref}/{path}"
    return src


class _HttpResolver:
    schemes = ("https", "http", "github", "gitlab")

    def resolve(self, src: str, ctx: ResolveContext) -> str:
        import httpx

        url = normalize_source(src)
        if url.startswith("http://") and not ctx.allow_insecure:
            raise ImportError_(
                "refusing to import over plain http:// (use https, or pass --allow-insecure)"
            )
        tok = ctx.token or os.environ.get("MODELITH_IMPORT_TOKEN") or os.environ.get("GITHUB_TOKEN")
        headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        try:
            with httpx.Client(follow_redirects=True, timeout=ctx.timeout, headers=headers) as c:
                resp = c.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            hint = " (private repo? pass a token)" if code in (401, 403, 404) else ""
            raise ImportError_(f"fetch failed: HTTP {code}{hint}") from e
        except Exception as e:  # noqa: BLE001 - network/DNS/timeout -> one clean message
            raise ImportError_(f"could not fetch {url}: {e}") from e
        if len(resp.content) > _MAX_BYTES:
            raise ImportError_(
                f"config is too large ({len(resp.content)} bytes > {_MAX_BYTES} cap)"
            )
        ctype = (resp.headers.get("content-type") or "").lower()
        text = resp.text
        if "text/html" in ctype or text.lstrip()[:1] == "<":
            raise ImportError_(
                "the URL returned an HTML page, not a config — use the RAW file URL "
                "(or a github:/gitlab: shorthand)"
            )
        return text


class _FileResolver:
    schemes = ("file",)

    def resolve(self, src: str, ctx: ResolveContext) -> str:
        from pathlib import Path

        p = Path(src[len("file://"):] if src.startswith("file://") else src)
        if not p.exists():
            raise ImportError_(f"no such file: {src}")
        return p.read_text(encoding="utf-8")


register_resolver(_HttpResolver())
register_resolver(_FileResolver())


# --- public API --------------------------------------------------------------


def is_remote(src: str) -> bool:
    """True when `src` is anything but a local file path (a URL, a shorthand, or a custom
    scheme like mdl://). Used by the CLI to decide the 'missing file' vs 'fetch' message."""
    return _scheme_of(src) != "file"


# kept for back-compat with callers/tests that used the old name
def is_url(src: str) -> bool:
    return is_remote(src)


def fetch_config_text(
    src: str, *, token: str | None = None, allow_insecure: bool = False, timeout: float = 15.0
) -> str:
    """Resolve `src` to its reverse: config text via the registered resolver for its
    scheme. Loads plugin resolvers (entry-point group `modelith.config_resolvers`) on first
    use, so an installed enterprise package's scheme (e.g. mdl://) is picked up
    automatically. Raises ImportError_ on any failure."""
    _load_plugins()
    scheme = _scheme_of(src)
    resolver = _REGISTRY.get(scheme)
    if resolver is None:
        known = ", ".join(sorted(_REGISTRY))
        raise ImportError_(
            f"no import handler for '{scheme}://' sources. Known schemes: {known}. "
            "An enterprise scheme like mdl:// needs its package installed."
        )
    ctx = ResolveContext(token=token, allow_insecure=allow_insecure, timeout=timeout)
    return resolver.resolve(src, ctx)
