"""Collibra Ontology Domains resolver (spec §4).

`CollibraOntologyDomainsResolver` browses ontology terms held in Collibra, reusing the
governance Collibra connection but pointed at Ontology Domains instead of the
meta-model. It is a *read/UX* resolver only (autocomplete/search/hover); it never
writes and is never a build dependency (spec §4).

Two-phase GraphQL, because Collibra models an ontology as a *domain of assets*, not a
single catalog:
  1. **list_ontologies** enumerates domains whose type is one of the configured
     `domain_types` (varies per enterprise, hence config-driven) — each becomes a
     browsable "ontology".
  2. **search(within=<domain id>)** runs a scoped `assets` query inside one domain,
     pulling each asset's displayName plus the configured URI / Definition string
     attributes.

The GraphQL HTTP call goes through an injectable `transport` (a callable
`(query, variables) -> dict`), mirroring the governance adapter's `MockTransport`
pattern so the whole flow is testable offline with recorded responses.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from mdl_ontology.providers.base import OntologyRef, ResolvedTerm, TermCard, local_name, score

# GraphQL query text is data, not secrets — kept inline so the shape is auditable.
_LIST_DOMAINS = """
query ListOntologyDomains($types: [String!]) {
  domains(where: { type: { publicId: { in: $types } } }) {
    id
    name
    description
    assetCount
  }
}
"""

_SEARCH_ASSETS = """
query SearchAssets($domainId: UUID!, $q: String, $limit: Int!) {
  assets(
    where: { domain: { id: { eq: $domainId } }, displayName: { contains: $q } }
    limit: $limit
  ) {
    id
    displayName
    stringAttributes { type { publicId } value }
  }
}
"""

_GET_ASSET = """
query GetAsset($id: UUID!) {
  assets(where: { id: { eq: $id } }, limit: 1) {
    id
    displayName
    stringAttributes { type { publicId } value }
  }
}
"""

# default attribute publicIds; overridable via config `attributes`
_DEFAULT_ATTRS = {"uri": "uri", "definition": "Definition"}


class CollibraOntologyDomainsResolver:
    """Browse ontology terms stored as Collibra assets in Ontology Domains."""

    def __init__(
        self,
        name: str,
        layer: str,
        url: str,
        *,
        token_env: str | None = None,
        domain_types: list[str] | None = None,
        attributes: dict[str, str] | None = None,
        prefixes: dict[str, str] | None = None,
        transport: Callable[[str, dict], dict] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.name = name
        self.layer = layer
        self.base_url = url.rstrip("/")
        self.token_env = token_env
        # which Collibra domain types are treated as ontologies (config-driven)
        self.domain_types = domain_types or ["Ontology"]
        attrs = {**_DEFAULT_ATTRS, **(attributes or {})}
        self._uri_attr = attrs["uri"]
        self._def_attr = attrs["definition"]
        self.prefixes = dict(prefixes or {})
        self.timeout = timeout
        self._transport = transport  # injectable; real HTTP built lazily if None

    # --- transport ----------------------------------------------------------

    def _run(self, query: str, variables: dict) -> dict | None:
        try:
            if self._transport is not None:
                return self._transport(query, variables)
            return self._http_graphql(query, variables)
        except Exception:  # noqa: BLE001 - a failing catalog degrades to no results
            return None

    def _http_graphql(self, query: str, variables: dict) -> dict:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.token_env:
            tok = os.environ.get(self.token_env)
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        resp = httpx.post(
            f"{self.base_url}/graphql",
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("data") or {}

    # --- browse phase one ---------------------------------------------------

    def list_ontologies(self) -> list[OntologyRef]:
        data = self._run(_LIST_DOMAINS, {"types": self.domain_types})
        if not data:
            return []
        out: list[OntologyRef] = []
        for d in data.get("domains") or []:
            did = d.get("id")
            if not did:
                continue
            out.append(
                OntologyRef(
                    id=did,
                    name=d.get("name") or did,
                    description=d.get("description"),
                    count=d.get("assetCount"),
                )
            )
        return out

    # --- search phase two ---------------------------------------------------

    def search(
        self, query: str, *, within: str | None = None, limit: int = 20
    ) -> list[ResolvedTerm]:
        q = (query or "").strip()
        domains = [within] if within else [o.id for o in self.list_ontologies()]
        results: list[tuple[int, ResolvedTerm]] = []
        seen: set[str] = set()
        for domain_id in domains:
            data = self._run(
                _SEARCH_ASSETS, {"domainId": domain_id, "q": q, "limit": limit}
            )
            if not data:
                continue
            for a in data.get("assets") or []:
                term = self._asset_to_term(a)
                if term is None or term.iri in seen:
                    continue
                seen.add(term.iri)
                hay = f"{term.label or ''} {term.definition or ''}".lower()
                results.append((score(q.lower(), hay, (term.label or "").lower()), term))
        results.sort(key=lambda t: (-t[0], t[1].label or ""))
        return [t for _, t in results[:limit]]

    def describe(self, ref: str) -> TermCard | None:
        # Collibra assets are addressed by id, but alignments store the URI; resolve by
        # scanning (bounded) or by direct id when ref looks like a UUID.
        if _looks_like_uuid(ref):
            data = self._run(_GET_ASSET, {"id": ref})
            assets = (data or {}).get("assets") or []
            term = self._asset_to_term(assets[0]) if assets else None
        else:
            hits = self.search(local_name(ref), limit=25)
            term = next((t for t in hits if t.iri == ref or ref in t.iri), None)
        if term is None:
            return None
        return TermCard(
            iri=term.iri,
            prefixed=term.prefixed,
            label=term.label,
            definition=term.definition,
            source=self.name,
            synonyms=[],
            parents=[],
            children=[],
        )

    # --- resolution ---------------------------------------------------------

    def expand(self, prefixed: str) -> str | None:
        if ":" not in prefixed:
            return None
        pfx, tail = prefixed.split(":", 1)
        ns = self.prefixes.get(pfx)
        return ns + tail if ns is not None else None

    def resolves(self, prefixed: str) -> bool:
        try:
            return self.describe(prefixed) is not None
        except Exception:  # noqa: BLE001
            return False

    # --- helpers ------------------------------------------------------------

    def _asset_to_term(self, asset: dict) -> ResolvedTerm | None:
        attrs = {
            (sa.get("type") or {}).get("publicId"): sa.get("value")
            for sa in asset.get("stringAttributes") or []
        }
        iri = attrs.get(self._uri_attr)
        if not iri:
            return None
        return ResolvedTerm(
            iri=iri,
            prefixed=self._prefixed(iri),
            label=asset.get("displayName"),
            definition=attrs.get(self._def_attr),
            source=self.name,
            source_kind="glossary-term",
        )

    def _prefixed(self, iri: str) -> str:
        for pfx, ns in self.prefixes.items():
            if iri.startswith(ns):
                return f"{pfx}:{iri[len(ns):]}"
        return iri


def _looks_like_uuid(s: str) -> bool:
    return len(s) == 36 and s.count("-") == 4
