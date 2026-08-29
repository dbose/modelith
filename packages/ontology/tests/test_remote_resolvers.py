"""Remote OntologyResolver adapters (spec §4) over mocked/recorded responses.

No live network: OLS4/OntoPortal patch the provider's `_get_json`; Collibra uses its
injectable `transport`. Each test asserts the adapter normalises its API's shape into
the neutral ResolvedTerm / OntologyRef / TermCard contract.
"""

from __future__ import annotations

from pathlib import Path

from mdl_ontology import cache_from_registry, cache_resolved_term
from mdl_ontology.providers.collibra import CollibraOntologyDomainsResolver
from mdl_ontology.providers.ols import OLS4Resolver
from mdl_ontology.providers.ontoportal import OntoPortalResolver
from mdl_ontology.registry import OntologyRegistry, VocabularySource, build_registry

# --- OLS4 ------------------------------------------------------------------

_OLS_SEARCH = {
    "response": {
        "docs": [
            {
                "iri": "http://purl.obolibrary.org/obo/MONDO_0004992",
                "label": "cancer",
                "description": ["A disease characterised by uncontrolled growth."],
                "obo_id": "MONDO:0004992",
                "ontology_name": "mondo",
            }
        ]
    }
}
_OLS_ONTOLOGIES = {
    "_embedded": {
        "ontologies": [
            {
                "ontologyId": "mondo",
                "numberOfTerms": 25000,
                "config": {"title": "Mondo Disease Ontology", "description": "diseases"},
            }
        ]
    }
}
_OLS_TERM = {
    "_embedded": {
        "terms": [
            {
                "iri": "http://purl.obolibrary.org/obo/MONDO_0004992",
                "label": "cancer",
                "description": ["A disease..."],
                "obo_id": "MONDO:0004992",
                "synonyms": ["malignant neoplasm"],
                "_links": {
                    "parents": {"href": "http://x/parents"},
                },
            }
        ]
    }
}
_OLS_PARENTS = {
    "_embedded": {
        "terms": [
            {"iri": "http://x/DISEASE", "label": "disease", "obo_id": "MONDO:0000001"}
        ]
    }
}


def _ols(monkeypatch):
    r = OLS4Resolver("ols", "industry", "https://ols.example/api", ontologies=["mondo"])

    def fake_get_json(path, params=None):
        if path == "/search":
            return _OLS_SEARCH
        if path == "/ontologies":
            return _OLS_ONTOLOGIES
        if path.endswith("/terms"):
            return _OLS_TERM
        if path == "http://x/parents":
            return _OLS_PARENTS
        return None

    monkeypatch.setattr(r, "_get_json", fake_get_json)
    return r


def test_ols_search(monkeypatch):
    r = _ols(monkeypatch)
    hits = r.search("cancer")
    assert len(hits) == 1
    assert hits[0].iri.endswith("MONDO_0004992")
    assert hits[0].label == "cancer"
    assert hits[0].prefixed == "MONDO:0004992"
    assert hits[0].source == "ols"


def test_ols_list_ontologies(monkeypatch):
    r = _ols(monkeypatch)
    onts = r.list_ontologies()
    assert onts[0].id == "mondo"
    assert onts[0].name == "Mondo Disease Ontology"
    assert onts[0].count == 25000


def test_ols_describe_with_hierarchy(monkeypatch):
    r = _ols(monkeypatch)
    card = r.describe("http://purl.obolibrary.org/obo/MONDO_0004992")
    assert card is not None
    assert card.label == "cancer"
    assert "malignant neoplasm" in card.synonyms
    assert card.parents and card.parents[0]["label"] == "disease"


def test_ols_describe_curie_falls_back_to_search(monkeypatch):
    """A CURIE like `fibo:FinancialInstrument` is an obo_id label, not a
    mechanically-expandable prefix (FIBO IRIs carry module paths). Naive expand
    misses; describe must fall back to search + match on obo_id. (Regression from the
    demo-OLS dogfood.)"""
    r = OLS4Resolver(
        "ols", "industry", "https://ols.example/api",
        prefixes={"fibo": "https://x/fibo/"},  # wrong-length expansion on purpose
    )

    def fake_get_json(path, params=None):
        # /terms lookups by the (wrong) expanded iri miss; only /search finds it
        if path == "/search":
            return {"response": {"docs": [{
                "iri": "https://x/fibo/FBC/FI/FinancialInstrument",
                "obo_id": "fibo:FinancialInstrument",
                "label": "Financial Instrument",
                "description": ["A tradable contract."],
            }]}}
        return None  # /terms returns nothing for the naive-expanded iri

    monkeypatch.setattr(r, "_get_json", fake_get_json)
    card = r.describe("fibo:FinancialInstrument")
    assert card is not None
    assert card.label == "Financial Instrument"
    assert card.iri == "https://x/fibo/FBC/FI/FinancialInstrument"


# --- OntoPortal / BioPortal ------------------------------------------------

_BP_SEARCH = {
    "collection": [
        {
            "@id": "http://acme/onto/Party",
            "prefLabel": "Party",
            "definition": ["A legal person."],
            "synonym": ["Counterparty"],
            "links": {"ontology": "https://data.example/ontologies/ACME"},
        }
    ]
}
_BP_ONTOLOGIES = [{"acronym": "ACME", "name": "Acme Core Ontology"}]


def test_ontoportal_search(monkeypatch):
    r = OntoPortalResolver("bp", "industry", "https://bp.example", apikey_env="X")

    def fake(path, params=None):
        return _BP_SEARCH if path == "/search" else _BP_ONTOLOGIES

    monkeypatch.setattr(r, "_get_json", fake)
    hits = r.search("party")
    assert hits[0].iri == "http://acme/onto/Party"
    assert hits[0].label == "Party"
    assert hits[0].definition == "A legal person."


def test_ontoportal_list(monkeypatch):
    r = OntoPortalResolver("bp", "industry", "https://bp.example", apikey_env="X")
    monkeypatch.setattr(r, "_get_json", lambda p, params=None: _BP_ONTOLOGIES)
    onts = r.list_ontologies()
    assert onts[0].id == "ACME"


# --- Collibra Ontology Domains (injectable transport) ----------------------


def _collibra_transport(query, variables):
    if "domains(" in query:
        return {
            "domains": [
                {"id": "d1", "name": "Enterprise Ontology", "assetCount": 2},
            ]
        }
    if "assets(" in query and "displayName: { contains" in query:
        return {
            "assets": [
                {
                    "id": "a1",
                    "displayName": "Tradable Asset",
                    "stringAttributes": [
                        {"type": {"publicId": "uri"}, "value": "https://acme/TradableAsset"},
                        {"type": {"publicId": "Definition"}, "value": "An asset you can trade."},
                    ],
                }
            ]
        }
    return {}


def test_collibra_two_phase_search():
    r = CollibraOntologyDomainsResolver(
        "col", "core", "https://acme.collibra.com",
        domain_types=["Ontology"], transport=_collibra_transport,
    )
    onts = r.list_ontologies()
    assert onts[0].id == "d1"
    assert onts[0].name == "Enterprise Ontology"

    hits = r.search("tradable", within="d1")
    assert hits[0].iri == "https://acme/TradableAsset"
    assert hits[0].label == "Tradable Asset"
    assert hits[0].definition == "An asset you can trade."
    assert hits[0].source_kind == "glossary-term"


def test_collibra_custom_attribute_map():
    def transport(query, variables):
        if "domains(" in query:
            return {"domains": [{"id": "d1", "name": "D"}]}
        return {
            "assets": [
                {
                    "id": "a1",
                    "displayName": "Party",
                    "stringAttributes": [
                        {"type": {"publicId": "conceptURI"}, "value": "https://x/Party"},
                        {"type": {"publicId": "desc"}, "value": "A party."},
                    ],
                }
            ]
        }

    r = CollibraOntologyDomainsResolver(
        "col", "core", "https://x", domain_types=["Ontology"],
        attributes={"uri": "conceptURI", "definition": "desc"}, transport=transport,
    )
    hits = r.search("party", within="d1")
    assert hits[0].iri == "https://x/Party"
    assert hits[0].definition == "A party."


# --- registry wiring -------------------------------------------------------


def test_registry_registers_remote_provider():
    reg = OntologyRegistry(".")
    reg.register(
        VocabularySource(name="ols", layer="industry", type="ols",
                         url="https://ols.example/api")
    )
    reg.register(
        VocabularySource(name="col", layer="core", type="collibra",
                         url="https://x", domain_types=["Ontology"])
    )
    kinds = {type(p).__name__ for p in reg.providers}
    assert "OLS4Resolver" in kinds
    assert "CollibraOntologyDomainsResolver" in kinds


def test_unknown_remote_type_is_skipped_not_fatal():
    reg = OntologyRegistry(".")
    reg.register(VocabularySource(name="x", layer="industry", type="mystery"))
    # source recorded, but no provider constructed
    assert "x" in reg.sources
    assert reg.providers == []


# --- cache-on-align (spec §4) ----------------------------------------------


def test_cache_resolved_term_roundtrips_offline(tmp_path):
    cache_resolved_term(
        tmp_path,
        "https://acme/TradableAsset",
        label="Tradable Asset",
        definition="An asset you can trade.",
        synonyms=["Security"],
        source="collibra",
    )
    # a registry over just the resolved cache resolves + searches it offline
    reg = build_registry(tmp_path, [])
    reg.load()
    hits = reg.search("tradable")
    assert any(h.iri == "https://acme/TradableAsset" for h in hits)
    # synonym carried through
    assert any("TradableAsset" in h.iri for h in reg.search("security"))


def test_cache_from_registry_snapshots_remote(tmp_path, monkeypatch):
    reg = OntologyRegistry(tmp_path)
    r = CollibraOntologyDomainsResolver(
        "col", "core", "https://x", domain_types=["Ontology"],
        transport=_collibra_transport,
    )
    reg.providers.append(r)
    path = cache_from_registry(tmp_path, reg, "https://acme/TradableAsset")
    assert path is not None
    assert Path(path).exists()
    assert "TradableAsset" in Path(path).read_text()


def test_cache_from_registry_falls_back_to_search(tmp_path, monkeypatch):
    """When a resolver has search but no term-detail (describe) endpoint — the OLS
    mock case — cache-on-align still snapshots the term from the search hit."""
    reg = OntologyRegistry(tmp_path)
    r = OLS4Resolver("ols", "industry", "https://ols.example/api")

    def fake_get_json(path, params=None):
        # only /search is served; /terms (describe) returns nothing
        if path == "/search":
            return _OLS_SEARCH
        return None

    monkeypatch.setattr(r, "_get_json", fake_get_json)
    reg.providers.append(r)
    # align to the prefixed/obo id the search returns
    path = cache_from_registry(tmp_path, reg, "MONDO:0004992")
    assert path is not None
    assert "MONDO_0004992" in Path(path).read_text()


# --- optional live smoke test (skipped offline / in CI) --------------------

import os  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.skipif(
    os.environ.get("MDL_LIVE_OLS") != "1",
    reason="live OLS4 smoke test; set MDL_LIVE_OLS=1 to run",
)
def test_ols_live_smoke():
    r = OLS4Resolver("ols", "industry", "https://www.ebi.ac.uk/ols4/api")
    hits = r.search("diabetes", limit=3)
    assert hits and hits[0].iri.startswith("http")
