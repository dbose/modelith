"""Docs site generator tests: files exist, deterministic, offline, neighbourhood BFS."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "tests"))

from mdl_docs import neighbourhood, render_site  # noqa: E402
from mdl_emit_erd import emit_mermaid  # noqa: E402

from mdl_core.repo import ModelRepo  # noqa: E402

from model_builders import write_model  # noqa: E402


@pytest.fixture
def model(tmp_path):
    write_model(tmp_path)
    return ModelRepo.load(tmp_path).model


def test_site_files_exist(model, tmp_path):
    out = tmp_path / "site"
    files = render_site(model, out)
    assert (out / "index.html").exists()
    assert (out / "glossary.html").exists()
    assert (out / "assets" / "docs.css").exists()
    assert (out / "assets" / "mermaid.min.js").exists()
    # one page per logical entity
    entity_pages = list((out / "entities").glob("*.html"))
    assert len(entity_pages) == len(model.logical_entities)
    assert all(p in files for p in entity_pages)


def test_entity_page_structure(model, tmp_path):
    out = tmp_path / "site"
    render_site(model, out)
    page = (out / "entities" / "counterparty.html").read_text(encoding="utf-8")
    # the dbt-docs-style sections and the neighbourhood ERD
    assert "Attributes" in page
    assert "Neighbourhood" in page
    assert 'class="mermaid"' in page
    # the left tree-view lists all entities on every page
    for le in model.logical_entities.values():
        assert le.name in page


def test_offline_no_cdn(model, tmp_path):
    out = tmp_path / "site"
    files = render_site(model, out)
    for p in files:
        if p.suffix == ".html":
            text = p.read_text(encoding="utf-8")
            assert "//cdn" not in text
            assert "https://" not in text  # no remote stylesheet/script refs


def test_deterministic(model, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    render_site(model, a)
    render_site(model, b)
    for rel in ["index.html", "glossary.html", "entities/counterparty.html"]:
        assert (a / rel).read_text() == (b / rel).read_text()


def test_neighbourhood_radius(model):
    # counterparty <-> trade is a single edge; radius grows the reachable set.
    cp = next(e.id for e in model.logical_entities.values() if e.name == "counterparty")
    assert neighbourhood(model, cp, 0) == {cp}
    r1 = neighbourhood(model, cp, 1)
    assert len(r1) == 2  # counterparty + trade
    # radius 2 is stable here (only 2 entities), never exceeds the model
    assert neighbourhood(model, cp, 2) <= set(model.logical_entities)


def test_emit_mermaid_include_filters(model):
    cp = next(e.id for e in model.logical_entities.values() if e.name == "counterparty")
    scoped = emit_mermaid(model, include={cp})
    full = emit_mermaid(model)
    # unchanged default still renders both entities
    assert "counterparty" in full and "trade" in full
    # scoped to counterparty alone drops the trade table and the cross edge
    assert "counterparty" in scoped
    assert "\n  trade {" not in scoped
