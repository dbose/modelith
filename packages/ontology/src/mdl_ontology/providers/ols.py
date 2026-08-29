"""OLS4 resolver (spec §4).

`OLS4Resolver` talks to an OLS4 instance (EBI's public one at
https://www.ebi.ac.uk/ols4/api, or an internal OLS-compatible deployment) — no auth
for the public instance. It normalises OLS's search/ontology/term JSON into the neutral
`ResolvedTerm`/`OntologyRef`/`TermCard` shapes the registry facade already speaks.

OLS4 API surfaces used:
- ``GET /api/search?q=<text>&ontology=<id>&rows=<n>`` -> ``response.docs[]`` with
  ``iri``/``label``/``description``/``ontology_name``/``obo_id``/``synonym``.
- ``GET /api/ontologies?size=<n>`` -> ``_embedded.ontologies[]`` with ``ontologyId``,
  ``config.title``/``description``/``numberOfTerms``.
- ``GET /api/ontologies/<id>/terms?iri=<iri>`` -> a term with HAL ``_links.parents`` /
  ``_links.children`` for the hierarchy.

The public shape is stable across OLS3/OLS4; the same adapter serves an internal
OLS-compatible registry (that variant just points `url` elsewhere — see
`OLSCompatibleResolver`).
"""

from __future__ import annotations

from urllib.parse import quote

from mdl_ontology.providers.base import OntologyRef, ResolvedTerm, TermCard, local_name
from mdl_ontology.providers.remote import RemoteProvider


def _first(v):
    """OLS returns some fields as a list, some as a scalar. Take the first."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


class OLS4Resolver(RemoteProvider):
    """A public OLS4 (or OLS-compatible) instance. `url` is the API root, e.g.
    ``https://www.ebi.ac.uk/ols4/api``."""

    def list_ontologies(self) -> list[OntologyRef]:
        data = self._get_json("/ontologies", {"size": 200})
        if not data:
            return []
        embedded = (data.get("_embedded") or {}).get("ontologies") or []
        out: list[OntologyRef] = []
        for o in embedded:
            oid = o.get("ontologyId")
            if not oid:
                continue
            if self.ontologies and oid not in self.ontologies:
                continue
            cfg = o.get("config") or {}
            out.append(
                OntologyRef(
                    id=oid,
                    name=cfg.get("title") or oid,
                    description=cfg.get("description"),
                    namespace=cfg.get("baseUris")[0]
                    if isinstance(cfg.get("baseUris"), list) and cfg.get("baseUris")
                    else None,
                    count=o.get("numberOfTerms"),
                )
            )
        return out

    def search(
        self, query: str, *, within: str | None = None, limit: int = 20
    ) -> list[ResolvedTerm]:
        q = (query or "").strip()
        if not q:
            return []
        params: dict = {"q": q, "rows": limit}
        scope = within or (self.ontologies[0] if self.ontologies else None)
        if scope:
            params["ontology"] = scope
        data = self._get_json("/search", params)
        if not data:
            return []
        docs = (data.get("response") or {}).get("docs") or []
        out: list[ResolvedTerm] = []
        seen: set[str] = set()
        for d in docs:
            iri = d.get("iri")
            if not iri or iri in seen:
                continue
            seen.add(iri)
            out.append(
                ResolvedTerm(
                    iri=iri,
                    prefixed=d.get("obo_id") or self._prefixed(iri),
                    label=_first(d.get("label")),
                    definition=_first(d.get("description")),
                    source=self.name,
                    source_kind="ontology-class",
                )
            )
            if len(out) >= limit:
                break
        return out

    def describe(self, ref: str) -> TermCard | None:
        iri = ref if ref.startswith("http") else self.expand(ref)
        term = self._fetch_term(iri) if iri else None
        # A CURIE like `fibo:FinancialInstrument` is an obo_id label, not a
        # mechanically-expandable prefix (FIBO IRIs carry module paths), so a naive
        # expand often misses. Fall back to search and match on iri / obo_id.
        if term is None:
            tail = ref.split(":", 1)[-1]
            for hit in self.search(tail, limit=25):
                if hit.iri == ref or hit.prefixed == ref or hit.iri == iri:
                    iri = hit.iri
                    term = self._fetch_term(iri) or {
                        "iri": hit.iri,
                        "obo_id": hit.prefixed,
                        "label": hit.label,
                        "description": [hit.definition] if hit.definition else [],
                    }
                    break
        if term is None:
            return None
        iri = term.get("iri") or iri
        parents = self._neighbours(term, "parents")
        children = self._neighbours(term, "children")
        return TermCard(
            iri=iri,
            prefixed=term.get("obo_id") or self._prefixed(iri),
            label=_first(term.get("label")) or local_name(iri),
            definition=_first(term.get("description")),
            source=self.name,
            synonyms=list(term.get("synonyms") or []),
            parents=parents,
            children=children,
        )

    # --- helpers ------------------------------------------------------------

    def _fetch_term(self, iri: str | None) -> dict | None:
        """GET one term-detail by IRI (in-scope ontology, or let OLS pick)."""
        if not iri:
            return None
        onto = self.ontologies[0] if self.ontologies else None
        path = f"/ontologies/{onto}/terms" if onto else "/terms"
        data = self._get_json(path, {"iri": iri})
        if not data:
            return None
        docs = (data.get("_embedded") or {}).get("terms") or []
        return docs[0] if docs else (data if data.get("iri") else None)

    def _neighbours(self, term: dict, rel: str) -> list[dict]:
        link = ((term.get("_links") or {}).get(rel) or {}).get("href")
        if not link:
            return []
        data = self._get_json(link)
        if not data:
            return []
        docs = (data.get("_embedded") or {}).get("terms") or []
        out = []
        for t in docs:
            iri = t.get("iri")
            if not iri:
                continue
            out.append(
                {
                    "iri": iri,
                    "prefixed": t.get("obo_id") or self._prefixed(iri),
                    "label": _first(t.get("label")) or local_name(iri),
                }
            )
        return sorted(out, key=lambda c: c["label"] or "")

    def _prefixed(self, iri: str) -> str:
        for pfx, ns in self.prefixes.items():
            if iri.startswith(ns):
                return f"{pfx}:{iri[len(ns):]}"
        return iri


class OLSCompatibleResolver(OLS4Resolver):
    """An internal OLS-compatible deployment. Identical API shape to public OLS4; a
    distinct class so config/telemetry can tell them apart and so an internal instance
    can send a bearer token (via `token_env`) that the public one never needs."""

    def _double_encode(self, iri: str) -> str:  # some internal deployments need this
        return quote(quote(iri, safe=""), safe="")
