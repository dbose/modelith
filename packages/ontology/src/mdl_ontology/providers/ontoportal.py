"""OntoPortal / BioPortal resolver (spec §4).

`OntoPortalResolver` talks to an OntoPortal-family instance (BioPortal, AgroPortal, or
a private OntoPortal deployment). These require an API key, passed as an `apikey` query
param sourced from `apikey_env`.

API surfaces used:
- ``GET /search?q=<text>&ontologies=<acronyms>&pagesize=<n>&apikey=`` -> ``collection[]``
  with ``@id``/``prefLabel``/``synonym[]``/``definition[]`` and ``links.ontology``.
- ``GET /ontologies?apikey=`` -> ``[{acronym, name, ...}]``.
- ``GET /ontologies/<acronym>/classes/<double-encoded-id>?apikey=`` -> a class with
  ``parents``/``children`` links for the hierarchy.

Everything normalises to the neutral `ResolvedTerm`/`OntologyRef`/`TermCard` shapes.
"""

from __future__ import annotations

from urllib.parse import quote

from mdl_ontology.providers.base import OntologyRef, ResolvedTerm, TermCard, local_name
from mdl_ontology.providers.remote import RemoteProvider


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


class OntoPortalResolver(RemoteProvider):
    """A BioPortal/OntoPortal instance. `url` is the API root (e.g.
    ``https://data.bioontology.org``); `apikey_env` names the env var with the key."""

    def list_ontologies(self) -> list[OntologyRef]:
        data = self._get_json("/ontologies", self._apikey_param())
        if not data:
            return []
        out: list[OntologyRef] = []
        for o in data:
            acr = o.get("acronym")
            if not acr:
                continue
            if self.ontologies and acr not in self.ontologies:
                continue
            out.append(
                OntologyRef(id=acr, name=o.get("name") or acr, description=None)
            )
        return out

    def search(
        self, query: str, *, within: str | None = None, limit: int = 20
    ) -> list[ResolvedTerm]:
        q = (query or "").strip()
        if not q:
            return []
        params = {"q": q, "pagesize": limit, **self._apikey_param()}
        scope = within or (",".join(self.ontologies) if self.ontologies else None)
        if scope:
            params["ontologies"] = scope
        data = self._get_json("/search", params)
        if not data:
            return []
        out: list[ResolvedTerm] = []
        seen: set[str] = set()
        for c in data.get("collection") or []:
            iri = c.get("@id")
            if not iri or iri in seen:
                continue
            seen.add(iri)
            out.append(
                ResolvedTerm(
                    iri=iri,
                    prefixed=self._prefixed(iri),
                    label=c.get("prefLabel") or local_name(iri),
                    definition=_first(c.get("definition")),
                    source=self.name,
                    source_kind="ontology-class",
                )
            )
            if len(out) >= limit:
                break
        return out

    def describe(self, ref: str) -> TermCard | None:
        iri = self.expand(ref) if not ref.startswith("http") else ref
        if iri is None:
            hits = self.search(ref, limit=1)
            if not hits:
                return None
            iri = hits[0].iri
        # find the class via search (BioPortal needs the ontology acronym to GET a
        # class directly; search returns links we can follow without knowing it)
        hits = self.search(local_name(iri), limit=25)
        match = next((h for h in hits if h.iri == iri), None)
        if match is None:
            return None
        acronym = self._ontology_of(iri)
        parents, children, synonyms = [], [], []
        if acronym:
            cls = self._get_json(
                f"/ontologies/{acronym}/classes/{quote(quote(iri, safe=''), safe='')}",
                self._apikey_param(),
            )
            if cls:
                synonyms = list(cls.get("synonym") or [])
                parents = self._neighbours(cls, "parents", acronym)
                children = self._neighbours(cls, "children", acronym)
        return TermCard(
            iri=iri,
            prefixed=self._prefixed(iri),
            label=match.label,
            definition=match.definition,
            source=self.name,
            synonyms=synonyms,
            parents=parents,
            children=children,
        )

    # --- helpers ------------------------------------------------------------

    def _neighbours(self, cls: dict, rel: str, acronym: str) -> list[dict]:
        href = ((cls.get("links") or {}).get(rel))
        if not href:
            return []
        data = self._get_json(href, self._apikey_param())
        if not isinstance(data, list):
            return []
        out = []
        for t in data:
            iri = t.get("@id")
            if not iri:
                continue
            out.append(
                {
                    "iri": iri,
                    "prefixed": self._prefixed(iri),
                    "label": t.get("prefLabel") or local_name(iri),
                }
            )
        return sorted(out, key=lambda c: c["label"] or "")

    def _ontology_of(self, iri: str) -> str | None:
        # search returns links.ontology; refetch minimally to find the acronym
        data = self._get_json("/search", {"q": local_name(iri), "pagesize": 5,
                                          **self._apikey_param()})
        for c in (data or {}).get("collection") or []:
            if c.get("@id") == iri:
                onto = ((c.get("links") or {}).get("ontology")) or ""
                return onto.rstrip("/").rsplit("/", 1)[-1] or None
        return None

    def _prefixed(self, iri: str) -> str:
        for pfx, ns in self.prefixes.items():
            if iri.startswith(ns):
                return f"{pfx}:{iri[len(ns):]}"
        return iri
