"""Fetch a shared reverse: config from a URL or a git host, safely.

`mdl reverse-config import` accepts a local file, a raw URL, or — for convenience — the
URL you see in a browser (a GitHub/GitLab *blob* page) or a `github:`/`gitlab:` shorthand;
those are normalised to the raw-file URL. Fetching is guarded: https-only by default, a
size cap, and a content-type/parse sanity check so an HTML error page or a giant body
can't be silently written into a project. Private repos work via an optional bearer token
(a `--token`, or `$MODELITH_IMPORT_TOKEN` / `$GITHUB_TOKEN`), mirroring the auth-header
pattern the ontology RemoteProvider already uses.

Pure/deterministic apart from the actual GET, so URL normalisation and the guards are
unit-testable without the network.
"""

from __future__ import annotations

import os
import re

# 10 MiB is orders of magnitude more than any real reverse: config; a body larger than
# this is a mistake (wrong URL, an HTML page, a tarball) — reject before parsing.
_MAX_BYTES = 10 * 1024 * 1024

_GH_BLOB = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/(.+)$")
_GL_BLOB = re.compile(r"^https?://gitlab\.com/([^/]+)/([^/]+)/-/blob/(.+)$")
_SHORT = re.compile(r"^(github|gitlab):([^/]+)/([^/@]+)/(.+?)(?:@([^@]+))?$")


class ImportError_(Exception):
    """A user-facing import failure (bad URL, insecure, too big, not a config)."""


def normalize_source(src: str) -> str:
    """Turn a human-facing git URL / shorthand into a raw-file URL. A raw URL, a plain
    file path, or an unrecognised URL passes through unchanged.

    - github.com/o/r/blob/<ref>/<path>    -> raw.githubusercontent.com/o/r/<ref>/<path>
    - gitlab.com/o/r/-/blob/<ref>/<path>  -> gitlab.com/o/r/-/raw/<ref>/<path>
    - github:o/r/<path>[@ref]             -> raw.githubusercontent.com/o/r/<ref|HEAD>/<path>
    - gitlab:o/r/<path>[@ref]             -> gitlab.com/o/r/-/raw/<ref|HEAD>/<path>
    """
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


def is_url(src: str) -> bool:
    return src.startswith(("http://", "https://")) or bool(_SHORT.match(src))


def fetch_config_text(
    src: str, *, token: str | None = None, allow_insecure: bool = False, timeout: float = 15.0
) -> str:
    """Fetch the reverse: config text from `src` (a URL or git shorthand), with the
    safety guards. Raises ImportError_ with a clear message on any failure."""
    import httpx

    url = normalize_source(src)
    if url.startswith("http://") and not allow_insecure:
        raise ImportError_(
            "refusing to import over plain http:// (use https, or pass --allow-insecure)"
        )
    tok = token or os.environ.get("MODELITH_IMPORT_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        hint = " (private repo? pass a token)" if code in (401, 403, 404) else ""
        raise ImportError_(f"fetch failed: HTTP {code}{hint}") from e
    except Exception as e:  # noqa: BLE001 - network/DNS/timeout -> one clean message
        raise ImportError_(f"could not fetch {url}: {e}") from e

    body = resp.content
    if len(body) > _MAX_BYTES:
        raise ImportError_(f"config is too large ({len(body)} bytes > {_MAX_BYTES} cap)")
    ctype = (resp.headers.get("content-type") or "").lower()
    text = resp.text
    # Reject an HTML error/landing page early (a blob URL that slipped normalisation, a
    # 200-with-login-page). YAML configs are text/plain, text/yaml, or application/*.
    if "text/html" in ctype or text.lstrip()[:1] == "<":
        raise ImportError_(
            "the URL returned an HTML page, not a config — use the RAW file URL "
            "(or a github:/gitlab: shorthand)"
        )
    return text
