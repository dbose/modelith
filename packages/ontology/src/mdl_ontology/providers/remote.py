"""Shared plumbing for remote ontology resolvers (spec §4).

A remote resolver is strictly a live-lookup / authoring-UX concern: autocomplete,
search-as-you-type, hover definitions. It is NEVER a build-time dependency — whatever
URI it surfaces is written into `ontology_refs` and pinned via `ontology.lock` exactly
like a vendored term (spec §4). So these providers only implement the browse/search
half of the contract; `expand`/`resolves` degrade gracefully (a remote source vouches
for a term only if it can look it up, and never blocks validation offline).

`RemoteProvider` gives subclasses: a lazily-created `httpx.Client` with auth headers,
a tiny in-memory TTL cache so typeahead doesn't hammer the API, and default
`expand`/`resolves` behaviour. Subclasses implement `list_ontologies`, `search` and
`describe` against their specific API shape.
"""

from __future__ import annotations

import os
import time
from typing import Any

from mdl_ontology.providers.base import OntologyRef, ResolvedTerm, TermCard


class _TTLCache:
    """Minimal time-boxed cache. Keeps the last `maxsize` keys; evicts oldest.

    Time is injected (`_now`) rather than read from the clock directly so tests stay
    deterministic and so it never touches a disallowed clock in a workflow context."""

    def __init__(self, ttl: float = 300.0, maxsize: int = 512) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._store: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any, now: float) -> Any | None:
        hit = self._store.get(key)
        if hit is None:
            return None
        stamp, value = hit
        if now - stamp > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def put(self, key: Any, value: Any, now: float) -> None:
        if len(self._store) >= self.maxsize:
            # evict the oldest entry
            oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
            self._store.pop(oldest, None)
        self._store[key] = (now, value)


class RemoteProvider:
    """Base for HTTP-backed resolvers. Subclasses set `name`/`layer` and implement
    `list_ontologies`/`search`/`describe`."""

    def __init__(
        self,
        name: str,
        layer: str,
        url: str,
        *,
        apikey_env: str | None = None,
        token_env: str | None = None,
        ontologies: list[str] | None = None,
        prefixes: dict[str, str] | None = None,
        timeout: float = 10.0,
        ttl: float = 300.0,
    ) -> None:
        self.name = name
        self.layer = layer
        self.base_url = url.rstrip("/")
        self.apikey_env = apikey_env
        self.token_env = token_env
        self.ontologies = ontologies  # scope to these vocab ids, or None for all
        self.prefixes = dict(prefixes or {})
        self.timeout = timeout
        self._cache = _TTLCache(ttl=ttl)
        self._client: Any | None = None

    # --- http ---------------------------------------------------------------

    def _now(self) -> float:
        # Wall clock is fine at request time (not during a workflow replay); isolate
        # it here so a subclass/test can override.
        return time.time()

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token_env:
            tok = os.environ.get(self.token_env)
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        return headers

    def _client_or_make(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                timeout=self.timeout, headers=self._auth_headers(), follow_redirects=True
            )
        return self._client

    def _get_json(self, path: str, params: dict | None = None) -> Any | None:
        """GET a JSON body, cached by (path, params). Returns None on any network or
        decode error — a failing remote source must never break the browse facade."""
        key = (path, tuple(sorted((params or {}).items())))
        now = self._now()
        cached = self._cache.get(key, now)
        if cached is not None:
            return cached
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        try:
            resp = self._client_or_make().get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception:  # noqa: BLE001 - remote failures degrade to "no results"
            return None
        self._cache.put(key, data, now)
        return data

    def _apikey_param(self) -> dict[str, str]:
        if self.apikey_env:
            key = os.environ.get(self.apikey_env)
            if key:
                return {"apikey": key}
        return {}

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    # --- contract defaults --------------------------------------------------

    def list_ontologies(self) -> list[OntologyRef]:  # pragma: no cover - overridden
        return []

    def search(
        self, query: str, *, within: str | None = None, limit: int = 20
    ) -> list[ResolvedTerm]:  # pragma: no cover - overridden
        return []

    def describe(self, ref: str) -> TermCard | None:  # pragma: no cover - overridden
        return None

    def expand(self, prefixed: str) -> str | None:
        if ":" not in prefixed:
            return None
        pfx, tail = prefixed.split(":", 1)
        ns = self.prefixes.get(pfx)
        return ns + tail if ns is not None else None

    def resolves(self, prefixed: str) -> bool:
        """A remote source vouches for a term only if it can look it up. Never blocks
        validation: returns False (not an error) when offline or unknown, so a locked
        cache / local file is what actually gates MDL-E213."""
        try:
            return self.describe(prefixed) is not None
        except Exception:  # noqa: BLE001
            return False
