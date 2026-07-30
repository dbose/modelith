"""Ontology API tests (E1) against the vendored FIBO subset."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_SUBSET = Path(__file__).resolve().parents[3] / "ontologies" / "industry" / "fibo-subset"


@pytest.fixture
def fibo_model_dir(model_dir):
    # vendor the subset into the model repo and declare it
    dest = model_dir / "ontologies" / "industry" / "fibo-subset"
    shutil.copytree(_SUBSET, dest)
    proj = model_dir / "mdl-project.yaml"
    proj.write_text(
        proj.read_text().replace(
            "ontology_stack: []",
            """ontology_stack:
  - name: fibo
    layer: industry
    format: turtle
    path: ontologies/industry/fibo-subset
    prefixes:
      fibo-fnd-pty-pty: "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/"
      fibo-fnd-agr-ctr: "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/"
      fibo-fbc-fi-fi: "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/"
      fibo-sec-fund: "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Funds/"
""",
        )
    )
    return model_dir


@pytest.fixture
def fclient(fibo_model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    return TestClient(create_app(fibo_model_dir))


def test_search_ranks_terms(fclient):
    doc = fclient.get("/api/ontology/search", params={"q": "bond"}).json()
    labels = [r["label"] for r in doc["results"]]
    assert "Bond" in labels
    assert doc["loaded_terms"] > 30
    top = doc["results"][0]
    assert top["definition"]  # cards carry definitions


def test_term_detail_with_hierarchy(fclient):
    card = fclient.get(
        "/api/ontology/term", params={"ref": "fibo-fbc-fi-fi:FinancialInstrument"}
    ).json()
    assert card["label"] == "Financial Instrument"
    narrower = {c["label"] for c in card["narrower"]}
    assert {"Security", "Debt Instrument", "Equity Instrument"} <= narrower
    broader = {c["label"] for c in card["broader"]}
    assert "Contract" in broader


def test_unknown_term_404(fclient):
    assert fclient.get("/api/ontology/term", params={"ref": "fibo-fbc-fi-fi:Nope"}).status_code == 404


def test_stack_groups_model_terms_by_layer(fclient):
    doc = fclient.get("/api/ontology/stack").json()
    core = doc["layers"]["core"]
    names = {t["name"] for t in core}
    assert "Counterparty" in names  # the model's core-layer FIBO-aligned entity
    cpty = next(t for t in core if t["name"] == "Counterparty")
    assert cpty["aligned_to"]["resolved"] is True
    assert cpty["aligned_to"]["label"] == "Party In Role"
    # resolved external terms are returned for ghost-node rendering
    assert any(t["label"] == "Party In Role" for t in doc["external_terms"])


def test_coverage_endpoint(fclient):
    doc = fclient.get("/api/ontology/coverage").json()
    assert doc["total_core"] >= 1
    assert 0 <= doc["coverage_pct"] <= 100
